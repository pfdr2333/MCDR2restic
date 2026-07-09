# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.snapshots.snapshot_cache import (
    build_snapshot_cache_key,
    ensure_snapshot_cache_fresh,
    get_snapshot_cache_config,
    get_snapshot_page_size,
)
from mcdr2restic.snapshots.snapshot_db import (
    read_snapshot_page,
)
from mcdr2restic.core.utils import non_negative_int


def render_status_output(
    snapshot_query_lock: threading.Lock,
    cfg: Dict[str, Any],
    language: str,
    server: Optional[PluginServerInterface],
    snapshot_page: int,
    backup_running_provider: Callable[[], bool],
    restore_running_provider: Callable[[], bool],
    mc_ready_provider: Callable[[Optional[PluginServerInterface]], bool],
) -> str:
    status_view = build_status_view(cfg, language, server, backup_running_provider, restore_running_provider, mc_ready_provider)
    snapshot_lines = render_snapshot_status_lines(snapshot_query_lock, cfg, language, server, snapshot_page)
    return '\n'.join(format_status_view(status_view, language) + snapshot_lines)

def build_status_view(
    cfg: Dict[str, Any],
    language: str,
    server: Optional[PluginServerInterface],
    backup_running_provider: Callable[[], bool],
    restore_running_provider: Callable[[], bool],
    mc_ready_provider: Callable[[Optional[PluginServerInterface]], bool],
) -> Dict[str, Any]:
    running = backup_running_provider()
    activity = build_player_activity_view(cfg.get('runtime', {}), language)
    schedules = build_schedule_status_view(cfg, language)
    last_backup_status_raw = activity.pop('last_backup_status_raw')
    return {
        'enabled': localized_bool(bool(cfg.get('enabled', True)), language),
        'backup_running': localized_bool(running, language),
        'restore_running': localized_bool(restore_running_provider(), language),
        'mc_ready': localized_bool(mc_ready_provider(server), language),
        **activity,
        **schedules,
        'last_backup_status': localized_backup_status(last_backup_status_raw, language)
    }


def build_player_activity_view(runtime_state: Dict[str, Any], language: str) -> Dict[str, Any]:
    joined = bool(runtime_state.get('player_joined_since_last_check', False))
    joined = joined or bool(runtime_state.get('player_joined_since_last_backup', False))
    return {
        'current_online': non_negative_int(runtime_state.get('current_online_players', 0)),
        'joined': localized_bool(joined, language),
        'left': localized_bool(bool(runtime_state.get('player_left_since_last_check', False)), language),
        'last_online_check': runtime_state.get('last_online_check') or localized_never(language),
        'last_online_source': localized_online_source(runtime_state.get('last_online_check_source'), language),
        'last_backup_status_raw': runtime_state.get('last_backup_status', 'never')
    }


def build_schedule_status_view(cfg: Dict[str, Any], language: str) -> Dict[str, str]:
    return {
        'normal_next_text': schedule_status_text(cfg, False, language),
        'force_next_text': schedule_status_text(cfg, True, language)
    }


def format_status_view(status_view: Dict[str, Any], language: str) -> List[str]:
    if is_zh_language(language):
        return format_status_view_zh(status_view)
    return format_status_view_en(status_view)

def format_status_view_zh(status_view: Dict[str, Any]) -> List[str]:
    return [
        'MCDR2Restic 状态',
        '启用: {}'.format(status_view['enabled']),
        '备份中: {}'.format(status_view['backup_running']),
        '恢复中: {}'.format(status_view['restore_running']),
        'MC 就绪: {}'.format(status_view['mc_ready']),
        '玩家活动:',
        '  当前在线: {}'.format(status_view['current_online']),
        '  本周期有人加入: {}'.format(status_view['joined']),
        '  本周期有人退出: {}'.format(status_view['left']),
        '  最近在线检查: {}'.format(status_view['last_online_check']),
        '  检查来源: {}'.format(status_view['last_online_source']),
        '调度:',
        '  正常备份: {}'.format(status_view['normal_next_text']),
        '  强制备份: {}'.format(status_view['force_next_text']),
        '最近备份状态: {}'.format(status_view['last_backup_status'])
    ]

def format_status_view_en(status_view: Dict[str, Any]) -> List[str]:
    return [
        'MCDR2Restic Status',
        'Enabled: {}'.format(status_view['enabled']),
        'Backup running: {}'.format(status_view['backup_running']),
        'Restore running: {}'.format(status_view['restore_running']),
        'Minecraft ready: {}'.format(status_view['mc_ready']),
        'Player activity:',
        '  Current online: {}'.format(status_view['current_online']),
        '  Joined this period: {}'.format(status_view['joined']),
        '  Left this period: {}'.format(status_view['left']),
        '  Last online check: {}'.format(status_view['last_online_check']),
        '  Check source: {}'.format(status_view['last_online_source']),
        'Schedules:',
        '  Normal backup: {}'.format(status_view['normal_next_text']),
        '  Forced backup: {}'.format(status_view['force_next_text']),
        'Last backup status: {}'.format(status_view['last_backup_status'])
    ]

def render_snapshot_status_lines(
    snapshot_query_lock: threading.Lock,
    cfg: Dict[str, Any],
    language: str,
    server: Optional[PluginServerInterface],
    page: int
) -> List[str]:
    page = max(1, int(page))
    title = 'Restic 快照' if is_zh_language(language) else 'Restic Snapshots'
    if server is None:
        return render_snapshot_status_message(title, language, '无法访问 MCDR 数据目录，跳过快照列表', 'Cannot access the MCDR data folder, snapshots skipped')

    restic_cfg = cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}
    snapshot_cfg = get_snapshot_cache_config(cfg)
    if not bool(snapshot_cfg.get('enabled', True)):
        return render_snapshot_status_message(title, language, '快照列表缓存已在配置中关闭', 'Snapshot list cache is disabled in config')

    try:
        page_context = load_snapshot_status_page(snapshot_query_lock, server, restic_cfg, snapshot_cfg, language, page)
    except Exception as exc:
        return render_snapshot_status_message(title, language, '查询失败: {}'.format(exc), 'Query failed: {}'.format(exc))

    return format_snapshot_status_page(cfg, language, title, page_context)

def render_snapshot_status_message(title: str, language: str, zh_message: str, en_message: str) -> List[str]:
    return ['', '{}:'.format(title), '  {}'.format(zh_message if is_zh_language(language) else en_message)]

def load_snapshot_status_page(
    snapshot_query_lock: threading.Lock,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    snapshot_cfg: Dict[str, Any],
    language: str,
    requested_page: int
) -> Dict[str, Any]:
    page_size = get_snapshot_page_size(snapshot_cfg)
    cache_key = build_snapshot_cache_key(restic_cfg)
    refresh_note = ensure_snapshot_cache_fresh(snapshot_query_lock, server, restic_cfg, cache_key, snapshot_cfg, language)
    page_data = read_snapshot_page(server, cache_key, requested_page, page_size, snapshot_cfg)
    total = int(page_data.get('total', 0))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(requested_page, total_pages)
    if page != requested_page:
        page_data = read_snapshot_page(server, cache_key, page, page_size, snapshot_cfg)
    return {
        'page': page,
        'page_size': page_size,
        'total': total,
        'total_pages': total_pages,
        'refresh_note': refresh_note,
        'page_data': page_data
    }

def format_snapshot_status_page(
    cfg: Dict[str, Any],
    language: str,
    title: str,
    page_context: Dict[str, Any]
) -> List[str]:
    page = int(page_context['page'])
    page_data = page_context['page_data']
    lines = ['', '{}:'.format(title)]
    append_snapshot_cache_summary(lines, language, page_context)
    append_snapshot_cache_notes(lines, language, page_data, str(page_context.get('refresh_note') or ''))
    append_snapshot_rows(lines, cfg, language, page, page_context)
    return lines

def append_snapshot_cache_summary(lines: List[str], language: str, page_context: Dict[str, Any]):
    updated_at = page_context['page_data'].get('updated_at_text') or localized_never(language)
    total = int(page_context['total'])
    page = int(page_context['page'])
    total_pages = int(page_context['total_pages'])
    if is_zh_language(language):
        lines.append('  缓存: {}，共 {} 个，第 {}/{} 页'.format(updated_at, total, page, total_pages))
    else:
        lines.append('  Cache: {}, total {}, page {}/{}'.format(updated_at, total, page, total_pages))

def append_snapshot_cache_notes(
    lines: List[str],
    language: str,
    page_data: Dict[str, Any],
    refresh_note: str
):
    error = str(page_data.get('error') or '').strip()
    invalidated = bool(page_data.get('invalidated', False))
    invalidation_reason = str(page_data.get('invalidation_reason') or '').strip()
    if refresh_note:
        lines.append('  {}'.format(refresh_note))
    if invalidated and invalidation_reason:
        if is_zh_language(language):
            lines.append('  缓存已失效: {}'.format(invalidation_reason))
        else:
            lines.append('  Cache invalidated: {}'.format(invalidation_reason))
    if error:
        if is_zh_language(language):
            lines.append('  最近刷新失败，正在显示旧缓存: {}'.format(error))
        else:
            lines.append('  Last refresh failed; showing stale cache: {}'.format(error))

def append_snapshot_rows(
    lines: List[str],
    cfg: Dict[str, Any],
    language: str,
    page: int,
    page_context: Dict[str, Any]
):
    page_data = page_context['page_data']
    rows = page_data.get('rows', [])
    if not rows:
        lines.append('  {}'.format('暂无快照' if is_zh_language(language) else 'No snapshots'))
        return

    page_size = int(page_context['page_size'])
    for index, row in enumerate(rows, start=(page - 1) * page_size + 1):
        lines.append(format_snapshot_line(index, row, language))
    append_snapshot_next_page_hint(lines, cfg, language, page, int(page_context['total_pages']))

def append_snapshot_next_page_hint(
    lines: List[str],
    cfg: Dict[str, Any],
    language: str,
    page: int,
    total_pages: int
):
    if page >= total_pages:
        return
    root = str(cfg.get('command', {}).get('root', '!!restic'))
    if is_zh_language(language):
        lines.append('  下一页: {} status p {}'.format(root, page + 1))
    else:
        lines.append('  Next page: {} status p {}'.format(root, page + 1))

def format_snapshot_line(index: int, row: sqlite3.Row, language: str) -> str:
    short_id = str(row['short_id'] or row['id'] or '')[:8]
    time_text = format_restic_time_for_display(str(row['time_text'] or ''))
    host = str(row['hostname'] or '')
    tags = str(row['tags_text'] or '')
    paths = str(row['paths_text'] or '')
    if len(paths) > 80:
        paths = paths[:77] + '...'
    extras = []
    if host:
        extras.append(host)
    if tags:
        extras.append('#{}'.format(tags))
    if paths:
        extras.append(paths)
    detail = ' | '.join(extras)
    if detail:
        return '  {}. {} {} | {}'.format(index, short_id, time_text, detail)
    return '  {}. {} {}'.format(index, short_id, time_text)

def format_restic_time_for_display(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        return '-'
    match = re.match(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})', text)
    if match:
        return '{} {}'.format(match.group(1), match.group(2))
    return text

def schedule_status_text(cfg: Dict[str, Any], forced: bool, language: str) -> str:
    try:
        result = compute_force_wait_seconds(cfg) if forced else compute_wait_seconds(cfg)
        if result is None:
            return '关闭' if is_zh_language(language) else 'disabled'
        wait_seconds, due_text = result
        schedule = cfg.get('force_schedule' if forced else 'schedule', {})
        detail = localized_schedule_detail(schedule, due_text, language)
        if is_zh_language(language):
            return '{} 秒后（{}）'.format(int(wait_seconds), detail)
        return 'in {}s ({})'.format(int(wait_seconds), detail)
    except Exception as exc:
        if is_zh_language(language):
            return '无法计算：{}'.format(exc)
        return 'cannot calculate: {}'.format(exc)

def localized_schedule_detail(schedule: Dict[str, Any], due_text: str, language: str) -> str:
    try:
        interval_seconds = int(schedule.get('interval_seconds', 0)) if isinstance(schedule, dict) else 0
    except Exception:
        interval_seconds = 0
    if interval_seconds > 0:
        if is_zh_language(language):
            return '固定间隔 {} 秒'.format(interval_seconds)
        return 'fixed interval {}s'.format(interval_seconds)
    return due_text

def localized_text(language: str, zh_text: str, en_text: str) -> str:
    return zh_text if is_zh_language(language) else en_text

def localized_bool(value: bool, language: str) -> str:
    if is_zh_language(language):
        return '是' if value else '否'
    return 'yes' if value else 'no'

def localized_never(language: str) -> str:
    return '从未' if is_zh_language(language) else 'never'

def localized_backup_status(status: Any, language: str) -> str:
    text = str(status or 'never')
    if not is_zh_language(language):
        return text
    mapping = {
        'never': '从未',
        'running': '运行中',
        'success': '成功',
        'failed': '失败',
        'canceled': '已取消'
    }
    return mapping.get(text, text)

def localized_online_source(source: Any, language: str) -> str:
    if not source:
        return localized_never(language)
    text = str(source)
    if not is_zh_language(language):
        return text
    mapping = {
        'join event': '玩家加入事件',
        'left event': '玩家退出事件',
        'server stop': '服务端停止'
    }
    if text.startswith('rcon '):
        return 'RCON {}'.format(text[5:])
    return mapping.get(text, text)
