# -*- coding: utf-8 -*-
"""Centralized constant values shared across configuration and runtime layers."""

from __future__ import annotations

from typing import Final


PLUGIN_ID: Final[str] = "mcdr2restic"

CONFIG_NAME: Final[str] = "config.yml"

STATE_NAME: Final[str] = "state.yml"

SNAPSHOT_DB_NAME: Final[str] = "snapshots.sqlite3"

LEGACY_CONFIG_NAME: Final[str] = "config.json"

CONFIG_VERSION: Final[int] = 9

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
