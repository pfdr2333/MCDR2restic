# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.minecraft.save_flush_watcher import SaveFlushWatcher


@dataclass
class ConfigRuntimeState:
    config: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    language: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)
    player_state_persisted_at: float = 0.0


@dataclass
class ServiceRuntimeState:
    server: Optional[PluginServerInterface] = None
    server_ready: bool = False
    scheduler: Optional[Any] = None
    update_checker: Optional[Any] = None
    command_handlers: Optional[Any] = None
    onebot: Optional[Any] = None
    discord: Optional[Any] = None
    custom_webhook: Optional[Any] = None
    stopping: threading.Event = field(default_factory=threading.Event)
    snapshot_query_lock: threading.Lock = field(default_factory=threading.Lock)
    save_flush_watcher: SaveFlushWatcher = field(default_factory=SaveFlushWatcher)


@dataclass
class BackupRuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel: threading.Event = field(default_factory=threading.Event)
    current_process: Optional[subprocess.Popen] = None
    thread: Optional[threading.Thread] = None
    label: Optional[str] = None


@dataclass
class RestoreRuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: Optional[threading.Thread] = None
    state_lock: threading.RLock = field(default_factory=threading.RLock)
    session: Optional[Any] = None


@dataclass
class PluginRuntime:
    config_state: ConfigRuntimeState = field(default_factory=ConfigRuntimeState)
    service: ServiceRuntimeState = field(default_factory=ServiceRuntimeState)
    backup: BackupRuntimeState = field(default_factory=BackupRuntimeState)
    restore: RestoreRuntimeState = field(default_factory=RestoreRuntimeState)

    def release_backup_slot(self):
        """备份槽位的唯一释放路径：清空状态标记并释放锁（若仍持有）。"""
        self.backup.label = None
        self.backup.thread = None
        self.backup.cancel.clear()
        if self.backup.lock.locked():
            self.backup.lock.release()


def create_runtime() -> PluginRuntime:
    return PluginRuntime()
