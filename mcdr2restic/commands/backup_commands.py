# -*- coding: utf-8 -*-
from __future__ import annotations

from mcdreforged.api.all import CommandSource

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.config.config_loader import load_config, save_enabled_unlocked
from mcdr2restic.minecraft.minecraft_service import is_backup_running, is_mc_ready, request_cancel_current_backup
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
        source.reply('MCDR2Restic 定时备份已启用')

    def command_stop(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        with self.context.app_runtime.config_state.lock:
            save_enabled_unlocked(self.context.app_runtime, server, False)
            save_config_unlocked(self.context.app_runtime, server)
        self.context.wake_scheduler()
        self.reply_stop_result(source)

    def reply_stop_result(self, source: CommandSource):
        if is_backup_running(self.context.app_runtime):
            request_cancel_current_backup(self.context.app_runtime, 'manual stop')
            source.reply('MCDR2Restic 定时备份已禁用，已请求停止当前备份')
            return
        source.reply('MCDR2Restic 定时备份已禁用')

    def command_backup(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        if is_restore_running(self.context.app_runtime):
            source.reply('当前正在执行恢复流程，拒绝启动备份')
            return
        if not is_mc_ready(self.context.app_runtime, server):
            source.reply('Minecraft 服务端尚未确认正常运行，拒绝备份')
            return
        if self.context.backup_runner_factory().start_thread(server, 'manual'):
            source.reply('已开始立即备份，完成结果会发送到日志和已启用的通知渠道')
            return
        source.reply('当前已有备份在执行，拒绝重复启动')

    def command_reload(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        load_config(self.context.app_runtime, server, source)
        self.context.reload_services(server)
