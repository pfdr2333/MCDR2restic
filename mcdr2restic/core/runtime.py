# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface


@dataclass
class ConfigRuntimeState:
    config: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass
class ServiceRuntimeState:
    server: Optional[PluginServerInterface] = None
    server_ready: bool = False
    scheduler: Optional[Any] = None
    update_checker: Optional[Any] = None
    command_handlers: Optional[Any] = None
    onebot: Optional[Any] = None
    discord: Optional[Any] = None
    stopping: threading.Event = field(default_factory=threading.Event)
    snapshot_query_lock: threading.Lock = field(default_factory=threading.Lock)


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

    def prepare_for_load(self, server: PluginServerInterface, server_ready: bool):
        self.service.server = server
        self.service.server_ready = server_ready
        self.service.stopping.clear()
        self.backup.cancel.clear()

    @property
    def config(self) -> Dict[str, Any]:
        return self.config_state.config

    @config.setter
    def config(self, value: Dict[str, Any]):
        self.config_state.config = value

    @property
    def state(self) -> Dict[str, Any]:
        return self.config_state.state

    @state.setter
    def state(self, value: Dict[str, Any]):
        self.config_state.state = value

    @property
    def config_lock(self) -> threading.RLock:
        return self.config_state.lock

    @property
    def server(self) -> Optional[PluginServerInterface]:
        return self.service.server

    @server.setter
    def server(self, value: Optional[PluginServerInterface]):
        self.service.server = value

    @property
    def server_ready(self) -> bool:
        return self.service.server_ready

    @server_ready.setter
    def server_ready(self, value: bool):
        self.service.server_ready = bool(value)

    @property
    def scheduler(self) -> Optional[Any]:
        return self.service.scheduler

    @scheduler.setter
    def scheduler(self, value: Optional[Any]):
        self.service.scheduler = value

    @property
    def update_checker(self) -> Optional[Any]:
        return self.service.update_checker

    @update_checker.setter
    def update_checker(self, value: Optional[Any]):
        self.service.update_checker = value

    @property
    def command_handlers(self) -> Optional[Any]:
        return self.service.command_handlers

    @command_handlers.setter
    def command_handlers(self, value: Optional[Any]):
        self.service.command_handlers = value

    @property
    def onebot(self) -> Optional[Any]:
        return self.service.onebot

    @onebot.setter
    def onebot(self, value: Optional[Any]):
        self.service.onebot = value

    @property
    def discord(self) -> Optional[Any]:
        return self.service.discord

    @discord.setter
    def discord(self, value: Optional[Any]):
        self.service.discord = value

    @property
    def plugin_stopping(self) -> threading.Event:
        return self.service.stopping

    @property
    def snapshot_query_lock(self) -> threading.Lock:
        return self.service.snapshot_query_lock

    @property
    def backup_lock(self) -> threading.Lock:
        return self.backup.lock

    @property
    def backup_cancel(self) -> threading.Event:
        return self.backup.cancel

    @property
    def current_process(self) -> Optional[subprocess.Popen]:
        return self.backup.current_process

    @current_process.setter
    def current_process(self, value: Optional[subprocess.Popen]):
        self.backup.current_process = value

    @property
    def current_backup_thread(self) -> Optional[threading.Thread]:
        return self.backup.thread

    @current_backup_thread.setter
    def current_backup_thread(self, value: Optional[threading.Thread]):
        self.backup.thread = value

    @property
    def current_backup_label(self) -> Optional[str]:
        return self.backup.label

    @current_backup_label.setter
    def current_backup_label(self, value: Optional[str]):
        self.backup.label = value

    @property
    def restore_lock(self) -> threading.Lock:
        return self.restore.lock

    @property
    def current_restore_thread(self) -> Optional[threading.Thread]:
        return self.restore.thread

    @current_restore_thread.setter
    def current_restore_thread(self, value: Optional[threading.Thread]):
        self.restore.thread = value

    @property
    def restore_state_lock(self) -> threading.RLock:
        return self.restore.state_lock

    @property
    def restore_session(self) -> Optional[Any]:
        return self.restore.session

    @restore_session.setter
    def restore_session(self, value: Optional[Any]):
        self.restore.session = value


def create_runtime() -> PluginRuntime:
    return PluginRuntime()
