# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.defaults.default_constants import DEFAULT_SAVE_ALL_DONE_PATTERNS
from mcdr2restic.minecraft.minecraft_service import (
    check_canceled,
    execute_mc_command,
    is_mc_ready,
    sleep_or_cancel,
)
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_config import (
    assert_backup_sources_do_not_contain_repository,
    build_restic_environment,
    is_local_restic_repository,
    resolve_restic_repository_path,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_AUTO_INIT_LOCAL_REPOSITORY,
    RESTIC_CFG_BACKUP_COMMAND,
    RESTIC_CFG_MAINTENANCE_COMMANDS,
    RESTIC_CFG_TIMEOUT_SECONDS,
    RESTIC_COMMAND_BACKUP,
    RESTIC_COMMAND_INIT,
    RESTIC_ENV_REPOSITORY,
    RESTIC_PHASE_MAINTENANCE,
)
from mcdr2restic.restic.restic_download import (
    ensure_default_restic_executable_available,
)
from mcdr2restic.restic.restic_guidance import command_root_from_config
from mcdr2restic.restic.restic_lock_recovery import (
    run_restic_command_with_lock_recovery,
)
from mcdr2restic.restic.restic_context import (
    ResticOperationContext,
    SnapshotCacheInvalidator,
)
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section, safe_int


def run_backup_body(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    label: str,
    invalidate_snapshot_cache_func: SnapshotCacheInvalidator,
) -> str:
    restic_cfg = get_config_section(cfg, "restic")
    command_root = command_root_from_config(cfg)
    ctx = ResticOperationContext(
        app_runtime=app_runtime,
        server=server,
        restic_cfg=restic_cfg,
        deadline=make_restic_deadline(restic_cfg),
        invalidate_snapshot_cache=invalidate_snapshot_cache_func,
        command_root=command_root,
    )
    prepare_backup_repository(ctx)
    prepare_minecraft_for_backup(app_runtime, server, cfg)
    return run_backup_command(ctx)


def run_maintenance_body(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    invalidate_snapshot_cache_func: SnapshotCacheInvalidator,
):
    restic_cfg = get_config_section(cfg, "restic")
    ctx = ResticOperationContext(
        app_runtime=app_runtime,
        server=server,
        restic_cfg=restic_cfg,
        deadline=make_restic_deadline(restic_cfg),
        invalidate_snapshot_cache=invalidate_snapshot_cache_func,
        command_root=command_root_from_config(cfg),
    )
    newly_initialized = prepare_maintenance_repository(ctx)
    run_backup_maintenance(ctx, newly_initialized)


def prepare_backup_repository(ctx: ResticOperationContext) -> bool:
    if not is_mc_ready(ctx.app_runtime, ctx.server):
        raise BackupProblem(i18n_key="error.backup.minecraft_not_ready")
    assert_backup_sources_do_not_contain_repository(ctx.restic_cfg)
    ensure_default_restic_executable_available(ctx.server, ctx.restic_cfg)
    return ensure_restic_repository_initialized(ctx)


def prepare_maintenance_repository(ctx: ResticOperationContext) -> bool:
    ensure_default_restic_executable_available(ctx.server, ctx.restic_cfg)
    return ensure_restic_repository_initialized(ctx)


def run_backup_maintenance(ctx: ResticOperationContext, newly_initialized: bool):
    if newly_initialized:
        ctx.server.logger.info(
            server_tr(ctx.server, "info.restic.maintenance_skipped_after_init")
        )
        return
    for command in ctx.restic_cfg.get(RESTIC_CFG_MAINTENANCE_COMMANDS, []):
        check_canceled(ctx.app_runtime)
        result = run_restic_command_with_lock_recovery(
            ctx,
            command,
            RESTIC_PHASE_MAINTENANCE,
        )
        assert_restic_success(ctx.restic_cfg, result, ctx.command_root)
        invalidate_snapshot_cache_for_operation(
            ctx, "snapshot.cache.reason.maintenance_finished"
        )


def prepare_minecraft_for_backup(
    app_runtime: PluginRuntime, server: PluginServerInterface, cfg: Dict[str, Any]
):
    minecraft_cfg = (
        get_config_section(cfg, "minecraft")
    )
    check_canceled(app_runtime)
    execute_mc_command(
        app_runtime,
        server,
        minecraft_cfg.get("save_off_command", "save-off"),
        "save-off",
    )
    sleep_or_cancel(
        app_runtime, float(minecraft_cfg.get("wait_after_save_off_seconds", 2))
    )

    check_canceled(app_runtime)
    wait_for_save_all_flush(app_runtime, server, minecraft_cfg)


def wait_for_save_all_flush(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    minecraft_cfg: Dict[str, Any],
):
    """发送 save-all 并等待落盘完成提示，未启用或超时时退回固定等待。"""

    timeout = float(minecraft_cfg.get("wait_after_save_all_seconds", 10))
    patterns = save_all_done_patterns(minecraft_cfg)
    use_completion = bool(minecraft_cfg.get("wait_for_save_all_completion", True))

    if not use_completion or not patterns or timeout <= 0:
        execute_mc_command(
            app_runtime,
            server,
            minecraft_cfg.get("save_all_command", "save-all flush"),
            "save-all",
        )
        if timeout > 0:
            sleep_or_cancel(app_runtime, timeout)
        return

    watcher = app_runtime.service.save_flush_watcher
    watcher.arm(patterns)
    started = time.monotonic()
    try:
        execute_mc_command(
            app_runtime,
            server,
            minecraft_cfg.get("save_all_command", "save-all flush"),
            "save-all",
        )
        completed = watcher.wait(
            timeout, check_canceled=lambda: check_canceled(app_runtime)
        )
    finally:
        watcher.disarm()
    if completed:
        server.logger.info(
            server_tr(
                server,
                "log.minecraft.save_flush_completed",
                seconds=max(1, int(time.monotonic() - started)),
            )
        )
    else:
        server.logger.warning(
            server_tr(
                server,
                "warn.minecraft.save_flush_wait_timeout",
                seconds=int(timeout),
            )
        )


def save_all_done_patterns(minecraft_cfg: Dict[str, Any]) -> List[str]:
    patterns = minecraft_cfg.get(
        "save_all_done_patterns", list(DEFAULT_SAVE_ALL_DONE_PATTERNS)
    )
    if not isinstance(patterns, list):
        return []
    return [str(pattern) for pattern in patterns if str(pattern)]


def run_backup_command(ctx: ResticOperationContext) -> str:
    check_canceled(ctx.app_runtime)
    result = run_restic_command_with_lock_recovery(
        ctx,
        ctx.restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []),
        RESTIC_COMMAND_BACKUP,
    )
    assert_restic_success(ctx.restic_cfg, result, ctx.command_root)
    invalidate_snapshot_cache_for_operation(
        ctx, "snapshot.cache.reason.backup_finished"
    )
    return result.snapshot_id


def make_restic_deadline(restic_cfg: Dict[str, Any]) -> Optional[float]:
    timeout_seconds = safe_int(restic_cfg.get(RESTIC_CFG_TIMEOUT_SECONDS, 0), 0)
    if timeout_seconds <= 0:
        return None
    return time.monotonic() + max(1, timeout_seconds)


def ensure_restic_repository_initialized(ctx: ResticOperationContext) -> bool:
    restic_cfg = ctx.restic_cfg
    server = ctx.server
    if not bool(restic_cfg.get(RESTIC_CFG_AUTO_INIT_LOCAL_REPOSITORY, True)):
        return False
    env = build_restic_environment(restic_cfg)
    repository = str(env.get(RESTIC_ENV_REPOSITORY, "") or "").strip()
    if not repository:
        return False
    if not is_local_restic_repository(repository):
        server.logger.debug(
            server_tr(
                server, "debug.restic.auto_init_skipped_remote", repository=repository
            )
        )
        return False

    repository_path = resolve_restic_repository_path(restic_cfg, repository)
    config_path = os.path.join(repository_path, "config")
    if os.path.isfile(config_path):
        return False
    if os.path.exists(repository_path) and not os.path.isdir(repository_path):
        raise BackupProblem(
            i18n_key="error.restic.repository_path_not_directory",
            repository_path=repository_path,
        )

    os.makedirs(repository_path, exist_ok=True)
    server.logger.info(
        server_tr(
            server, "info.restic.auto_init_started", repository_path=repository_path
        )
    )
    result = run_restic_command_with_lock_recovery(
        ctx,
        [RESTIC_COMMAND_INIT],
        RESTIC_COMMAND_INIT,
    )
    assert_restic_success(restic_cfg, result, ctx.command_root)
    invalidate_snapshot_cache_for_operation(
        ctx, "snapshot.cache.reason.repository_initialized"
    )
    return True


def invalidate_snapshot_cache_for_operation(ctx: ResticOperationContext, reason_key: str):
    invalidate = ctx.invalidate_snapshot_cache
    if invalidate is None:
        return
    invalidate(ctx.server, ctx.restic_cfg, server_tr(ctx.server, reason_key))
