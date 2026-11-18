# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.minecraft.save_flush_watcher import SaveFlushWatcher


@dataclass
class ConfigRuntimeState:
    config: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    language: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)
    # 负无穷表示从未落盘：0.0 会在开机不足 30 秒的机器上被节流窗口误伤
    player_state_persisted_at: float = float("-inf")


@dataclass
class ServiceRuntimeState:
    server: Optional[PluginServerInterface] = None
    server_ready: bool = False
    scheduler: Optional[Any] = None
    update_checker: Optional[Any] = None
    stale_watchdog: Optional[Any] = None
    command_handlers: Optional[Any] = None
    onebot: Optional[Any] = None
    discord: Optional[Any] = None
    custom_webhook: Optional[Any] = None
    stopping: threading.Event = field(default_factory=threading.Event)
    snapshot_query_lock: threading.Lock = field(default_factory=threading.Lock)
    save_flush_watcher: SaveFlushWatcher = field(default_factory=SaveFlushWatcher)


@dataclass
class PendingBackupState:
    """推迟中的定时备份。

    cancel 是推迟阶段专用的中止信号：与 backup.cancel 分开，避免取消推迟
    后残留的取消事件误杀下一次正常备份。deadline_monotonic 是本次推迟的
    期限（单调时钟），越过即跳过本次，等下一个计划点。
    """

    scheduled_at: str = ""
    deadline_monotonic: Optional[float] = None
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None

    @property
    def scheduled_time_text(self) -> str:
        return self.scheduled_at or "-"

    def deadline_passed(self) -> bool:
        return (
            self.deadline_monotonic is not None
            and time.monotonic() >= self.deadline_monotonic
        )


@dataclass
class ResticExecutionSlot:
    """一次 restic 进程执行的独立取消与进程槽位。

    备份使用 ``BackupRuntimeState`` 本身；中转库复制等并行任务必须使用
    独立实例，避免共用一个 cancel 事件时互相误杀。
    """

    cancel: threading.Event = field(default_factory=threading.Event)
    current_process: Optional[subprocess.Popen] = None
    cancel_reason: Optional[str] = None


@dataclass
class BackupRuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel: threading.Event = field(default_factory=threading.Event)
    current_process: Optional[subprocess.Popen] = None
    cancel_reason: Optional[str] = None
    thread: Optional[threading.Thread] = None
    label: Optional[str] = None
    pending: Optional[PendingBackupState] = None
    pending_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class RestoreRuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: Optional[threading.Thread] = None
    state_lock: threading.RLock = field(default_factory=threading.RLock)
    session: Optional[Any] = None


@dataclass
class StagingRuntimeState:
    """[beta] 中转库复制 worker 的锁、触发与独立进程槽位。

    复制与维护/purge/手管 restic 由 ``io_lock`` 串行；复制进程使用
    独立 ``copy_slot``，不复用备份的 cancel，避免互相误杀。
    """

    io_lock: threading.Lock = field(default_factory=threading.Lock)
    copy_slot: ResticExecutionSlot = field(default_factory=ResticExecutionSlot)
    copy_trigger: threading.Event = field(default_factory=threading.Event)
    worker: Optional[Any] = None
    # 每次 start 递增；旧线程发现代际不符即退出，避免重载后出现两个复制循环
    worker_generation: int = 0
    # purge 正在改名/重建远端：恢复入口据此拒绝，避免读到半成品远端
    purge_active: bool = False


@dataclass
class PluginRuntime:
    config_state: ConfigRuntimeState = field(default_factory=ConfigRuntimeState)
    service: ServiceRuntimeState = field(default_factory=ServiceRuntimeState)
    backup: BackupRuntimeState = field(default_factory=BackupRuntimeState)
    restore: RestoreRuntimeState = field(default_factory=RestoreRuntimeState)
    staging: StagingRuntimeState = field(default_factory=StagingRuntimeState)

    def release_backup_slot(self):
        """备份槽位的唯一释放路径：清空状态标记并释放锁（若仍持有）。"""
        self.backup.label = None
        self.backup.thread = None
        self.backup.cancel.clear()
        self.backup.cancel_reason = None
        if self.backup.lock.locked():
            self.backup.lock.release()

    def acquire_backup_slot_waiting(
        self,
        give_up_when: Optional[Callable[[], bool]] = None,
        interval: float = 1.0,
    ) -> bool:
        """轮询等待备份槽位；插件停止时立即放弃，避免僵尸线程拿到锁执行。

        give_up_when 返回 True 时放弃等待（如等待维护时发现槽位被普通备份占用）。
        """
        while True:
            if self.service.stopping.is_set():
                return False
            if self.backup.lock.acquire(timeout=interval):
                return True
            if give_up_when is not None and give_up_when():
                return False

    def register_pending_backup(
        self,
        scheduled_at: str = "",
        deadline_monotonic: Optional[float] = None,
    ) -> PendingBackupState:
        """登记推迟中的定时备份；单槽位，新登记覆盖旧登记（旧任务自行退出）。"""
        pending = PendingBackupState(
            scheduled_at=scheduled_at, deadline_monotonic=deadline_monotonic
        )
        with self.backup.pending_lock:
            previous = self.backup.pending
            self.backup.pending = pending
        if previous is not None:
            previous.cancel.set()
        return pending

    def clear_pending_backup(self, pending: PendingBackupState):
        with self.backup.pending_lock:
            if self.backup.pending is pending:
                self.backup.pending = None

    def current_pending_backup(self) -> Optional[PendingBackupState]:
        with self.backup.pending_lock:
            return self.backup.pending

    def cancel_pending_backup(self) -> bool:
        pending = self.current_pending_backup()
        if pending is None:
            return False
        pending.cancel.set()
        return True


def create_runtime() -> PluginRuntime:
    return PluginRuntime()
