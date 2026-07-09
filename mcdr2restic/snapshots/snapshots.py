# -*- coding: utf-8 -*-
from __future__ import annotations

# Compatibility facade: external imports keep working while responsibilities live
# in focused modules named after their reason to change.
from mcdr2restic.restore.restore_task_repository import (
    add_restore_task,
    clear_restore_tasks,
    delete_restore_task,
    format_restore_task,
    list_restore_tasks,
    restore_tasks_output,
)
from mcdr2restic.snapshots.snapshot_cache import (
    build_snapshot_cache_key,
    ensure_snapshot_cache_fresh,
    get_snapshot_cache_config,
    get_snapshot_page_size,
    get_snapshot_query_timeout,
    invalidate_snapshot_cache,
    mark_snapshot_cache_invalid,
    refresh_snapshot_cache,
)
from mcdr2restic.snapshots.snapshot_db import (
    ensure_snapshot_db_schema,
    get_snapshot_db_path,
    open_snapshot_db,
    parse_restic_time_epoch,
    read_snapshot_meta,
    read_snapshot_page,
)
from mcdr2restic.snapshots.snapshot_importer import (
    import_restic_snapshots_to_sql,
    iter_json_array_stream,
)


__all__ = [
    "add_restore_task",
    "build_snapshot_cache_key",
    "clear_restore_tasks",
    "delete_restore_task",
    "ensure_snapshot_cache_fresh",
    "ensure_snapshot_db_schema",
    "format_restore_task",
    "get_snapshot_cache_config",
    "get_snapshot_db_path",
    "get_snapshot_page_size",
    "get_snapshot_query_timeout",
    "import_restic_snapshots_to_sql",
    "invalidate_snapshot_cache",
    "iter_json_array_stream",
    "list_restore_tasks",
    "mark_snapshot_cache_invalid",
    "open_snapshot_db",
    "parse_restic_time_epoch",
    "read_snapshot_meta",
    "read_snapshot_page",
    "refresh_snapshot_cache",
    "restore_tasks_output",
]
