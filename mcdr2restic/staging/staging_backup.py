# -*- coding: utf-8 -*-
"""[beta] 中转库备份主流程：冻结前预检 + 冻结窗口内只写本地库。

预检失败绝不进入 ``save-off``。本地库写满时带超时执行应急 prune，
随后仍以备份失败收尾，由 BackupRunner 统一恢复世界自动保存。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import format_bytes, get_config_section, now_text, safe_int
from mcdr2restic.minecraft.minecraft_service import is_mc_ready
from mcdr2restic.minecraft.player_gate_service import (
    start_backup_player_gate_monitor,
)
from mcdr2restic.restic.restic_config import (
    assert_backup_sources_do_not_contain_repository,
    nearest_existing_directory,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_COMMAND_BACKUP,
    RESTIC_OPTION_JSON,
)
from mcdr2restic.restic.restic_context import ResticOperationContext
from mcdr2restic.restic.restic_download import (
    ensure_default_restic_executable_available,
)
from mcdr2restic.restic.restic_guidance import command_root_from_config
from mcdr2restic.restic.restic_lock_recovery import (
    run_restic_command_with_lock_recovery,
)
from mcdr2restic.restic.restic_result import (
    assert_restic_success,
    combine_restic_output,
)
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.restic.restic_service import (
    make_restic_deadline,
    prepare_minecraft_for_backup,
)
from mcdr2restic.staging.staging_config import (
    StagingPaths,
    StagingSettings,
    build_backup_args,
    build_emergency_prune_args,
    build_remote_restic_config,
    get_staging_settings,
    validate_staging_settings,
)
from mcdr2restic.staging.staging_repository import (
    PRUNE_PHASE,
    assert_matching_chunkers,
    ensure_local_repository,
    make_remote_probe_deadline,
    probe_repository,
)
from mcdr2restic.staging.staging_state import record_local_snapshot_time
from mcdr2restic.staging.staging_worker import request_copy


DISK_PREFLIGHT_PHASE = "staging-dry-run"
DISK_FULL_MARKERS = (
    "no space left on device",
    "not enough space",
    "insufficient disk space",
    "disk full",
    "no space left",
)


def run_staging_backup_body(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    _invalidate_snapshot_cache_func: Any = None,
) -> str:
    restic_cfg = get_config_section(cfg, "restic")
    settings = get_staging_settings(cfg)
    paths = validate_staging_settings(settings, restic_cfg)
    if paths is None:
        raise BackupProblem(i18n_key="error.staging.disabled")
    if not is_mc_ready(app_runtime, server):
        raise BackupProblem(i18n_key="error.backup.minecraft_not_ready")
    # 中转库不改变远端仓库的隔离要求：远端不能位于任何 backup 源内
    assert_backup_sources_do_not_contain_repository(restic_cfg)

    deadline = make_restic_deadline(restic_cfg)
    ensure_default_restic_executable_available(server, restic_cfg)
    local_cfg = ensure_local_repository(
        app_runtime, server, restic_cfg, settings, paths
    )
    preflight_chunker_parameters(app_runtime, local_cfg, restic_cfg, settings)
    estimated_bytes = run_disk_preflight(
        app_runtime, server, cfg, local_cfg, paths, deadline
    )
    assert_staging_disk_space(
        server, paths.repository_path, estimated_bytes, settings
    )

    prepare_minecraft_for_backup(app_runtime, server, cfg)
    monitor = start_backup_player_gate_monitor(app_runtime, server, cfg)
    try:
        snapshot_id = run_local_backup(
            app_runtime,
            server,
            cfg,
            local_cfg,
            settings,
            paths,
            deadline,
        )
    finally:
        if monitor is not None:
            monitor.stop()
    record_local_latest_snapshot(app_runtime, server, local_cfg)
    request_copy(app_runtime)
    return snapshot_id


def preflight_chunker_parameters(
    app_runtime: PluginRuntime,
    local_cfg: Dict[str, Any],
    restic_cfg: Dict[str, Any],
    settings: StagingSettings,
):
    """[beta] 远端可达时比对 chunker；不可达留到 copy 阶段软失败。"""

    remote_cfg = build_remote_restic_config(restic_cfg)
    probe = probe_repository(
        app_runtime,
        remote_cfg,
        deadline=make_remote_probe_deadline(settings.remote_probe_timeout_seconds),
    )
    if probe.ok:
        assert_matching_chunkers(
            app_runtime,
            local_cfg,
            remote_cfg,
            remote_probe_timeout_seconds=settings.remote_probe_timeout_seconds,
        )
    # 远端缺失、鉴权或网络问题都留到 copy 阶段：缺失会由 worker 初始化，
    # 其余按软失败重试，不阻断本地备份。


def run_disk_preflight(  # noqa: PLR0913 预检需要完整上下文
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    local_cfg: Dict[str, Any],
    paths: StagingPaths,
    deadline: Optional[float],
) -> int:
    """[beta] dry-run 估算写入量；返回 data_added_packed 字节数。"""

    args = build_backup_args(local_cfg, paths.cache_dir)
    args = ensure_option(args, "--dry-run")
    args = ensure_option(args, RESTIC_OPTION_JSON)
    result = run_restic_command(
        app_runtime, local_cfg, args, DISK_PREFLIGHT_PHASE, deadline
    )
    assert_restic_success(
        local_cfg,
        result,
        command_root_from_config(cfg),
        getattr(server, "logger", None),
    )
    summary = result.summary or {}
    estimated_bytes = safe_int(summary.get("data_added_packed", 0), 0)
    server.logger.info(
        server_tr(
            server,
            "info.staging.disk_preflight",
            estimated=format_bytes(estimated_bytes),
            path=paths.repository_path,
        )
    )
    return max(0, estimated_bytes)


def assert_staging_disk_space(
    server: PluginServerInterface,
    repository_path: str,
    estimated_bytes: int,
    settings: StagingSettings,
):
    """[beta] 剩余空间 - 预计写入量 必须不低于保留阈值。"""

    check_root = nearest_existing_directory(repository_path)
    usage = shutil.disk_usage(check_root)
    if not usage.total:
        return
    reserve_bytes = int(usage.total * settings.min_free_percent_after / 100.0)
    projected_free = int(usage.free) - int(estimated_bytes)
    if projected_free >= reserve_bytes:
        return
    raise BackupProblem(
        i18n_key="error.staging.disk_insufficient",
        path=check_root,
        free=format_bytes(int(usage.free)),
        estimated=format_bytes(int(estimated_bytes)),
        projected=format_bytes(projected_free),
        reserve=format_bytes(reserve_bytes),
        min_free_percent="{:.0f}".format(settings.min_free_percent_after),
    )


def run_local_backup(  # noqa: PLR0913 备份参数集中在一个内部入口
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    local_cfg: Dict[str, Any],
    settings: StagingSettings,
    paths: StagingPaths,
    deadline: Optional[float],
) -> str:
    ctx = ResticOperationContext(
        app_runtime=app_runtime,
        server=server,
        restic_cfg=local_cfg,
        deadline=deadline,
        invalidate_snapshot_cache=None,
        command_root=command_root_from_config(cfg),
    )
    args = build_backup_args(local_cfg, paths.cache_dir)
    result = run_restic_command_with_lock_recovery(ctx, args, RESTIC_COMMAND_BACKUP)
    try:
        assert_restic_success(
            local_cfg, result, ctx.command_root, getattr(server, "logger", None)
        )
    except BackupProblem as exc:
        if is_disk_full_failure(result):
            emergency_prune_after_disk_full(app_runtime, server, ctx, settings, paths)
        raise exc
    return result.snapshot_id


def is_disk_full_failure(result: Any) -> bool:
    text = combine_restic_output(result).lower()
    return any(marker in text for marker in DISK_FULL_MARKERS)


def emergency_prune_after_disk_full(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    ctx: ResticOperationContext,
    settings: StagingSettings,
    paths: StagingPaths,
):
    """[beta] 盘满时带超时的应急 prune；失败只告警，不掩盖原始备份错误。"""

    if app_runtime.backup.cancel.is_set():
        return
    timeout_seconds = max(1, settings.emergency_prune_timeout_seconds)
    server.logger.warning(
        server_tr(
            server,
            "warn.staging.emergency_prune_started",
            seconds=timeout_seconds,
        )
    )
    prune_ctx = replace(
        ctx,
        deadline=time.monotonic() + timeout_seconds,
    )
    args = build_emergency_prune_args(settings, paths.cache_dir)
    try:
        result = run_restic_command_with_lock_recovery(
            prune_ctx, args, PRUNE_PHASE
        )
        assert_restic_success(
            ctx.restic_cfg, result, ctx.command_root, getattr(server, "logger", None)
        )
    except Exception as exc:
        server.logger.warning(
            server_tr(
                server,
                "warn.staging.emergency_prune_failed",
                error=exc,
            )
        )
        return
    server.logger.info(server_tr(server, "info.staging.emergency_prune_finished"))


def record_local_latest_snapshot(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    _local_cfg: Dict[str, Any],
):
    # 不能在冻结窗口内再跑一次 snapshots：那会把 save-off 再拉长一个命令的
    # 时长。快照时间取备份结束时刻，足够状态页展示精度。
    record_local_snapshot_time(app_runtime, server, now_text())


def ensure_option(args: List[str], option: str) -> List[str]:
    if option in args:
        return args
    if "--" in args:
        index = args.index("--")
        return [*args[:index], option, *args[index:]]
    return [*args, option]


__all__ = [
    "assert_staging_disk_space",
    "ensure_option",
    "is_disk_full_failure",
    "run_disk_preflight",
    "run_local_backup",
    "run_staging_backup_body",
]
