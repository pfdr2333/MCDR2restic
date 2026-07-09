# -*- coding: utf-8 -*-
from __future__ import annotations

import traceback
from typing import Any, Dict, Optional

from mcdreforged.api.all import Info, PluginServerInterface

from mcdr2restic.backup.backup_runner import BackupRunner
from mcdr2restic.backup.backup_scheduler import BackupScheduler
from mcdr2restic.commands.command_handlers import CommandHandlers
from mcdr2restic.core.runtime import PluginRuntime, create_runtime
from mcdr2restic.restore.restore_workflow import (
    handle_restore_server_startup,
    handle_restore_server_stop,
    is_restore_running,
)
from mcdr2restic.minecraft.minecraft_service import (
    is_backup_running,
    is_mc_ready,
    request_cancel_current_backup,
    server_is_running,
    try_force_save_on,
)
from mcdr2restic.core.bootstrap import BootstrapResult
from mcdr2restic.config.config_loader import load_config
from mcdr2restic.config.state_store import ensure_runtime, get_config_snapshot, save_config_unlocked
from mcdr2restic.notifications import DiscordWebhookClient, NotificationDispatcher, OneBotClient
from mcdr2restic.minecraft.player_activity_service import (
    handle_player_joined,
    handle_player_left,
    should_skip_for_no_player_activity,
)
from mcdr2restic.restore.restore_task_repository import clear_restore_tasks
from mcdr2restic.snapshots.snapshot_cache import invalidate_snapshot_cache as invalidate_snapshot_cache_impl
from mcdr2restic.update.update_check import UpdateChecker
from mcdr2restic.core.utils import now_text


class PluginEntrypoint:
    def __init__(self, app_runtime: PluginRuntime, bootstrap_result: BootstrapResult):
        self.runtime = app_runtime
        self.bootstrap_result = bootstrap_result

    def on_load(self, server: PluginServerInterface, prev_module):
        self.shutdown_previous_module(server, prev_module)
        self.prepare_runtime(server)
        load_config(self.runtime, server)
        self.register_commands(server)
        self.register_help_messages(server)
        self.restart_onebot(server)
        self.restart_discord(server)
        self.start_scheduler(server)
        self.restart_update_checker(server, startup_check=True)
        server.logger.info('MCDR2Restic 已加载')

    def shutdown_previous_module(self, server: PluginServerInterface, prev_module):
        if prev_module is None or not hasattr(prev_module, '_shutdown_runtime'):
            return
        try:
            prev_module._shutdown_runtime(server, 'plugin reload')
        except Exception:
            server.logger.warning('清理上一插件实例时发生异常:\n{}'.format(traceback.format_exc()))

    def prepare_runtime(self, server: PluginServerInterface):
        self.runtime.service.server = server
        for message in self.bootstrap_result.logs:
            server.logger.info('[bootstrap] {}'.format(message))
        self.bootstrap_result.logs.clear()
        self.runtime.service.stopping.clear()
        self.runtime.backup.cancel.clear()
        self.runtime.service.server_ready = server_is_running(self.runtime, server)

    def on_unload(self, server: PluginServerInterface):
        self.shutdown_runtime(server, 'plugin unload')

    def on_server_startup(self, server: PluginServerInterface):
        self.runtime.service.server_ready = True
        server.logger.info('检测到 Minecraft 服务端启动完成，允许备份')
        handle_restore_server_startup(self.runtime, server)

    def on_server_stop(self, server: PluginServerInterface, server_return_code: int):
        self.runtime.service.server_ready = False
        self.record_server_stopped(server)
        if handle_restore_server_stop(self.runtime, server, server_return_code, clear_restore_tasks):
            return
        if is_backup_running(self.runtime):
            server.logger.warning('Minecraft 服务端已停止，正在请求中止当前备份')
            request_cancel_current_backup(self.runtime, 'server stopped')

    def record_server_stopped(self, server: PluginServerInterface):
        with self.runtime.config_state.lock:
            ensure_runtime(self.runtime.config_state.config)
            runtime_state = self.runtime.config_state.config['runtime']
            runtime_state['known_online_players'] = []
            runtime_state['current_online_players'] = 0
            runtime_state['last_online_check'] = now_text()
            runtime_state['last_online_check_source'] = 'server stop'
            runtime_state['last_online_check_result'] = '0 online after server stop'
            save_config_unlocked(self.runtime, server)

    def on_mcdr_stop(self, server: PluginServerInterface):
        self.shutdown_runtime(server, 'MCDR stop')

    def on_player_joined(self, server: PluginServerInterface, player: str, info: Info):
        handle_player_joined(self.runtime, server, player)

    def on_player_left(self, server: PluginServerInterface, player: str):
        handle_player_left(self.runtime, server, player)

    def shutdown_runtime(self, server: PluginServerInterface, reason: str):
        self.runtime.service.stopping.set()
        self.stop_update_checker()
        self.stop_scheduler()
        self.cancel_running_backup(reason)
        try_force_save_on(self.runtime, server, reason, get_config_snapshot)
        self.stop_onebot()
        self.runtime.service.discord = None
        if self.runtime.service.server is not None:
            server.logger.info('MCDR2Restic 已停止: {}'.format(reason))

    def stop_update_checker(self):
        if self.runtime.service.update_checker is None:
            return
        self.runtime.service.update_checker.stop()
        self.runtime.service.update_checker = None

    def stop_scheduler(self):
        if self.runtime.service.scheduler is None:
            return
        self.runtime.service.scheduler.stop()
        self.runtime.service.scheduler = None

    def cancel_running_backup(self, reason: str):
        if not is_backup_running(self.runtime):
            return
        request_cancel_current_backup(self.runtime, reason)
        thread = self.runtime.backup.thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def stop_onebot(self):
        if self.runtime.service.onebot is None:
            return
        self.runtime.service.onebot.stop()
        self.runtime.service.onebot = None

    def register_commands(self, server: PluginServerInterface):
        self.runtime.service.command_handlers = self.create_command_handlers()
        self.runtime.service.command_handlers.register_commands(server)

    def register_help_messages(self, server: PluginServerInterface):
        if self.runtime.service.command_handlers is None:
            self.runtime.service.command_handlers = self.create_command_handlers()
        self.runtime.service.command_handlers.register_help_messages(server)

    def create_command_handlers(self) -> CommandHandlers:
        return CommandHandlers(
            self.runtime,
            self.create_backup_runner,
            self.reload_services,
            self.wake_scheduler,
            self.invalidate_snapshot_cache
        )

    def reload_services(self, server: PluginServerInterface):
        self.restart_onebot(server)
        self.restart_discord(server)
        self.restart_update_checker(server, startup_check=False)
        self.wake_scheduler()

    def invalidate_snapshot_cache(
        self,
        server: Optional[PluginServerInterface] = None,
        restic_cfg: Optional[Dict[str, Any]] = None,
        reason: str = 'repository changed'
    ):
        invalidate_snapshot_cache_impl(
            server,
            restic_cfg,
            reason,
            default_server=self.runtime.service.server,
            config_snapshot_provider=lambda: get_config_snapshot(self.runtime)
        )

    def restart_onebot(self, server: PluginServerInterface):
        self.stop_onebot()
        self.runtime.service.onebot = OneBotClient(
            server,
            get_config_snapshot(self.runtime).get('onebot', {}),
            self.bootstrap_result.websocket_client
        )
        self.runtime.service.onebot.start()

    def restart_discord(self, server: PluginServerInterface):
        self.runtime.service.discord = DiscordWebhookClient(server, get_config_snapshot(self.runtime).get('discord', {}))

    def restart_update_checker(self, server: PluginServerInterface, startup_check: bool):
        self.stop_update_checker()
        update_cfg = get_config_snapshot(self.runtime).get('update_check', {})
        if not isinstance(update_cfg, dict) or not bool(update_cfg.get('enabled', True)):
            server.logger.info('MCDR2Restic 版本更新检查已关闭')
            return
        self.runtime.service.update_checker = UpdateChecker(
            server,
            startup_check and bool(update_cfg.get('check_on_startup', True)),
            lambda: get_config_snapshot(self.runtime)
        )
        self.runtime.service.update_checker.start()

    def start_scheduler(self, server: PluginServerInterface):
        notification_dispatcher = self.create_notification_dispatcher()
        backup_runner = self.create_backup_runner()
        self.runtime.service.scheduler = BackupScheduler(
            server,
            lambda: get_config_snapshot(self.runtime),
            backup_runner.run_locked,
            lambda target: is_mc_ready(self.runtime, target),
            lambda cfg: should_skip_for_no_player_activity(self.runtime, cfg),
            notification_dispatcher.notify_admins
        )
        self.runtime.service.scheduler.start()

    def wake_scheduler(self):
        if self.runtime.service.scheduler is not None:
            self.runtime.service.scheduler.wakeup()

    def create_notification_dispatcher(self) -> NotificationDispatcher:
        return NotificationDispatcher(self.runtime, lambda: get_config_snapshot(self.runtime))

    def create_backup_runner(self) -> BackupRunner:
        return BackupRunner(
            self.runtime,
            is_restore_running,
            self.create_notification_dispatcher().notify_admins,
            self.invalidate_snapshot_cache
        )


def create_plugin_entrypoint(bootstrap_result: BootstrapResult) -> PluginEntrypoint:
    return PluginEntrypoint(create_runtime(), bootstrap_result)
