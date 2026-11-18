# -*- coding: utf-8 -*-
"""备份玩家人数门控。

远程备份库上传慢，save-off 会一直保持到备份结束；高峰期执行备份会把
大量未落盘的区块数据压在 JVM 内存里，最终挤爆内存。门控在两个位置拦截：

- 触发前在线人数达到阈值则推迟备份，降回阈值内自动开始；推迟可被
  取消、停用、停止或期限终止，每轮都会复检配置与插件状态。
- 备份运行中人数持续超阈达到容忍时长则中断备份并恢复世界自动保存。
  restic 按内容寻址存储，中断的备份没有副作用，残余数据由 forget/prune
  周期回收。

阈值 ``backup_player_gate_max_online`` 就是门控的唯一开关：大于 0 开启，
等于 0 关闭。远程备份库在配置生成与迁移时默认写入阈值，关闭必须显式
改成 0；本地库默认 0。

人数优先通过 RCON 实时采样，RCON 不可用时回退加入/退出事件维护的计数；
采样失败一律放行（fail-open），避免备份被一次 RCON 故障无限期卡住。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import (
    ensure_runtime,
    get_config_snapshot,
    save_config_unlocked,
)
from mcdr2restic.core.i18n import config_language, server_tr, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import TextEnum
from mcdr2restic.core.utils import get_config_section, non_negative_int
from mcdr2restic.minecraft.minecraft_service import (
    request_cancel_current_backup,
    server_is_running,
)
from mcdr2restic.minecraft.player_activity_service import (
    get_online_check_command,
    query_online_players,
)
from mcdr2restic.backup.gate_stall import mark_gate_stall_started
from mcdr2restic.restic.restic_config import is_local_restic_repository


# 门控采样间隔：触发前等待与运行中监控共用；远小于中断容忍时长的最小合理值
PLAYER_GATE_POLL_SECONDS = 60.0
# 等待期间检查取消/停用/期限的粒度：用户可见响应保持在秒级
PLAYER_GATE_ABORT_CHECK_SECONDS = 1.0


AdminNotifier = Callable[[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None]


class GateWaitStatus(TextEnum):
    PASSED = "passed"
    CANCELED = "canceled"
    DISABLED = "disabled"
    STOPPING = "stopping"
    DEADLINE = "deadline"


@dataclass(frozen=True)
class GateWaitResult:
    status: GateWaitStatus
    current: int = 0
    max_online: int = 0

    @property
    def passed(self) -> bool:
        return self.status == GateWaitStatus.PASSED


def player_gate_settings(cfg: Dict[str, Any]) -> Tuple[int, int]:
    """读取门控设置 (max_online, overlimit_seconds)。

    阈值就是门控的唯一开关：max_online > 0 即开启，等于 0 关闭。
    坏值按 0 处理（放行），避免把备份锁死在无效配置上。
    """

    schedule = get_config_section(cfg, "schedule")
    max_online = non_negative_int(schedule.get("backup_player_gate_max_online", 0))
    overlimit_seconds = non_negative_int(
        schedule.get("backup_player_gate_overlimit_seconds", 600)
    )
    return max_online, overlimit_seconds


def warn_if_remote_gate_disabled(
    server: PluginServerInterface, cfg: Dict[str, Any]
) -> bool:
    """远程仓库显式把阈值改成 0 时提醒一次：关闭是允许的，但必须知情。"""

    max_online, _ = player_gate_settings(cfg)
    if max_online > 0:
        return False
    restic_cfg = get_config_section(cfg, "restic")
    repository = str(restic_cfg.get("repository") or "").strip()
    if not repository or is_local_restic_repository(repository):
        return False
    server.logger.warning(server_tr(server, "warn.backup.player_gate_disabled_remote"))
    return True


def resolve_online_player_count(
    app_runtime, server: Optional[PluginServerInterface], cfg: Dict[str, Any]
) -> Optional[int]:
    """解析当前在线人数；服务器未运行视为 0，RCON 失败回退事件计数。"""
    if server is None or not server_is_running(app_runtime, server):
        return 0
    command = get_online_check_command(cfg)
    rcon_query = getattr(server, "rcon_query", None)
    if command and callable(rcon_query):
        count = query_online_players(
            app_runtime,
            server,
            rcon_query,
            command,
            server_tr(server, "reason.player_gate"),
        )
        if count is not None:
            return count
    # RCON 不可用：回退加入/退出事件维护的计数（可能滞后于真实人数）
    with app_runtime.config_state.lock:
        ensure_runtime(app_runtime.config_state.config)
        runtime_state = app_runtime.config_state.config["runtime"]
        return non_negative_int(runtime_state.get("current_online_players", 0))


def wait_for_player_gate(
    app_runtime,
    server: PluginServerInterface,
    admin_notifier: AdminNotifier,
    pending=None,
) -> GateWaitResult:
    """触发前人数门控：超阈则推迟，降回阈值内返回 PASSED。

    每轮重读配置快照，热更新阈值、关闭门控、停用插件、取消推迟或越过
    期限都会在下一次检查（秒级）内结束等待。pending 为推迟登记对象，
    调度线程与命令/状态页通过它共享取消与期限信息。
    """
    announced = False
    wait_rounds = 0
    last_count = 0
    last_max_online = 0
    while True:
        abort = _gate_abort_result(app_runtime, pending, last_count, last_max_online)
        if abort is not None:
            return abort
        cfg = get_config_snapshot(app_runtime)
        if not bool(cfg.get("enabled", True)):
            return GateWaitResult(GateWaitStatus.DISABLED, last_count, last_max_online)
        max_online, _ = player_gate_settings(cfg)
        if max_online <= 0:
            return GateWaitResult(GateWaitStatus.PASSED, last_count, max_online)
        if pending is not None and pending.deadline_passed():
            return GateWaitResult(GateWaitStatus.DEADLINE, last_count, max_online)
        count = resolve_online_player_count(app_runtime, server, cfg)
        last_count = count or 0
        last_max_online = max_online
        if count is None or count < max_online:
            if announced:
                _log_gate_released(server, cfg, pending, last_count, max_online)
            return GateWaitResult(GateWaitStatus.PASSED, last_count, max_online)
        wait_rounds += 1
        announced = _log_gate_waiting(
            server,
            cfg,
            pending,
            count,
            max_online,
            announced,
            wait_rounds,
            admin_notifier,
            app_runtime,
        )
        # 停滞时长由独立看门狗按运行时钟重算，门控等待只负责登记起点
        mark_gate_stall_started(app_runtime, server)
        _wait_next_gate_poll(app_runtime, pending)


def _gate_abort_result(
    app_runtime, pending, last_count: int, last_max_online: int
) -> Optional[GateWaitResult]:
    if app_runtime.service.stopping.is_set():
        return GateWaitResult(GateWaitStatus.STOPPING, last_count, last_max_online)
    if pending is not None and pending.cancel.is_set():
        return GateWaitResult(GateWaitStatus.CANCELED, last_count, last_max_online)
    return None


def _log_gate_released(
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    pending,
    current: int,
    max_online: int,
):
    server.logger.info(
        tr(
            config_language(cfg, get_mcdr_language(server)),
            "info.backup.player_gate_released",
            current=current,
            max_online=max_online,
            scheduled_time=pending.scheduled_time_text if pending else "-",
        )
    )


def _log_gate_waiting(  # noqa: PLR0913 等待态日志的完整上下文集中在一个内部入口
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    pending,
    current: int,
    max_online: int,
    announced: bool,
    wait_rounds: int,
    admin_notifier: AdminNotifier,
    app_runtime,
) -> bool:
    language = config_language(cfg, get_mcdr_language(server))
    scheduled_time = pending.scheduled_time_text if pending else "-"
    if not announced:
        _count_gate_deferral(app_runtime, server)
        message = tr(
            language,
            "info.backup.player_gate_waiting",
            current=current,
            max_online=max_online,
            scheduled_time=scheduled_time,
        )
        server.logger.warning(message)
        if bool(
            get_config_section(cfg, "notification").get("notify_on_skip", False)
        ):
            admin_notifier(
                "backup_player_gate_waiting", {"message": message}, cfg, False
            )
        return True
    if wait_rounds % 5 == 0:
        # 等待期间约每 5 分钟一行日志，说明仍在推迟及原因
        server.logger.info(
            tr(
                language,
                "info.backup.player_gate_still_waiting",
                current=current,
                max_online=max_online,
                scheduled_time=scheduled_time,
            )
        )
    return True


def _wait_next_gate_poll(app_runtime, pending) -> bool:
    """等下一轮采样；返回 True 表示继续循环，False 表示应立即复检退出。

    用 1 秒粒度切片等待，取消/停用/插件停止不必等满 60 秒采样间隔。
    """

    deadline = time.monotonic() + PLAYER_GATE_POLL_SECONDS
    while time.monotonic() < deadline:
        if app_runtime.service.stopping.is_set():
            return False
        if pending is not None and (pending.cancel.is_set() or pending.deadline_passed()):
            return False
        # 用 pending.cancel 做可中断休眠；无 pending 时退化为短睡
        if pending is not None:
            if pending.cancel.wait(PLAYER_GATE_ABORT_CHECK_SECONDS):
                return False
        else:
            time.sleep(PLAYER_GATE_ABORT_CHECK_SECONDS)
    return True


def _count_gate_deferral(app_runtime, server: PluginServerInterface):
    """记录一次因门控开始的推迟，供陈旧备份告警说明"被拦下几次"。"""

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        runtime_state["gate_deferral_count_since_success"] = (
            non_negative_int(runtime_state.get("gate_deferral_count_since_success", 0))
            + 1
        )
        try:
            save_config_unlocked(app_runtime, server)
        except Exception as exc:
            # 计数落盘失败只影响告警文案精度，不能让门控等待中断
            server.logger.warning(
                tr(
                    config_language(
                        get_config_snapshot(app_runtime),
                        get_mcdr_language(server),
                    ),
                    "warn.backup.gate_state_save_failed",
                    error=exc,
                )
            )


class PlayerGateMonitor:
    """备份运行中的人数监控线程。

    save-off 窗口内人数持续超阈达到容忍时长即请求取消当前备份；
    人数降回阈值内则重置计时。备份取消事件置位后监控自动退出，
    避免与其它取消来源重复打断收尾流程。
    """

    def __init__(
        self,
        app_runtime,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
    ):
        self._app_runtime = app_runtime
        self._server = server
        self._cfg = cfg
        self._stop_event = threading.Event()
        max_online, overlimit_seconds = player_gate_settings(cfg)
        self._max_online = max_online
        self._overlimit_seconds = overlimit_seconds
        self._thread = threading.Thread(
            target=self._run,
            name="MCDR2Restic-BackupPlayerGate",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self):
        over_since: Optional[float] = None
        warned = False
        while not self._stop_event.wait(PLAYER_GATE_POLL_SECONDS):
            if self._app_runtime.backup.cancel.is_set():
                return
            count = resolve_online_player_count(
                self._app_runtime, self._server, self._cfg
            )
            if count is None:
                # 采样失败按放行处理，不因 RCON 故障中断备份
                continue
            if count < self._max_online:
                if over_since is not None:
                    over_since = None
                    warned = False
                    self._log("info.backup.player_gate_back_in_limit", count)
                continue
            now = time.monotonic()
            if over_since is None:
                over_since = now
            if not warned:
                warned = True
                self._log(
                    "warn.backup.player_gate_overlimit_started",
                    count,
                    overlimit_seconds=self._overlimit_seconds,
                )
                continue
            if now - over_since >= self._overlimit_seconds:
                self._interrupt(count)
                return

    def _interrupt(self, count: int):
        language = config_language(self._cfg, get_mcdr_language(self._server))
        reason = tr(
            language,
            "reason.backup.player_gate_overlimit",
            current=count,
            max_online=self._max_online,
            seconds=self._overlimit_seconds,
        )
        self._server.logger.warning(reason)
        request_cancel_current_backup(self._app_runtime, reason)

    def _log(self, key: str, count: int, **extra):
        language = config_language(self._cfg, get_mcdr_language(self._server))
        message = tr(
            language, key, current=count, max_online=self._max_online, **extra
        )
        if key.startswith("warn."):
            self._server.logger.warning(message)
        else:
            self._server.logger.info(message)


def start_backup_player_gate_monitor(
    app_runtime,
    server: Optional[PluginServerInterface],
    cfg: Dict[str, Any],
) -> Optional[PlayerGateMonitor]:
    """save-off 生效后调用；未启用门控、配置无效或无服务器时返回 None。

    阈值/容忍时长为 0 等无效配置按放行处理，避免把备份锁死在门控上。
    """
    max_online, overlimit_seconds = player_gate_settings(cfg)
    if max_online <= 0 or overlimit_seconds <= 0 or server is None:
        return None
    monitor = PlayerGateMonitor(app_runtime, server, cfg)
    monitor.start()
    return monitor
