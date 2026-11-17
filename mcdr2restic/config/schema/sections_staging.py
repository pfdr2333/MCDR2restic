# -*- coding: utf-8 -*-
"""[beta] 中转库配置段声明。

默认关闭。关闭时插件完全走现有直传流程：不创建本地库、不启动复制线程、
不修改 cache 与备份命令。
"""

from __future__ import annotations

from mcdr2restic.config.schema.model import Field, Section


DEFAULT_STAGING_PASSWORD_FILE = "./config/mcdr2restic/staging-password.txt"
DEFAULT_STAGING_COPY_BACKOFF = (60, 300, 900)


STAGING = Section(
    key="staging",
    intro_comment_key="template.comment.staging",
    fields=(
        Field(
            "enabled",
            False,
            comment_key="template.comment.staging.enabled",
        ),
        Field(
            "repository",
            "",
            comment_key="template.comment.staging.repository",
        ),
        Field(
            "password",
            "",
            comment_key="template.comment.staging.password",
        ),
        Field(
            "password_file",
            DEFAULT_STAGING_PASSWORD_FILE,
            comment_key="template.comment.staging.password_file",
        ),
        Field(
            "min_free_percent_after",
            10,
            comment_key="template.comment.staging.min_free_percent_after",
        ),
        Field(
            "keep_last",
            2,
            comment_key="template.comment.staging.keep_last",
        ),
        Field(
            "prune_max_unused",
            "5%",
            comment_key="template.comment.staging.prune_max_unused",
        ),
        Field(
            "deep_prune_interval_days",
            7,
            comment_key="template.comment.staging.deep_prune_interval_days",
        ),
        Field(
            "deep_prune_max_unused",
            "0",
            comment_key="template.comment.staging.deep_prune_max_unused",
        ),
        Field(
            "prune_max_repack_size",
            "20G",
            comment_key="template.comment.staging.prune_max_repack_size",
        ),
        Field(
            "cache_dir",
            "",
            comment_key="template.comment.staging.cache_dir",
        ),
        Field(
            "copy_retry_max",
            3,
            comment_key="template.comment.staging.copy_retry_max",
        ),
        Field(
            "copy_retry_backoff_seconds",
            list(DEFAULT_STAGING_COPY_BACKOFF),
            comment_key="template.comment.staging.copy_retry_backoff_seconds",
        ),
        Field(
            "copy_limit_upload_kib",
            0,
            comment_key="template.comment.staging.copy_limit_upload_kib",
        ),
        Field(
            "remote_stale_warning_hours",
            24,
            comment_key="template.comment.staging.remote_stale_warning_hours",
        ),
        Field(
            "emergency_prune_timeout_seconds",
            120,
            comment_key="template.comment.staging.emergency_prune_timeout_seconds",
        ),
        Field(
            "remote_probe_timeout_seconds",
            30,
            comment_key="template.comment.staging.remote_probe_timeout_seconds",
        ),
    ),
)
