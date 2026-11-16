# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import (
    ensure_runtime,
    get_config_snapshot,
    save_config_unlocked,
)
from mcdr2restic.core.i18n import config_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import (
    EXPECTED_BUSINESS_ERRORS,
    MaintenanceOutcome,
    MaintenanceRunStatus,
)
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section, now_text
from mcdr2restic.restic.restic_service import run_maintenance_body


RestoreRunningProvider = Callable[[PluginRuntime], bool]
SnapshotInvalidator = Callable[[PluginServerInterface, Dict[str, Any], str], None]
AdminNotifier = Callable[
    [str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None
]


class MaintenanceRunner:
    def __init__(
        self,
        app_runtime: PluginRuntime,
        restore_running_provider: RestoreRunningProvider,
        snapshot_invalidator: SnapshotInvalidator,
        admin_notifier: Optional[AdminNotifier] = None,
    ):
        self.app_runtime = app_runtime
        self.restore_running_provider = restore_running_provider
        self.snapshot_invalidator = snapshot_invalidator
        self.admin_notifier = admin_notifier or (lambda *args, **kwargs: None)

    def start_thread(self, server: PluginServerInterface) -> bool:
        """手动维护：抢不到槽位直接返回失败，由命令提示稍后再试。"""

        if self.restore_running_provider(self.app_runtime):
            return False
        if not self.app_runtime.backup.lock.acquire(blocking=False):
            return False
        thread = threading.Thread(
            target=self._run_with_acquired_lock,
            args=(server,),
            name="MCDR2Restic-Maintenance",
            daemon=True,
        )
        self.app_runtime.backup.thread = thread
        try:
            thread.start()
        except Exception:
            # 启动失败必须回滚，否则锁永久泄漏阻塞后续所有备份与维护
            self.app_runtime.release_backup_slot()
            raise
        return True

    def run_scheduled(
        self, server: PluginServerInterface, scheduled_at: str = ""
    ) -> bool:
        """定时维护：槽位被占用时跳过本次，绝不让调度线程等锁（P1-3）。

        返回 True 表示已启动维护线程；False 表示本次被跳过。
        """

        language = get_mcdr_language(server)
        try:
            cfg = get_config_snapshot(self.app_runtime)
            language = config_language(cfg, get_mcdr_language(server))
            if self.restore_running_provider(self.app_runtime):
                self._skip_scheduled(
                    server,
                    cfg,
                    scheduled_at,
                    tr(language, "warn.maintenance.restore_running"),
                )
                return False
            if not self.app_runtime.backup.lock.acquire(blocking=False):
                self._skip_scheduled(
                    server,
                    cfg,
                    scheduled_at,
                    tr(language, "info.maintenance.skip_slot_busy"),
                )
                return False
        except Exception as exc:
            # 配置快照失败也要用已初始化的 language 上报（P2-1）
            self._log_failure(server, language, exc)
            return False
        thread = threading.Thread(
            target=self._run_with_acquired_lock,
            args=(server, scheduled_at),
            name="MCDR2Restic-Maintenance",
            daemon=True,
        )
        self.app_runtime.backup.thread = thread
        try:
            thread.start()
        except Exception:
            self.app_runtime.release_backup_slot()
            raise
        return True

    def run_waiting(self, server: PluginServerInterface) -> bool:
        """手动维护：等待槽位后执行；插件停止时放弃。"""

        language = get_mcdr_language(server)
        try:
            cfg = get_config_snapshot(self.app_runtime)
            language = config_language(cfg, get_mcdr_language(server))
        except Exception as exc:
            self._log_failure(server, language, exc)
            return False
        if self.restore_running_provider(self.app_runtime):
            server.logger.warning(tr(language, "warn.maintenance.restore_running"))
            return False
        if not self.app_runtime.acquire_backup_slot_waiting():
            # 仅在插件停止时走到这里，避免卸载后僵尸线程抢到锁执行 prune
            server.logger.info(tr(language, "info.maintenance.skipped_on_plugin_stop"))
            return False
        return self._run_with_acquired_lock(server)

    def _run_with_acquired_lock(
        self, server: PluginServerInterface, scheduled_at: str = ""
    ) -> bool:
        # 先登记 maintenance 标记再取配置快照：等待方（定时备份）轮询 give_up_when
        # 依赖该标记，取快照的耗时窗口内不得让它误判维护未在运行（P1-6）
        self.app_runtime.backup.label = "maintenance"
        language = get_mcdr_language(server)
        cfg: Optional[Dict[str, Any]] = None
        try:
            cfg = get_config_snapshot(self.app_runtime)
            language = config_language(cfg, get_mcdr_language(server))
            # 取消请求可能在拿锁与线程启动之间到达，clear 前必须检查（P1-7）
            if not self.app_runtime.backup.cancel.is_set():
                self.app_runtime.backup.cancel.clear()
            server.logger.info(tr(language, "info.maintenance.started"))
            outcome = run_maintenance_body(
                self.app_runtime, server, cfg, self.snapshot_invalidator
            )
            self._record_maintenance_result(server, outcome)
            self._log_outcome(server, outcome)
            if outcome.skipped:
                self._notify_outcome(server, cfg, outcome, failure=False)
            return outcome.success
        except Exception as exc:
            detail = tr(language, "error.maintenance.failed", error=exc)
            if isinstance(exc, EXPECTED_BUSINESS_ERRORS):
                # 仓库冲突等预期错误消息已本地化，堆栈只会淹没中文消息（P2-2）
                server.logger.error(detail)
            else:
                server.logger.error("{}\n{}".format(detail, traceback.format_exc()))
            outcome = MaintenanceOutcome(MaintenanceRunStatus.FAILED, detail)
            self._record_maintenance_result(server, outcome)
            self._notify_outcome(
                server,
                cfg or {"notification": {"notify_on_failure": True}},
                outcome,
                failure=True,
            )
            return False
        finally:
            self.app_runtime.release_backup_slot()

    def _skip_scheduled(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        scheduled_at: str,
        message: str,
    ):
        server.logger.info(message)
        self._record_maintenance_result(
            server, MaintenanceOutcome(MaintenanceRunStatus.SKIPPED, message)
        )
        if bool(
            get_config_section(cfg, "notification").get("notify_on_skip", False)
        ):
            self.admin_notifier(
                "maintenance_skipped",
                {
                    "message": message,
                    "scheduled_time": scheduled_at or "-",
                    "time": now_text(),
                },
                cfg,
                False,
            )

    def _log_outcome(
        self, server: PluginServerInterface, outcome: MaintenanceOutcome
    ):
        if outcome.success or outcome.skipped:
            server.logger.info(outcome.message)
        else:
            server.logger.error(outcome.message)

    def _notify_outcome(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        outcome: MaintenanceOutcome,
        failure: bool,
    ):
        notification_cfg = get_config_section(cfg, "notification")
        toggle = (
            "notify_on_failure" if failure else "notify_on_skip"
        )
        if not bool(notification_cfg.get(toggle, failure)):
            return
        self.admin_notifier(
            "maintenance_failed" if failure else "maintenance_skipped",
            {
                "status": outcome.status_value,
                "message": outcome.message,
                "time": now_text(),
            },
            cfg,
            failure,
        )

    def _record_maintenance_result(
        self,
        server: PluginServerInterface,
        outcome: MaintenanceOutcome,
    ):
        with self.app_runtime.config_state.lock:
            ensure_runtime(
                self.app_runtime.config_state.config,
                self.app_runtime.config_state.state,
            )
            runtime_state = self.app_runtime.config_state.config["runtime"]
            runtime_state["last_maintenance_end_time"] = now_text()
            runtime_state["last_maintenance_status"] = outcome.status_value
            runtime_state["last_maintenance_message"] = outcome.message
            save_config_unlocked(self.app_runtime, server)

    def _log_failure(
        self, server: PluginServerInterface, language: str, exc: Exception
    ):
        server.logger.error(
            "{}\n{}".format(
                tr(language, "error.maintenance.failed", error=exc),
                traceback.format_exc(),
            )
        )
