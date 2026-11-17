# -*- coding: utf-8 -*-
"""[beta] 中转库运行时状态的集中写入入口。

所有 staging_* 运行时键都通过这里落盘，避免各模块各写一套锁与保存逻辑。
状态保存失败只影响状态页精度，不能让备份或复制流程中断。
"""

from __future__ import annotations

from typing import Any, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import (
    ensure_runtime,
    get_config_snapshot,
    save_config_unlocked,
)
from mcdr2restic.core.i18n import config_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.runtime import PluginRuntime


STAGING_COPY_STATUS_NEVER = "never"
STAGING_COPY_STATUS_RUNNING = "running"
STAGING_COPY_STATUS_SUCCESS = "success"
STAGING_COPY_STATUS_FAILED = "failed"
STAGING_COPY_STATUS_BACKLOG = "backlog"
STAGING_COPY_STATUS_PERMANENT = "permanent"
STAGING_COPY_STATUS_CANCELED = "canceled"
STAGING_COPY_STATUS_SKIPPED = "skipped"


def update_staging_runtime(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface] = None,
    **values: Any,
):
    """[beta] 原子更新 staging_* 运行时键并尽力落盘。"""

    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        runtime_state = app_runtime.config_state.config["runtime"]
        for key, value in values.items():
            runtime_state[key] = value
        try:
            save_config_unlocked(app_runtime, server)
        except Exception as exc:
            _log_state_save_failure(app_runtime, server, exc)


def _log_state_save_failure(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface],
    exc: Exception,
):
    logger = getattr(server, "logger", None) if server is not None else None
    if logger is None:
        return
    language = config_language(
        get_config_snapshot(app_runtime), get_mcdr_language(server)
    )
    logger.warning(
        tr(language, "warn.staging.state_save_failed", error=exc)
    )


def staging_runtime_state(app_runtime: PluginRuntime) -> dict:
    return get_config_snapshot(app_runtime).get("runtime", {})


def record_local_snapshot_time(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface],
    snapshot_time: str,
):
    update_staging_runtime(
        app_runtime, server, staging_local_latest_time=snapshot_time
    )
