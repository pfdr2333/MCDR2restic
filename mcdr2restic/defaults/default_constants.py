# -*- coding: utf-8 -*-
"""Centralized constant values shared across configuration and runtime layers."""

from __future__ import annotations

from typing import Final


PLUGIN_ID: Final[str] = "mcdr2restic"

CONFIG_NAME: Final[str] = "config.yml"

STATE_NAME: Final[str] = "state.yml"

SNAPSHOT_DB_NAME: Final[str] = "snapshots.sqlite3"

LEGACY_CONFIG_NAME: Final[str] = "config.json"

CONFIG_VERSION: Final[int] = 10

DEFAULT_MAINTENANCE_CRON: Final[str] = "0 0 3 * * *"

DEFAULT_MESSAGE_PREFIX: Final[str] = "[MCDR2Restic]"

PLUGIN_REPOSITORY_URL: Final[str] = "https://github.com/pfdr2333/MCDR2restic"

DEFAULT_UPDATE_API_URL: Final[str] = (
    "https://api.github.com/repos/pfdr2333/MCDR2restic/releases/latest"
)

DEFAULT_PROXY_PREFIXES: Final[tuple[str, ...]] = (
    "https://gh.llkk.cc/",
    "https://gh-proxy.com/",
    "https://hub.gitmirror.com/",
)

SNAPSHOT_PAGE_SIZE: Final[int] = 10

SNAPSHOT_QUERY_TIMEOUT_SECONDS: Final[int] = 30

RESTIC_PROGRESS_INTERVAL_SECONDS: Final[int] = 5

DEFAULT_SAVE_ALL_DONE_PATTERNS: Final[tuple[str, ...]] = (
    "Saved the game",
    "Saved the world",
)

DEFAULT_RESTIC_EXECUTABLE_POSIX: Final[str] = "./config/mcdr2restic/restic"

DEFAULT_RESTIC_EXECUTABLE_WINDOWS: Final[str] = ".\\config\\mcdr2restic\\restic.exe"
