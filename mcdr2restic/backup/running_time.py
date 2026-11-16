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
        save_config_unlocked(app_runtime, server)
        return True


def reset_running_clock_state(
    runtime_state: Dict[str, Any],
    server_running: bool = True,
    now: Optional[float] = None,
) -> None:
    """成功备份后清零累计；服务器仍在运行时从当前时刻重新计时。"""

    runtime_state[ACCUMULATED_KEY] = 0
    runtime_state[RUNNING_SINCE_KEY] = (
        (time.time() if now is None else now) if server_running else None
    )


def parse_epoch(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
