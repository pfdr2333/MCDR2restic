# -*- coding: utf-8 -*-
"""restic 执行相关的配置段声明。"""

from __future__ import annotations

from typing import List

from mcdr2restic.config.schema.model import Field, Section
from mcdr2restic.defaults.default_constants import (
    DEFAULT_DISK_CHECK_MIN_FREE_PERCENT,
    DEFAULT_RESTIC_EXECUTABLE_POSIX,
    RESTIC_PROGRESS_INTERVAL_SECONDS,
)

# 与三份配置模板保持一致：缺 --keep-within 时旧快照永不过期，--prune 不释放空间
MAINTENANCE_COMMANDS: List[List[str]] = [
    ["forget", "--keep-within", "1d", "--keep-daily", "7", "--prune"],
]

BACKUP_COMMAND: List[str] = [
    "backup",
    "./server/world",
    "--tag",
    "minecraft",
    "--host",
    "mcdr2Restic",
]

# 升级可疑输出为失败的输出规则
ERROR_REGEXES: List[str] = [
    "(?i)^fatal:",
    "(?i)^error(?:s)?\\b(?!:\\s*0\\b)",
    "(?i)\\b(permission denied|input/output error|read error|unreadable|failed to|unable to)\\b",
    "(?i)\\bno such file or directory\\b",
]

# 抑制已知无害的类错误输出
IGNORE_ERROR_REGEXES: List[str] = [
    "(?i)errors?:\\s*0\\b",
    "(?i)no errors? (?:were )?found",
]

RESTIC = Section(
    key="restic",
    fields=(
        Field("executable", DEFAULT_RESTIC_EXECUTABLE_POSIX),
        Field("working_directory", ""),
        Field("repository", "./restic-repo"),
        Field("disk_check_enabled", True),
        Field("disk_check_min_free_percent", DEFAULT_DISK_CHECK_MIN_FREE_PERCENT),
        Field("password", "123456"),
        Field("password_file", ""),
        Field("use_path_restic", False),
        Field("auto_init_local_repository", True),
        Field("environment", {}),
        Field("maintenance_commands", [list(cmd) for cmd in MAINTENANCE_COMMANDS]),
        Field("backup_command", list(BACKUP_COMMAND)),
        Field("timeout_seconds", 0),
        Field("progress_interval_seconds", RESTIC_PROGRESS_INTERVAL_SECONDS),
        Field("success_exit_codes", [0]),
        Field("error_regexes", list(ERROR_REGEXES)),
        Field("ignore_error_regexes", list(IGNORE_ERROR_REGEXES)),
        Field("max_output_chars_in_notification", 1800),
    ),
)
