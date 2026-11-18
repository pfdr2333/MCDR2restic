# -*- coding: utf-8 -*-
"""服务器实际运行时间累计。

RPO 只应在服务器运行、数据可能变化时增长；停服期间不计时。运行片段
用 epoch 起点持久化，服务器停止/插件卸载时结算进累计值，成功备份后
累计值清零并从当前时刻重新开始。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import ensure_runtime, save_config_unlocked
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import non_negative_int


ACCUMULATED_KEY = "backup_running_seconds_since_success"
RUNNING_SINCE_KEY = "server_running_since_epoch"
HEARTBEAT_KEY = "server_running_heartbeat_at"
# 心跳结算间隔：进程被强杀/断电后，最多丢弃一个间隔内无法确认存活的时长
RUNNING_CLOCK_HEARTBEAT_SECONDS = 1800
# 心跳超过该倍数的间隔即判定为过期；正常重载最多滞后一个间隔
HEARTBEAT_STALE_FACTOR = 2


def running_seconds_since_success(
    runtime_state: Dict[str, Any], now: Optional[float] = None
) -> int:
    """累计运行秒数 = 已结算片段 + 当前仍在进行的片段。"""

    total = non_negative_int(runtime_state.get(ACCUMULATED_KEY, 0))
    since = parse_epoch(runtime_state.get(RUNNING_SINCE_KEY))
    if since is not None:
        current = time.time() if now is None else now
        total += max(0, int(current - since))
    return total


def running_hours_since_success(
    runtime_state: Dict[str, Any], now: Optional[float] = None
) -> int:
    return running_seconds_since_success(runtime_state, now) // 3600


def start_server_running_clock(
    app_runtime: PluginRuntime, server: PluginServerInterface
) -> bool:
    """服务器开始运行时登记片段起点；已有未结算片段时保持不变。"""

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        if parse_epoch(runtime_state.get(RUNNING_SINCE_KEY)) is not None:
            return False
        runtime_state[RUNNING_SINCE_KEY] = time.time()
        runtime_state[HEARTBEAT_KEY] = runtime_state[RUNNING_SINCE_KEY]
        save_config_unlocked(app_runtime, server)
        return True


def stop_server_running_clock(
    app_runtime: PluginRuntime, server: PluginServerInterface
) -> bool:
    """服务器停止或插件卸载时把当前片段结算进累计值。"""

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        since = parse_epoch(runtime_state.get(RUNNING_SINCE_KEY))
        if since is None:
            return False
        runtime_state[ACCUMULATED_KEY] = non_negative_int(
            runtime_state.get(ACCUMULATED_KEY, 0)
        ) + max(0, int(time.time() - since))
        runtime_state[RUNNING_SINCE_KEY] = None
        runtime_state[HEARTBEAT_KEY] = time.time()
        save_config_unlocked(app_runtime, server)
        return True


def settle_running_clock(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    now: Optional[float] = None,
) -> bool:
    """把当前运行片段结算进累计值，并把片段起点推到当前时刻。

    周期性心跳结算的写入点：结算同时刷新心跳时间，崩溃重启后
    ``recover_running_clock`` 只信任到最后一个心跳，从而不把停机时长
    算成运行时长。
    """

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        since = parse_epoch(runtime_state.get(RUNNING_SINCE_KEY))
        if since is None:
            return False
        current = time.time() if now is None else now
        runtime_state[ACCUMULATED_KEY] = non_negative_int(
            runtime_state.get(ACCUMULATED_KEY, 0)
        ) + max(0, int(current - since))
        runtime_state[RUNNING_SINCE_KEY] = current
        runtime_state[HEARTBEAT_KEY] = current
        save_config_unlocked(app_runtime, server)
        return True


def recover_running_clock(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    server_ready: bool,
    now: Optional[float] = None,
) -> bool:
    """启动/重载时结算遗留的运行片段。

    进程被强杀或断电时 ``server_running_since_epoch`` 会停留在崩溃前，
    重启后 ``now - since`` 会把停机时长算成运行时长。这里只结算到最后
    一个心跳；心跳之后无法确认服务器是否在运行，直接丢弃——宁可少算
    （晚一点告警），也不制造假告警。插件热重载时心跳仍然新鲜，按连续
    片段处理，不丢时间。
    """

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        since = parse_epoch(runtime_state.get(RUNNING_SINCE_KEY))
        if since is None:
            return False
        current = time.time() if now is None else now
        heartbeat = parse_epoch(runtime_state.get(HEARTBEAT_KEY))
        stale_after = RUNNING_CLOCK_HEARTBEAT_SECONDS * HEARTBEAT_STALE_FACTOR
        if heartbeat is None or current - heartbeat > stale_after:
            credited_until = heartbeat if heartbeat is not None else since
        else:
            credited_until = current
        runtime_state[ACCUMULATED_KEY] = non_negative_int(
            runtime_state.get(ACCUMULATED_KEY, 0)
        ) + max(0, int(credited_until - since))
        runtime_state[HEARTBEAT_KEY] = credited_until
        runtime_state[RUNNING_SINCE_KEY] = current if server_ready else None
        save_config_unlocked(app_runtime, server)
        return True


def reset_running_clock_state(
    runtime_state: Dict[str, Any],
    server_running: bool = True,
    now: Optional[float] = None,
) -> None:
    """成功备份后清零累计；服务器仍在运行时从当前时刻重新计时。"""

    runtime_state[ACCUMULATED_KEY] = 0
    current = time.time() if now is None else now
    runtime_state[RUNNING_SINCE_KEY] = current if server_running else None
    runtime_state[HEARTBEAT_KEY] = current if server_running else None


def parse_epoch(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
