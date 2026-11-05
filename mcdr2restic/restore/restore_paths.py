# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_config import (
    canonical_path_for_comparison,
    get_backup_source_paths,
    get_effective_restic_repository,
    is_local_restic_repository,
    normalize_filesystem_path,
    path_contains_or_equals,
    resolve_restic_repository_path,
)
from mcdr2restic.restic.restic_constants import RESTIC_CFG_WORKING_DIRECTORY


RESTORE_SNAPSHOT_WHITESPACE_PATTERN = re.compile(r"\s")
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


def normalize_restore_snapshot(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise BackupProblem(i18n_key="error.restore.snapshot_empty")
    if RESTORE_SNAPSHOT_WHITESPACE_PATTERN.search(text):
        raise BackupProblem(i18n_key="error.restore.snapshot_whitespace")
    return text


def normalize_restore_include_path(
    value: Any, restic_cfg: Dict[str, Any], command_root: str
) -> str:
    raw = str(value or "").strip().strip("\"'")
    if not raw:
        raise BackupProblem(i18n_key="error.restore.path_empty")

    relative = resolve_restore_relative_path(raw, restic_cfg)
    parts = normalize_restore_path_parts(relative)
    if not parts:
        raise BackupProblem(
            i18n_key="error.restore.use_full_snapshot_command",
            command_root=command_root,
        )
    return "/" + "/".join(parts)


def resolve_restore_relative_path(raw: str, restic_cfg: Dict[str, Any]) -> str:
    workdir = get_restic_working_directory(restic_cfg)
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if os.path.isabs(expanded) and path_contains_or_equals(workdir, expanded):
        # 规范化保留 \\?\ 等前缀形态，relpath 两侧必须同为常规形态
        absolute = canonical_path_for_comparison(normalize_filesystem_path(expanded))
        return os.path.relpath(absolute, canonical_path_for_comparison(workdir))
    if is_strict_filesystem_absolute_path(expanded):
        raise BackupProblem(i18n_key="error.restore.path_outside_workdir", path=raw)
    return raw


def normalize_restore_path_parts(relative_path: str) -> List[str]:
    relative = str(relative_path or "")
    relative = relative.replace("\\", "/").strip()
    while relative.startswith("/"):
        relative = relative[1:]
    if relative.startswith("./"):
        relative = relative[2:]

    parts: List[str] = []
    for part in relative.split("/"):
        part = part.strip()
        if not part or part == ".":
            continue
        if part == "..":
            raise BackupProblem(i18n_key="error.restore.path_parent_reference")
        parts.append(part)
    return parts


def get_restic_working_directory(restic_cfg: Dict[str, Any]) -> str:
    return normalize_filesystem_path(
        str(restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or os.getcwd())
    )


def is_strict_filesystem_absolute_path(path: str) -> bool:
    text = str(path or "")
    return bool(
        WINDOWS_ABSOLUTE_PATH_PATTERN.match(text)
        or text.startswith("\\\\")
        or text.startswith("//")
    )


def normalize_restore_target_directory(
    value: Any, restic_cfg: Dict[str, Any], data_dir: str = ""
) -> str:
    raw = str(value or "").strip().strip("\"'")
    if not raw:
        raise BackupProblem(i18n_key="error.restore.target_empty")
    expanded = os.path.expanduser(os.path.expandvars(raw))
    workdir = get_restic_working_directory(restic_cfg)
    if os.path.isabs(expanded):
        target = normalize_filesystem_path(expanded)
    else:
        target = normalize_filesystem_path(os.path.join(workdir, expanded))
    assert_restore_target_allowed(target, restic_cfg, data_dir=data_dir)
    return target


def assert_restore_target_allowed(
    target: str, restic_cfg: Dict[str, Any], data_dir: str = ""
):
    target_path = canonical_path_for_comparison(normalize_filesystem_path(target))
    workdir = get_restic_working_directory(restic_cfg)
    if os.path.normcase(target_path) == os.path.normcase(
        canonical_path_for_comparison(workdir)
    ):
        raise BackupProblem(
            i18n_key="error.restore.target_is_working_directory", path=target
        )
    repository = get_effective_restic_repository(restic_cfg)
    repository_path = ""
    if repository and is_local_restic_repository(repository):
        repository_path = resolve_restic_repository_path(restic_cfg, repository)
        if path_contains_or_equals(repository_path, target_path):
            raise BackupProblem(
                i18n_key="error.restore.target_inside_repository",
                repository_path=repository_path,
            )
    for source_path in get_backup_source_paths(restic_cfg):
        if path_contains_or_equals(source_path, target_path):
            raise BackupProblem(
                i18n_key="error.restore.target_inside_backup_sources",
                source_path=source_path,
            )
    # 目标若为受保护目录的祖先，快照会覆写保护范围本身，同样必须拒绝
    protected_dirs = [workdir, *get_backup_source_paths(restic_cfg)]
    if repository_path:
        protected_dirs.append(repository_path)
    if data_dir:
        protected_dirs.append(data_dir)
    for protected in protected_dirs:
        if path_contains_or_equals(target_path, protected):
            raise BackupProblem(
                i18n_key="error.restore.target_contains_protected",
                protected_path=protected,
            )
