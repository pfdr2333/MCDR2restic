# -*- coding: utf-8 -*-
"""[beta] 中转库维护顺序：先补齐复制，再远端维护，最后本地 prune。

有复制 backlog 时整轮维护跳过：本地 prune 会删除尚未复制到远端的快照。
本地 prune 仅在游标已覆盖全部本地快照后执行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import config_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import MaintenanceOutcome, MaintenanceRunStatus
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section, now_text
from mcdr2restic.minecraft.minecraft_service import check_canceled
from mcdr2restic.restic.restic_constants import RESTIC_PHASE_MAINTENANCE
from mcdr2restic.restic.restic_context import ResticOperationContext
from mcdr2restic.restic.restic_download import (
    ensure_default_restic_executable_available,
)
from mcdr2restic.restic.restic_guidance import command_root_from_config
from mcdr2restic.restic.restic_lock_recovery import (
    run_restic_command_with_lock_recovery,
)
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.restic.restic_service import make_restic_deadline
from mcdr2restic.staging.staging_config import (
    StagingPaths,
    StagingSettings,
    build_cache_cleanup_args,
    build_local_forget_args,
    build_local_restic_config,
    get_staging_settings,
    validate_staging_settings,
)
from mcdr2restic.staging.staging_copy import run_copy_cycle
from mcdr2restic.staging.staging_repository import (
    CACHE_CLEANUP_PHASE,
    PRUNE_PHASE,
)
from mcdr2restic.staging.staging_state import (
    acquire_staging_io_lock,
    release_staging_io_lock,
    staging_runtime_state,
    update_staging_runtime,
)
from mcdr2restic.staging.staging_warning import parse_runtime_time


COPY_BACKLOG_WAIT_SECONDS = 5.0


def run_staging_maintenance_body(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    _invalidate_snapshot_cache_func: Any = None,
) -> MaintenanceOutcome:
    restic_cfg = get_config_section(cfg, "restic")
    settings = get_staging_settings(cfg)
    paths = validate_staging_settings(settings, restic_cfg)
    if paths is None:
        raise ValueError("staging maintenance entered while disabled")
    ensure_default_restic_executable_available(server, restic_cfg)
    if not acquire_staging_io_lock(app_runtime, timeout_seconds=COPY_BACKLOG_WAIT_SECONDS):
        return MaintenanceOutcome(
            MaintenanceRunStatus.SKIPPED,
            tr(
                config_language(cfg, get_mcdr_language(server)),
                "info.staging.maintenance_busy",
            ),
        )
    try:
        return run_maintenance_locked(
            app_runtime, server, cfg, restic_cfg, settings, paths
        )
    finally:
        release_staging_io_lock(app_runtime)


def run_maintenance_locked(  # noqa: PLR0913 维护上下文集中在一个内部入口
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    restic_cfg: Dict[str, Any],
    settings: StagingSettings,
    paths: StagingPaths,
) -> MaintenanceOutcome:
    language = config_language(cfg, get_mcdr_language(server))
    copy_result = run_copy_cycle(
        app_runtime, server, cfg, settings, paths, slot=app_runtime.staging.copy_slot
    )
    if not copy_result.success:
        return MaintenanceOutcome(
            MaintenanceRunStatus.SKIPPED,
            tr(
                language,
                "info.staging.maintenance_copy_backlog",
                error=copy_result.message,
            ),
        )
    deadline = make_restic_deadline(restic_cfg)
    command_root = command_root_from_config(cfg)
    if not copy_result.remote_initialized:
        run_remote_maintenance_commands(
            app_runtime, server, restic_cfg, deadline, command_root
        )
    local_cfg = build_local_restic_config(restic_cfg, settings, paths)
    local_ctx = ResticOperationContext(
        app_runtime=app_runtime,
        server=server,
        restic_cfg=local_cfg,
        deadline=deadline,
        invalidate_snapshot_cache=None,
        command_root=command_root,
    )
    deep = should_deep_prune(app_runtime, settings)
    run_local_prune(local_ctx, settings, paths, deep)
    cleanup_restic_cache(app_runtime, server, local_cfg, paths)
    return MaintenanceOutcome(
        MaintenanceRunStatus.SUCCESS,
        tr(
            language,
            "info.staging.maintenance_completed",
            keep_last=settings.keep_last,
            prune="deep" if deep else "daily",
        ),
    )


def run_remote_maintenance_commands(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    deadline: Optional[float],
    command_root: str,
):
    commands = restic_cfg.get("maintenance_commands", [])
    if not isinstance(commands, list) or not commands:
        return
    ctx = ResticOperationContext(
        app_runtime=app_runtime,
        server=server,
        restic_cfg=restic_cfg,
        deadline=deadline,
        invalidate_snapshot_cache=None,
        command_root=command_root,
    )
    for command in commands:
        check_canceled(app_runtime)
        result = run_restic_command_with_lock_recovery(
            ctx, command, RESTIC_PHASE_MAINTENANCE
        )
        assert_restic_success(
            restic_cfg, result, command_root, getattr(server, "logger", None)
        )


def run_local_prune(
    local_ctx: ResticOperationContext,
    settings: StagingSettings,
    paths: StagingPaths,
    deep: bool,
):
    check_canceled(local_ctx.app_runtime)
    args = build_local_forget_args(settings, paths.cache_dir, deep)
    result = run_restic_command_with_lock_recovery(
        local_ctx, args, PRUNE_PHASE
    )
    assert_restic_success(
        local_ctx.restic_cfg,
        result,
        local_ctx.command_root,
        getattr(local_ctx.server, "logger", None),
    )
    if deep:
        update_staging_runtime(
            local_ctx.app_runtime,
            local_ctx.server,
            staging_last_deep_prune_time=now_text(),
        )


def cleanup_restic_cache(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    local_cfg: Dict[str, Any],
    paths: StagingPaths,
):
    try:
        result = run_restic_command(
            app_runtime,
            local_cfg,
            build_cache_cleanup_args(paths.cache_dir),
            CACHE_CLEANUP_PHASE,
            None,
            slot=app_runtime.staging.copy_slot,
        )
        assert_restic_success(
            local_cfg, result, logger=getattr(server, "logger", None)
        )
    except Exception as exc:
        server.logger.warning(
            tr(
                get_mcdr_language(server),
                "warn.staging.maintenance_cache_cleanup_failed",
                error=exc,
            )
        )


def should_deep_prune(app_runtime: PluginRuntime, settings: StagingSettings) -> bool:
    days = settings.deep_prune_interval_days
    if days <= 0:
        return False
    last_text = str(
        staging_runtime_state(app_runtime).get("staging_last_deep_prune_time") or ""
    ).strip()
    last_moment = parse_runtime_time(last_text)
    if last_moment is None:
        return True
    return (
        datetime.now().astimezone() - last_moment
    ).total_seconds() >= days * 24 * 3600


__all__ = [
    "run_staging_maintenance_body",
    "should_deep_prune",
]
