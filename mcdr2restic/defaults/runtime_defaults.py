# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
from typing import Any, Dict

from mcdr2restic.core.models import BackupRunStatus
from mcdr2restic.defaults.default_freeze import freeze_default


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
}

DEFAULT_RUNTIME = freeze_default(_DEFAULT_RUNTIME)


def build_default_runtime() -> Dict[str, Any]:
    return copy.deepcopy(_DEFAULT_RUNTIME)
