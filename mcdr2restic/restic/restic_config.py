# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import shlex
from typing import Any, Dict, List, Sequence, Set

from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_constants import (
    RESTIC_BACKUP_VALUE_OPTIONS,
    RESTIC_CFG_BACKUP_COMMAND,
    RESTIC_CFG_ENVIRONMENT,
    RESTIC_CFG_PASSWORD,
    RESTIC_CFG_PASSWORD_FILE,
    RESTIC_CFG_REPOSITORY,
    RESTIC_CFG_WORKING_DIRECTORY,
    RESTIC_COMMAND_BACKUP,
    RESTIC_ENV_PASSWORD,
    RESTIC_ENV_PASSWORD_COMMAND,
    RESTIC_ENV_PASSWORD_FILE,
    RESTIC_ENV_REPOSITORY,
    RESTIC_REPOSITORY_OPTIONS,
)


def build_restic_environment(restic_cfg: Dict[str, Any]) -> Dict[str, str]:
    env = os.environ.copy()
    configured_env = restic_cfg.get(RESTIC_CFG_ENVIRONMENT, {})
    if isinstance(configured_env, dict):
        for key, value in configured_env.items():
            if value is None:
                env.pop(str(key), None)
            else:
                env[str(key)] = str(value)
    apply_repository_environment(env, restic_cfg)
    apply_password_environment(env, restic_cfg)
    return env


def apply_repository_environment(env: Dict[str, str], restic_cfg: Dict[str, Any]):
    repository = str(restic_cfg.get(RESTIC_CFG_REPOSITORY, "") or "").strip()
    if repository:
        env[RESTIC_ENV_REPOSITORY] = repository


def apply_password_environment(env: Dict[str, str], restic_cfg: Dict[str, Any]):
    password = str(restic_cfg.get(RESTIC_CFG_PASSWORD, "") or "")
    password_file = str(restic_cfg.get(RESTIC_CFG_PASSWORD_FILE, "") or "").strip()
    if password:
        env[RESTIC_ENV_PASSWORD] = password
        env.pop(RESTIC_ENV_PASSWORD_FILE, None)
        env.pop(RESTIC_ENV_PASSWORD_COMMAND, None)
        return
    if password_file:
        env.pop(RESTIC_ENV_PASSWORD, None)
        env[RESTIC_ENV_PASSWORD_FILE] = password_file


def is_local_restic_repository(repository: str) -> bool:
    repo = repository.strip()
    if not repo:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", repo):
        return True
    lowered = repo.lower()
    remote_prefixes = (
        "sftp:",
        "rest:",
        "s3:",
        "b2:",
        "azure:",
        "gs:",
        "rclone:",
        "swift:",
        "opendal:",
        "http:",
        "https:",
    )
    return not lowered.startswith(remote_prefixes)


def resolve_restic_repository_path(restic_cfg: Dict[str, Any], repository: str) -> str:
    repository = os.path.expanduser(os.path.expandvars(repository))
    if os.path.isabs(repository):
        return os.path.abspath(repository)
    cwd = restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or os.getcwd()
    return os.path.abspath(os.path.join(str(cwd), repository))


def assert_backup_sources_do_not_contain_repository(restic_cfg: Dict[str, Any]):
    repository = get_effective_restic_repository(restic_cfg)
    if not repository or not is_local_restic_repository(repository):
        return
    repository_path = resolve_restic_repository_path(restic_cfg, repository)
    conflicts = find_repository_source_conflicts(restic_cfg, repository_path)
    if conflicts:
        raise_repository_source_conflict(repository_path, conflicts)


def find_repository_source_conflicts(
    restic_cfg: Dict[str, Any], repository_path: str
) -> List[str]:
    return [
        source_path
        for source_path in get_backup_source_paths(restic_cfg)
        if path_contains_or_equals(source_path, repository_path)
    ]


def raise_repository_source_conflict(repository_path: str, conflicts: List[str]):
    raise BackupProblem(
        i18n_key="error.restic.repository_inside_backup_sources",
        repository_path=repository_path,
        conflicts=", ".join(conflicts),
    )


def get_effective_restic_repository(restic_cfg: Dict[str, Any]) -> str:
    env = build_restic_environment(restic_cfg)
    repository = str(env.get(RESTIC_ENV_REPOSITORY, "") or "").strip()
    if repository:
        return repository
    try:
        args = normalize_command_args(restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []))
    except BackupProblem:
        return ""
    return find_option_value(args, set(RESTIC_REPOSITORY_OPTIONS))


def find_option_value(args: List[str], names: Set[str]) -> str:
    for index, item in enumerate(args):
        for name in names:
            if item == name and index + 1 < len(args):
                return str(args[index + 1]).strip()
            if item.startswith(name + "="):
                return item.split("=", 1)[1].strip()
    return ""


def get_backup_source_paths(restic_cfg: Dict[str, Any]) -> List[str]:
    args = normalize_command_args(restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []))
    sources = extract_restic_backup_sources(args)
    cwd = str(restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or os.getcwd())
    return [resolve_backup_source_path(cwd, source) for source in sources]


def extract_restic_backup_sources(args: List[str]) -> List[str]:
    try:
        index = args.index(RESTIC_COMMAND_BACKUP) + 1
    except ValueError:
        index = 0
    return collect_restic_backup_sources(args, index)


def collect_restic_backup_sources(args: List[str], start_index: int) -> List[str]:
    sources: List[str] = []
    index = start_index
    while index < len(args):
        item = args[index]
        if item == "--":
            sources.extend(args[index + 1 :])
            break
        if item.startswith("-") and item != "-":
            index += restic_backup_option_width(args, index)
            continue
        sources.append(item)
        index += 1
    return sources


def restic_backup_option_width(args: List[str], index: int) -> int:
    option = args[index]
    if "=" in option:
        return 1
    name = option.split("=", 1)[0]
    if name in restic_backup_value_options() and index + 1 < len(args):
        return 2
    return 1


def restic_backup_value_options() -> Set[str]:
    return set(RESTIC_BACKUP_VALUE_OPTIONS)


def resolve_backup_source_path(cwd: str, source: str) -> str:
    source = os.path.expanduser(os.path.expandvars(str(source)))
    if os.path.isabs(source):
        return normalize_filesystem_path(source)
    return normalize_filesystem_path(os.path.join(cwd, source))


def normalize_filesystem_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.normpath(path)))


def path_contains_or_equals(parent: str, child: str) -> bool:
    parent_path = normalize_filesystem_path(parent)
    child_path = normalize_filesystem_path(child)
    try:
        common = os.path.commonpath([parent_path, child_path])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(parent_path)


def normalize_command_args(value: Any) -> List[str]:
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    raise BackupProblem(i18n_key="error.restic.command_args_invalid", value=value)
