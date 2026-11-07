# -*- coding: utf-8 -*-
"""备份玩家人数门控。

远程备份库上传慢，save-off 会一直保持到备份结束；高峰期执行备份会把
大量未落盘的区块数据压在 JVM 内存里，最终挤爆内存。门控在两个位置拦截：

- 触发前在线人数超阈则推迟备份，降回阈值内自动开始（无限等待，插件
  停止或门控关闭时提前放行/退出）；
- 备份运行中人数持续超阈达到容忍时长则中断备份并恢复世界自动保存。
  restic 按内容寻址存储，中断的备份没有副作用，残余数据由 forget/prune
  周期回收。

人数优先通过 RCON 实时采样，RCON 不可用时回退加入/退出事件维护的计数；
采样失败一律放行（fail-open），避免备份被一次 RCON 故障无限期卡住。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import ensure_runtime, get_config_snapshot
from mcdr2restic.core.i18n import config_language, server_tr, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.utils import get_config_section, non_negative_int
from mcdr2restic.minecraft.minecraft_service import (
    request_cancel_current_backup,
    server_is_running,
)
from mcdr2restic.minecraft.player_activity_service import (
    get_online_check_command,
    query_online_players,
)


# 门控采样间隔：触发前等待与运行中监控共用；远小于中断容忍时长的最小合理值
PLAYER_GATE_POLL_SECONDS = 60.0


AdminNotifier = Callable[[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None]


def player_gate_settings(cfg: Dict[str, Any]) -> Tuple[bool, int, int]:
    """读取门控三元组 (enabled, max_online, overlimit_seconds)，坏值回退默认。"""
    schedule = get_config_section(cfg, "schedule")
    enabled = bool(schedule.get("backup_player_gate_enabled", False))
    max_online = non_negative_int(schedule.get("backup_player_gate_max_online", 5))
    overlimit_seconds = non_negative_int(
        schedule.get("backup_player_gate_overlimit_seconds", 600)
    )
    return enabled, max_online, overlimit_seconds


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
) -> bool:
    """触发前人数门控：超阈则推迟，降回阈值内返回 True。

    无限等待直到放行；返回 False 表示插件停止中，放弃本次备份。
    每轮循环重读配置快照，热更新阈值或关闭门控立即生效。
    """
    announced = False
    wait_rounds = 0
    while not app_runtime.service.stopping.is_set():
        cfg = get_config_snapshot(app_runtime)
        enabled, max_online, _ = player_gate_settings(cfg)
        if not enabled or max_online <= 0:
            return True
        count = resolve_online_player_count(app_runtime, server, cfg)
        if count is None or count < max_online:
            return True
        wait_rounds += 1
        language = config_language(cfg, get_mcdr_language(server))
        if not announced:
            announced = True
            message = tr(
                language,
                "info.backup.player_gate_waiting",
                current=count,
                max_online=max_online,
            )
            server.logger.warning(message)
            notification_cfg = get_config_section(cfg, "notification")
            if bool(notification_cfg.get("notify_on_skip", False)):
                admin_notifier(
                    "backup_player_gate_waiting", {"message": message}, cfg, False
                )
        elif wait_rounds % 5 == 0:
            # 等待期间约每 5 分钟一行日志，说明仍在推迟及原因
            server.logger.info(
                tr(
                    language,
                    "info.backup.player_gate_still_waiting",
                    current=count,
                    max_online=max_online,
                )
            )
        if app_runtime.service.stopping.wait(PLAYER_GATE_POLL_SECONDS):
            break
    return False


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
        _, max_online, overlimit_seconds = player_gate_settings(cfg)
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
    enabled, max_online, overlimit_seconds = player_gate_settings(cfg)
    if not enabled or max_online <= 0 or overlimit_seconds <= 0 or server is None:
        return None
    monitor = PlayerGateMonitor(app_runtime, server, cfg)
    monitor.start()
    return monitor
