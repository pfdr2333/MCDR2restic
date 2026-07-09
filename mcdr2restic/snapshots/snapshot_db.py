# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.config_paths import get_data_file_path
from mcdr2restic.defaults.default_constants import SNAPSHOT_DB_NAME
from mcdr2restic.core.utils import now_text, tail_text


SNAPSHOT_REFRESH_ERROR_TAIL_CHARS = 800


def get_snapshot_db_path(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
) -> str:
    database = str(snapshot_cfg.get('database', SNAPSHOT_DB_NAME) or SNAPSHOT_DB_NAME).strip()
    if not database:
        database = SNAPSHOT_DB_NAME
    if os.path.isabs(database):
        return database
    return get_data_file_path(server, database)


def open_snapshot_db(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
) -> sqlite3.Connection:
    path = get_snapshot_db_path(server, snapshot_cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_snapshot_db_schema(conn)
    return conn


def ensure_snapshot_db_schema(conn: sqlite3.Connection):
    ensure_snapshot_meta_table(conn)
    ensure_snapshots_table(conn)
    ensure_restore_tasks_table(conn)
    ensure_restore_tasks_columns(conn)
    ensure_snapshot_meta_columns(conn)
    conn.commit()


def ensure_snapshot_meta_table(conn: sqlite3.Connection):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS snapshot_meta (
            cache_key TEXT PRIMARY KEY,
            updated_at_epoch REAL NOT NULL DEFAULT 0,
            updated_at_text TEXT,
            snapshot_count INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            invalidated INTEGER NOT NULL DEFAULT 1,
            invalidation_reason TEXT NOT NULL DEFAULT '',
            last_refresh_duration REAL NOT NULL DEFAULT 0
        )
        '''
    )


def ensure_snapshots_table(conn: sqlite3.Connection):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS snapshots (
            cache_key TEXT NOT NULL,
            id TEXT NOT NULL,
            short_id TEXT NOT NULL,
            time_text TEXT NOT NULL,
            time_epoch REAL NOT NULL DEFAULT 0,
            hostname TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            tags_text TEXT NOT NULL DEFAULT '',
            paths_text TEXT NOT NULL DEFAULT '',
            program_version TEXT NOT NULL DEFAULT '',
            summary_json TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (cache_key, id)
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_snapshots_page ON snapshots (cache_key, time_epoch DESC, id DESC)'
    )


def ensure_restore_tasks_table(conn: sqlite3.Connection):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS restore_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT NOT NULL DEFAULT '',
            created_at_text TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            item_type TEXT NOT NULL,
            include_path TEXT NOT NULL
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_restore_tasks_cache ON restore_tasks (cache_key, id)'
    )


def ensure_restore_tasks_columns(conn: sqlite3.Connection):
    columns = table_columns(conn, 'restore_tasks')
    if 'cache_key' not in columns:
        conn.execute("ALTER TABLE restore_tasks ADD COLUMN cache_key TEXT NOT NULL DEFAULT ''")
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_restore_tasks_cache ON restore_tasks (cache_key, id)'
    )


def ensure_snapshot_meta_columns(conn: sqlite3.Connection):
    columns = table_columns(conn, 'snapshot_meta')
    additions = {
        'invalidated': 'ALTER TABLE snapshot_meta ADD COLUMN invalidated INTEGER NOT NULL DEFAULT 1',
        'invalidation_reason': "ALTER TABLE snapshot_meta ADD COLUMN invalidation_reason TEXT NOT NULL DEFAULT ''"
    }
    for column, statement in additions.items():
        if column not in columns:
            conn.execute(statement)


def table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    return {
        str(row[1])
        for row in conn.execute('PRAGMA table_info({})'.format(table_name)).fetchall()
    }


def read_snapshot_meta(conn: sqlite3.Connection, cache_key: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        '''
        SELECT
            updated_at_epoch, updated_at_text, snapshot_count, error,
            invalidated, invalidation_reason, last_refresh_duration
        FROM snapshot_meta
        WHERE cache_key = ?
        ''',
        (cache_key,)
    ).fetchone()


def commit_refreshed_snapshot_cache(
    conn: sqlite3.Connection,
    cache_key: str,
    temp_key: str,
    snapshot_count: int,
    duration_seconds: float,
):
    with conn:
        conn.execute('DELETE FROM snapshots WHERE cache_key = ?', (cache_key,))
        conn.execute('UPDATE snapshots SET cache_key = ? WHERE cache_key = ?', (cache_key, temp_key))
        conn.execute(
            '''
            INSERT OR REPLACE INTO snapshot_meta
            (
                cache_key, updated_at_epoch, updated_at_text, snapshot_count,
                error, invalidated, invalidation_reason, last_refresh_duration
            )
            VALUES (?, ?, ?, ?, '', 0, '', ?)
            ''',
            (cache_key, time.time(), now_text(), snapshot_count, duration_seconds)
        )


def record_snapshot_refresh_failure(
    conn: sqlite3.Connection,
    cache_key: str,
    temp_key: str,
    exc: Exception,
    duration_seconds: float,
) -> str:
    error = tail_text(str(exc), SNAPSHOT_REFRESH_ERROR_TAIL_CHARS)
    existing = read_snapshot_meta(conn, cache_key)
    with conn:
        conn.execute('DELETE FROM snapshots WHERE cache_key = ?', (temp_key,))
        conn.execute(
            '''
            INSERT OR REPLACE INTO snapshot_meta
            (
                cache_key, updated_at_epoch, updated_at_text, snapshot_count,
                error, invalidated, invalidation_reason, last_refresh_duration
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ''',
            build_snapshot_refresh_failure_row(cache_key, existing, error, duration_seconds)
        )
    return error


def build_snapshot_refresh_failure_row(
    cache_key: str,
    existing: Optional[sqlite3.Row],
    error: str,
    duration_seconds: float,
) -> Tuple[Any, ...]:
    return (
        cache_key,
        float(existing['updated_at_epoch'] or 0) if existing is not None else 0,
        existing['updated_at_text'] if existing is not None else None,
        int(existing['snapshot_count'] or 0) if existing is not None else 0,
        error,
        existing['invalidation_reason'] if existing is not None else 'refresh failed',
        duration_seconds
    )


def delete_snapshot_temp_rows(conn: sqlite3.Connection, temp_key: str):
    with conn:
        conn.execute('DELETE FROM snapshots WHERE cache_key = ?', (temp_key,))


def insert_snapshot_row(conn: sqlite3.Connection, cache_key: str, snapshot: Dict[str, Any]):
    snapshot_id = str(snapshot.get('id') or snapshot.get('short_id') or '').strip()
    if not snapshot_id:
        return

    paths = normalize_snapshot_list(snapshot.get('paths', []))
    tags = normalize_snapshot_list(snapshot.get('tags', []))
    time_text = str(snapshot.get('time') or '')
    conn.execute(
        '''
        INSERT OR REPLACE INTO snapshots
        (cache_key, id, short_id, time_text, time_epoch, hostname, username, tags_text, paths_text, program_version, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            cache_key,
            snapshot_id,
            snapshot_id[:8],
            time_text,
            parse_restic_time_epoch(time_text),
            str(snapshot.get('hostname') or ''),
            str(snapshot.get('username') or ''),
            ','.join(str(item) for item in tags if str(item).strip()),
            ', '.join(str(item) for item in paths if str(item).strip()),
            str(snapshot.get('program_version') or ''),
            json.dumps(snapshot.get('summary') or {}, ensure_ascii=False, sort_keys=True)
        )
    )


def normalize_snapshot_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return [value]


def parse_restic_time_epoch(value: str) -> float:
    text = str(value or '').strip()
    if not text:
        return 0.0
    text = text.replace('Z', '+00:00')
    text = re.sub(r'(\.\d{6})\d+', r'\1', text)
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def read_snapshot_page(
    server: PluginServerInterface,
    cache_key: str,
    page: int,
    page_size: int,
    snapshot_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    page = max(1, int(page))
    offset = (page - 1) * page_size
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        meta = read_snapshot_meta(conn, cache_key)
        return {
            'total': read_snapshot_count(conn, cache_key),
            'updated_at_text': meta['updated_at_text'] if meta is not None else None,
            'error': meta['error'] if meta is not None else '',
            'invalidated': bool(meta['invalidated']) if meta is not None else True,
            'invalidation_reason': meta['invalidation_reason'] if meta is not None else '',
            'rows': read_snapshot_rows(conn, cache_key, page_size, offset)
        }


def read_snapshot_count(conn: sqlite3.Connection, cache_key: str) -> int:
    row = conn.execute(
        'SELECT COUNT(*) FROM snapshots WHERE cache_key = ?',
        (cache_key,)
    ).fetchone()
    return int(row[0] or 0)


def read_snapshot_rows(
    conn: sqlite3.Connection,
    cache_key: str,
    page_size: int,
    offset: int,
):
    return conn.execute(
        '''
        SELECT id, short_id, time_text, hostname, username, tags_text, paths_text
        FROM snapshots
        WHERE cache_key = ?
        ORDER BY time_epoch DESC, id DESC
        LIMIT ? OFFSET ?
        ''',
        (cache_key, page_size, offset)
    ).fetchall()
