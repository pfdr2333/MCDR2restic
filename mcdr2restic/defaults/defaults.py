# -*- coding: utf-8 -*-
from __future__ import annotations

# Compatibility facade: default values are split by data type, and mutable
# defaults are provided through build_* constructors.
from mcdr2restic.defaults.default_config import (
    DEFAULT_CONFIG,
    build_base_default_config,
    build_default_config,
    build_default_discord_config,
    build_default_minecraft_config,
    build_default_onebot_config,
    build_default_restic_config,
    build_default_snapshot_cache_config,
    build_default_update_check_config,
    default_config_for_language,
)
from mcdr2restic.defaults.default_config_templates import (
    DEFAULT_BACKUP_SOURCE_MARKER,
    DEFAULT_BACKUP_WORLD_PATHS,
    DEFAULT_CONFIG_TEMPLATE_EN,
    DEFAULT_CONFIG_TEMPLATE_ZH,
    adapt_default_config_template_for_platform,
    add_windows_session_lock_exclude,
    get_default_backup_source_paths,
    get_default_config_template,
    render_default_backup_sources,
    resolve_generation_relative_path,
)
from mcdr2restic.defaults.default_constants import (
    CONFIG_NAME,
    CONFIG_VERSION,
    DEFAULT_PROXY_PREFIXES,
    DEFAULT_UPDATE_API_URL,
    LEGACY_CONFIG_NAME,
    PLUGIN_ID,
    PLUGIN_REPOSITORY_URL,
    RESTIC_PROGRESS_INTERVAL_SECONDS,
    SNAPSHOT_DB_NAME,
    SNAPSHOT_PAGE_SIZE,
    SNAPSHOT_QUERY_TIMEOUT_SECONDS,
    STATE_NAME,
)
from mcdr2restic.core.language import get_mcdr_language, is_zh_language
from mcdr2restic.defaults.message_defaults import (
    DEFAULT_MESSAGES_EN,
    DEFAULT_MESSAGES_ZH,
    build_default_messages,
    get_default_message_template,
)
from mcdr2restic.defaults.restic_release_defaults import RESTIC_FALLBACK_RELEASE, build_restic_fallback_release
from mcdr2restic.defaults.runtime_defaults import DEFAULT_RUNTIME, build_default_runtime


__all__ = [
    'CONFIG_NAME',
    'CONFIG_VERSION',
    'DEFAULT_BACKUP_SOURCE_MARKER',
    'DEFAULT_BACKUP_WORLD_PATHS',
    'DEFAULT_CONFIG',
    'DEFAULT_CONFIG_TEMPLATE_EN',
    'DEFAULT_CONFIG_TEMPLATE_ZH',
    'DEFAULT_MESSAGES_EN',
    'DEFAULT_MESSAGES_ZH',
    'DEFAULT_PROXY_PREFIXES',
    'DEFAULT_RUNTIME',
    'DEFAULT_UPDATE_API_URL',
    'LEGACY_CONFIG_NAME',
    'PLUGIN_ID',
    'PLUGIN_REPOSITORY_URL',
    'RESTIC_FALLBACK_RELEASE',
    'RESTIC_PROGRESS_INTERVAL_SECONDS',
    'SNAPSHOT_DB_NAME',
    'SNAPSHOT_PAGE_SIZE',
    'SNAPSHOT_QUERY_TIMEOUT_SECONDS',
    'STATE_NAME',
    'adapt_default_config_template_for_platform',
    'add_windows_session_lock_exclude',
    'build_base_default_config',
    'build_default_config',
    'build_default_discord_config',
    'build_default_messages',
    'build_default_minecraft_config',
    'build_default_onebot_config',
    'build_default_restic_config',
    'build_default_runtime',
    'build_default_snapshot_cache_config',
    'build_default_update_check_config',
    'build_restic_fallback_release',
    'default_config_for_language',
    'get_default_backup_source_paths',
    'get_default_config_template',
    'get_default_message_template',
    'get_mcdr_language',
    'is_zh_language',
    'render_default_backup_sources',
    'resolve_generation_relative_path',
]
