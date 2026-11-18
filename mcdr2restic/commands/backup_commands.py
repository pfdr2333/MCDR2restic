# -*- coding: utf-8 -*-
from __future__ import annotations

from mcdreforged.api.all import CommandSource

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.config.config_loader import load_config, save_enabled_unlocked
from mcdr2restic.core.i18n import reply_tr, server_tr, source_error_text
from mcdr2restic.core.models import BackupTrigger, LocalizedValueError
from mcdr2restic.minecraft.minecraft_service import (
    is_backup_running,
    is_mc_ready,
    request_cancel_current_backup,
    request_cancel_running_restore,
)
from mcdr2restic.restore.restore_workflow import is_restore_running
from mcdr2restic.config.state_store import save_config_unlocked


class BackupCommands:
    def __init__(self, context: CommandContext):
        self.context = context

    def command_start(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        with self.context.app_runtime.config_state.lock:
            save_enabled_unlocked(self.context.app_runtime, server, True)
            save_config_unlocked(self.context.app_runtime, server)
        self.context.wake_scheduler()
        reply_tr(source, server, "info.backup.enabled")

    def command_stop(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        with self.context.app_runtime.config_state.lock:
            save_enabled_unlocked(self.context.app_runtime, server, False)
            save_config_unlocked(self.context.app_runtime, server)
        pending = self.context.app_runtime.current_pending_backup()
        self.context.app_runtime.cancel_pending_backup()
        self.context.wake_scheduler()
        self.reply_stop_result(source, pending)

    def reply_stop_result(self, source: CommandSource, pending=None):
        server = self.context.server_from_source(source)
        if is_backup_running(self.context.app_runtime):
            request_cancel_current_backup(
                self.context.app_runtime, server_tr(server, "reason.backup.manual_stop")
            )
            reply_tr(source, server, "info.backup.disabled_cancel_requested")
            return
        if pending is not None:
            reply_tr(
                source,
                server,
                "info.backup.disabled_pending_canceled",
                scheduled_time=pending.scheduled_time_text,
            )
            return
        reply_tr(source, server, "info.backup.disabled")

    def command_cancel(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        pending = self.context.app_runtime.current_pending_backup()
        # 取消当前任务时一并取消排队中的推迟，否则备份结束后它会立刻接上
        if pending is not None:
            self.context.app_runtime.cancel_pending_backup()
        # 恢复流程优先：它的 restic 进程同样记录在 backup.current_process 上
        if is_restore_running(self.context.app_runtime):
            request_cancel_running_restore(
                self.context.app_runtime, server_tr(server, "reason.manual_cancel")
            )
            reply_tr(source, server, "info.restore.cancel_requested")
            return
        if self.context.app_runtime.staging.purge_active:
            # purge 的 restic 进程走中转库独立槽位，命令级取消只置位取消事件，
            # 由 purge 在阶段之间回滚（P2-3）
            self.context.app_runtime.staging.copy_slot.cancel.set()
            reply_tr(source, server, "info.staging.purge_cancel_requested")
            return
        if is_backup_running(self.context.app_runtime):
            request_cancel_current_backup(
                self.context.app_runtime, server_tr(server, "reason.manual_cancel")
            )
            if pending is not None:
                reply_tr(
                    source,
                    server,
                    "info.backup.cancel_requested_with_pending",
                    scheduled_time=pending.scheduled_time_text,
                )
                return
            reply_tr(source, server, "info.backup.cancel_requested")
            return
        if pending is not None:
            reply_tr(
                source,
                server,
                "info.backup.pending_canceled",
                scheduled_time=pending.scheduled_time_text,
            )
            return
        reply_tr(source, server, "info.cancel.nothing_running")

    def command_backup(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        if is_restore_running(self.context.app_runtime):
            reply_tr(source, server, "error.backup.restore_running")
            return
        if not is_mc_ready(self.context.app_runtime, server):
            reply_tr(source, server, "error.backup.minecraft_not_ready")
            return
        try:
            started = self.context.backup_runner_factory().start_thread(
                server, BackupTrigger.MANUAL
            )
        except Exception as exc:
            reply_tr(source, server, "error.backup.thread_start_failed", error=exc)
            return
        if started:
            reply_tr(source, server, "info.backup.manual_started")
            return
        reply_tr(source, server, "error.backup.already_running")

    def command_reload(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        try:
            load_config(self.context.app_runtime, server, source)
        except LocalizedValueError as exc:
            # 配置损坏等预期错误直接回干净消息，交给 MCDR 只会刷 traceback（P2-2）
            source.reply(source_error_text(source, server, exc))
            return
        self.context.reload_services(server)
