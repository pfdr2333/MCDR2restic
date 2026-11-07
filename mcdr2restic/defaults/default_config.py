# -*- coding: utf-8 -*-
"""默认配置入口。

具体键值声明已收敛到 ``mcdr2restic.config.schema``（单一事实源），
本模块只保留历史函数名作为兼容 API，供运行时与测试调用。
"""

from __future__ import annotations

from typing import Any, Dict, List, TypeAlias

from mcdr2restic.config.schema import (
    DEFAULT_LANGUAGE_CONFIG_VALUE,
    derive_base_default_config,
    section_defaults,
)
from mcdr2restic.config.schema.sections_restic import (
    BACKUP_COMMAND,
    ERROR_REGEXES,
    IGNORE_ERROR_REGEXES,
    MAINTENANCE_COMMANDS,
)
from mcdr2restic.core.i18n import normalize_language_name
from mcdr2restic.defaults.message_defaults import build_default_messages


ConfigDict: TypeAlias = Dict[str, Any]
StringList: TypeAlias = List[str]
CommandList: TypeAlias = List[StringList]


def build_default_config(language: str = "zh_cn") -> ConfigDict:
    """Build defaults and localize mutable message templates when needed."""

    cfg = build_base_default_config()
    cfg["language"] = default_language_config_value(language)
    cfg["messages"] = build_default_messages(language)
    return cfg


def build_base_default_config() -> ConfigDict:
    """Return the baseline default configuration shared by all languages."""

    return derive_base_default_config()


def default_language_config_value(language: str) -> str:
    return normalize_language_name(language) or DEFAULT_LANGUAGE_CONFIG_VALUE


# ---- 各段默认值的兼容包装：实现一律转回 schema，避免双份维护 ----


def build_default_command_config() -> ConfigDict:
    return section_defaults("command")


def build_default_schedule_config() -> ConfigDict:
    return section_defaults("schedule")


def build_default_force_schedule_config() -> ConfigDict:
    return section_defaults("force_schedule")


def build_default_maintenance_schedule_config() -> ConfigDict:
    return section_defaults("maintenance_schedule")


def build_default_update_check_config() -> ConfigDict:
    return section_defaults("update_check")


def build_default_minecraft_config() -> ConfigDict:
    return section_defaults("minecraft")


def build_default_restic_config() -> ConfigDict:
    return section_defaults("restic")


def build_default_onebot_config() -> ConfigDict:
    return section_defaults("onebot")


def build_default_discord_config() -> ConfigDict:
    return section_defaults("discord")


def build_default_custom_webhook_config() -> ConfigDict:
    return section_defaults("custom_webhook")


def build_default_notification_config() -> ConfigDict:
    return section_defaults("notification")


def build_default_snapshot_cache_config() -> ConfigDict:
    return section_defaults("snapshot_cache")


def build_default_restore_config() -> ConfigDict:
    return section_defaults("restore")


def build_default_maintenance_commands() -> CommandList:
    return [list(command) for command in MAINTENANCE_COMMANDS]


def build_default_backup_command() -> StringList:
    return list(BACKUP_COMMAND)


def build_default_error_regexes() -> StringList:
    return list(ERROR_REGEXES)


def build_default_ignore_error_regexes() -> StringList:
    return list(IGNORE_ERROR_REGEXES)
