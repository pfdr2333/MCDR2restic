# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
from typing import Any, Dict

from mcdr2restic.core.models import BackupRunStatus, MaintenanceRunStatus


_DEFAULT_RUNTIME: Dict[str, Any] = {
    "player_activity_since_last_backup": False,
    "player_joined_since_last_backup": False,
    "player_joined_since_last_check": False,
    "player_left_since_last_check": False,
    "known_online_players": [],
    "current_online_players": 0,
    "last_online_check": None,
    "last_online_check_source": None,
    "last_online_check_result": None,
    "last_player_joined": None,
    "last_player_left": None,
    "last_backup_start_time": None,
    "last_backup_end_time": None,
    "last_backup_status": BackupRunStatus.NEVER.value,
    "last_backup_message": "",
    # 仅真实成功备份更新；RPO 与陈旧告警只认这个时间
    "last_success_backup_end_time": None,
    "last_backup_duration_seconds": 0,
    # 距上次成功备份的服务器实际运行时间；停服期间不计入
    "backup_running_seconds_since_success": 0,
    "server_running_since_epoch": None,
    # 最后一次确认服务器仍在运行的时间点：崩溃后据此丢弃停机时长
    "server_running_heartbeat_at": None,
    # 跳过只记录在这里，不污染 last_backup_status
    "last_skip_time": None,
    "last_skip_reason": "",
    "last_skip_scheduled_time": None,
    "gate_deferral_count_since_success": 0,
    # 首次被人数门控推迟时的运行时间基线；None 表示当前没有积压的推迟
    "gate_stall_baseline_seconds": None,
    # 同一时刻的墙钟文本，仅用于告警文案展示“从什么时候开始”
    "gate_stall_started_at": None,
    "stale_backup_warned_at": None,
    "last_maintenance_end_time": None,
    "last_maintenance_status": MaintenanceRunStatus.NEVER.value,
    "last_maintenance_message": "",
    # [beta] 中转库：游标只认已成功复制到远端的本地快照；状态页只读这些缓存值，
    # 避免每次渲染状态都去探测本地/远端仓库。
    "staging_cursor_time": None,
    "staging_cursor_ids": [],
    # 非空表示游标与本地快照不一致（ahead / missing_ids），此时 backlog 记为 -1
    "staging_cursor_anomaly": "",
    "staging_last_copy_time": None,
    "staging_last_copy_success_time": None,
    "staging_last_copy_status": "never",
    "staging_last_copy_message": "",
    "staging_copy_backlog": 0,
    "staging_local_latest_time": None,
    "staging_remote_latest_time": None,
    # 上次复制后远端快照探测失败：staging_remote_latest_time 只是上次已知值
    "staging_remote_probe_failed": False,
    "staging_last_deep_prune_time": None,
    "staging_chunker_mismatch": False,
    "staging_remote_stale_warned_at": None,
    "staging_hint_last_at": None,
}


def build_default_runtime() -> Dict[str, Any]:
    return copy.deepcopy(_DEFAULT_RUNTIME)
