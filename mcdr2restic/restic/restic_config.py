# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import os
import re
import shlex
import shutil
import socket
from typing import Any, Dict, List, Sequence, Set

from mcdr2restic.core.models import BackupProblem
from mcdr2restic.defaults.default_constants import (
    DEFAULT_DISK_CHECK_MIN_FREE_PERCENT,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_BACKUP_VALUE_OPTIONS,
    RESTIC_CFG_BACKUP_COMMAND,
    RESTIC_CFG_DISK_CHECK_ENABLED,
    RESTIC_CFG_DISK_CHECK_MIN_FREE_PERCENT,
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


def resolve_path_against_working_directory(
    value: str, restic_cfg: Dict[str, Any]
) -> str:
    """展开 ~ 与环境变量后解析路径，相对路径以 restic working_directory 兜底。"""
    text = os.path.expanduser(os.path.expandvars(str(value)))
    if os.path.isabs(text):
        return os.path.abspath(text)
    cwd = restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or os.getcwd()
    return os.path.abspath(os.path.join(str(cwd), text))


def resolve_restic_repository_path(restic_cfg: Dict[str, Any], repository: str) -> str:
    return resolve_path_against_working_directory(repository, restic_cfg)


def normalize_disk_check_percent(value: Any) -> float:
    """阈值收拢到 [0, 100]；缺失或非法时回退默认值，不让坏配置卡死备份。"""

    try:
        percent = float(value)
    except (TypeError, ValueError):
        percent = DEFAULT_DISK_CHECK_MIN_FREE_PERCENT
    if math.isnan(percent) or math.isinf(percent):
        percent = DEFAULT_DISK_CHECK_MIN_FREE_PERCENT
    return min(100.0, max(0.0, percent))


def nearest_existing_directory(path: str) -> str:
    """仓库目录可能尚不存在（首次备份），向上找已存在的祖先做磁盘探测。"""

    current = os.path.abspath(path)
    while not os.path.isdir(current):
        parent = os.path.dirname(current)
        if parent == current:
            return current
        current = parent
    return current


def assert_repository_disk_space_available(restic_cfg: Dict[str, Any]):
    """本地仓库所在盘剩余空间低于阈值时拒绝备份；远端仓库无法探测，直接跳过。"""

    if not bool(restic_cfg.get(RESTIC_CFG_DISK_CHECK_ENABLED, True)):
        return
    repository = str(restic_cfg.get(RESTIC_CFG_REPOSITORY) or "").strip()
    if not repository or not is_local_restic_repository(repository):
        return
    min_free_percent = normalize_disk_check_percent(
        restic_cfg.get(RESTIC_CFG_DISK_CHECK_MIN_FREE_PERCENT)
    )
    if min_free_percent <= 0:
        return
    check_root = nearest_existing_directory(
        resolve_restic_repository_path(restic_cfg, repository)
    )
    usage = shutil.disk_usage(check_root)
    if not usage.total:
        return
    free_percent = usage.free * 100.0 / usage.total
    if free_percent >= min_free_percent:
        return
    raise BackupProblem(
        i18n_key="error.backup.disk_space_low",
        path=check_root,
        free_percent="{:.1f}".format(free_percent),
        min_free_percent="{:g}".format(min_free_percent),
    )


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


def mask_restic_repository(repository: str) -> str:
    """日志不得泄露 rest:<scheme>://user:pass@host 里的凭据段（P3-4）。

    密码本身可含 @，须以 authority 段（:// 到首个 / 之间）内最后一个 @
    作为凭据结束锚点，不能用简单正则一次性匹配。
    """

    text = str(repository or "")
    scheme_end = text.find("://")
    if scheme_end == -1:
        return text
    authority_start = scheme_end + len("://")
    slash = text.find("/", authority_start)
    authority = text[authority_start:] if slash == -1 else text[authority_start:slash]
    at_index = authority.rfind("@")
    colon_index = authority.find(":")
    if at_index == -1 or colon_index == -1 or colon_index >= at_index:
        return text
    masked_authority = (
        authority[: colon_index + 1] + "***" + authority[at_index:]
    )
    return (
        text[:authority_start]
        + masked_authority
        + ("" if slash == -1 else text[slash:])
    )


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
    return [resolve_backup_source_path(restic_cfg, source) for source in sources]


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


def resolve_backup_source_path(restic_cfg: Dict[str, Any], source: str) -> str:
    return normalize_filesystem_path(
        resolve_path_against_working_directory(source, restic_cfg)
    )


def normalize_filesystem_path(path: str) -> str:
    normalized = os.path.abspath(os.path.normpath(path))
    try:
        return os.path.realpath(normalized)
    except OSError:
        # Windows realpath 非严格模式只容忍白名单内的 winerror，UNC 不可达
        # （ERROR_BAD_NETPATH 53）会直接抛 FileNotFoundError；解析不了就退回
        # abspath 结果，不因网络状态中断备份/恢复流程
        return normalized


# Windows 的 verbatim 前缀与 UNC 等价写法不会被 realpath/normpath 归一，
# commonpath 会因盘符形态不同而失配，恢复目标校验可借此绕过（P0-5）。
# 剥除仅用于比较，落盘与执行仍使用 normalize_filesystem_path 的结果。
WINDOWS_VERBATIM_UNC_PREFIX = "\\\\?\\UNC\\"
WINDOWS_VERBATIM_PREFIX = "\\\\?\\"
# 管理共享 \\主机\X$\… 与盘符 X:\… 指向同一位置，可确定性互映射；
# 普通共享无法离线判定，不做映射。
UNC_ADMIN_SHARE_PATTERN = re.compile(
    r"^(?:\\\\(?P<host>[^\\/]+)\\)(?P<share>[A-Za-z])\$(?P<rest>\\.*)?$"
)
_local_unc_host_names: Set[str] | None = None


def _local_unc_host_candidates() -> Set[str]:
    global _local_unc_host_names
    if _local_unc_host_names is None:
        candidates = {"localhost", "127.0.0.1", "::1"}
        try:
            hostname = socket.gethostname().rstrip(".").lower()
        except Exception:
            hostname = ""
        if hostname:
            candidates.add(hostname)
            candidates.add(hostname.split(".")[0])
        _local_unc_host_names = candidates
    return _local_unc_host_names


def strip_windows_verbatim_prefix(text: str) -> str:
    if text.upper().startswith(WINDOWS_VERBATIM_UNC_PREFIX):
        return "\\\\" + text[len(WINDOWS_VERBATIM_UNC_PREFIX):]
    if text.upper().startswith(WINDOWS_VERBATIM_PREFIX):
        return text[len(WINDOWS_VERBATIM_PREFIX):]
    return text


def map_local_admin_share_unc_to_drive(text: str) -> str:
    match = UNC_ADMIN_SHARE_PATTERN.match(text)
    if not match:
        return text
    host = match.group("host").rstrip(".").lower()
    if host not in _local_unc_host_candidates():
        return text
    rest = match.group("rest") or "\\"
    return f"{match.group('share').upper()}:{rest}"


def is_admin_share_unc_path(path: str) -> bool:
    # 主机名无法离线穷尽（局域网 IP/FQDN/DNS 别名都能指向本机），而恢复目标
    # 没有任何以管理共享书写的合法场景，按写法整体拒绝（P0-6）。
    return bool(
        UNC_ADMIN_SHARE_PATTERN.match(strip_windows_verbatim_prefix(str(path or "")))
    )


def canonical_path_for_comparison(path: str) -> str:
    if os.name != "nt":
        return str(path or "")
    text = strip_windows_verbatim_prefix(str(path or ""))
    text = map_local_admin_share_unc_to_drive(text)
    # verbatim 内的 ".." 不会被 normpath/realpath 折叠，剥除后需补一次
    return os.path.normpath(text)


def path_contains_or_equals(parent: str, child: str) -> bool:
    parent_path = canonical_path_for_comparison(normalize_filesystem_path(parent))
    child_path = canonical_path_for_comparison(normalize_filesystem_path(child))
    try:
        common = os.path.commonpath([parent_path, child_path])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(parent_path)


def normalize_command_args(value: Any) -> List[str]:
    if isinstance(value, str):
        return split_command_string(value)
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    raise BackupProblem(i18n_key="error.restic.command_args_invalid", value=value)


def split_command_string(value: str) -> List[str]:
    # Windows 命令行不是 POSIX 语法：反斜杠是路径分隔符，不能当转义符吞掉
    posix = os.name != "nt"
    parts = shlex.split(str(value), posix=posix)
    if posix:
        return parts
    return [strip_surrounding_quotes(part) for part in parts]


def strip_surrounding_quotes(part: str) -> str:
    if len(part) >= 2 and part[0] == part[-1] and part[0] in ("'", '"'):
        return part[1:-1]
    return part
