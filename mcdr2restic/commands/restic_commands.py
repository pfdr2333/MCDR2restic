# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List

from mcdreforged.api.all import CommandSource

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.core.i18n import reply_tr, server_tr, source_error_text
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restore.restore_workflow import is_restore_running
from mcdr2restic.restic.restic_constants import (
    RESTIC_COMMAND_INIT,
    RESTIC_COMMAND_UNLOCK,
)
from mcdr2restic.restic.restic_download import ensure_default_restic_executable_available
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.restic.restic_service import make_restic_deadline


class ResticCommands:
    def __init__(self, context: CommandContext):
        self.context = context

    def command_init(self, source: CommandSource):
        self.command_manage_repository(source, RESTIC_COMMAND_INIT)

    def command_maintenance(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        if self.context.maintenance_runner_factory().start_thread(server):
            reply_tr(source, server, "info.maintenance.manual_started")
            return
        if is_restore_running(self.context.app_runtime):
            reply_tr(source, server, "error.restic.manual.restore_running")
            return
        reply_tr(source, server, "error.restic.manual.backup_running")

    def command_unlock(self, source: CommandSource):
        self.command_manage_repository(source, RESTIC_COMMAND_UNLOCK)

    def command_manage_repository(self, source: CommandSource, command: str):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        restic_cfg = cfg.get("restic", {}) if isinstance(cfg.get("restic"), dict) else {}
        if self.reject_manual_restic_command(source, command):
            return

        self.context.app_runtime.backup.label = "manual-{}".format(command)
        self.context.app_runtime.backup.cancel.clear()
        try:
            ensure_default_restic_executable_available(server, restic_cfg)
            result = run_restic_command(
                self.context.app_runtime,
                restic_cfg,
                self.restic_command_args(command),
                command,
                make_restic_deadline(restic_cfg),
            )
            assert_restic_success(restic_cfg, result, self.context.get_command_root())
        except BackupProblem as exc:
            source.reply(source_error_text(source, server, exc))
            return
        finally:
            self.release_manual_restic_slot()

        self.handle_manual_restic_success(source, server, restic_cfg, command)

    def reject_manual_restic_command(
        self, source: CommandSource, command: str
    ) -> bool:
        server = self.context.server_from_source(source)
        if is_restore_running(self.context.app_runtime):
            reply_tr(source, server, "error.restic.manual.restore_running")
            return True
        if self.context.app_runtime.backup.lock.acquire(blocking=False):
            return False
        reply_tr(source, server, "error.restic.manual.backup_running")
        return True

    def release_manual_restic_slot(self):
        self.context.app_runtime.backup.label = None
        self.context.app_runtime.backup.cancel.clear()
        if self.context.app_runtime.backup.lock.locked():
            self.context.app_runtime.backup.lock.release()

    def restic_command_args(self, command: str) -> List[str]:
        if command == RESTIC_COMMAND_INIT:
            return [RESTIC_COMMAND_INIT]
        if command == RESTIC_COMMAND_UNLOCK:
            return [RESTIC_COMMAND_UNLOCK]
        raise BackupProblem(i18n_key="error.restic.command_empty", phase=command)

    def handle_manual_restic_success(
        self,
        source: CommandSource,
        server: Any,
        restic_cfg: Dict[str, Any],
        command: str,
    ):
        if command == RESTIC_COMMAND_INIT:
            self.context.snapshot_invalidator(
                server,
                restic_cfg,
                server_tr(server, "snapshot.cache.reason.repository_initialized"),
            )
            reply_tr(source, server, "info.restic.init.completed")
            return
        reply_tr(source, server, "info.restic.unlock.completed")
