# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any, Dict, List

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import normalize_translate
from mcdr2restic.snapshots.snapshot_db import open_snapshot_db
from mcdr2restic.core.utils import now_text


def add_restore_task(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
    snapshot: str,
    item_type: str,
    include_path: str,
) -> int:
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        with conn:
            cursor = conn.execute(
                '''
                INSERT INTO restore_tasks (cache_key, created_at_text, snapshot, item_type, include_path)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (cache_key, now_text(), snapshot, item_type, include_path)
            )
        return int(cursor.lastrowid)


def list_restore_tasks(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
) -> List[sqlite3.Row]:
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        return conn.execute(
            '''
            SELECT id, cache_key, created_at_text, snapshot, item_type, include_path
            FROM restore_tasks
            WHERE cache_key = ?
            ORDER BY id ASC
            ''',
            (cache_key,)
        ).fetchall()


def delete_restore_task(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
    task_id: int,
) -> bool:
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        with conn:
            cursor = conn.execute(
                'DELETE FROM restore_tasks WHERE id = ? AND cache_key = ?',
                (int(task_id), cache_key)
            )
        return int(cursor.rowcount or 0) > 0


def clear_restore_tasks(server: PluginServerInterface, snapshot_cfg: Dict[str, Any], cache_key: str) -> int:
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        with conn:
            cursor = conn.execute('DELETE FROM restore_tasks WHERE cache_key = ?', (cache_key,))
        return int(cursor.rowcount or 0)


def format_restore_task(row: sqlite3.Row, translate_or_language: Any) -> str:
    item_type = str(row['item_type'])
    type_text = localized_restore_item_type(item_type, translate_or_language)
    return '  {}. [{}] {} -> {}'.format(row['id'], type_text, row['snapshot'], row['include_path'])


def localized_restore_item_type(item_type: str, translate_or_language: Any) -> str:
    translate = normalize_translate(translate_or_language)
    key_by_type = {
        'file': 'restore.item.file',
        'folder': 'restore.item.folder',
        'full': 'restore.item.full',
    }
    key = key_by_type.get(item_type)
    return translate(key) if key else item_type


def restore_tasks_output(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
    translate_or_language: Any,
    command_root: str,
) -> str:
    translate = normalize_translate(translate_or_language)
    tasks = list_restore_tasks(server, snapshot_cfg, cache_key)
    lines = [translate('title.restore.tasks')]
    if not tasks:
        lines.append('  {}'.format(translate('info.restore.no_tasks')))
        return '\n'.join(lines)
    lines.extend(format_restore_task(task, translate) for task in tasks)
    lines.append(translate('hint.restore.apply', command_root=command_root))
    return '\n'.join(lines)
