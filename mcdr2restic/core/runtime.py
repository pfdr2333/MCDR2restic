# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import threading
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
    command_handlers: Optional[Any] = None
    onebot: Optional[Any] = None
    discord: Optional[Any] = None
    custom_webhook: Optional[Any] = None
    stopping: threading.Event = field(default_factory=threading.Event)
    snapshot_query_lock: threading.Lock = field(default_factory=threading.Lock)
    save_flush_watcher: SaveFlushWatcher = field(default_factory=SaveFlushWatcher)


@dataclass
class BackupRuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel: threading.Event = field(default_factory=threading.Event)
    current_process: Optional[subprocess.Popen] = None
    thread: Optional[threading.Thread] = None
    label: Optional[str] = None


@dataclass
class RestoreRuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: Optional[threading.Thread] = None
    state_lock: threading.RLock = field(default_factory=threading.RLock)
    session: Optional[Any] = None


@dataclass
class PluginRuntime:
    config_state: ConfigRuntimeState = field(default_factory=ConfigRuntimeState)
    service: ServiceRuntimeState = field(default_factory=ServiceRuntimeState)
    backup: BackupRuntimeState = field(default_factory=BackupRuntimeState)
    restore: RestoreRuntimeState = field(default_factory=RestoreRuntimeState)

    def release_backup_slot(self):
        """备份槽位的唯一释放路径：清空状态标记并释放锁（若仍持有）。"""
        self.backup.label = None
        self.backup.thread = None
        self.backup.cancel.clear()
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


def create_runtime() -> PluginRuntime:
    return PluginRuntime()
