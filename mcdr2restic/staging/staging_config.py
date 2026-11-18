# -*- coding: utf-8 -*-
"""[beta] 中转库配置解析、路径校验与 restic 参数构造。

本模块只做纯数据变换，不启动进程、不读写仓库，便于单测覆盖。
路径隔离按真实路径校验：中转库、cache_dir、password_file 都必须位于
任何 backup 源之外，否则备份在 save-off 前硬失败。
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple

from mcdr2restic.core.models import BackupProblem
from mcdr2restic.core.utils import get_config_section, non_negative_int, safe_int
from mcdr2restic.restic.restic_config import (
    find_repository_source_conflicts,
    get_effective_restic_repository,
    is_local_restic_repository,
    normalize_command_args,
    normalize_filesystem_path,
    path_contains_or_equals,
    resolve_path_against_working_directory,
    resolve_restic_repository_path,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_BACKUP_COMMAND,
    RESTIC_CFG_PASSWORD,
    RESTIC_CFG_PASSWORD_FILE,
    RESTIC_CFG_REPOSITORY,
)

DEFAULT_COPY_RETRY_BACKOFF = (60, 300, 900)
DEFAULT_CACHE_DIR_NAME = "cache"
RESTIC_FROM_PASSWORD = "RESTIC_FROM_PASSWORD"
RESTIC_FROM_PASSWORD_FILE = "RESTIC_FROM_PASSWORD_FILE"
RESTIC_FROM_PASSWORD_COMMAND = "RESTIC_FROM_PASSWORD_COMMAND"
RESTIC_FROM_REPOSITORY = "RESTIC_FROM_REPOSITORY"

# backup_command 中出现这些选项时仓库/密码不再由插件控制，路径隔离失效
FORBIDDEN_BACKUP_OPTION_NAMES = frozenset(
    {
        "--repo",
        "--repository",
        "-r",
        "--repository-file",
        "--password-file",
        "--password-command",
    }
)

CACHE_DIR_OPTION = "--cache-dir"
LIMIT_UPLOAD_OPTION = "--limit-upload"


@dataclass(frozen=True)
class StagingSettings:
    """[beta] 中转库配置的只读视图。"""

    enabled: bool
    repository: str
    password: str
    password_file: str
    min_free_percent_after: float
    keep_last: int
    prune_max_unused: str
    deep_prune_interval_days: int
    deep_prune_max_unused: str
    prune_max_repack_size: str
    cache_dir: str
    copy_retry_max: int
    copy_retry_backoff_seconds: Tuple[int, ...]
    copy_limit_upload_kib: int
    remote_stale_warning_hours: int
    emergency_prune_timeout_seconds: int
    remote_probe_timeout_seconds: int


@dataclass(frozen=True)
class StagingPaths:
    """[beta] 解析后的中转库相关绝对路径。"""

    repository_path: str
    cache_dir: str
    password_file: str


def get_staging_settings(cfg: Dict[str, Any]) -> StagingSettings:
    section = get_config_section(cfg, "staging")
    return StagingSettings(
        enabled=bool(section.get("enabled", False)),
        repository=str(section.get("repository") or "").strip(),
        password=str(section.get("password") or ""),
        password_file=str(section.get("password_file") or "").strip(),
        min_free_percent_after=clamp_percent(
            section.get("min_free_percent_after", 10)
        ),
        keep_last=max(1, safe_int(section.get("keep_last", 2), 2)),
        prune_max_unused=non_empty_text(section.get("prune_max_unused"), "5%"),
        deep_prune_interval_days=non_negative_int(
            section.get("deep_prune_interval_days", 7), 7
        ),
        deep_prune_max_unused=non_empty_text(
            section.get("deep_prune_max_unused"), "0"
        ),
        prune_max_repack_size=non_empty_text(
            section.get("prune_max_repack_size"), "20G"
        ),
        cache_dir=str(section.get("cache_dir") or "").strip(),
        copy_retry_max=non_negative_int(section.get("copy_retry_max", 3), 3),
        copy_retry_backoff_seconds=parse_backoff(
            section.get("copy_retry_backoff_seconds")
        ),
        copy_limit_upload_kib=non_negative_int(
            section.get("copy_limit_upload_kib", 0), 0
        ),
        remote_stale_warning_hours=non_negative_int(
            section.get("remote_stale_warning_hours", 24), 24
        ),
        emergency_prune_timeout_seconds=non_negative_int(
            section.get("emergency_prune_timeout_seconds", 120), 120
        ),
        remote_probe_timeout_seconds=non_negative_int(
            section.get("remote_probe_timeout_seconds", 30), 30
        ),
    )


def staging_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(get_config_section(cfg, "staging").get("enabled", False))


def clamp_percent(value: Any) -> float:
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return 10.0
    if math.isnan(percent) or math.isinf(percent):
        return 10.0
    return min(100.0, max(0.0, percent))


def non_empty_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def parse_backoff(value: Any) -> Tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return DEFAULT_COPY_RETRY_BACKOFF
    delays = tuple(non_negative_int(item) for item in value)
    return delays or DEFAULT_COPY_RETRY_BACKOFF


def resolve_staging_paths(
    settings: StagingSettings, restic_cfg: Dict[str, Any]
) -> StagingPaths:
    repository_path = normalize_filesystem_path(
        resolve_path_against_working_directory(settings.repository, restic_cfg)
    )
    cache_dir = resolve_cache_dir(settings, repository_path, restic_cfg)
    password_file = ""
    if settings.password_file:
        password_file = normalize_filesystem_path(
            resolve_path_against_working_directory(settings.password_file, restic_cfg)
        )
    return StagingPaths(
        repository_path=repository_path,
        cache_dir=cache_dir,
        password_file=password_file,
    )


def resolve_cache_dir(
    settings: StagingSettings, repository_path: str, restic_cfg: Dict[str, Any]
) -> str:
    if settings.cache_dir:
        return normalize_filesystem_path(
            resolve_path_against_working_directory(settings.cache_dir, restic_cfg)
        )
    return normalize_filesystem_path(
        os.path.join(os.path.dirname(repository_path), DEFAULT_CACHE_DIR_NAME)
    )


def validate_staging_settings(
    settings: StagingSettings,
    restic_cfg: Dict[str, Any],
    paths: Optional[StagingPaths] = None,
):
    """[beta] 备份/复制前的配置硬校验；返回解析后的路径。

    关闭时直接返回 None：关闭分支不得创建目录或探测文件系统。
    """

    if not settings.enabled:
        return None
    if not settings.repository:
        raise BackupProblem(i18n_key="error.staging.repository_empty")
    if not is_local_restic_repository(settings.repository):
        raise BackupProblem(
            i18n_key="error.staging.repository_not_local",
            repository=settings.repository,
        )
    if not settings.password and not settings.password_file:
        raise BackupProblem(i18n_key="error.staging.password_missing")
    resolved = paths or resolve_staging_paths(settings, restic_cfg)
    if os.path.exists(resolved.repository_path) and not os.path.isdir(
        resolved.repository_path
    ):
        raise BackupProblem(
            i18n_key="error.staging.repository_path_not_directory",
            repository_path=resolved.repository_path,
        )
    assert_repository_not_overlapping_remote(restic_cfg, resolved)
    assert_paths_outside_backup_sources(restic_cfg, resolved)
    assert_password_file_exists(settings, resolved)
    assert_backup_command_has_no_repository_options(restic_cfg)
    return resolved


def assert_repository_not_overlapping_remote(
    restic_cfg: Dict[str, Any], paths: StagingPaths
):
    """[beta] 中转库与本地路径型远端仓库不得相同或互相包含。

    两者指向同一目录时复制变成同库自复制，维护会对同一个库执行两次 prune；
    purge 还会移动本地库。网络型远端没有本地路径，不在此校验范围。
    """

    remote_value = get_effective_restic_repository(restic_cfg)
    if not remote_value or not is_local_restic_repository(remote_value):
        return
    remote_path = resolve_restic_repository_path(restic_cfg, remote_value)
    if path_contains_or_equals(
        paths.repository_path, remote_path
    ) or path_contains_or_equals(remote_path, paths.repository_path):
        raise BackupProblem(
            i18n_key="error.staging.repository_overlaps_remote",
            local_path=paths.repository_path,
            remote_path=remote_path,
        )


def assert_password_file_exists(
    settings: StagingSettings, paths: StagingPaths
):
    """[beta] password_file 是实际生效的凭据时，文件必须存在。

    否则用户只会在 restic 报 "unable to open password file" 时才发现问题。
    填了 password 时 password_file 不生效，不做存在性校验。
    """

    _password, password_file = local_source_credentials(settings, paths)
    if password_file and not os.path.isfile(password_file):
        raise BackupProblem(
            i18n_key="error.staging.password_file_missing", path=password_file
        )


def assert_paths_outside_backup_sources(
    restic_cfg: Dict[str, Any], paths: StagingPaths
):
    for path, label in (
        (paths.repository_path, "repository"),
        (paths.cache_dir, "cache_dir"),
        (paths.password_file, "password_file"),
    ):
        if not path:
            continue
        conflicts = find_repository_source_conflicts(restic_cfg, path)
        if conflicts:
            raise BackupProblem(
                i18n_key="error.staging.path_inside_backup_sources",
                config_key=label,
                path=path,
                conflicts=", ".join(conflicts),
            )


def assert_backup_command_has_no_repository_options(restic_cfg: Dict[str, Any]):
    try:
        args = normalize_command_args(restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []))
    except BackupProblem:
        # 命令本身无法解析时交给现有备份命令校验报错，这里不掩盖原始问题
        return
    for item in args:
        name = str(item).split("=", 1)[0]
        if name in FORBIDDEN_BACKUP_OPTION_NAMES:
            raise BackupProblem(
                i18n_key="error.staging.backup_command_has_repository",
                option=name,
            )


def build_local_restic_config(
    restic_cfg: Dict[str, Any],
    settings: StagingSettings,
    paths: StagingPaths,
) -> Dict[str, Any]:
    """[beta] 用中转库仓库与密码覆盖用户 restic 配置。"""

    password, password_file = local_source_credentials(settings, paths)
    local = copy.deepcopy(restic_cfg)
    local[RESTIC_CFG_REPOSITORY] = paths.repository_path
    local[RESTIC_CFG_PASSWORD] = password
    local[RESTIC_CFG_PASSWORD_FILE] = password_file
    return local


def build_remote_restic_config(restic_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """[beta] 远端继续沿用用户原始仓库与密码。"""

    return copy.deepcopy(restic_cfg)


def remote_repository_value(restic_cfg: Dict[str, Any]) -> str:
    return get_effective_restic_repository(restic_cfg)


def build_copy_source_config(
    dest_restic_cfg: Dict[str, Any],
    source_password: str,
    source_password_file: str,
) -> Dict[str, Any]:
    """[beta] copy/init --from-repo 的源仓库凭据走 RESTIC_FROM_* 环境变量。

    restic 的 RESTIC_PASSWORD 只作用于目标仓库；源仓库必须使用独立的
    RESTIC_FROM_PASSWORD(_FILE)，否则两端密码不同时无法打开源库。
    """

    cfg = copy.deepcopy(dest_restic_cfg)
    environment = cfg.get("environment")
    environment = dict(environment) if isinstance(environment, dict) else {}
    for key in (
        RESTIC_FROM_PASSWORD,
        RESTIC_FROM_PASSWORD_FILE,
        RESTIC_FROM_PASSWORD_COMMAND,
        RESTIC_FROM_REPOSITORY,
    ):
        environment.pop(key, None)
    if source_password:
        environment[RESTIC_FROM_PASSWORD] = source_password
    elif source_password_file:
        environment[RESTIC_FROM_PASSWORD_FILE] = source_password_file
    cfg["environment"] = environment
    return cfg


def local_source_credentials(
    settings: StagingSettings, paths: StagingPaths
) -> Tuple[str, str]:
    """中转库凭据：password 优先，password_file 仅在 password 为空时生效。

    与主 restic 段及配置注释语义一致；否则默认 password_file 指向的文件
    不存在时，只填 password 的用户启用即坏。
    """

    if settings.password:
        return settings.password, ""
    return "", paths.password_file


def remote_source_credentials(restic_cfg: Dict[str, Any]) -> Tuple[str, str]:
    return (
        str(restic_cfg.get(RESTIC_CFG_PASSWORD) or ""),
        str(restic_cfg.get(RESTIC_CFG_PASSWORD_FILE) or "").strip(),
    )


def build_backup_args(
    restic_cfg: Dict[str, Any], cache_dir: str
) -> List[str]:
    """[beta] 本地备份参数：用户原备份命令 + 受控 cache 目录。"""

    args = remove_option(
        normalize_command_args(restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, [])),
        {CACHE_DIR_OPTION},
    )
    return with_cache_dir(args, cache_dir)


def build_copy_args(
    local_repository_path: str,
    snapshot_ids: Sequence[str],
    cache_dir: str,
    limit_upload_kib: int = 0,
) -> List[str]:
    args = [
        "copy",
        "--from-repo",
        local_repository_path,
        *[str(item) for item in snapshot_ids],
        "--retry-lock",
        "5m",
    ]
    args = with_cache_dir(args, cache_dir)
    return with_limit_upload(args, limit_upload_kib)


def build_init_args(
    from_repository: Optional[str] = None, copy_chunker_params: bool = False
) -> List[str]:
    args = ["init"]
    if from_repository:
        args.extend(["--from-repo", str(from_repository)])
    if copy_chunker_params:
        args.append("--copy-chunker-params")
    return args


def build_cat_config_args() -> List[str]:
    return ["cat", "config"]


def build_snapshots_args(latest_only: bool = False) -> List[str]:
    args = ["snapshots", "--json"]
    if latest_only:
        args.extend(["--latest", "1"])
    return args


def build_local_forget_args(
    settings: StagingSettings, cache_dir: str, deep: bool
) -> List[str]:
    max_unused = (
        settings.deep_prune_max_unused if deep else settings.prune_max_unused
    )
    args = [
        "forget",
        "--keep-last",
        str(settings.keep_last),
        "--prune",
        "--max-unused",
        max_unused,
    ]
    if deep:
        args.extend(["--max-repack-size", settings.prune_max_repack_size])
    return with_cache_dir(args, cache_dir)


def build_emergency_prune_args(settings: StagingSettings, cache_dir: str) -> List[str]:
    # 官方建议：先 forget 掉多余快照，再用 --max-repack-size 0 让 prune
    # 尽量少占用额外的临时空间（不是关闭重打包）
    args = [
        "forget",
        "--keep-last",
        str(settings.keep_last),
        "--prune",
        "--max-repack-size",
        "0",
    ]
    return with_cache_dir(args, cache_dir)


def build_cache_cleanup_args(cache_dir: str) -> List[str]:
    return with_cache_dir(["cache", "--cleanup"], cache_dir)


def with_cache_dir(args: Sequence[str], cache_dir: str) -> List[str]:
    cleaned = remove_option(list(args), {CACHE_DIR_OPTION})
    if not cache_dir:
        return cleaned
    return insert_before_double_dash(cleaned, [CACHE_DIR_OPTION, cache_dir])


def with_limit_upload(args: Sequence[str], limit_upload_kib: int) -> List[str]:
    cleaned = remove_option(list(args), {LIMIT_UPLOAD_OPTION})
    if limit_upload_kib <= 0:
        return cleaned
    return insert_before_double_dash(
        cleaned, [LIMIT_UPLOAD_OPTION, str(limit_upload_kib)]
    )


def remove_option(args: List[str], names: Collection[str]) -> List[str]:
    names_set = set(names)
    cleaned: List[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        name = item.split("=", 1)[0]
        if name in names_set:
            if "=" not in item and index + 1 < len(args):
                index += 2
                continue
            index += 1
            continue
        cleaned.append(item)
        index += 1
    return cleaned


def insert_before_double_dash(args: List[str], new_args: List[str]) -> List[str]:
    if "--" in args:
        index = args.index("--")
        return [*args[:index], *new_args, *args[index:]]
    return [*args, *new_args]
