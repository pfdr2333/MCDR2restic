# -*- coding: utf-8 -*-
"""快照缓存与恢复流程的配置段声明。"""

from __future__ import annotations

from mcdr2restic.config.schema.model import Field, Section
from mcdr2restic.defaults.default_constants import (
    SNAPSHOT_DB_NAME,
    SNAPSHOT_PAGE_SIZE,
    SNAPSHOT_QUERY_TIMEOUT_SECONDS,
)

SNAPSHOT_CACHE = Section(
    key="snapshot_cache",
    fields=(
        Field(
            "enabled",
            True,
            comment_key="template.comment.snapshot_cache.enabled",
        ),
        Field(
            "page_size",
            SNAPSHOT_PAGE_SIZE,
            comment_key="template.comment.snapshot_cache.page_size",
        ),
        Field(
            "query_timeout_seconds",
            SNAPSHOT_QUERY_TIMEOUT_SECONDS,
            comment_key="template.comment.snapshot_cache.query_timeout_seconds",
        ),
        Field(
            "database",
            SNAPSHOT_DB_NAME,
            comment_key="template.comment.snapshot_cache.database",
        ),
    ),
)

RESTORE = Section(
    key="restore",
    fields=(
        Field(
            "pre_restore_backup_tag",
            "mcdr2restic-pre-restore",
            comment_key="template.comment.restore.pre_restore_backup_tag",
        ),
        Field(
            "start_timeout_seconds",
            120,
            comment_key="template.comment.restore.start_timeout_seconds",
        ),
    ),
)
