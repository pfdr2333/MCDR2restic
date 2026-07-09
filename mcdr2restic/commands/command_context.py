# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import CommandSource, PluginServerInterface

from mcdr2restic.backup.backup_runner import BackupRunner
from mcdr2restic.config.config_loader import get_command_root
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.core.utils import safe_int


BackupRunnerFactory = Callable[[], BackupRunner]
ReloadServices = Callable[[PluginServerInterface], None]
WakeScheduler = Callable[[], None]
SnapshotInvalidator = Callable[[Optional[PluginServerInterface], Optional[Dict[str, Any]], str], None]


@dataclass
class CommandContext:
    app_runtime: PluginRuntime
    backup_runner_factory: BackupRunnerFactory
    reload_services: ReloadServices
    wake_scheduler: WakeScheduler
    snapshot_invalidator: SnapshotInvalidator

    def server_from_source(self, source: CommandSource) -> PluginServerInterface:
        return self.app_runtime.service.server or source.get_server()

    def check_command_permission(self, source: CommandSource) -> bool:
        level = self.get_command_permission_level()
        try:
            allowed = source.has_permission(level)
        except Exception:
            allowed = False
        if not allowed:
            source.reply('权限不足，需要 MCDR 权限等级 >= {}'.format(level))
        return allowed

    def get_command_permission_level(self) -> int:
        cfg = get_config_snapshot(self.app_runtime)
        return safe_int(cfg.get('command', {}).get('permission_level', 3), 3)

    def get_command_root(self) -> str:
        return get_command_root(self.app_runtime)
