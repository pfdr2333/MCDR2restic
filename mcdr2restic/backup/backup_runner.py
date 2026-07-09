# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional, Union

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import ensure_runtime, get_config_snapshot, save_config_unlocked
from mcdr2restic.core.i18n import FALLBACK_LANGUAGE, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import (
    BackupCanceled,
    BackupRunStatus,
    BackupRunOutcome,
    BackupTrigger,
    backup_trigger_label,
)
from mcdr2restic.minecraft.minecraft_service import try_force_save_on
from mcdr2restic.restic.restic_service import run_backup_body
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import now_text


RestoreRunningProvider = Callable[[PluginRuntime], bool]
AdminNotifier = Callable[[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None]
SnapshotInvalidator = Callable[[PluginServerInterface, Dict[str, Any], str], None]


class BackupRunner:
    def __init__(
        self,
        app_runtime: PluginRuntime,
        restore_running_provider: RestoreRunningProvider,
        admin_notifier: AdminNotifier,
        snapshot_invalidator: SnapshotInvalidator
    ):
        self.app_runtime = app_runtime
        self.restore_running_provider = restore_running_provider
        self.admin_notifier = admin_notifier
        self.snapshot_invalidator = snapshot_invalidator

    def start_thread(self, server: PluginServerInterface, label: Union[BackupTrigger, str]) -> bool:
        label_text = backup_trigger_label(label)
        if self.restore_running_provider(self.app_runtime):
            return False
        if not self.app_runtime.backup.lock.acquire(blocking=False):
            return False
        thread = threading.Thread(
            target=self._run_with_acquired_lock,
            args=(server, label_text),
            name='MCDR2Restic-Backup-{}'.format(label_text),
            daemon=True
        )
        self.app_runtime.backup.thread = thread
        thread.start()
        return True

    def run_locked(self, server: PluginServerInterface, label: Union[BackupTrigger, str]) -> bool:
        label_text = backup_trigger_label(label)
        language = get_mcdr_language(server)
        if self.restore_running_provider(self.app_runtime):
            server.logger.warning(tr(language, 'warn.backup.restore_running', label=label_text))
            return False
        if not self.app_runtime.backup.lock.acquire(blocking=False):
            server.logger.warning(tr(language, 'warn.backup.already_running', label=label_text))
            return False
        self._run_with_acquired_lock(server, label_text)
        return True

    def _run_with_acquired_lock(self, server: PluginServerInterface, label: str):
        self.app_runtime.backup.label = label
        self.app_runtime.backup.cancel.clear()
        started = time.monotonic()
        start_time = now_text()
        cfg = get_config_snapshot(self.app_runtime)
        try:
            self._record_backup_started(server, label, start_time)
            self._notify_backup_started(cfg, label, start_time)
            outcome = self._execute_backup_run(server, cfg, label, started)
            outcome = self._include_save_on_result(server, outcome)
            finished = now_text()
            self._record_backup_finished(server, outcome, finished)
            self._notify_backup_finished(cfg, label, start_time, finished, outcome)
        finally:
            self._release_backup_slot()

    def _record_backup_started(self, server: PluginServerInterface, label: str, start_time: str):
        with self.app_runtime.config_state.lock:
            ensure_runtime(self.app_runtime.config_state.config, self.app_runtime.config_state.state)
            runtime_state = self.app_runtime.config_state.config['runtime']
            runtime_state['last_backup_start_time'] = start_time
            runtime_state['last_backup_end_time'] = None
            runtime_state['last_backup_status'] = BackupRunStatus.RUNNING.value
            runtime_state['last_backup_message'] = tr(FALLBACK_LANGUAGE, 'runtime.backup.started', label=label)
            save_config_unlocked(self.app_runtime, server)

    def _notify_backup_started(self, cfg: Dict[str, Any], label: str, start_time: str):
        notification_cfg = cfg.get('notification', {}) if isinstance(cfg.get('notification'), dict) else {}
        if not bool(notification_cfg.get('notify_on_start', True)):
            return
        self.admin_notifier('backup_start', {'label': label, 'start_time': start_time}, cfg, False)

    def _execute_backup_run(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        label: str,
        started_at: float
    ) -> BackupRunOutcome:
        language = get_mcdr_language(server)
        try:
            server.logger.info(tr(language, 'info.backup.started', label=label))
            run_backup_body(self.app_runtime, server, cfg, label, self.snapshot_invalidator)
            return self._successful_outcome(server, label, started_at)
        except BackupCanceled as exc:
            return self._canceled_outcome(server, label, started_at, exc)
        except Exception as exc:
            return self._failed_outcome(server, label, started_at, exc)

    def _successful_outcome(self, server: PluginServerInterface, label: str, started_at: float) -> BackupRunOutcome:
        duration_seconds = int(time.monotonic() - started_at)
        message = tr(get_mcdr_language(server), 'info.backup.success', label=label, duration_seconds=duration_seconds)
        server.logger.info(message)
        return BackupRunOutcome(BackupRunStatus.SUCCESS, message, '', duration_seconds)

    def _canceled_outcome(
        self,
        server: PluginServerInterface,
        label: str,
        started_at: float,
        exc: Exception
    ) -> BackupRunOutcome:
        duration_seconds = int(time.monotonic() - started_at)
        message = tr(get_mcdr_language(server), 'warn.backup.canceled', label=label, error=exc)
        server.logger.warning(message)
        return BackupRunOutcome(BackupRunStatus.CANCELED, message, str(exc), duration_seconds)

    def _failed_outcome(
        self,
        server: PluginServerInterface,
        label: str,
        started_at: float,
        exc: Exception
    ) -> BackupRunOutcome:
        duration_seconds = int(time.monotonic() - started_at)
        message = tr(get_mcdr_language(server), 'error.backup.failed', label=label, error=exc)
        server.logger.error('{}\n{}'.format(message, traceback.format_exc()))
        return BackupRunOutcome(BackupRunStatus.FAILED, message, str(exc), duration_seconds)

    def _include_save_on_result(self, server: PluginServerInterface, outcome: BackupRunOutcome) -> BackupRunOutcome:
        try:
            try_force_save_on(self.app_runtime, server, 'backup finally', get_config_snapshot)
            return outcome
        except Exception as exc:
            return self._save_on_failure_outcome(server, outcome, exc)

    def _save_on_failure_outcome(
        self,
        server: PluginServerInterface,
        outcome: BackupRunOutcome,
        exc: Exception
    ) -> BackupRunOutcome:
        language = get_mcdr_language(server)
        server.logger.error(tr(language, 'error.backup.save_on_failed', error=exc))
        detail = tr(language, 'error.backup.save_on_detail', error=exc)
        if outcome.status == BackupRunStatus.SUCCESS:
            return BackupRunOutcome(
                BackupRunStatus.FAILED,
                tr(language, 'error.backup.success_then_save_on_failed', message=outcome.message, detail=detail),
                detail,
                outcome.duration_seconds
            )
        merged_detail = join_backup_detail_texts(outcome.detail, detail)
        return BackupRunOutcome(outcome.status, outcome.message, merged_detail, outcome.duration_seconds)

    def _record_backup_finished(self, server: PluginServerInterface, outcome: BackupRunOutcome, finished_at: str):
        with self.app_runtime.config_state.lock:
            ensure_runtime(self.app_runtime.config_state.config, self.app_runtime.config_state.state)
            runtime_state = self.app_runtime.config_state.config['runtime']
            runtime_state['last_backup_end_time'] = finished_at
            runtime_state['last_backup_status'] = outcome.status_value
            runtime_state['last_backup_message'] = outcome.message
            save_config_unlocked(self.app_runtime, server)

    def _notify_backup_finished(
        self,
        cfg: Dict[str, Any],
        label: str,
        started_at_text: str,
        finished_at_text: str,
        outcome: BackupRunOutcome
    ):
        if not should_notify_backup_finished(cfg, outcome):
            return
        self.admin_notifier(
            'backup_failure' if outcome.failed else 'backup_success',
            {
                'label': label,
                'status': outcome.status_value,
                'message': outcome.message,
                'detail': outcome.detail or outcome.message,
                'start_time': started_at_text,
                'end_time': finished_at_text,
                'duration_seconds': outcome.duration_seconds
            },
            cfg,
            outcome.failed
        )

    def _release_backup_slot(self):
        self.app_runtime.backup.label = None
        self.app_runtime.backup.thread = None
        self.app_runtime.backup.cancel.clear()
        if self.app_runtime.backup.lock.locked():
            self.app_runtime.backup.lock.release()


def should_notify_backup_finished(cfg: Dict[str, Any], outcome: BackupRunOutcome) -> bool:
    notification_cfg = cfg.get('notification', {}) if isinstance(cfg.get('notification'), dict) else {}
    if outcome.status == BackupRunStatus.SUCCESS:
        return bool(notification_cfg.get('notify_on_success', True))
    return bool(notification_cfg.get('notify_on_failure', True))


def join_backup_detail_texts(*parts: str) -> str:
    return '; '.join(str(part).strip() for part in parts if str(part).strip())
