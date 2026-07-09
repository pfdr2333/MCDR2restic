# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
from typing import Any, Dict

from mcdr2restic.defaults.default_constants import (
    CONFIG_VERSION,
    DEFAULT_PROXY_PREFIXES,
    DEFAULT_UPDATE_API_URL,
    PLUGIN_REPOSITORY_URL,
    RESTIC_PROGRESS_INTERVAL_SECONDS,
    SNAPSHOT_DB_NAME,
    SNAPSHOT_PAGE_SIZE,
    SNAPSHOT_QUERY_TIMEOUT_SECONDS,
)
from mcdr2restic.defaults.default_freeze import freeze_default
from mcdr2restic.core.language import is_zh_language
from mcdr2restic.defaults.message_defaults import build_default_messages


def build_default_config(language: str = 'zh_cn') -> Dict[str, Any]:
    cfg = build_base_default_config()
    if not is_zh_language(language):
        cfg['messages'] = build_default_messages(language)
    return cfg


def build_base_default_config() -> Dict[str, Any]:
    return {
        'enabled': True,
        'command': build_default_command_config(),
        'schedule': build_default_schedule_config(),
        'force_schedule': build_default_force_schedule_config(),
        'update_check': build_default_update_check_config(),
        'minecraft': build_default_minecraft_config(),
        'restic': build_default_restic_config(),
        'onebot': build_default_onebot_config(),
        'discord': build_default_discord_config(),
        'notification': build_default_notification_config(),
        'snapshot_cache': build_default_snapshot_cache_config(),
        'restore': build_default_restore_config(),
        'messages': build_default_messages('zh_cn'),
        'config_version': CONFIG_VERSION,
    }


def build_default_command_config() -> Dict[str, Any]:
    return {
        'root': '!!restic',
        'aliases': ['!!m2r'],
        'permission_level': 3,
    }


def build_default_schedule_config() -> Dict[str, Any]:
    return {
        'interval_seconds': 0,
        'cron_expression': '0 0 0,3,6,9,12,15,18,21 * * *',
        'require_player_activity_in_wait_period': True,
        'online_check_command': 'list',
    }


def build_default_force_schedule_config() -> Dict[str, Any]:
    return {
        'interval_seconds': 0,
        'cron_expression': '0',
    }


def build_default_update_check_config() -> Dict[str, Any]:
    return {
        'enabled': True,
        'check_on_startup': True,
        'daily_time': '00:00',
        'api_url': DEFAULT_UPDATE_API_URL,
        'release_page_url': PLUGIN_REPOSITORY_URL + '/releases/latest',
        'proxy_prefixes': list(DEFAULT_PROXY_PREFIXES),
        'timeout_seconds': 10,
    }


def build_default_minecraft_config() -> Dict[str, Any]:
    return {
        'save_off_command': 'save-off',
        'save_all_command': 'save-all flush',
        'save_on_command': 'save-on',
        'wait_after_save_off_seconds': 2,
        'wait_after_save_all_seconds': 10,
        'wait_after_save_on_seconds': 1,
    }


def build_default_restic_config() -> Dict[str, Any]:
    return {
        'executable': './restic',
        'working_directory': '',
        'repository': './restic-repo',
        'password': '123456',
        'password_file': '',
        'auto_download': True,
        'download_version': 'latest',
        'download_proxy_prefixes': list(DEFAULT_PROXY_PREFIXES),
        'download_timeout_seconds': 120,
        'auto_init_local_repository': True,
        'environment': {},
        'maintenance_commands': build_default_maintenance_commands(),
        'backup_command': build_default_backup_command(),
        'timeout_seconds': 0,
        'progress_interval_seconds': RESTIC_PROGRESS_INTERVAL_SECONDS,
        'success_exit_codes': [0],
        'error_regexes': build_default_error_regexes(),
        'ignore_error_regexes': build_default_ignore_error_regexes(),
        'max_output_chars_in_notification': 1800,
    }


def build_default_maintenance_commands() -> list:
    return [
        ['forget', '--keep-daily', '7', '--prune'],
    ]


def build_default_backup_command() -> list:
    return [
        'backup',
        './server/world',
        '--tag',
        'minecraft',
        '--host',
        'mcdr2Restic',
    ]


def build_default_error_regexes() -> list:
    return [
        '(?i)^fatal:',
        '(?i)^error(?:s)?\\b(?!:\\s*0\\b)',
        '(?i)\\b(permission denied|input/output error|read error|unreadable|failed to|unable to)\\b',
        '(?i)\\bno such file or directory\\b',
    ]


def build_default_ignore_error_regexes() -> list:
    return [
        '(?i)errors?:\\s*0\\b',
        '(?i)no errors? (?:were )?found',
    ]


def build_default_onebot_config() -> Dict[str, Any]:
    return {
        'enabled': False,
        'ws_url': 'ws://127.0.0.1:8777',
        'access_token': '',
        'use_header_auth': False,
        'admin_qqs': [123456789],
        'message_prefix': '[MCDR2Restic]',
        'connect_timeout_seconds': 10,
        'send_timeout_seconds': 10,
        'reconnect_interval_seconds': 5,
    }


def build_default_discord_config() -> Dict[str, Any]:
    return {
        'enabled': False,
        'webhook_url': '',
        'username': 'MCDR2Restic',
        'avatar_url': '',
        'message_prefix': '[MCDR2Restic]',
        'mention_user_ids': [],
        'mention_role_ids': [],
        'mention_everyone': False,
        'send_timeout_seconds': 10,
    }


def build_default_snapshot_cache_config() -> Dict[str, Any]:
    return {
        'enabled': True,
        'page_size': SNAPSHOT_PAGE_SIZE,
        'query_timeout_seconds': SNAPSHOT_QUERY_TIMEOUT_SECONDS,
        'database': SNAPSHOT_DB_NAME,
    }


def build_default_notification_config() -> Dict[str, Any]:
    return {
        'notify_on_start': True,
        'notify_on_success': True,
        'notify_on_failure': True,
        'notify_on_skip': False,
    }


def build_default_restore_config() -> Dict[str, Any]:
    return {
        'pre_restore_backup_tag': 'mcdr2restic-pre-restore',
        'stop_timeout_seconds': 120,
        'start_timeout_seconds': 120,
    }


def default_config_for_language(language: str) -> Dict[str, Any]:
    return build_default_config(language)


DEFAULT_CONFIG = freeze_default(build_base_default_config())
