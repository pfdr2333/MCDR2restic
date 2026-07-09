# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.minecraft.minecraft_service import check_canceled, execute_mc_command, is_mc_ready, sleep_or_cancel
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
from mcdr2restic.restic.restic_download import ensure_default_restic_executable_available
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import safe_int


SnapshotCacheInvalidator = Callable[[PluginServerInterface, Dict[str, Any], str], None]


def run_backup_body(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    label: str,
    invalidate_snapshot_cache_func: SnapshotCacheInvalidator
) -> str:
    restic_cfg = cfg.get('restic', {})
    deadline = make_restic_deadline(restic_cfg)
    newly_initialized = prepare_backup_repository(app_runtime, server, restic_cfg, deadline, invalidate_snapshot_cache_func)
    run_backup_maintenance(app_runtime, server, restic_cfg, deadline, newly_initialized, invalidate_snapshot_cache_func)
    prepare_minecraft_for_backup(app_runtime, server, cfg)
    return run_backup_command(app_runtime, server, restic_cfg, deadline, invalidate_snapshot_cache_func)


def prepare_backup_repository(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    deadline: Optional[float],
    invalidate_snapshot_cache_func: SnapshotCacheInvalidator
) -> bool:
    if not is_mc_ready(app_runtime, server):
        raise BackupProblem('Minecraft 服务端尚未确认正常运行')
    assert_backup_sources_do_not_contain_repository(restic_cfg)
    ensure_default_restic_executable_available(server, restic_cfg)
    return ensure_restic_repository_initialized(app_runtime, server, restic_cfg, deadline, invalidate_snapshot_cache_func)


def run_backup_maintenance(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    deadline: Optional[float],
    newly_initialized: bool,
    invalidate_snapshot_cache_func: SnapshotCacheInvalidator
):
    if newly_initialized:
        server.logger.info('本地 restic 仓库刚初始化，跳过本次备份前维护命令')
        return
    for command in restic_cfg.get(RESTIC_CFG_MAINTENANCE_COMMANDS, []):
        check_canceled(app_runtime)
        result = run_restic_command(app_runtime, restic_cfg, command, RESTIC_PHASE_MAINTENANCE, deadline)
        assert_restic_success(restic_cfg, result)
        invalidate_snapshot_cache_func(server, restic_cfg, 'maintenance command finished')


def prepare_minecraft_for_backup(app_runtime: PluginRuntime, server: PluginServerInterface, cfg: Dict[str, Any]):
    minecraft_cfg = cfg.get('minecraft', {}) if isinstance(cfg.get('minecraft'), dict) else {}
    check_canceled(app_runtime)
    execute_mc_command(app_runtime, server, minecraft_cfg.get('save_off_command', 'save-off'), 'save-off')
    sleep_or_cancel(app_runtime, float(minecraft_cfg.get('wait_after_save_off_seconds', 2)))

    check_canceled(app_runtime)
    execute_mc_command(app_runtime, server, minecraft_cfg.get('save_all_command', 'save-all flush'), 'save-all')
    sleep_or_cancel(app_runtime, float(minecraft_cfg.get('wait_after_save_all_seconds', 10)))


def run_backup_command(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    deadline: Optional[float],
    invalidate_snapshot_cache_func: SnapshotCacheInvalidator
) -> str:
    check_canceled(app_runtime)
    result = run_restic_command(
        app_runtime,
        restic_cfg,
        restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []),
        RESTIC_COMMAND_BACKUP,
        deadline
    )
    assert_restic_success(restic_cfg, result)
    invalidate_snapshot_cache_func(server, restic_cfg, 'backup command finished')
    return result.snapshot_id

def make_restic_deadline(restic_cfg: Dict[str, Any]) -> Optional[float]:
    timeout_seconds = safe_int(restic_cfg.get(RESTIC_CFG_TIMEOUT_SECONDS, 0), 0)
    if timeout_seconds <= 0:
        return None
    return time.monotonic() + max(1, timeout_seconds)

def ensure_restic_repository_initialized(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    deadline: Optional[float],
    invalidate_snapshot_cache_func: SnapshotCacheInvalidator
) -> bool:
    if not bool(restic_cfg.get(RESTIC_CFG_AUTO_INIT_LOCAL_REPOSITORY, True)):
        return False
    env = build_restic_environment(restic_cfg)
    repository = str(env.get(RESTIC_ENV_REPOSITORY, '') or '').strip()
    if not repository:
        return False
    if not is_local_restic_repository(repository):
        server.logger.debug('restic 仓库不是本地路径，跳过自动初始化: {}'.format(repository))
        return False

    repository_path = resolve_restic_repository_path(restic_cfg, repository)
    config_path = os.path.join(repository_path, 'config')
    if os.path.isfile(config_path):
        return False
    if os.path.exists(repository_path) and not os.path.isdir(repository_path):
        raise BackupProblem('本地 restic 仓库路径已存在但不是目录: {}'.format(repository_path))

    os.makedirs(repository_path, exist_ok=True)
    server.logger.info('本地 restic 仓库不存在或未初始化，正在执行 restic init: {}'.format(repository_path))
    result = run_restic_command(app_runtime, restic_cfg, [RESTIC_COMMAND_INIT], RESTIC_COMMAND_INIT, deadline)
    assert_restic_success(restic_cfg, result)
    invalidate_snapshot_cache_func(server, restic_cfg, 'repository initialized')
    return True
