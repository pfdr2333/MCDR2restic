# -*- coding: utf-8 -*-
"""Centralized constant values shared across configuration and runtime layers."""

from __future__ import annotations

from typing import Final


PLUGIN_ID: Final[str] = "mcdr2restic"

CONFIG_NAME: Final[str] = "config.yml"

STATE_NAME: Final[str] = "state.yml"

SNAPSHOT_DB_NAME: Final[str] = "snapshots.sqlite3"

LEGACY_CONFIG_NAME: Final[str] = "config.json"

CONFIG_VERSION: Final[int] = 12

DEFAULT_MAINTENANCE_CRON: Final[str] = "0 0 3 * * *"

# 定时备份距上次备份结束的最小间隔：避免长备份刚结束又立刻开下一份
DEFAULT_MIN_GAP_SECONDS: Final[int] = 1800

# 陈旧备份告警的说明文档页（页面级 URL，锚点会随标题增删漂移）
DOCUMENTATION_CONFIGURATION_URL: Final[str] = "https://m2r.pfdr.cc/configuration/"

# [beta] 中转库说明文档页
DOCUMENTATION_STAGING_URL: Final[str] = "https://m2r.pfdr.cc/staging/"

# [beta] 门控等待超时跳过备份后的方案提示冷却时间
STAGING_HINT_COOLDOWN_SECONDS: Final[int] = 7 * 24 * 60 * 60

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

# 备份前磁盘预检默认阈值：本地仓库所在盘剩余空间低于该百分比时拒绝备份
DEFAULT_DISK_CHECK_MIN_FREE_PERCENT: Final[float] = 10.0

DEFAULT_RESTIC_EXECUTABLE_POSIX: Final[str] = "./config/mcdr2restic/restic"

DEFAULT_RESTIC_EXECUTABLE_WINDOWS: Final[str] = ".\\config\\mcdr2restic\\restic.exe"
