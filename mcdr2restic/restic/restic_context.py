# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.runtime import PluginRuntime


SnapshotCacheInvalidator = Callable[[PluginServerInterface, Dict[str, Any], str], None]


@dataclass(frozen=True)
class ResticOperationContext:
    """单次 restic 操作（备份/维护/仓库初始化/恢复）共享的运行上下文。

    快照缓存失效回调仅在会写入仓库数据的操作（备份/维护/初始化）中提供；
    恢复流程只读取仓库，故允许为空。
    """

    app_runtime: PluginRuntime
    server: PluginServerInterface
    restic_cfg: Dict[str, Any]
    deadline: Optional[float] = None
    invalidate_snapshot_cache: Optional[SnapshotCacheInvalidator] = None
    command_root: str = "!!restic"
