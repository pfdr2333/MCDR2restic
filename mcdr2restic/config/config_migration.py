# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.config_paths import get_data_file_path
from mcdr2restic.config.config_template import (
    get_discord_block_lines,
    get_force_schedule_lines,
    get_restic_auto_download_lines,
    get_restic_auto_init_lines,
    get_restic_download_proxy_lines,
    get_restic_download_timeout_lines,
    get_restic_download_version_lines,
    get_restic_password_file_lines,
    get_restic_password_lines,
    get_restic_progress_interval_lines,
    get_restic_repository_lines,
    get_restic_timeout_lines,
    get_restore_block_lines,
    get_schedule_activity_lines,
    get_schedule_online_command_lines,
    get_snapshot_cache_block_lines,
    get_update_check_lines,
    yaml_scalar,
)
from mcdr2restic.defaults.default_constants import CONFIG_NAME, CONFIG_VERSION
from mcdr2restic.core.language import is_zh_language
from mcdr2restic.core.utils import safe_int


ResticLineBuilder = Callable[[str, Dict[str, Any]], List[str]]
TOP_LEVEL_KEY_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*\s*:')


def migrate_legacy_config(cfg: Dict[str, Any]):
    old_version = read_config_version(cfg)
    migrate_schedule_config(cfg)
    migrate_restic_config(cfg, old_version)
    cfg['config_version'] = CONFIG_VERSION


def read_config_version(cfg: Dict[str, Any]) -> int:
    try:
        return int(cfg.get('config_version', 0) or 0)
    except Exception:
        return 0


def migrate_schedule_config(cfg: Dict[str, Any]):
    schedule = cfg.get('schedule')
    if not isinstance(schedule, dict):
        return

    old_key = 'require_player_joined_in_wait_period'
    new_key = 'require_player_activity_in_wait_period'
    if new_key not in schedule and old_key in schedule:
        schedule[new_key] = bool(schedule.get(old_key, True))
    schedule.pop('online_check_interval_seconds', None)


def migrate_restic_config(cfg: Dict[str, Any], old_version: int):
    restic = cfg.get('restic')
    if not isinstance(restic, dict):
        return

    environment = restic.get('environment')
    if not isinstance(environment, dict):
        environment = {}
    migrate_restic_secret_keys(restic, environment)
    if old_version < 9 and safe_int(restic.get('timeout_seconds', 0), 0) == 3600:
        restic['timeout_seconds'] = 0


def migrate_restic_secret_keys(restic: Dict[str, Any], environment: Dict[str, Any]):
    if 'repository' not in restic and environment.get('RESTIC_REPOSITORY'):
        restic['repository'] = environment.get('RESTIC_REPOSITORY')
    if 'password' not in restic and environment.get('RESTIC_PASSWORD'):
        restic['password'] = environment.get('RESTIC_PASSWORD')
    if 'password_file' not in restic and environment.get('RESTIC_PASSWORD_FILE'):
        restic['password_file'] = environment.get('RESTIC_PASSWORD_FILE')
    if 'password' not in restic and restic.get('password_file'):
        restic['password'] = ''


def migrate_config_file(server: PluginServerInterface, language: str, cfg: Dict[str, Any]):
    path = get_data_file_path(server, CONFIG_NAME)
    if not os.path.exists(path):
        return
    try:
        migrate_config_file_or_raise(server, path, language, cfg)
    except Exception as exc:
        server.logger.warning('迁移配置文件 {} 失败: {}'.format(CONFIG_NAME, exc))


def migrate_config_file_or_raise(
    server: PluginServerInterface,
    path: str,
    language: str,
    cfg: Dict[str, Any],
):
    with open(path, 'r', encoding='utf8') as file:
        lines = file.readlines()
    original = ''.join(lines)
    updated = ''.join(apply_config_file_migrations(lines, language, cfg))
    if updated == original:
        return

    with open(path, 'w', encoding='utf8') as file:
        file.write(updated)
    server.logger.info('已迁移并补全配置文件 {}'.format(CONFIG_NAME))


def apply_config_file_migrations(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    lines = ensure_schedule_migration_lines(lines, language, cfg)
    lines = remove_deprecated_schedule_lines(lines)
    lines = ensure_restic_migration_lines(lines, language, cfg)
    lines = ensure_restic_timeout_value(lines, cfg)
    lines = ensure_force_schedule_block(lines, language, cfg)
    lines = ensure_update_check_block(lines, language, cfg)
    lines = ensure_discord_block(lines, language, cfg)
    lines = ensure_snapshot_cache_block(lines, language, cfg)
    lines = ensure_restore_block(lines, language, cfg)
    return ensure_config_version_tail(lines, language)


def ensure_schedule_migration_lines(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    schedule = cfg.get('schedule', {}) if isinstance(cfg.get('schedule'), dict) else {}
    insertions: List[str] = []
    if not has_nested_key(lines, 'schedule', 'require_player_activity_in_wait_period'):
        insertions.extend(get_schedule_activity_lines(language, schedule))
    if not has_nested_key(lines, 'schedule', 'online_check_command'):
        insertions.extend(get_schedule_online_command_lines(language, schedule))
    if not insertions:
        return lines
    return insert_into_top_level_block(lines, 'schedule', insertions)


def ensure_force_schedule_block(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    if has_top_level_key(lines, 'force_schedule'):
        return lines
    force_schedule = cfg.get('force_schedule', {}) if isinstance(cfg.get('force_schedule'), dict) else {}
    return insert_before_config_version_or_end(lines, get_force_schedule_lines(language, force_schedule))


def ensure_update_check_block(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    if has_top_level_key(lines, 'update_check'):
        return lines
    update_check = cfg.get('update_check', {}) if isinstance(cfg.get('update_check'), dict) else {}
    return insert_before_top_level_key(lines, 'minecraft', get_update_check_lines(language, update_check))


def ensure_restic_migration_lines(
    lines: List[str],
    language: str,
    cfg: Dict[str, Any],
) -> List[str]:
    restic = cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}
    insertions: List[str] = []
    for key, builder in restic_migration_builders():
        if not has_nested_key(lines, 'restic', key):
            insertions.extend(builder(language, restic))
    if not insertions:
        return lines
    return insert_into_top_level_block(lines, 'restic', insertions)


def restic_migration_builders() -> List[Tuple[str, ResticLineBuilder]]:
    return [
        ('repository', get_restic_repository_lines),
        ('password', get_restic_password_lines),
        ('password_file', get_restic_password_file_lines),
        ('auto_download', get_restic_auto_download_lines),
        ('download_version', get_restic_download_version_lines),
        ('download_proxy_prefixes', get_restic_download_proxy_lines),
        ('download_timeout_seconds', get_restic_download_timeout_lines),
        ('auto_init_local_repository', get_restic_auto_init_lines),
        ('timeout_seconds', get_restic_timeout_lines),
        ('progress_interval_seconds', get_restic_progress_interval_lines),
    ]


def ensure_restic_timeout_value(lines: List[str], cfg: Dict[str, Any]) -> List[str]:
    restic = cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}
    start, end = find_top_level_block(lines, 'restic')
    if start is None:
        return lines

    pattern = re.compile(r'^(\s+timeout_seconds\s*:\s*).*$')
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if match:
            lines[index] = rewrite_yaml_value_line(lines[index], match.group(1), restic.get('timeout_seconds', 0))
            break
    return lines


def rewrite_yaml_value_line(line: str, prefix: str, value: Any) -> str:
    newline = '\n' if line.endswith('\n') else ''
    return '{}{}{}'.format(prefix, yaml_scalar(value), newline)


def ensure_discord_block(lines: List[str], language: str, cfg: Dict[str, Any]) -> List[str]:
    if has_top_level_key(lines, 'discord'):
        return lines
    discord = cfg.get('discord', {}) if isinstance(cfg.get('discord'), dict) else {}
    return insert_before_top_level_key(lines, 'notification', get_discord_block_lines(language, discord))


def ensure_snapshot_cache_block(lines: List[str], language: str, cfg: Dict[str, Any]) -> List[str]:
    if has_top_level_key(lines, 'snapshot_cache'):
        return lines
    snapshot_cache = cfg.get('snapshot_cache', {}) if isinstance(cfg.get('snapshot_cache'), dict) else {}
    return insert_before_top_level_key(lines, 'messages', get_snapshot_cache_block_lines(language, snapshot_cache))


def ensure_restore_block(lines: List[str], language: str, cfg: Dict[str, Any]) -> List[str]:
    if has_top_level_key(lines, 'restore'):
        return lines
    restore_cfg = cfg.get('restore', {}) if isinstance(cfg.get('restore'), dict) else {}
    return insert_before_top_level_key(lines, 'messages', get_restore_block_lines(language, restore_cfg))


def ensure_config_version_tail(lines: List[str], language: str) -> List[str]:
    marker_comment = config_version_marker_comment(language)
    version_line = 'config_version: {}\n'.format(CONFIG_VERSION)
    lines = remove_config_version_lines(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and not lines[-1].endswith('\n'):
        lines[-1] = lines[-1] + '\n'
    if lines and lines[-1].strip():
        lines.append('\n')
    lines.append(marker_comment)
    lines.append(version_line)
    return lines


def config_version_marker_comment(language: str) -> str:
    if is_zh_language(language):
        return '# 配置文件版本标识。请保留在文件尾部，方便后续迁移。\n'
    return '# Config file version marker. Keep it at the end for future migrations.\n'


def remove_config_version_lines(lines: List[str]) -> List[str]:
    cleaned: List[str] = []
    for line in lines:
        if re.match(r'^config_version\s*:', line):
            if cleaned and is_config_version_comment(cleaned[-1]):
                cleaned.pop()
            continue
        cleaned.append(line)
    return cleaned


def is_config_version_comment(line: str) -> bool:
    stripped = line.strip().lower()
    return stripped.startswith('#') and (
        '配置文件版本标识' in stripped or
        'config file version marker' in stripped
    )


def remove_deprecated_schedule_lines(lines: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, 'schedule')
    if start is None:
        return lines

    remove_indexes = deprecated_schedule_line_indexes(lines, start, end)
    if not remove_indexes:
        return lines
    return [line for index, line in enumerate(lines) if index not in remove_indexes]


def deprecated_schedule_line_indexes(lines: List[str], start: int, end: int) -> Set[int]:
    deprecated_keys = {
        'require_player_joined_in_wait_period',
        'online_check_interval_seconds'
    }
    remove_indexes: Set[int] = set()
    key_pattern = re.compile(r'^\s+([A-Za-z_][A-Za-z0-9_-]*)\s*:')
    for index in range(start + 1, end):
        match = key_pattern.match(lines[index])
        if not match or match.group(1) not in deprecated_keys:
            continue
        remove_indexes.add(index)
        remove_indexes.update(deprecated_schedule_comment_indexes(lines, start, index))
    return remove_indexes


def deprecated_schedule_comment_indexes(lines: List[str], start: int, index: int) -> Set[int]:
    indexes: Set[int] = set()
    comment_index = index - 1
    while comment_index > start and is_deprecated_schedule_comment(lines[comment_index]):
        indexes.add(comment_index)
        comment_index -= 1
    return indexes


def is_deprecated_schedule_comment(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith('#'):
        return False
    lowered = stripped.lower()
    keywords = [
        'require_player_joined_in_wait_period',
        'online_check_interval_seconds',
        '没人进入',
        '等待周期内每隔',
        '采样',
        'nobody joined',
        'sampling interval',
        'waiting period'
    ]
    return any(keyword in lowered for keyword in keywords)


def has_top_level_key(lines: List[str], key: str) -> bool:
    pattern = re.compile(r'^{}\s*:'.format(re.escape(key)))
    return any(pattern.match(line) for line in lines)


def has_nested_key(lines: List[str], block_key: str, nested_key: str) -> bool:
    start, end = find_top_level_block(lines, block_key)
    if start is None:
        return False
    pattern = re.compile(r'^\s+{}\s*:'.format(re.escape(nested_key)))
    return any(pattern.match(line) for line in lines[start + 1:end])


def insert_into_top_level_block(lines: List[str], block_key: str, insertions: List[str]) -> List[str]:
    start, end = find_top_level_block(lines, block_key)
    if start is None:
        block = ['\n', '{}:\n'.format(block_key)] + insertions
        return insert_before_config_version_or_end(lines, block)
    return lines[:end] + insertions + lines[end:]


def insert_before_config_version_or_end(lines: List[str], insertions: List[str]) -> List[str]:
    version_index = find_config_version_index(lines)
    if version_index is None:
        return lines + insertions
    return lines[:version_index] + insertions + lines[version_index:]


def find_config_version_index(lines: List[str]) -> Optional[int]:
    for index, line in enumerate(lines):
        if re.match(r'^config_version\s*:', line):
            return index
    return None


def insert_before_top_level_key(lines: List[str], key: str, insertions: List[str]) -> List[str]:
    pattern = re.compile(r'^{}\s*:'.format(re.escape(key)))
    for index, line in enumerate(lines):
        if pattern.match(line):
            insertion_index = find_top_level_block_preamble_start(lines, index)
            return lines[:insertion_index] + insertions + lines[insertion_index:]
    return insert_before_config_version_or_end(lines, insertions)


def find_top_level_block(lines: List[str], block_key: str) -> Tuple[Optional[int], int]:
    start = find_top_level_block_start(lines, block_key)
    if start is None:
        return None, len(lines)
    return start, find_top_level_block_end(lines, start)


def find_top_level_block_start(lines: List[str], block_key: str) -> Optional[int]:
    pattern = re.compile(r'^{}\s*:'.format(re.escape(block_key)))
    for index, line in enumerate(lines):
        if pattern.match(line):
            return index
    return None


def find_top_level_block_end(lines: List[str], start: int) -> int:
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
    return not line.strip() or line.startswith('#')
