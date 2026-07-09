# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any, Dict, List

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.language import is_zh_language
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


def format_restore_task(row: sqlite3.Row, language: str) -> str:
    item_type = str(row['item_type'])
    type_text = localized_restore_item_type(item_type, language)
    return '  {}. [{}] {} -> {}'.format(row['id'], type_text, row['snapshot'], row['include_path'])


def localized_restore_item_type(item_type: str, language: str) -> str:
    if is_zh_language(language):
        return {'file': '文件', 'folder': '文件夹', 'full': '整份快照'}.get(item_type, item_type)
    return {'file': 'file', 'folder': 'folder', 'full': 'full snapshot'}.get(item_type, item_type)


def restore_tasks_output(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
    language: str,
    command_root: str,
) -> str:
    tasks = list_restore_tasks(server, snapshot_cfg, cache_key)
    if is_zh_language(language):
        return restore_tasks_output_zh(tasks, language, command_root)
    return restore_tasks_output_en(tasks, language, command_root)


def restore_tasks_output_zh(
    tasks: List[sqlite3.Row],
    language: str,
    command_root: str,
) -> str:
    lines = ['MCDR2Restic 恢复任务列表']
    if not tasks:
        lines.append('  暂无任务')
        return '\n'.join(lines)
    lines.extend(format_restore_task(task, language) for task in tasks)
    lines.append('提示: {} restore apply 可以执行任务'.format(command_root))
    return '\n'.join(lines)


def restore_tasks_output_en(
    tasks: List[sqlite3.Row],
    language: str,
    command_root: str,
) -> str:
    lines = ['MCDR2Restic Restore Tasks']
    if not tasks:
        lines.append('  No tasks')
        return '\n'.join(lines)
    lines.extend(format_restore_task(task, language) for task in tasks)
    lines.append('Hint: {} restore apply can execute the queued tasks'.format(command_root))
    return '\n'.join(lines)
