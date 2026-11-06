# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import yaml
from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.config_paths import get_data_file_path
from mcdr2restic.core.i18n import server_tr
from mcdr2restic.config.config_template import (
    find_comment_run_start,
    find_nested_entry_end,
    find_nested_key_index,
    get_language_lines,
    get_restic_auto_init_lines,
    get_restic_password_file_lines,
    get_restic_password_lines,
    get_restic_progress_interval_lines,
    get_restic_repository_lines,
    get_restic_timeout_lines,
    get_restic_use_path_restic_lines,
    get_schedule_activity_lines,
    get_schedule_online_command_lines,
    get_top_level_block_lines,
    template_top_level_keys,
    yaml_scalar,
    TOP_LEVEL_KEY_PATTERN,
)
from mcdr2restic.defaults.config_template_resources import (
    available_config_template_languages,
    config_template_text,
)
from mcdr2restic.defaults.default_constants import CONFIG_NAME, CONFIG_VERSION
from mcdr2restic.core.models import ConfigError
from mcdr2restic.core.utils import get_config_section, safe_int


ResticLineBuilder = Callable[[str, Dict[str, Any]], List[str]]


def migrate_legacy_config(cfg: Dict[str, Any]):
    old_version = read_config_version(cfg)
    migrate_schedule_config(cfg)
    migrate_restic_config(cfg, old_version)
    cfg["config_version"] = CONFIG_VERSION


def read_config_version(cfg: Dict[str, Any]) -> int:
    try:
        return int(cfg.get("config_version", 0) or 0)
    except Exception:
        return 0


def migrate_schedule_config(cfg: Dict[str, Any]):
    schedule = cfg.get("schedule")
    if not isinstance(schedule, dict):
        return

    old_key = "require_player_joined_in_wait_period"
    new_key = "require_player_activity_in_wait_period"
    if new_key not in schedule and old_key in schedule:
        schedule[new_key] = bool(schedule.get(old_key, True))
    schedule.pop("online_check_interval_seconds", None)


def migrate_restic_config(cfg: Dict[str, Any], old_version: int):
    restic = cfg.get("restic")
    if not isinstance(restic, dict):
        return

    environment = restic.get("environment")
    if not isinstance(environment, dict):
        environment = {}
    migrate_restic_secret_keys(restic, environment)
    if old_version < 9 and safe_int(restic.get("timeout_seconds", 0), 0) == 3600:
        restic["timeout_seconds"] = 0


def migrate_restic_secret_keys(restic: Dict[str, Any], environment: Dict[str, Any]):
    if "repository" not in restic and environment.get("RESTIC_REPOSITORY"):
        restic["repository"] = environment.get("RESTIC_REPOSITORY")
    if "password" not in restic and environment.get("RESTIC_PASSWORD"):
        restic["password"] = environment.get("RESTIC_PASSWORD")
    if "password_file" not in restic and environment.get("RESTIC_PASSWORD_FILE"):
        restic["password_file"] = environment.get("RESTIC_PASSWORD_FILE")
    if "password" not in restic and restic.get("password_file"):
        restic["password"] = ""


def migrate_config_file(
    server: PluginServerInterface, language: str, cfg: Dict[str, Any]
):
    path = get_data_file_path(server, CONFIG_NAME)
    if not os.path.exists(path):
        return
    migrate_config_file_or_raise(server, path, language, cfg)


def migrate_config_file_or_raise(
    server: PluginServerInterface,
    path: str,
    language: str,
    cfg: Dict[str, Any],
):
    with open(path, "r", encoding="utf8", newline="") as file:
        lines = file.readlines()
    original = "".join(lines)
    updated = "".join(apply_config_file_migrations(lines, language, cfg))
    if updated == original:
        return

    source_data = validate_config_text(original, path)
    source_version = config_version_from_mapping(source_data, path)
    warn_deprecated_config_keys(server, source_data)
    validate_config_text(updated, path)
    atomically_replace_config_with_backup(path, updated, source_version)
    server.logger.info(server_tr(server, "info.config.migrated", name=CONFIG_NAME))


def config_version_from_mapping(data: Dict[str, Any], path: str) -> int:
    version = data.get("config_version")
    if version is None:
        return 0
    try:
        return int(version)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "error.config.version_invalid", path=path, value=version
        ) from exc


DEPRECATED_SCHEDULE_KEYS = (
    "require_player_joined_in_wait_period",
    "online_check_interval_seconds",
)

DEPRECATED_RESTORE_KEYS = ("stop_timeout_seconds",)

# restic 自动下载相关配置已固化为内置默认值，旧键不再生效（2026-11）
DEPRECATED_RESTIC_KEYS = (
    "auto_download",
    "download_version",
    "download_proxy_prefixes",
    "download_timeout_seconds",
)


def collect_deprecated_config_keys(cfg: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    if "runtime" in cfg:
        keys.append("runtime")
    schedule = cfg.get("schedule")
    if isinstance(schedule, dict):
        keys.extend(
            "schedule.{}".format(key)
            for key in DEPRECATED_SCHEDULE_KEYS
            if key in schedule
        )
    restore = cfg.get("restore")
    if isinstance(restore, dict):
        keys.extend(
            "restore.{}".format(key)
            for key in DEPRECATED_RESTORE_KEYS
            if key in restore
        )
    restic = cfg.get("restic")
    if isinstance(restic, dict):
        keys.extend(
            "restic.{}".format(key)
            for key in DEPRECATED_RESTIC_KEYS
            if key in restic
        )
    return keys


def warn_deprecated_config_keys(
    server: PluginServerInterface, cfg: Dict[str, Any]
):
    for key in collect_deprecated_config_keys(cfg):
        server.logger.warning(
            server_tr(server, "warn.config.deprecated_key_removed", config_key=key)
        )


def validate_config_text(text: str, path: str) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError("error.config.yaml_invalid", path=path) from exc
    if not isinstance(data, dict):
        raise ConfigError("error.config.root_not_mapping", path=path)
    return data


def atomically_replace_config_with_backup(
    path: str, updated: str, source_version: int
):
    config_path = Path(path)
    temporary_path = config_path.with_name("{}.temp".format(config_path.name))
    backup_path = config_path.with_name(
        "{}.v{}.bak".format(config_path.name, source_version)
    )
    if backup_path.exists():
        raise ConfigError("error.config.migration_backup_exists", path=backup_path)

    try:
        write_durable_text_file(temporary_path, updated, exclusive=False)
        with config_path.open("r", encoding="utf8", newline="") as file:
            original = file.read()
        write_durable_text_file(backup_path, original, exclusive=True)
        os.replace(temporary_path, config_path)
    except OSError as exc:
        raise ConfigError(
            "error.config.migration_write_failed", path=config_path, error=exc
        ) from exc


def write_durable_text_file(path: Path, text: str, exclusive: bool):
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf8", newline="") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())


def apply_config_file_migrations(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    lines = ensure_language_line(lines, language, cfg)
    lines = ensure_schedule_migration_lines(lines, language, cfg)
    lines = remove_deprecated_schedule_lines(lines)
    lines = remove_deprecated_restore_lines(lines)
    lines = remove_deprecated_restic_lines(lines)
    # runtime 顶级块是内存生成态，文件里的残留块早已无任何效果（P3-7）
    lines = remove_deprecated_runtime_lines(lines)
    lines = ensure_restic_migration_lines(lines, language, cfg)
    lines = ensure_restic_timeout_value(lines, cfg)
    lines = ensure_missing_top_level_blocks(lines, language)
    return ensure_config_version_tail(lines, language)


def ensure_language_line(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    if has_top_level_key(lines, "language"):
        return lines
    configured_language = str(cfg.get("language") or language)
    return insert_before_top_level_key(
        lines, "command", get_language_lines(language, configured_language)
    )


def ensure_schedule_migration_lines(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    schedule = get_config_section(cfg, "schedule")
    insertions: List[str] = []
    if not has_nested_key(lines, "schedule", "require_player_activity_in_wait_period"):
        insertions.extend(get_schedule_activity_lines(language, schedule))
    if not has_nested_key(lines, "schedule", "online_check_command"):
        insertions.extend(get_schedule_online_command_lines(language, schedule))
    if not insertions:
        return lines
    return insert_into_top_level_block(lines, "schedule", insertions)


# 顶级配置块迁移注册表：新增配置段时必须把键名加入此列表并保持模板顺序，
# test_apply_config_file_migrations_inserts_all_registered_top_level_blocks 兜底防漏。
WHOLE_BLOCK_SECTIONS = (
    "force_schedule",
    "maintenance_schedule",
    "update_check",
    "discord",
    "custom_webhook",
    "snapshot_cache",
    "restore",
)


def ensure_missing_top_level_blocks(lines: List[str], language: str) -> List[str]:
    # 逆模板序处理：先插入靠后的段，靠前的段才能以它们为锚点紧贴插入。
    for section in reversed(WHOLE_BLOCK_SECTIONS):
        if has_top_level_key(lines, section):
            continue
        block = get_top_level_block_lines(language, section)
        if not block:
            continue
        lines = insert_block_at_template_position(lines, language, section, block)
    return lines


def insert_block_at_template_position(
    lines: List[str], language: str, section: str, block: List[str]
) -> List[str]:
    template_keys = template_top_level_keys(language)
    successors = template_keys[template_keys.index(section) + 1 :]
    for successor in successors:
        if has_top_level_key(lines, successor):
            return insert_before_top_level_key(lines, successor, block)
    return insert_before_config_version_or_end(lines, block)


def ensure_restic_migration_lines(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    restic = get_config_section(cfg, "restic")
    insertions: List[str] = []
    for key, builder in restic_migration_builders():
        if not has_nested_key(lines, "restic", key):
            insertions.extend(builder(language, restic))
    if not insertions:
        return lines
    return insert_into_top_level_block(lines, "restic", insertions)


def restic_migration_builders() -> List[Tuple[str, ResticLineBuilder]]:
    return [
        ("repository", get_restic_repository_lines),
        ("password", get_restic_password_lines),
        ("password_file", get_restic_password_file_lines),
        ("use_path_restic", get_restic_use_path_restic_lines),
        ("auto_init_local_repository", get_restic_auto_init_lines),
        ("timeout_seconds", get_restic_timeout_lines),
        ("progress_interval_seconds", get_restic_progress_interval_lines),
    ]


def ensure_restic_timeout_value(lines: List[str], cfg: Dict[str, Any]) -> List[str]:
    restic = get_config_section(cfg, "restic")
    start, end = find_top_level_block(lines, "restic")
    if start is None:
        return lines

    pattern = re.compile(r"^(\s+timeout_seconds\s*:\s*).*$")
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if match:
            lines[index] = rewrite_yaml_value_line(
                lines[index], match.group(1), restic.get("timeout_seconds", 0)
            )
            break
    return lines


def rewrite_yaml_value_line(line: str, prefix: str, value: Any) -> str:
    newline = "\n" if line.endswith("\n") else ""
    return "{}{}{}".format(prefix, yaml_scalar(value), newline)


def ensure_config_version_tail(lines: List[str], language: str) -> List[str]:
    marker_comment = config_version_marker_comment(language)
    version_line = "config_version: {}\n".format(CONFIG_VERSION)
    lines = remove_config_version_lines(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    if lines and lines[-1].strip():
        lines.append("\n")
    lines.append(marker_comment)
    lines.append(version_line)
    return lines


def config_version_marker_comment(language: str) -> str:
    return "{}\n".format(
        config_template_text(language, "template.snippet.config_version_marker_comment")
    )


def remove_config_version_lines(lines: List[str]) -> List[str]:
    cleaned: List[str] = []
    for line in lines:
        if re.match(r"^config_version\s*:", line):
            if cleaned and is_config_version_comment(cleaned[-1]):
                cleaned.pop()
            continue
        cleaned.append(line)
    return cleaned


def is_config_version_comment(line: str) -> bool:
    stripped = line.strip().lower()
    comments = {
        config_template_text(language, "template.snippet.config_version_marker_comment")
        .strip()
        .lower()
        for language in available_config_template_languages()
    }
    return stripped in comments


def remove_deprecated_schedule_lines(lines: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, "schedule")
    if start is None:
        return lines

    remove_indexes = deprecated_schedule_line_indexes(lines, start, end)
    if not remove_indexes:
        return lines
    return [line for index, line in enumerate(lines) if index not in remove_indexes]


def remove_deprecated_restore_lines(lines: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, "restore")
    if start is None:
        return lines

    key_pattern = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_-]*)\s*:")
    remove_indexes: Set[int] = set()
    for index in range(start + 1, end):
        match = key_pattern.match(lines[index])
        if match and match.group(1) in DEPRECATED_RESTORE_KEYS:
            remove_indexes.add(index)
    if not remove_indexes:
        return lines
    return [line for index, line in enumerate(lines) if index not in remove_indexes]


def remove_deprecated_restic_lines(lines: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, "restic")
    if start is None:
        return lines

    block = lines[start:end]
    remove_indexes: Set[int] = set()
    for key in DEPRECATED_RESTIC_KEYS:
        key_index = find_nested_key_index(block, key)
        if key_index is None:
            continue
        comment_start = find_comment_run_start(block, key_index)
        entry_end = find_nested_entry_end(block, key_index)
        remove_indexes.update(range(start + comment_start, start + entry_end))
    if not remove_indexes:
        return lines
    return [line for index, line in enumerate(lines) if index not in remove_indexes]


def remove_deprecated_runtime_lines(lines: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, "runtime")
    if start is None:
        return lines
    return lines[:start] + lines[end:]


def deprecated_schedule_line_indexes(
    lines: List[str], start: int, end: int
) -> Set[int]:
    remove_indexes: Set[int] = set()
    key_pattern = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_-]*)\s*:")
    for index in range(start + 1, end):
        match = key_pattern.match(lines[index])
        if not match or match.group(1) not in DEPRECATED_SCHEDULE_KEYS:
            continue
        remove_indexes.add(index)
        remove_indexes.update(deprecated_schedule_comment_indexes(lines, start, index))
    return remove_indexes


def deprecated_schedule_comment_indexes(
    lines: List[str], start: int, index: int
) -> Set[int]:
    indexes: Set[int] = set()
    comment_index = index - 1
    while comment_index > start and is_deprecated_schedule_comment(
        lines[comment_index]
    ):
        indexes.add(comment_index)
        comment_index -= 1
    return indexes


def is_deprecated_schedule_comment(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return False
    lowered = stripped.lower()
    keywords = [
        "require_player_joined_in_wait_period",
        "online_check_interval_seconds",
        "没人进入",
        "等待周期内每隔",
        "采样",
        "nobody joined",
        "sampling interval",
        "waiting period",
    ]
    return any(keyword in lowered for keyword in keywords)


def has_top_level_key(lines: List[str], key: str) -> bool:
    pattern = re.compile(r"^{}\s*:".format(re.escape(key)))
    return any(pattern.match(line) for line in lines)


def has_nested_key(lines: List[str], block_key: str, nested_key: str) -> bool:
    start, end = find_top_level_block(lines, block_key)
    if start is None:
        return False
    pattern = re.compile(r"^\s+{}\s*:".format(re.escape(nested_key)))
    return any(pattern.match(line) for line in lines[start + 1 : end])


def insert_into_top_level_block(
    lines: List[str], block_key: str, insertions: List[str]
) -> List[str]:
    start, end = find_top_level_block(lines, block_key)
    if start is None:
        block = ["\n", "{}:\n".format(block_key), *insertions]
        return insert_before_config_version_or_end(lines, block)
    return lines[:end] + insertions + lines[end:]


def insert_before_config_version_or_end(
    lines: List[str], insertions: List[str]
) -> List[str]:
    version_index = find_config_version_index(lines)
    if version_index is None:
        return lines + insertions
    return lines[:version_index] + insertions + lines[version_index:]


def find_config_version_index(lines: List[str]) -> Optional[int]:
    for index, line in enumerate(lines):
        if re.match(r"^config_version\s*:", line):
            return index
    return None


def insert_before_top_level_key(
    lines: List[str], key: str, insertions: List[str]
) -> List[str]:
    pattern = re.compile(r"^{}\s*:".format(re.escape(key)))
    for index, line in enumerate(lines):
        if pattern.match(line):
            insertion_index = find_top_level_block_preamble_start(lines, index)
            return lines[:insertion_index] + insertions + lines[insertion_index:]
    return insert_before_config_version_or_end(lines, insertions)


def find_top_level_block(lines: List[str], block_key: str) -> Tuple[Optional[int], int]:
    start = find_top_level_block_start(lines, block_key)
    if start is None:
        return None, len(lines)
    return start, find_top_level_block_end_with_preamble(lines, start)


def find_top_level_block_start(lines: List[str], block_key: str) -> Optional[int]:
    pattern = re.compile(r"^{}\s*:".format(re.escape(block_key)))
    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


# 与 config_template.find_top_level_block_end 不同：迁移插入点需把下一顶级键上方的
# 注释/空行一并视作前块的尾部，故单独命名以示区别。
def find_top_level_block_end_with_preamble(lines: List[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if TOP_LEVEL_KEY_PATTERN.match(lines[index]):
            return find_top_level_block_preamble_start(lines, index)
    return len(lines)


def find_top_level_block_preamble_start(lines: List[str], top_level_index: int) -> int:
    index = top_level_index
    while index > 0 and is_top_level_block_preamble_line(lines[index - 1]):
        index -= 1
    return index


def is_top_level_block_preamble_line(line: str) -> bool:
    return not line.strip() or line.startswith("#")
