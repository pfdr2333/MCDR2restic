# -*- coding: utf-8 -*-
"""门控停滞状态：记录“首次被人数门控推迟”的时间起点。

只持久化一个运行时间基线和一个墙钟文本，已等待时长永远由运行时钟现算。
这样重启、崩溃、停服都不会丢失进度：进度不是累加出来的，而是
``当前运行时间 - 基线``。成功备份后调用 ``clear_gate_stall`` 复位。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.running_time import running_seconds_since_success
from mcdr2restic.config.state_store import ensure_runtime, save_config_unlocked
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import now_text

BASELINE_KEY = "gate_stall_baseline_seconds"
STARTED_AT_KEY = "gate_stall_started_at"
WARNED_AT_KEY = "stale_backup_warned_at"


def mark_gate_stall_started(
    app_runtime: PluginRuntime, server: PluginServerInterface
) -> bool:
    """首次门控推迟时登记起点；已在停滞中则保持不变。

    返回 True 表示本次是新的停滞起点（调用方可用于日志）。
    """

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        if runtime_state.get(BASELINE_KEY) is not None:
            return False
        runtime_state[BASELINE_KEY] = running_seconds_since_success(runtime_state)
        runtime_state[STARTED_AT_KEY] = now_text()
        save_config_unlocked(app_runtime, server)
        return True


def clear_gate_stall(runtime_state: Dict[str, Any]) -> None:
    """复位停滞起点；调用方必须已持有 ``config_state.lock``。"""

    runtime_state[BASELINE_KEY] = None
    runtime_state[STARTED_AT_KEY] = None


def reset_gate_stall_on_disabled(runtime_state: Dict[str, Any]) -> bool:
    """停用备份时丢弃停滞计时与冷却时间戳；返回是否有改动。

    停用期间不存在“该备份却没备份”的前提，继续计时会让重新启用后立刻按
    停用期告警（P3-2）。重新启用后由下一次门控推迟重新登记起点。
    调用方必须已持有 ``config_state.lock``。
    """

    if all(
        runtime_state.get(key) is None
        for key in (BASELINE_KEY, STARTED_AT_KEY, WARNED_AT_KEY)
    ):
        return False
    clear_gate_stall(runtime_state)
    runtime_state[WARNED_AT_KEY] = None
    return True


def reset_gate_stall_on_backups_disabled(
    app_runtime: PluginRuntime, server: PluginServerInterface
) -> bool:
    """``!!m2r stop`` 等停用入口的落盘版本：释放停滞计时。"""

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        if not reset_gate_stall_on_disabled(runtime_state):
            return False
        save_config_unlocked(app_runtime, server)
        return True


def gate_stall_baseline(runtime_state: Dict[str, Any]) -> Optional[int]:
    value = runtime_state.get(BASELINE_KEY)
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def gate_stall_elapsed_seconds(runtime_state: Dict[str, Any]) -> Optional[int]:
    """已停滞的运行秒数；没有停滞起点时返回 None。"""

    baseline = gate_stall_baseline(runtime_state)
    if baseline is None:
        return None
    return max(0, running_seconds_since_success(runtime_state) - baseline)
