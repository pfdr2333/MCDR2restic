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

# 模板中的手工排版值（流式序列与备份源占位标记），逐字输出；值本身以 default 为准
MAINTENANCE_COMMANDS_TEMPLATE_LINES = (
    "    - [",
    '        "forget",',
    '        "--keep-within", "1d",',
    '        "--keep-daily", "7",',
    '        "--prune"',
    "      ]",
)

# __MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__ 行由渲染管线展开为平台相关的默认备份源
BACKUP_COMMAND_TEMPLATE_LINES = (
    '    - "backup"',
    "    __MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__",
    '    - "--tag"',
    '    - "minecraft"',
    '    - "--host"',
    '    - "mcdr2Restic"',
)

RESTIC = Section(
    key="restic",
    fields=(
        Field(
            "executable",
            DEFAULT_RESTIC_EXECUTABLE_POSIX,
            comment_key="template.comment.restic.executable",
            value_marker="__MCDR2RESTIC_EXECUTABLE__",
        ),
        Field(
            "working_directory",
            "",
            comment_key="template.comment.restic.working_directory",
        ),
        Field(
            "repository",
            "./restic-repo",
            comment_key="template.comment.restic.repository",
            value_marker="__MCDR2RESTIC_REPOSITORY__",
        ),
        Field(
            "disk_check_enabled",
            True,
            comment_key="template.comment.restic.disk_check_enabled",
        ),
        Field(
            "disk_check_min_free_percent",
            DEFAULT_DISK_CHECK_MIN_FREE_PERCENT,
            comment_key="template.comment.restic.disk_check_min_free_percent",
        ),
        Field("password", "123456", comment_key="template.comment.restic.password"),
        Field(
            "password_file",
            "",
            comment_key="template.comment.restic.password_file",
        ),
        Field(
            "use_path_restic",
            False,
            comment_key="template.comment.restic.use_path_restic",
        ),
        Field(
            "auto_init_local_repository",
            True,
            comment_key="template.comment.restic.auto_init_local_repository",
        ),
        Field(
            "environment",
            {},
            comment_key="template.comment.restic.environment",
        ),
        Field(
            "maintenance_commands",
            [list(command) for command in MAINTENANCE_COMMANDS],
            comment_key="template.comment.restic.maintenance_commands",
            raw_value_lines=MAINTENANCE_COMMANDS_TEMPLATE_LINES,
        ),
        Field(
            "backup_command",
            list(BACKUP_COMMAND),
            comment_key="template.comment.restic.backup_command",
            comment_markers=("__MCDR2RESTIC_DEFAULT_BACKUP_COMMENT__",),
            raw_value_lines=BACKUP_COMMAND_TEMPLATE_LINES,
        ),
        Field(
            "timeout_seconds",
            0,
            comment_key="template.comment.restic.timeout_seconds",
        ),
        Field(
            "progress_interval_seconds",
            RESTIC_PROGRESS_INTERVAL_SECONDS,
            comment_key="template.comment.restic.progress_interval_seconds",
        ),
        Field(
            "success_exit_codes",
            [0],
            comment_key="template.comment.restic.success_exit_codes",
        ),
        Field(
            "error_regexes",
            list(ERROR_REGEXES),
            comment_key="template.comment.restic.error_regexes",
            quote_single_items=True,
        ),
        Field(
            "ignore_error_regexes",
            list(IGNORE_ERROR_REGEXES),
            quote_single_items=True,
        ),
        Field("max_output_chars_in_notification", 1800),
    ),
)
