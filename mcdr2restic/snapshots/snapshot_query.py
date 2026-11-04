# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict

from mcdreforged.api.all import PluginServerInterface


@dataclass(frozen=True)
class SnapshotQueryContext:
    """一次快照缓存查询/刷新所需的全部输入。"""

    query_lock: threading.Lock
    server: PluginServerInterface
    restic_cfg: Dict[str, Any]
    snapshot_cfg: Dict[str, Any]
    language: str
    command_root: str = "!!restic"
