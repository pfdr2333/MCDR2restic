# -*- coding: utf-8 -*-
"""[beta] 中转库 purge：仅支持文件系统型远端。

改名旧远端目录保留为回滚副本，再从本地库重新初始化并复制。远端比本地新
时必须显式 --force；初始化失败会删除半成品目录并把旧目录改回原位。
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import config_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section
from mcdr2restic.restic.restic_config import (
    find_repository_source_conflicts,
    get_effective_restic_repository,
    is_local_restic_repository,
    resolve_restic_repository_path,
)
from mcdr2restic.restic.restic_download import (
    ensure_default_restic_executable_available,
)
from mcdr2restic.staging.staging_config import (
    build_copy_source_config,
    build_init_args,
    build_local_restic_config,
    build_remote_restic_config,
    get_staging_settings,
    local_source_credentials,
    staging_enabled,
    validate_staging_settings,
)
from mcdr2restic.staging.staging_repository import (
    INIT_REMOTE_PHASE,
    compare_restic_times,
    latest_snapshot_time,
    probe_repository,
    read_snapshots,
    run_initialization,
)
from mcdr2restic.staging.staging_state import (
    STAGING_COPY_STATUS_NEVER,
    update_staging_runtime,
)
from mcdr2restic.staging.staging_worker import request_copy


PURGE_BACKUP_SUFFIX = ".purged-"


def purge_staging_remote(  # noqa: C901 回滚分支与路径校验集中在一个入口
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    force: bool = False,
) -> str:
    restic_cfg = get_config_section(cfg, "restic")
    settings = get_staging_settings(cfg)
    if not staging_enabled(cfg):
        raise BackupProblem(i18n_key="error.staging.disabled")
    paths = validate_staging_settings(settings, restic_cfg)
    if paths is None:
        raise BackupProblem(i18n_key="error.staging.disabled")
    ensure_default_restic_executable_available(server, restic_cfg)

    remote_value = get_effective_restic_repository(restic_cfg)
    if not remote_value or not is_local_restic_repository(remote_value):
        raise BackupProblem(i18n_key="error.staging.purge_requires_filesystem")
    remote_path = resolve_restic_repository_path(restic_cfg, remote_value)
    conflicts = find_repository_source_conflicts(restic_cfg, remote_path)
    if conflicts:
        raise BackupProblem(
            i18n_key="error.restic.repository_inside_backup_sources",
            repository_path=remote_path,
            conflicts=", ".join(conflicts),
        )

    slot = app_runtime.staging.copy_slot
    slot.cancel.clear()
    local_cfg = build_local_restic_config(restic_cfg, settings, paths)
    local_probe = probe_repository(app_runtime, local_cfg, slot=slot)
    if not local_probe.ok:
        # purge 只能用本地库重建远端；本地库缺失时绝不能反过来用远端初始化，
        # 否则随后的 purge 会把唯一的远端副本也清掉
        raise BackupProblem(
            i18n_key="error.staging.local_repo_missing", error=local_probe.error
        )
    local_latest = latest_snapshot_time(
        read_snapshots(app_runtime, server, local_cfg, slot=slot)
    )
    remote_probe = probe_repository(
        app_runtime, build_remote_restic_config(restic_cfg), slot=slot
    )
    remote_latest = None
    if remote_probe.ok:
        remote_latest = latest_snapshot_time(
            read_snapshots(
                app_runtime,
                server,
                build_remote_restic_config(restic_cfg),
                slot=slot,
            )
        )
        validate_purge_direction(local_latest, remote_latest, force)

    backup_path = ""
    if os.path.exists(remote_path):
        backup_path = reserve_purge_backup_path(remote_path)
        try:
            shutil.move(remote_path, backup_path)
        except OSError as exc:
            raise BackupProblem(
                i18n_key="error.staging.purge_failed", error=exc
            ) from exc

    remote_cfg = build_remote_restic_config(restic_cfg)
    source_password, source_password_file = local_source_credentials(settings, paths)
    init_cfg = build_copy_source_config(
        remote_cfg, source_password, source_password_file
    )
    try:
        run_initialization(
            app_runtime,
            server,
            init_cfg,
            build_init_args(paths.repository_path, copy_chunker_params=True),
            INIT_REMOTE_PHASE,
            "error.staging.purge_failed",
            slot,
        )
        verify = probe_repository(app_runtime, remote_cfg, slot=slot)
        if not verify.ok:
            raise BackupProblem(
                i18n_key="error.staging.purge_failed", error=verify.error
            )
    except Exception:
        rollback_purge(remote_path, backup_path)
        raise

    reset_purge_runtime(app_runtime, server)
    request_copy(app_runtime)
    language = config_language(cfg, get_mcdr_language(server))
    if backup_path:
        return tr(
            language, "info.staging.purge_completed", backup_path=backup_path
        )
    return tr(language, "info.staging.purge_initialized")


def validate_purge_direction(
    local_latest: Optional[str], remote_latest: Optional[str], force: bool
):
    if force or remote_latest is None:
        return
    if local_latest is None or compare_restic_times(remote_latest, local_latest) > 0:
        raise BackupProblem(
            i18n_key="error.staging.purge_remote_newer",
            local_time=local_latest or "-",
            remote_time=remote_latest,
        )


def reserve_purge_backup_path(remote_path: str) -> str:
    base = "{}{}{}".format(
        remote_path,
        PURGE_BACKUP_SUFFIX,
        datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    if not os.path.exists(base):
        return base
    index = 1
    while True:
        candidate = "{}.{}".format(base, index)
        if not os.path.exists(candidate):
            return candidate
        index += 1


def rollback_purge(remote_path: str, backup_path: str):
    if os.path.exists(remote_path):
        shutil.rmtree(remote_path, ignore_errors=True)
    if backup_path and os.path.exists(backup_path):
        shutil.move(backup_path, remote_path)


def reset_purge_runtime(
    app_runtime: PluginRuntime, server: PluginServerInterface
):
    update_staging_runtime(
        app_runtime,
        server,
        staging_cursor_time=None,
        staging_cursor_ids=[],
        staging_copy_backlog=0,
        staging_remote_latest_time=None,
        staging_chunker_mismatch=False,
        staging_remote_stale_warned_at=None,
        staging_last_copy_time=None,
        staging_last_copy_success_time=None,
        staging_last_copy_status=STAGING_COPY_STATUS_NEVER,
        staging_last_copy_message="",
    )


__all__ = [
    "purge_staging_remote",
    "reserve_purge_backup_path",
    "validate_purge_direction",
]
