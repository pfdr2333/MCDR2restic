# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Union

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.running_time import reset_running_clock_state
from mcdr2restic.backup.scheduling import (
    ScheduledOccurrence,
    parse_schedule_time,
    schedule_min_gap_seconds,
)
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
    EXPECTED_BUSINESS_ERRORS,
    backup_trigger_display_label,
    backup_trigger_label,
)
from mcdr2restic.core.presentation import localized_backup_status
from mcdr2restic.minecraft.minecraft_service import try_force_save_on
from mcdr2restic.minecraft.player_gate_service import (
    GateWaitStatus,
    wait_for_player_gate,
)
from mcdr2restic.restic.restic_service import run_backup_body
from mcdr2restic.staging.staging_hint import maybe_show_staging_hint
from mcdr2restic.core.runtime import PendingBackupState, PluginRuntime
from mcdr2restic.core.utils import get_config_section, now_text


RestoreRunningProvider = Callable[[PluginRuntime], bool]
McReadyProvider = Callable[[PluginServerInterface], bool]
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
        mc_ready_provider: Optional[McReadyProvider] = None,
    ):
        self.app_runtime = app_runtime
        self.restore_running_provider = restore_running_provider
        self.admin_notifier = admin_notifier
        self.snapshot_invalidator = snapshot_invalidator
        self.mc_ready_provider = mc_ready_provider or (lambda _server: True)

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

    def start_scheduled_thread(
        self, server: PluginServerInterface, occurrence: ScheduledOccurrence
    ) -> bool:
        """定时备份：登记推迟槽位后交给 worker，调度线程立即返回。

        门控等待、放行后的复检、最小间隔与槽位等待全部在 worker 内完成，
        取消/状态命令通过 ``backup.pending`` 看到并终止这次推迟。
        """
        pending = self.app_runtime.register_pending_backup(
            occurrence.scheduled_at, occurrence.deadline_monotonic
        )
        thread = threading.Thread(
            target=self._run_scheduled_occurrence,
            args=(server, occurrence, pending),
            name="MCDR2Restic-ScheduledBackup",
            daemon=True,
        )
        pending.thread = thread
        try:
            thread.start()
        except Exception:
            self.app_runtime.clear_pending_backup(pending)
            raise
        return True

    def _run_scheduled_occurrence(
        self,
        server: PluginServerInterface,
        occurrence: ScheduledOccurrence,
        pending: PendingBackupState,
    ):
        try:
            if not self._prepare_scheduled_start(server, occurrence, pending):
                return
            reason = self._wait_for_backup_slot(server, pending)
            if reason is not None:
                self._handle_slot_wait_abort(server, occurrence, pending, reason)
                return
            try:
                if not self._scheduled_start_allowed(server, occurrence):
                    self.app_runtime.release_backup_slot()
                    return
            except Exception:
                # 拿锁后的检查自身异常也必须归还槽位，否则后续备份永久卡死
                self.app_runtime.release_backup_slot()
                raise
            self.app_runtime.clear_pending_backup(pending)
            self._run_with_acquired_lock(server, BackupTrigger.SCHEDULED.value, occurrence)
        except Exception as exc:
            # 推迟流程自身异常不能只有线程堆栈，要在主日志里留下可读原因
            language = self._language(server)
            server.logger.error(
                "{}\n{}".format(
                    tr(language, "error.backup.deferred_worker_failed", error=exc),
                    traceback.format_exc(),
                )
            )
            self.record_backup_skip(
                occurrence.scheduled_at, "worker_error", str(exc)
            )
        finally:
            self.app_runtime.clear_pending_backup(pending)

    def _prepare_scheduled_start(
        self,
        server: PluginServerInterface,
        occurrence: ScheduledOccurrence,
        pending: PendingBackupState,
    ) -> bool:
        if not self._scheduled_start_allowed(server, occurrence):
            return False
        gate = wait_for_player_gate(
            self.app_runtime, server, self.admin_notifier, pending
        )
        if not gate.passed:
            self._handle_gate_abort(server, occurrence, gate.status)
            return False
        # 放行后复检：等待期间可能已被 !!m2r stop 停用或服务器已关闭
        if not self._scheduled_start_allowed(server, occurrence):
            return False
        if pending.deadline_passed():
            self._handle_gate_abort(server, occurrence, GateWaitStatus.DEADLINE)
            return False
        return True

    def _scheduled_start_allowed(
        self, server: PluginServerInterface, occurrence: ScheduledOccurrence
    ) -> bool:
        cfg = get_config_snapshot(self.app_runtime)
        if not bool(cfg.get("enabled", True)):
            self._skip_scheduled(
                server, occurrence, "disabled", "info.backup.defer_aborted_disabled"
            )
            return False
        if not self.mc_ready_provider(server):
            self._skip_scheduled(
                server, occurrence, "not_ready", "warn.backup.not_ready.scheduled",
                notify_failure=True,
            )
            return False
        return not self._skip_for_min_gap(server, occurrence)

    def _wait_for_backup_slot(
        self, server: PluginServerInterface, pending: PendingBackupState
    ) -> Optional[str]:
        """等待备份槽位；返回 None 表示已拿到锁，否则返回放弃原因。"""

        while not self.app_runtime.service.stopping.is_set():
            if pending.cancel.is_set():
                return "canceled"
            if pending.deadline_passed():
                return "deadline"
            cfg = get_config_snapshot(self.app_runtime)
            if not bool(cfg.get("enabled", True)):
                return "disabled"
            if self.app_runtime.backup.lock.acquire(timeout=1.0):
                return None
        return "stopping"

    def _handle_slot_wait_abort(
        self,
        server: PluginServerInterface,
        occurrence: ScheduledOccurrence,
        pending: PendingBackupState,
        reason: str,
    ):
        if reason == "canceled":
            self._skip_scheduled(
                server, occurrence, "canceled", "info.backup.defer_canceled", notify=False
            )
            return
        if reason == "disabled":
            self._skip_scheduled(
                server, occurrence, "disabled", "info.backup.defer_aborted_disabled"
            )
            return
        if reason == "deadline":
            self._handle_gate_abort(server, occurrence, GateWaitStatus.DEADLINE)
            return
        # stopping：只写日志，不记为跳过
        server.logger.info(
            tr(
                self._language(server),
                "info.backup.skipped_on_plugin_stop",
                label=tr(self._language(server), "label.trigger.scheduled"),
            )
        )

    def _handle_gate_abort(
        self,
        server: PluginServerInterface,
        occurrence: ScheduledOccurrence,
        status: GateWaitStatus,
    ):
        if status == GateWaitStatus.CANCELED:
            self._skip_scheduled(
                server, occurrence, "canceled", "info.backup.defer_canceled", notify=False
            )
        elif status == GateWaitStatus.DISABLED:
            self._skip_scheduled(
                server, occurrence, "disabled", "info.backup.defer_aborted_disabled"
            )
        elif status == GateWaitStatus.DEADLINE:
            self._skip_scheduled(
                server, occurrence, "deadline", "warn.backup.defer_deadline_reached"
            )
            self._maybe_hint_staging(server)
        # STOPPING：卸载流程已在收尾，只记日志

    def _maybe_hint_staging(self, server: PluginServerInterface):
        """[beta] 门控等待超时跳过备份时，唯一一次主动提示中转库方案。"""

        try:
            maybe_show_staging_hint(
                self.app_runtime,
                server,
                get_config_snapshot(self.app_runtime),
                self.admin_notifier,
            )
        except Exception as exc:
            server.logger.warning(
                tr(self._language(server), "warn.staging.hint_failed", error=exc)
            )

    def _skip_for_min_gap(
        self, server: PluginServerInterface, occurrence: ScheduledOccurrence
    ) -> bool:
        cfg = get_config_snapshot(self.app_runtime)
        min_gap = schedule_min_gap_seconds(cfg)
        if min_gap <= 0:
            return False
        runtime_state = get_config_section(cfg, "runtime")
        last_end = parse_schedule_time(runtime_state.get("last_backup_end_time"))
        if last_end is None:
            return False
        now = datetime.now()
        if (now - last_end).total_seconds() >= min_gap:
            return False
        message = tr(
            self._language(server),
            "info.backup.skip_min_gap",
            min_gap_seconds=min_gap,
            scheduled_time=occurrence.scheduled_at,
            last_end_time=str(runtime_state.get("last_backup_end_time") or "-"),
        )
        self._record_and_notify_skip(server, cfg, occurrence, "min_gap", message)
        return True

    def _skip_scheduled(  # noqa: PLR0913 跳过分支的通知选项集中在一个内部入口
        self,
        server: PluginServerInterface,
        occurrence: ScheduledOccurrence,
        reason: str,
        message_key: str,
        notify_failure: bool = False,
        notify: bool = True,
    ):
        cfg = get_config_snapshot(self.app_runtime)
        language = self._language(server)
        message = tr(
            language,
            message_key,
            scheduled_time=occurrence.scheduled_at,
            next_due=occurrence.next_due.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if message_key.startswith("warn."):
            server.logger.warning(message)
        else:
            server.logger.info(message)
        self._record_and_notify_skip(
            server,
            cfg,
            occurrence,
            reason,
            message,
            notify=notify,
            notify_failure=notify_failure,
        )

    def _record_and_notify_skip(  # noqa: PLR0913 跳过记录的完整上下文由调用方一次性传入
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        occurrence: ScheduledOccurrence,
        reason: str,
        message: str,
        notify: bool = True,
        notify_failure: bool = False,
    ):
        self.record_backup_skip(occurrence.scheduled_at, reason, message)
        if not notify:
            return
        notification_cfg = get_config_section(cfg, "notification")
        if notify_failure:
            self.admin_notifier(
                "backup_not_ready",
                {"message": message},
                cfg,
                True,
            )
            return
        if bool(notification_cfg.get("notify_on_skip", False)):
            self.admin_notifier("backup_skipped", {"message": message}, cfg, False)

    def record_backup_skip(self, scheduled_time: str, reason: str, message: str):
        """记录跳过事件；last_backup_status 只表示上次真实执行，不被跳过污染。"""

        server = self.app_runtime.service.server
        with self.app_runtime.config_state.lock:
            ensure_runtime(
                self.app_runtime.config_state.config,
                self.app_runtime.config_state.state,
            )
            runtime_state = self.app_runtime.config_state.config["runtime"]
            runtime_state["last_skip_time"] = now_text()
            runtime_state["last_skip_reason"] = message
            runtime_state["last_skip_scheduled_time"] = scheduled_time
            if server is not None:
                save_config_unlocked(self.app_runtime, server)

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

    def _run_with_acquired_lock(
        self,
        server: PluginServerInterface,
        label: str,
        occurrence: Optional[ScheduledOccurrence] = None,
    ):
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
        language = config_language(cfg, get_mcdr_language(server))
        # backup.label 是内部状态标记，消息与通知里的标签按配置语言渲染
        label_text = backup_trigger_display_label(label, language)
        try:
            if occurrence is not None:
                deferred_seconds = max(
                    0, int((datetime.now() - occurrence.scheduled_datetime).total_seconds())
                )
                server.logger.info(
                    tr(
                        language,
                        "info.backup.deferred_started",
                        scheduled_time=occurrence.scheduled_at,
                        deferred_seconds=deferred_seconds,
                        label=label_text,
                    )
                )
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
        if isinstance(exc, EXPECTED_BUSINESS_ERRORS):
            # 磁盘不足、密码错误等预期错误消息已本地化，堆栈只会淹没中文消息（P2-2）
            server.logger.error(message)
        else:
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
            runtime_state["last_backup_duration_seconds"] = outcome.duration_seconds
            if outcome.status == BackupRunStatus.SUCCESS:
                # RPO 只认成功备份；成功后复位陈旧告警、门控推迟计数与运行时间累计
                runtime_state["last_success_backup_end_time"] = finished_at
                runtime_state["stale_backup_warned_at"] = None
                runtime_state["gate_deferral_count_since_success"] = 0
                reset_running_clock_state(runtime_state, server_running=True)
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
