# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Dict

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.core.i18n import config_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.restic.restic_service import run_maintenance_body


RestoreRunningProvider = Callable[[PluginRuntime], bool]
SnapshotInvalidator = Callable[[PluginServerInterface, Dict[str, Any], str], None]


class MaintenanceRunner:
    def __init__(
        self,
        app_runtime: PluginRuntime,
        restore_running_provider: RestoreRunningProvider,
        snapshot_invalidator: SnapshotInvalidator,
    ):
        self.app_runtime = app_runtime
        self.restore_running_provider = restore_running_provider
        self.snapshot_invalidator = snapshot_invalidator

    def start_thread(self, server: PluginServerInterface) -> bool:
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

    def run_waiting(self, server: PluginServerInterface) -> bool:
        cfg = get_config_snapshot(self.app_runtime)
        language = config_language(cfg, get_mcdr_language(server))
        if self.restore_running_provider(self.app_runtime):
            server.logger.warning(tr(language, "warn.maintenance.restore_running"))
            return False
        if not self.app_runtime.acquire_backup_slot_waiting():
            # 仅在插件停止时走到这里，避免卸载后僵尸线程抢到锁执行 prune
            server.logger.info(tr(language, "info.maintenance.skipped_on_plugin_stop"))
            return False
        return self._run_with_acquired_lock(server)

    def _run_with_acquired_lock(self, server: PluginServerInterface) -> bool:
        # 先登记 maintenance 标记再取配置快照：等待方（定时备份）轮询 give_up_when
        # 依赖该标记，取快照的耗时窗口内不得让它误判维护未在运行（P1-6）
        self.app_runtime.backup.label = "maintenance"
        try:
            cfg = get_config_snapshot(self.app_runtime)
            language = config_language(cfg, get_mcdr_language(server))
            self.app_runtime.backup.cancel.clear()
            server.logger.info(tr(language, "info.maintenance.started"))
            run_maintenance_body(
                self.app_runtime, server, cfg, self.snapshot_invalidator
            )
            server.logger.info(tr(language, "info.maintenance.success"))
            return True
        except Exception as exc:
            server.logger.error(
                "{}\n{}".format(
                    tr(language, "error.maintenance.failed", error=exc),
                    traceback.format_exc(),
                )
            )
            return False
        finally:
            self.app_runtime.release_backup_slot()

