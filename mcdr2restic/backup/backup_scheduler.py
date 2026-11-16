# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, NamedTuple, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.scheduling import (
    DEFAULT_MAINTENANCE_CRON,
    DEFAULT_NORMAL_CRON,
    DISABLED_CRON,
    ScheduledOccurrence,
    ScheduleSpec,
    compute_defer_deadline,
    format_schedule_time,
    parse_schedule_spec,
    parse_schedule_time,
    schedule_max_defer_seconds,
    schedule_min_gap_seconds,
)
from mcdr2restic.core.i18n import config_language, tr, tr_error
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import BackupTrigger
from mcdr2restic.core.utils import get_config_section, non_negative_int


ConfigProvider = Callable[[], Dict[str, Any]]
BackupRunner = Callable[[PluginServerInterface, BackupTrigger], bool]
ScheduledBackupRunner = Callable[[PluginServerInterface, ScheduledOccurrence], bool]
MaintenanceRunner = Callable[[PluginServerInterface, str], bool]
McReadyProvider = Callable[[PluginServerInterface], bool]
SkipPredicate = Callable[[Dict[str, Any]], bool]
AdminNotifier = Callable[
    [str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None
]
RecordBackupSkip = Callable[[str, str, str], None]
ScheduleSpecProvider = Callable[[Dict[str, Any]], Optional[ScheduleSpec]]
ScheduleTrigger = Callable[[datetime, datetime], None]
MAINTENANCE_BACKUP_PRIORITY_GRACE_SECONDS = 1.0


class SchedulerDependencies(NamedTuple):
    """调度器运转所需的全部协作对象。"""

    config_provider: ConfigProvider
    backup_runner: BackupRunner
    scheduled_backup_runner: ScheduledBackupRunner
    maintenance_runner: MaintenanceRunner
    mc_ready_provider: McReadyProvider
    skip_predicate: SkipPredicate
    admin_notifier: AdminNotifier
    record_backup_skip: RecordBackupSkip


class BackupScheduler:
    def __init__(
        self,
        server: PluginServerInterface,
        dependencies: SchedulerDependencies,
    ):
        self.server = server
        self.config_provider = dependencies.config_provider
        self.backup_runner = dependencies.backup_runner
        self.scheduled_backup_runner = dependencies.scheduled_backup_runner
        self.maintenance_runner = dependencies.maintenance_runner
        self.mc_ready_provider = dependencies.mc_ready_provider
        self.skip_predicate = dependencies.skip_predicate
        self.admin_notifier = dependencies.admin_notifier
        self.record_backup_skip = dependencies.record_backup_skip
        self.stop_event = threading.Event()
        self._wakeup_state_lock = threading.Lock()
        # 每个调度线程持有独立唤醒事件：共享单事件时一次唤醒只能被一个线程
        # 消费，其余线程要睡满原超时才重新读配置（P5-3）。
        # 非调度线程（测试等外部调用方）退回共享兜底事件。
        self._wakeup_events = {
            name: threading.Event()
            for name in (
                "MCDR2Restic-Scheduler-Normal",
                "MCDR2Restic-Scheduler-Force",
                "MCDR2Restic-Scheduler-Maintenance",
            )
        }
        self._foreign_wakeup_event = threading.Event()
        self.thread = threading.Thread(
            target=self._normal_main, name="MCDR2Restic-Scheduler-Normal", daemon=True
        )
        self.force_thread = threading.Thread(
            target=self._force_main, name="MCDR2Restic-Scheduler-Force", daemon=True
        )
        self.maintenance_thread = threading.Thread(
            target=self._maintenance_main,
            name="MCDR2Restic-Scheduler-Maintenance",
            daemon=True,
        )

    def start(self):
        self.thread.start()
        self.force_thread.start()
        self.maintenance_thread.start()

    def stop(self):
        self.stop_event.set()
        self._set_all_wakeup_events()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.force_thread.is_alive():
            self.force_thread.join(timeout=5)
        if self.maintenance_thread.is_alive():
            self.maintenance_thread.join(timeout=5)

    def wakeup(self):
        self._set_all_wakeup_events()

    def _set_all_wakeup_events(self):
        with self._wakeup_state_lock:
            for event in self._wakeup_events.values():
                event.set()
            self._foreign_wakeup_event.set()

    def _normal_main(self):
        self._run_schedule_loop(
            "schedule.normal", self._normal_spec, self._trigger_normal_backup
        )

    def _force_main(self):
        self._run_schedule_loop(
            "schedule.forced", self._force_spec, self._trigger_forced_backup
        )

    def _maintenance_main(self):
        self._run_schedule_loop(
            "schedule.maintenance",
            self._maintenance_spec,
            self._trigger_maintenance,
        )

    def _run_schedule_loop(
        self,
        label_key: str,
        spec_provider: ScheduleSpecProvider,
        schedule_trigger: ScheduleTrigger,
    ):
        language = self._language()
        label = tr(language, label_key)
        self.server.logger.info(tr(language, "log.scheduler.started", label=label))
        # 游标保存上一次的计划点：下一次从计划点推进，而不是从 now 重算，
        # 否则等待/执行耗时会让每天的计划点持续向后漂移（P1-1）
        cursor: Optional[datetime] = None
        while not self.stop_event.is_set():
            try:
                cfg = self.config_provider()
                if not bool(cfg.get("enabled", True)):
                    self._wait(5)
                    cursor = None
                    continue
                spec = spec_provider(cfg)
                if spec is None:
                    self._wait(60)
                    cursor = None
                    continue
                now = datetime.now()
                due = (
                    spec.next_after(cursor)
                    if cursor is not None
                    else spec.next_after(now)
                )
                if due <= now:
                    # 进程休眠/配置空窗错过计划点：对齐到未来最近的点，不补跑
                    due = spec.next_after(now)
                wait_seconds = max(0.0, (due - now).total_seconds())
                self.server.logger.info(
                    tr(
                        language,
                        "log.scheduler.next_run",
                        label=label,
                        seconds=int(wait_seconds),
                        due_text=format_schedule_time(due),
                    )
                )
                woken = self._wait(wait_seconds)
                if self.stop_event.is_set():
                    break
                if woken:
                    # 配置 reload / 线程唤醒：可能改了 cron，从当前时刻重新排程
                    cursor = None
                    continue
                schedule_trigger(due, spec.next_after(due))
                cursor = due
            except Exception as exc:
                # 循环兜底：坏配置等异常只允许跳过本轮，不允许杀死调度线程
                detail = tr_error(language, exc)
                self.server.logger.error(
                    "{}: {}".format(tr(language, "error.scheduler.unexpected"), detail)
                )
                cursor = None
                self._wait(30)
        self.server.logger.info(tr(language, "log.scheduler.stopped", label=label))

    def _normal_spec(self, cfg: Dict[str, Any]) -> Optional[ScheduleSpec]:
        try:
            return parse_schedule_spec(
                get_config_section(cfg, "schedule"),
                DEFAULT_NORMAL_CRON,
                disabled_when_zero_cron=False,
            )
        except Exception as exc:
            self._handle_schedule_error(cfg, exc, "error.schedule.compute_next")
            return None

    def _force_spec(self, cfg: Dict[str, Any]) -> Optional[ScheduleSpec]:
        try:
            return parse_schedule_spec(
                get_config_section(cfg, "force_schedule"),
                DISABLED_CRON,
                disabled_when_zero_cron=True,
            )
        except Exception as exc:
            self._handle_schedule_error(cfg, exc, "error.schedule.compute_forced_next")
            return None

    def _maintenance_spec(self, cfg: Dict[str, Any]) -> Optional[ScheduleSpec]:
        try:
            return parse_schedule_spec(
                get_config_section(cfg, "maintenance_schedule"),
                DEFAULT_MAINTENANCE_CRON,
                disabled_when_zero_cron=True,
                empty_uses_default=True,
            )
        except Exception as exc:
            self._handle_schedule_error(
                cfg, exc, "error.schedule.compute_maintenance_next"
            )
            return None

    def _handle_schedule_error(
        self, cfg: Dict[str, Any], exc: Exception, message_key: str
    ):
        language = self._language(cfg)
        message = tr(language, message_key)
        detail = tr_error(language, exc)
        self.server.logger.error("{}: {}".format(message, detail))
        self.admin_notifier("schedule_config_error", {"error": detail}, cfg, True)

    def _trigger_normal_backup(self, scheduled_at: datetime, next_due: datetime):
        cfg = self.config_provider()
        scheduled_text = format_schedule_time(scheduled_at)
        if not bool(cfg.get("enabled", True)):
            self._record_skip(cfg, scheduled_text, "disabled")
            return
        not_ready_message = self._not_ready_message(cfg, "warn.backup.not_ready.scheduled")
        if not_ready_message is not None:
            self.server.logger.warning(not_ready_message)
            self.admin_notifier(
                "backup_not_ready", {"message": not_ready_message}, cfg, True
            )
            self._record_skip(cfg, scheduled_text, "not_ready", not_ready_message)
            return
        if self.skip_predicate(cfg):
            self._handle_no_player_activity_skip(cfg, scheduled_text)
            return
        if self._within_min_gap(cfg, scheduled_text):
            return
        deadline = compute_defer_deadline(
            scheduled_at,
            next_due,
            non_negative_int(
                get_config_section(cfg, "runtime").get(
                    "last_backup_duration_seconds", 0
                )
            ),
            schedule_min_gap_seconds(cfg),
            schedule_max_defer_seconds(cfg),
        )
        now = datetime.now()
        if deadline <= now:
            message = tr(
                self._language(cfg),
                "warn.backup.defer_deadline_reached",
                scheduled_time=scheduled_text,
                next_due=format_schedule_time(next_due),
            )
            self.server.logger.warning(message)
            self._notify_skip(cfg, message)
            self.record_backup_skip(scheduled_text, "deadline", message)
            return
        occurrence = ScheduledOccurrence(
            scheduled_at=scheduled_text,
            scheduled_datetime=scheduled_at,
            next_due=next_due,
            deadline_monotonic=time.monotonic()
            + max(0.0, (deadline - now).total_seconds()),
        )
        self.scheduled_backup_runner(self.server, occurrence)

    def _trigger_forced_backup(self, scheduled_at: datetime, next_due: datetime):
        cfg = self.config_provider()
        scheduled_text = format_schedule_time(scheduled_at)
        if not bool(cfg.get("enabled", True)):
            return
        not_ready_message = self._not_ready_message(cfg, "warn.backup.not_ready.forced")
        if not_ready_message is not None:
            self.server.logger.warning(not_ready_message)
            self.admin_notifier(
                "backup_not_ready", {"message": not_ready_message}, cfg, True
            )
            self._record_skip(cfg, scheduled_text, "not_ready", not_ready_message)
            return
        self.backup_runner(self.server, BackupTrigger.FORCED)

    def _trigger_maintenance(self, scheduled_at: datetime, next_due: datetime):
        cfg = self.config_provider()
        if not bool(cfg.get("enabled", True)):
            return
        # 与同刻到点的备份让路：备份优先，维护拿不到槽位会在自身逻辑里直接跳过
        if self._wait(MAINTENANCE_BACKUP_PRIORITY_GRACE_SECONDS):
            return
        self.maintenance_runner(self.server, format_schedule_time(scheduled_at))

    def _not_ready_message(
        self, cfg: Dict[str, Any], not_ready_key: str
    ) -> Optional[str]:
        if self.mc_ready_provider(self.server):
            return None
        return tr(self._language(cfg), not_ready_key)

    def _within_min_gap(self, cfg: Dict[str, Any], scheduled_text: str) -> bool:
        min_gap = schedule_min_gap_seconds(cfg)
        if min_gap <= 0:
            return False
        runtime_state = get_config_section(cfg, "runtime")
        last_end_text = str(runtime_state.get("last_backup_end_time") or "")
        last_end = parse_schedule_time(last_end_text)
        if last_end is None:
            return False
        if (datetime.now() - last_end).total_seconds() >= min_gap:
            return False
        message = tr(
            self._language(cfg),
            "info.backup.skip_min_gap",
            min_gap_seconds=min_gap,
            scheduled_time=scheduled_text,
            last_end_time=last_end_text or "-",
        )
        self.server.logger.info(message)
        self._notify_skip(cfg, message)
        self.record_backup_skip(scheduled_text, "min_gap", message)
        return True

    def _record_skip(
        self,
        cfg: Dict[str, Any],
        scheduled_text: str,
        reason: str,
        message: Optional[str] = None,
    ):
        if message is None:
            message = tr(
                self._language(cfg),
                "info.backup.defer_aborted_disabled",
                scheduled_time=scheduled_text,
            )
        self.record_backup_skip(scheduled_text, reason, message)

    def _notify_skip(self, cfg: Dict[str, Any], message: str):
        if get_config_section(cfg, "notification").get("notify_on_skip", False):
            self.admin_notifier("backup_skipped", {"message": message}, cfg, False)

    def _handle_no_player_activity_skip(self, cfg: Dict[str, Any], scheduled_text: str):
        message = tr(
            self._language(cfg), "info.backup.skip_no_player_activity"
        )
        self.server.logger.info(message)
        self._notify_skip(cfg, message)
        self.record_backup_skip(scheduled_text, "no_activity", message)

    def _wait(self, seconds: float, wakeup_event: Optional[threading.Event] = None) -> bool:
        event = (
            wakeup_event
            or self._wakeup_events.get(
                threading.current_thread().name, self._foreign_wakeup_event
            )
        )
        end = time.monotonic() + max(0.0, seconds)
        # 先取走等待开始前已到达的唤醒再清事件，否则两次 _wait 之间的 wakeup 会被抹掉
        with self._wakeup_state_lock:
            pending_wakeup = event.is_set()
            event.clear()
        while not self.stop_event.is_set():
            if pending_wakeup:
                return True
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            if event.wait(timeout=min(30.0, remaining)):
                with self._wakeup_state_lock:
                    event.clear()
                return True
        return True

    def _language(self, cfg: Optional[Dict[str, Any]] = None) -> str:
        return config_language(
            cfg or self.config_provider(), get_mcdr_language(self.server)
        )
