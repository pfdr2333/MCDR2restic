# -*- coding: utf-8 -*-
"""核心调度与 Minecraft 交互相关的配置段声明。"""

from __future__ import annotations

from mcdr2restic.config.schema.model import Field, Section
from mcdr2restic.defaults.default_constants import (
    DEFAULT_MAINTENANCE_CRON,
    DEFAULT_PROXY_PREFIXES,
    DEFAULT_SAVE_ALL_DONE_PATTERNS,
    DEFAULT_UPDATE_API_URL,
    PLUGIN_REPOSITORY_URL,
)

COMMAND = Section(
    key="command",
    fields=(
        Field("root", "!!restic"),
        Field("aliases", ["!!m2r"]),
        Field("permission_level", 3),
    ),
)

SCHEDULE = Section(
    key="schedule",
    fields=(
        Field("interval_seconds", 0),
        Field("cron_expression", "0 0 0,3,6,9,12,15,18,21 * * *"),
        Field("require_player_activity_in_wait_period", True),
        Field("online_check_command", "list"),
    ),
)

FORCE_SCHEDULE = Section(
    key="force_schedule",
    fields=(
        Field("interval_seconds", 0),
        Field("cron_expression", "0"),
    ),
)

MAINTENANCE_SCHEDULE = Section(
    key="maintenance_schedule",
    fields=(
        Field("interval_seconds", 0),
        Field("cron_expression", DEFAULT_MAINTENANCE_CRON),
    ),
)

UPDATE_CHECK = Section(
    key="update_check",
    fields=(
        Field("enabled", True),
        Field("check_on_startup", True),
        Field("daily_time", "00:00"),
        Field("api_url", DEFAULT_UPDATE_API_URL),
        Field("release_page_url", PLUGIN_REPOSITORY_URL + "/releases/latest"),
        Field("proxy_prefixes", list(DEFAULT_PROXY_PREFIXES)),
        Field("timeout_seconds", 10),
    ),
)

MINECRAFT = Section(
    key="minecraft",
    fields=(
        Field("save_off_command", "save-off"),
        Field("save_all_command", "save-all flush"),
        Field("save_on_command", "save-on"),
        Field("wait_after_save_off_seconds", 2),
        Field("wait_after_save_all_seconds", 10),
        Field("wait_for_save_all_completion", True),
        Field("save_all_done_patterns", list(DEFAULT_SAVE_ALL_DONE_PATTERNS)),
        Field("wait_after_save_on_seconds", 1),
    ),
)
