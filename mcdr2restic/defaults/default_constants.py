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

# 自建 restic 镜像（CF R2）：URL 契约为 {base}/v<版本>/<GitHub release 资产原文件名>
DEFAULT_RESTIC_MIRROR_BASE: Final[str] = "https://dl.m2r.pfdr.cc"

# 公共 GitHub 加速前缀死亡率高，列表会随时间腐烂：
# 2026-11 实测仅下列两个可用（gh.llkk.cc / hub.gitmirror.com 已失效），需定期复检更换
DEFAULT_PROXY_PREFIXES: Final[tuple[str, ...]] = (
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
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
