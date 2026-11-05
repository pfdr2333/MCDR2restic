# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional, Union

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import (
    ensure_runtime,
    get_config_snapshot,
    save_config_unlocked,
)
from mcdr2restic.core.i18n import config_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import (
    BackupCanceled,
    BackupRunStatus,
    BackupRunOutcome,
    BackupTrigger,
    backup_trigger_display_label,
    backup_trigger_label,
)
from mcdr2restic.core.presentation import localized_backup_status
from mcdr2restic.minecraft.minecraft_service import try_force_save_on
from mcdr2restic.restic.restic_service import run_backup_body
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section, now_text


RestoreRunningProvider = Callable[[PluginRuntime], bool]
AdminNotifier = Callable[
    [str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None
]
SnapshotInvalidator = Callable[[PluginServerInterface, Dict[str, Any], str], None]


class BackupRunner:
    def __init__(
        self,
        app_runtime: PluginRuntime,
        restore_running_provider: RestoreRunningProvider,
        admin_notifier: AdminNotifier,
        snapshot_invalidator: SnapshotInvalidator,
    ):
        self.app_runtime = app_runtime
        self.restore_running_provider = restore_running_provider
        self.admin_notifier = admin_notifier
        self.snapshot_invalidator = snapshot_invalidator

    def start_thread(
        self, server: PluginServerInterface, label: Union[BackupTrigger, str]
    ) -> bool:
        label_text = backup_trigger_label(label)
        if self.restore_running_provider(self.app_runtime):
            return False
        if not self.app_runtime.backup.lock.acquire(blocking=False):
            return False
        thread = threading.Thread(
            target=self._run_with_acquired_lock,
            args=(server, label_text),
            name="MCDR2Restic-Backup-{}".format(label_text),
            daemon=True,
        )
        self.app_runtime.backup.thread = thread
        try:
            thread.start()
        except Exception:
            # 启动失败必须回滚，否则锁永久泄漏阻塞后续所有备份
            self.app_runtime.release_backup_slot()
            raise
        return True

    def start_deferred_thread(
        self, server: PluginServerInterface, label: Union[BackupTrigger, str]
    ) -> bool:
        """调度触发的备份：在新线程内等待槽位后执行，调度线程不被备份时长阻塞。

        等待与执行都经 run_locked 完成，插件停止时放弃等待、执行中可被取消。
        """
        thread = threading.Thread(
            target=self.run_locked,
            args=(server, label),
            kwargs={"wait_for_slot": True},
            name="MCDR2Restic-Backup-{}".format(backup_trigger_label(label)),
            daemon=True,
        )
        thread.start()
        return True

    def run_locked(
        self,
        server: PluginServerInterface,
        label: Union[BackupTrigger, str],
        wait_for_slot: bool = False,
    ) -> bool:
        raw_label = backup_trigger_label(label)
        language = self._language(server)
        label_text = backup_trigger_display_label(label, language)
        if self.restore_running_provider(self.app_runtime):
            server.logger.warning(
                tr(language, "warn.backup.restore_running", label=label_text)
            )
            return False
        if not self._acquire_backup_slot(wait_for_slot):
            if self.app_runtime.service.stopping.is_set():
                server.logger.info(
                    tr(language, "info.backup.skipped_on_plugin_stop", label=label_text)
                )
            else:
                server.logger.warning(
                    tr(language, "warn.backup.already_running", label=label_text)
                )
            return False
        self._run_with_acquired_lock(server, raw_label)
        return True

    def _acquire_backup_slot(self, wait_for_maintenance: bool) -> bool:
        if not wait_for_maintenance:
            return self.app_runtime.backup.lock.acquire(blocking=False)
        # 卸载时立即放弃等待，避免僵尸线程在关闭过程中拿到锁执行备份
        return self.app_runtime.acquire_backup_slot_waiting(
            lambda: self.app_runtime.backup.label != "maintenance"
        )

    def _run_with_acquired_lock(self, server: PluginServerInterface, label: str):
        # 调度触发的备份经 run_locked 进入，这里统一登记执行线程供卸载时取消
        self.app_runtime.backup.thread = threading.current_thread()
        self.app_runtime.backup.label = label
        # 取消请求可能在拿锁与线程启动之间到达，clear 前必须检查，
        # 否则 "!!restic stop" 会表现为已请求取消但备份照跑（P1-7）
        if not self.app_runtime.backup.cancel.is_set():
            self.app_runtime.backup.cancel.clear()
        started = time.monotonic()
        start_time = now_text()
        cfg = get_config_snapshot(self.app_runtime)
        # backup.label 是内部状态标记，消息与通知里的标签按配置语言渲染
        label_text = backup_trigger_display_label(
            label, config_language(cfg, get_mcdr_language(server))
        )
        try:
            self._record_backup_started(server, label_text, start_time)
            # 通知失败不允许中断备份生命周期，否则状态会卡在 running
            self._notify_safely(
                server,
                lambda: self._notify_backup_started(cfg, label_text, start_time),
            )
            outcome = self._execute_backup_run(server, cfg, label_text, started)
            outcome = self._include_save_on_result(server, outcome)
            finished = now_text()
            self._record_backup_finished(server, outcome, finished)
            self._notify_safely(
                server,
                lambda: self._notify_backup_finished(
                    cfg, label_text, start_time, finished, outcome
                ),
            )
        finally:
            self.app_runtime.release_backup_slot()

    def _notify_safely(self, server: PluginServerInterface, notify: Callable[[], None]):
        try:
            notify()
        except Exception as exc:
            server.logger.warning(
                tr(
                    self._language(server),
                    "warn.notify.backup_notify_failed",
                    error=exc,
                )
            )

    def _language(self, server: PluginServerInterface) -> str:
        return config_language(
            get_config_snapshot(self.app_runtime), get_mcdr_language(server)
        )

    def _record_backup_started(
        self, server: PluginServerInterface, label: str, start_time: str
    ):
        with self.app_runtime.config_state.lock:
            ensure_runtime(
                self.app_runtime.config_state.config,
                self.app_runtime.config_state.state,
            )
            runtime_state = self.app_runtime.config_state.config["runtime"]
            runtime_state["last_backup_start_time"] = start_time
            runtime_state["last_backup_end_time"] = None
            runtime_state["last_backup_status"] = BackupRunStatus.RUNNING.value
            runtime_state["last_backup_message"] = tr(
                config_language(
                    self.app_runtime.config_state.config,
                    get_mcdr_language(server),
                ),
                "runtime.backup.started",
                label=label,
            )
            save_config_unlocked(self.app_runtime, server)

    def _notify_backup_started(self, cfg: Dict[str, Any], label: str, start_time: str):
        notification_cfg = get_config_section(cfg, "notification")
        if not bool(notification_cfg.get("notify_on_start", True)):
            return
        self.admin_notifier(
            "backup_start", {"label": label, "start_time": start_time}, cfg, False
        )

    def _execute_backup_run(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        label: str,
        started_at: float,
    ) -> BackupRunOutcome:
        language = config_language(cfg, get_mcdr_language(server))
        try:
            server.logger.info(tr(language, "info.backup.started", label=label))
            run_backup_body(self.app_runtime, server, cfg, self.snapshot_invalidator)
            return self._successful_outcome(server, label, started_at)
        except BackupCanceled as exc:
            return self._canceled_outcome(server, label, started_at, exc)
        except Exception as exc:
            return self._failed_outcome(server, label, started_at, exc)

    def _successful_outcome(
        self, server: PluginServerInterface, label: str, started_at: float
    ) -> BackupRunOutcome:
        duration_seconds = int(time.monotonic() - started_at)
        message = tr(
            self._language(server),
            "info.backup.success",
            label=label,
            duration_seconds=duration_seconds,
        )
        server.logger.info(message)
        return BackupRunOutcome(BackupRunStatus.SUCCESS, message, "", duration_seconds)

    def _canceled_outcome(
        self,
        server: PluginServerInterface,
        label: str,
        started_at: float,
        exc: Exception,
    ) -> BackupRunOutcome:
        duration_seconds = int(time.monotonic() - started_at)
        message = tr(
            self._language(server), "warn.backup.canceled", label=label, error=exc
        )
        server.logger.warning(message)
        return BackupRunOutcome(
            BackupRunStatus.CANCELED, message, str(exc), duration_seconds
        )

    def _failed_outcome(
        self,
        server: PluginServerInterface,
        label: str,
        started_at: float,
        exc: Exception,
    ) -> BackupRunOutcome:
        duration_seconds = int(time.monotonic() - started_at)
        message = tr(
            self._language(server), "error.backup.failed", label=label, error=exc
        )
        server.logger.error("{}\n{}".format(message, traceback.format_exc()))
        return BackupRunOutcome(
            BackupRunStatus.FAILED, message, str(exc), duration_seconds
        )

    def _include_save_on_result(
        self, server: PluginServerInterface, outcome: BackupRunOutcome
    ) -> BackupRunOutcome:
        try:
            try_force_save_on(
                self.app_runtime,
                server,
                tr(self._language(server), "reason.backup_finally"),
                get_config_snapshot,
            )
            return outcome
        except Exception as exc:
            return self._save_on_failure_outcome(server, outcome, exc)

    def _save_on_failure_outcome(
        self, server: PluginServerInterface, outcome: BackupRunOutcome, exc: Exception
    ) -> BackupRunOutcome:
        language = self._language(server)
        server.logger.error(tr(language, "error.backup.save_on_failed", error=exc))
        detail = tr(language, "error.backup.save_on_detail", error=exc)
        if outcome.status == BackupRunStatus.SUCCESS:
            return BackupRunOutcome(
                BackupRunStatus.FAILED,
                tr(
                    language,
                    "error.backup.success_then_save_on_failed",
                    message=outcome.message,
                    detail=detail,
                ),
                detail,
                outcome.duration_seconds,
            )
        merged_detail = join_backup_detail_texts(outcome.detail, detail)
        return BackupRunOutcome(
            outcome.status, outcome.message, merged_detail, outcome.duration_seconds
        )

    def _record_backup_finished(
        self, server: PluginServerInterface, outcome: BackupRunOutcome, finished_at: str
    ):
        with self.app_runtime.config_state.lock:
            ensure_runtime(
                self.app_runtime.config_state.config,
                self.app_runtime.config_state.state,
            )
            runtime_state = self.app_runtime.config_state.config["runtime"]
            runtime_state["last_backup_end_time"] = finished_at
            runtime_state["last_backup_status"] = outcome.status_value
            runtime_state["last_backup_message"] = outcome.message
            save_config_unlocked(self.app_runtime, server)

    def _notify_backup_finished(
        self,
        cfg: Dict[str, Any],
        label: str,
        started_at_text: str,
        finished_at_text: str,
        outcome: BackupRunOutcome,
    ):
        if not should_notify_backup_finished(cfg, outcome):
            return
        # 通知值由 dispatcher 按配置语言渲染，{status} 须直出已译文案而非内部枚举（P3-1）
        self.admin_notifier(
            "backup_failure" if outcome.failed else "backup_success",
            {
                "label": label,
                "status": localized_backup_status(
                    outcome.status_value, config_language(cfg)
                ),
                "message": outcome.message,
                "detail": outcome.detail or outcome.message,
                "start_time": started_at_text,
                "end_time": finished_at_text,
                "duration_seconds": outcome.duration_seconds,
            },
            cfg,
            outcome.failed,
        )



def should_notify_backup_finished(
    cfg: Dict[str, Any], outcome: BackupRunOutcome
) -> bool:
    notification_cfg = get_config_section(cfg, "notification")
    if outcome.status == BackupRunStatus.SUCCESS:
        return bool(notification_cfg.get("notify_on_success", True))
    return bool(notification_cfg.get("notify_on_failure", True))


def join_backup_detail_texts(first: str, second: str) -> str:
    return "; ".join(
        str(part).strip() for part in (first, second) if str(part).strip()
    )
