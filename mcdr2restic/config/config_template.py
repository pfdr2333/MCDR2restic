# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.default_config_templates import get_default_config_template


TOP_LEVEL_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:")
NESTED_KEY_TEMPLATE = r"^(\s+){}\s*:"
DEFAULT_NESTED_INDENT = "  "


def get_schedule_activity_lines(language: str, schedule: Dict[str, Any]) -> List[str]:
    return get_nested_scalar_lines(
        language,
        "schedule",
        "require_player_activity_in_wait_period",
        section_value(
            language, "schedule", schedule, "require_player_activity_in_wait_period"
        ),
    )


def get_schedule_online_command_lines(
    language: str, schedule: Dict[str, Any]
) -> List[str]:
    return get_nested_scalar_lines(
        language,
        "schedule",
        "online_check_command",
        section_value(language, "schedule", schedule, "online_check_command"),
    )


def get_force_schedule_lines(
    language: str, force_schedule: Dict[str, Any]
) -> List[str]:
    values = section_values(language, "force_schedule", force_schedule)
    lines = get_top_level_block_lines(language, "force_schedule")
    lines = rewrite_nested_scalar(
        lines, "interval_seconds", values.get("interval_seconds")
    )
    return rewrite_nested_scalar(
        lines, "cron_expression", values.get("cron_expression")
    )


def get_update_check_lines(language: str, update_check: Dict[str, Any]) -> List[str]:
    values = section_values(language, "update_check", update_check)
    lines = get_top_level_block_lines(language, "update_check")
    for key in [
        "enabled",
        "check_on_startup",
        "daily_time",
        "api_url",
        "release_page_url",
        "timeout_seconds",
    ]:
        lines = rewrite_nested_scalar(lines, key, values.get(key))
    return rewrite_nested_sequence(
        lines, "proxy_prefixes", values.get("proxy_prefixes")
    )


def get_restic_repository_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    return get_restic_scalar_lines(language, restic, "repository")


def get_restic_password_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    return get_restic_scalar_lines(language, restic, "password")


def get_restic_password_file_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    return get_restic_scalar_lines(language, restic, "password_file")


def get_restic_auto_download_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    return get_restic_scalar_lines(language, restic, "auto_download")


def get_restic_download_version_lines(
    language: str, restic: Dict[str, Any]
) -> List[str]:
    return get_restic_scalar_lines(language, restic, "download_version")


def get_restic_download_proxy_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    values = section_values(language, "restic", restic)
    lines = get_nested_entry_lines(language, "restic", "download_proxy_prefixes")
    return rewrite_nested_sequence(
        lines, "download_proxy_prefixes", values.get("download_proxy_prefixes")
    )


def get_restic_download_timeout_lines(
    language: str, restic: Dict[str, Any]
) -> List[str]:
    return get_restic_scalar_lines(language, restic, "download_timeout_seconds")


def get_restic_auto_init_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    return get_restic_scalar_lines(language, restic, "auto_init_local_repository")


def get_restic_timeout_lines(language: str, restic: Dict[str, Any]) -> List[str]:
    return get_restic_scalar_lines(language, restic, "timeout_seconds")


def get_restic_progress_interval_lines(
    language: str, restic: Dict[str, Any]
) -> List[str]:
    return get_restic_scalar_lines(language, restic, "progress_interval_seconds")


def get_discord_block_lines(language: str, discord: Dict[str, Any]) -> List[str]:
    values = section_values(language, "discord", discord)
    lines = get_top_level_block_lines(language, "discord")
    for key in [
        "enabled",
        "webhook_url",
        "username",
        "avatar_url",
        "message_prefix",
        "mention_everyone",
        "send_timeout_seconds",
    ]:
        lines = rewrite_nested_scalar(lines, key, values.get(key))
    lines = rewrite_nested_sequence(
        lines, "mention_user_ids", values.get("mention_user_ids")
    )
    return rewrite_nested_sequence(
        lines, "mention_role_ids", values.get("mention_role_ids")
    )


def get_snapshot_cache_block_lines(
    language: str, snapshot_cache: Dict[str, Any]
) -> List[str]:
    values = section_values(language, "snapshot_cache", snapshot_cache)
    lines = get_top_level_block_lines(language, "snapshot_cache")
    for key in ["enabled", "page_size", "query_timeout_seconds", "database"]:
        lines = rewrite_nested_scalar(lines, key, values.get(key))
    return lines


def get_restore_block_lines(language: str, restore_cfg: Dict[str, Any]) -> List[str]:
    values = section_values(language, "restore", restore_cfg)
    lines = get_top_level_block_lines(language, "restore")
    for key in [
        "pre_restore_backup_tag",
        "stop_timeout_seconds",
        "start_timeout_seconds",
    ]:
        lines = rewrite_nested_scalar(lines, key, values.get(key))
    return lines


def get_restic_scalar_lines(
    language: str, restic: Dict[str, Any], key: str
) -> List[str]:
    return get_nested_scalar_lines(
        language, "restic", key, section_value(language, "restic", restic, key)
    )


def get_nested_scalar_lines(
    language: str, block_key: str, nested_key: str, value: Any
) -> List[str]:
    lines = get_nested_entry_lines(language, block_key, nested_key)
    return rewrite_nested_scalar(lines, nested_key, value)


def section_values(
    language: str, section_key: str, overrides: Dict[str, Any]
) -> Dict[str, Any]:
    defaults = build_default_config(language).get(section_key, {})
    if not isinstance(defaults, dict):
        defaults = {}
    values = dict(defaults)
    if isinstance(overrides, dict):
        values.update(overrides)
    return values


def section_value(
    language: str, section_key: str, overrides: Dict[str, Any], value_key: str
) -> Any:
    return section_values(language, section_key, overrides).get(value_key)


def get_top_level_block_lines(language: str, block_key: str) -> List[str]:
    lines = default_template_lines(language)
    start = find_top_level_key_index(lines, block_key)
    if start is None:
        return []
    end = find_top_level_block_end(lines, start)
    if start > 0 and not lines[start - 1].strip():
        start -= 1
    return lines[start:end]


def get_nested_entry_lines(language: str, block_key: str, nested_key: str) -> List[str]:
    block = get_top_level_block_lines(language, block_key)
    key_index = find_nested_key_index(block, nested_key)
    if key_index is None:
        return []
    start = find_comment_run_start(block, key_index)
    end = find_nested_entry_end(block, key_index)
    return block[start:end]


def default_template_lines(language: str) -> List[str]:
    return get_default_config_template(language).splitlines(keepends=True)


def find_top_level_key_index(lines: List[str], key: str) -> Optional[int]:
    pattern = re.compile(r"^{}\s*:".format(re.escape(key)))
    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


def find_top_level_block_end(lines: List[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if TOP_LEVEL_KEY_PATTERN.match(lines[index]):
            return index
    return len(lines)


def find_nested_key_index(lines: List[str], key: str) -> Optional[int]:
    pattern = re.compile(NESTED_KEY_TEMPLATE.format(re.escape(key)))
    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


def find_comment_run_start(lines: List[str], key_index: int) -> int:
    start = key_index
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start


def find_nested_entry_end(lines: List[str], key_index: int) -> int:
    indent = nested_key_indent(lines[key_index])
    sibling = re.compile(r"^{}\S[^:]*:".format(re.escape(indent)))
    for index in range(key_index + 1, len(lines)):
        if TOP_LEVEL_KEY_PATTERN.match(lines[index]) or sibling.match(lines[index]):
            return index
    return len(lines)


def nested_key_indent(line: str) -> str:
    match = re.match(r"^(\s*)", line)
    return match.group(1) if match else DEFAULT_NESTED_INDENT


def rewrite_nested_scalar(lines: List[str], key: str, value: Any) -> List[str]:
    index = find_nested_key_index(lines, key)
    if index is None:
        return lines
    rewritten = list(lines)
    rewritten[index] = rewrite_value_line(rewritten[index], key, value)
    return rewritten


def rewrite_value_line(line: str, key: str, value: Any) -> str:
    pattern = re.compile(r"^(\s*{}\s*:\s*).*$".format(re.escape(key)))
    match = pattern.match(line)
    if not match:
        return line
    return "{}{}\n".format(match.group(1), yaml_scalar(value))


def rewrite_nested_sequence(lines: List[str], key: str, values: Any) -> List[str]:
    index = find_nested_key_index(lines, key)
    if index is None:
        return lines
    indent = nested_key_indent(lines[index])
    end = find_nested_entry_end(lines, index)
    replacement = sequence_yaml_lines(indent, key, values)
    return lines[:index] + replacement + lines[end:]


def sequence_yaml_lines(indent: str, key: str, values: Any) -> List[str]:
    if not isinstance(values, list) or not values:
        return ["{}{}: []\n".format(indent, key)]
    lines = ["{}{}:\n".format(indent, key)]
    lines.extend("{}  - {}\n".format(indent, yaml_scalar(item)) for item in values)
    return lines


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    return json.dumps(str(value), ensure_ascii=False)
