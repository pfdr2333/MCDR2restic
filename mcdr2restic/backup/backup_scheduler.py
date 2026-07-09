# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.scheduling import compute_force_wait_seconds, compute_wait_seconds


ConfigProvider = Callable[[], Dict[str, Any]]
BackupRunner = Callable[[PluginServerInterface, str], bool]
McReadyProvider = Callable[[PluginServerInterface], bool]
SkipPredicate = Callable[[Dict[str, Any]], bool]
AdminNotifier = Callable[[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None]
ScheduleProvider = Callable[[], Optional[Tuple[float, str]]]
ScheduleTrigger = Callable[[], None]


class BackupScheduler:
    def __init__(
        self,
        server: PluginServerInterface,
        config_provider: ConfigProvider,
        backup_runner: BackupRunner,
        mc_ready_provider: McReadyProvider,
        skip_predicate: SkipPredicate,
        admin_notifier: AdminNotifier
    ):
        self.server = server
        self.config_provider = config_provider
        self.backup_runner = backup_runner
        self.mc_ready_provider = mc_ready_provider
        self.skip_predicate = skip_predicate
        self.admin_notifier = admin_notifier
        self.stop_event = threading.Event()
        self.wakeup_event = threading.Event()
        self.thread = threading.Thread(target=self._normal_main, name='MCDR2Restic-Scheduler-Normal', daemon=True)
        self.force_thread = threading.Thread(target=self._force_main, name='MCDR2Restic-Scheduler-Force', daemon=True)

    def start(self):
        self.thread.start()
        self.force_thread.start()

    def stop(self):
        self.stop_event.set()
        self.wakeup_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.force_thread.is_alive():
            self.force_thread.join(timeout=5)

    def wakeup(self):
        self.wakeup_event.set()

    def _normal_main(self):
        self._run_schedule_loop('正常', self._next_normal_schedule, self._trigger_normal_backup)

    def _force_main(self):
        self._run_schedule_loop('强制', self._next_force_schedule, self._trigger_forced_backup)

    def _run_schedule_loop(
        self,
        label: str,
        schedule_provider: ScheduleProvider,
        schedule_trigger: ScheduleTrigger,
    ):
        self.server.logger.info('MCDR2Restic {}调度线程已启动'.format(label))
        while not self.stop_event.is_set():
            schedule = schedule_provider()
            if schedule is None:
                continue
            wait_seconds, due_text = schedule
            self.server.logger.info('下次{}备份等待 {} 秒（{}）'.format(label, int(wait_seconds), due_text))
            if self._wait(wait_seconds) or self.stop_event.is_set():
                continue
            schedule_trigger()
        self.server.logger.info('MCDR2Restic {}调度线程已停止'.format(label))

    def _next_normal_schedule(self) -> Optional[Tuple[float, str]]:
        cfg = self.config_provider()
        if not self._enabled_or_sleep(cfg):
            return None
        try:
            return compute_wait_seconds(cfg)
        except Exception as exc:
            self._handle_schedule_error(cfg, exc, '计算下次备份时间失败')
            return None

    def _next_force_schedule(self) -> Optional[Tuple[float, str]]:
        cfg = self.config_provider()
        if not self._enabled_or_sleep(cfg):
            return None
        try:
            schedule = compute_force_wait_seconds(cfg)
        except Exception as exc:
            self._handle_schedule_error(cfg, exc, '计算下次强制备份时间失败')
            return None
        if schedule is None:
            self._wait(60)
        return schedule

    def _enabled_or_sleep(self, cfg: Dict[str, Any]) -> bool:
        if bool(cfg.get('enabled', True)):
            return True
        self._wait(5)
        return False

    def _handle_schedule_error(self, cfg: Dict[str, Any], exc: Exception, message: str):
        self.server.logger.error('{}: {}'.format(message, exc))
        self.admin_notifier('schedule_config_error', {'error': str(exc)}, cfg, True)
        self._wait(60)

    def _trigger_normal_backup(self):
        cfg = self.config_provider()
        if not self._can_start_backup(cfg, '到达备份时间，但 Minecraft 服务端尚未确认正常运行，跳过本次备份'):
            return
        if self.skip_predicate(cfg):
            self._handle_no_player_activity_skip(cfg)
            return
        self.backup_runner(self.server, 'scheduled')

    def _trigger_forced_backup(self):
        cfg = self.config_provider()
        if not self._can_start_backup(cfg, '到达强制备份时间，但 Minecraft 服务端尚未确认正常运行，跳过本次备份'):
            return
        self.backup_runner(self.server, 'forced')

    def _can_start_backup(self, cfg: Dict[str, Any], not_ready_message: str) -> bool:
        if not bool(cfg.get('enabled', True)):
            return False
        if self.mc_ready_provider(self.server):
            return True
        self.server.logger.warning(not_ready_message)
        self.admin_notifier('backup_not_ready', {'message': not_ready_message}, cfg, True)
        return False

    def _handle_no_player_activity_skip(self, cfg: Dict[str, Any]):
        message = '本周期没有玩家加入或退出，触发检查时也没有玩家在线，跳过本次正常备份'
        self.server.logger.info(message)
        if cfg.get('notification', {}).get('notify_on_skip', False):
            self.admin_notifier('backup_skip_no_player', {'message': message}, cfg, False)

    def _wait(self, seconds: float) -> bool:
        end = time.monotonic() + max(0.0, seconds)
        self.wakeup_event.clear()
        while not self.stop_event.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            if self.wakeup_event.wait(timeout=min(30.0, remaining)):
                self.wakeup_event.clear()
                return True
        return True
