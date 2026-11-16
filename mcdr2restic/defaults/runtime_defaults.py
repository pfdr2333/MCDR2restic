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
    # 跳过只记录在这里，不污染 last_backup_status
    "last_skip_time": None,
    "last_skip_reason": "",
    "last_skip_scheduled_time": None,
    "gate_deferral_count_since_success": 0,
    "stale_backup_warned_at": None,
    "last_maintenance_end_time": None,
    "last_maintenance_status": MaintenanceRunStatus.NEVER.value,
    "last_maintenance_message": "",
}


def build_default_runtime() -> Dict[str, Any]:
    return copy.deepcopy(_DEFAULT_RUNTIME)
