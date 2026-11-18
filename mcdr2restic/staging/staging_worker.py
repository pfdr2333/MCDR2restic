# -*- coding: utf-8 -*-
"""[beta] 中转库复制 worker。

独立线程，不占备份槽位；启动、本地备份成功、退避到期与手动命令都会
触发一次复制。复制与维护/purge/手管 restic 由 ``staging.io_lock`` 串行，
复制进程使用独立 cancel 槽位；restore 开始时复制让位并稍后重试。
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.core.i18n import config_language, server_tr, tr, tr_error
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.core.runtime import PluginRuntime, ResticExecutionSlot
from mcdr2restic.core.utils import get_config_section
from mcdr2restic.staging.staging_config import (
    StagingSettings,
    get_staging_settings,
    staging_enabled,
    validate_staging_settings,
)
from mcdr2restic.staging.staging_copy import CopyCycleResult, run_copy_cycle
from mcdr2restic.staging.staging_state import (
    STAGING_COPY_STATUS_FAILED,
    STAGING_COPY_STATUS_SKIPPED,
    update_staging_runtime,
)
from mcdr2restic.staging.staging_warning import maybe_warn_remote_stale


RESTORE_CHECK_SECONDS = 2.0
BUSY_RETRY_SECONDS = 30
RESTORE_RETRY_SECONDS = 120
WAIT_SLICE_SECONDS = 0.5
# 永久失败（鉴权/配额/chunker）后不再重试复制，但仍需低频检查远端落后，
# 否则之后没有新备份时远端缺口再也不会告警（P2-8）
PERMANENT_RECHECK_SECONDS = 3600

RestoreRunningProvider = Callable[[PluginRuntime], bool]
SnapshotInvalidator = Callable[[PluginServerInterface, Any, str], None]
AdminNotifier = Callable[..., None]


class StagingCopyWorker:
    """[beta] 单实例复制循环。

    停止信号与取消槽位都是实例私有的：``stop()`` 只影响自己，``start()``
    不会清理共享事件。重载时新实例递增 ``worker_generation``，旧线程在下一
    个检查点发现代际不符即退出，不会与新循环并存。
    """

    def __init__(
        self,
        app_runtime: PluginRuntime,
        restore_running_provider: RestoreRunningProvider,
        admin_notifier: Optional[AdminNotifier] = None,
        snapshot_invalidator: Optional[SnapshotInvalidator] = None,
    ):
        self.app_runtime = app_runtime
        self.restore_running_provider = restore_running_provider
        self.admin_notifier = admin_notifier
        self.snapshot_invalidator = snapshot_invalidator
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._generation = 0
        self._active_slot: Optional[ResticExecutionSlot] = None
        self._attempt = 0
        self._manual_requested = False
        self._next_wait_seconds: Optional[float] = None
        self._permanent_hold = False

    def start(self):
        state = self.app_runtime.staging
        state.worker_generation += 1
        self._generation = state.worker_generation
        self._stop.clear()
        state.copy_trigger.clear()
        state.worker = self
        thread = threading.Thread(
            target=self._run,
            name="MCDR2Restic-StagingCopy",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def stop(self):
        self._stop.set()
        # 唤醒等待中的循环；取消槽位让正在跑的 restic 尽快返回
        self.app_runtime.staging.copy_trigger.set()
        slot = self._active_slot
        if slot is not None:
            slot.cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None
        if self.app_runtime.staging.worker is self:
            self.app_runtime.staging.worker = None

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def trigger(self, manual: bool = False):
        if manual:
            self._manual_requested = True
        self.app_runtime.staging.copy_trigger.set()

    def _running(self) -> bool:
        """本实例仍是当前代际且未收到停止信号。"""

        if self._stop.is_set():
            return False
        return self._generation == self.app_runtime.staging.worker_generation

    def _run(self):
        first_round = True
        while self._running():
            if not first_round and not self._wait_for_next_round(
                self._next_wait_seconds
            ):
                return
            first_round = False
            if self._manual_requested:
                self._attempt = 0
                self._manual_requested = False
                # 手动触发视为用户已处理永久失败原因，允许立即重试
                self._permanent_hold = False
            try:
                result = self._run_once()
            except Exception as exc:
                self._report_worker_failure(exc)
                result = CopyCycleResult(
                    STAGING_COPY_STATUS_FAILED,
                    message=str(exc),
                    retryable=True,
                )
            self._next_wait_seconds = self._plan_next_wait(result)

    def _plan_next_wait(  # noqa: PLR0911 结果分类多，集中在一个入口
        self, result: Optional[CopyCycleResult]
    ) -> Optional[float]:
        if result is None:
            # 永久失败后的低频巡检轮：只查远端落后，不重试复制
            return float(PERMANENT_RECHECK_SECONDS) if self._permanent_hold else None
        if result.success:
            self._attempt = 0
            self._permanent_hold = False
            return None
        if result.status == STAGING_COPY_STATUS_SKIPPED:
            return float(BUSY_RETRY_SECONDS if not result.canceled else RESTORE_RETRY_SECONDS)
        if result.canceled:
            self._attempt = 0
            return float(RESTORE_RETRY_SECONDS)
        if result.permanent or result.auth_error or result.quota_error:
            self._attempt = 0
            # 不再重试复制，但保留小时级远端落后检查，缺口不会因"没有新备份"而沉默
            self._permanent_hold = True
            return float(PERMANENT_RECHECK_SECONDS)
        self._permanent_hold = False
        if not result.retryable:
            return None
        settings = self._current_settings()
        if settings is None or self._attempt >= settings.copy_retry_max:
            self._attempt = 0
            return None
        delay = settings.copy_retry_backoff_seconds[
            min(self._attempt, len(settings.copy_retry_backoff_seconds) - 1)
        ]
        self._attempt += 1
        return float(delay)

    def _run_once(self) -> Optional[CopyCycleResult]:  # noqa: PLR0911 前置条件多，逐个早退
        state = self.app_runtime.staging
        if not self._running():
            return None
        server = self.app_runtime.service.server
        if server is None:
            return None
        cfg = get_config_snapshot(self.app_runtime)
        if not staging_enabled(cfg):
            return None
        if self._permanent_hold:
            # 永久失败挂起期间不再尝试复制，只保留远端落后检查
            self._warn_remote_stale_only(server, cfg)
            return None
        if self.restore_running_provider(self.app_runtime):
            self._log_restore_yield(server)
            return CopyCycleResult(
                STAGING_COPY_STATUS_SKIPPED,
                message=tr(get_mcdr_language(server), "info.staging.copy_restore_yield"),
                retryable=True,
                canceled=True,
            )
        if not state.io_lock.acquire(blocking=False):
            return CopyCycleResult(
                STAGING_COPY_STATUS_SKIPPED,
                message=tr(get_mcdr_language(server), "info.staging.copy_busy"),
                retryable=True,
            )
        try:
            return self._run_locked(server, cfg)
        finally:
            state.io_lock.release()

    def _run_locked(
        self,
        server: PluginServerInterface,
        cfg: dict,
    ) -> Optional[CopyCycleResult]:
        restic_cfg = get_config_section(cfg, "restic")
        settings = get_staging_settings(cfg)
        try:
            paths = validate_staging_settings(settings, restic_cfg)
        except BackupProblem as exc:
            message = tr_error(get_mcdr_language(server), exc)
            server.logger.warning(
                tr(
                    get_mcdr_language(server),
                    "warn.staging.worker_config_error",
                    error=message,
                )
            )
            update_staging_runtime(
                self.app_runtime,
                server,
                staging_last_copy_status=STAGING_COPY_STATUS_FAILED,
                staging_last_copy_message=message,
            )
            return CopyCycleResult(
                STAGING_COPY_STATUS_FAILED,
                message=message,
                permanent=True,
            )
        if paths is None:
            return None

        # 每次复制使用 run-local 槽位：stop()/重载只打断自己这一轮，
        # 不会清掉其他实例（purge/维护）共用的取消信号
        slot = ResticExecutionSlot()
        self._active_slot = slot
        monitor_stop = threading.Event()
        monitor = threading.Thread(
            target=self._watch_restore,
            args=(monitor_stop, slot),
            name="MCDR2Restic-StagingCopy-RestoreWatch",
            daemon=True,
        )
        monitor.start()
        try:
            result = run_copy_cycle(
                self.app_runtime,
                server,
                cfg,
                settings,
                paths,
                slot=slot,
            )
        finally:
            self._active_slot = None
            monitor_stop.set()
            monitor.join(timeout=RESTORE_CHECK_SECONDS + 1.0)

        if not self._running():
            # 代际已变：本次结果属于被替换的实例，不再触发通知/缓存失效
            return result
        self._after_cycle(server, cfg, restic_cfg, settings, result)
        return result

    def _after_cycle(
        self,
        server: PluginServerInterface,
        cfg: dict,
        restic_cfg: dict,
        settings: StagingSettings,
        result: CopyCycleResult,
    ):
        if result.success and (result.copied_count > 0 or result.remote_initialized):
            self._invalidate_snapshot_cache(server, restic_cfg)
        if result.success and result.copied_count > 0:
            server.logger.info(result.message)
        maybe_warn_remote_stale(
            self.app_runtime,
            server,
            cfg,
            settings,
            self.admin_notifier,
        )

    def _warn_remote_stale_only(self, server: PluginServerInterface, cfg: dict):
        try:
            settings = get_staging_settings(cfg)
            maybe_warn_remote_stale(
                self.app_runtime,
                server,
                cfg,
                settings,
                self.admin_notifier,
            )
        except Exception as exc:
            server.logger.debug(
                tr(
                    get_mcdr_language(server),
                    "debug.staging.stale_warning_failed",
                    error=exc,
                )
            )

    def _invalidate_snapshot_cache(
        self, server: PluginServerInterface, restic_cfg: dict
    ):
        if self.snapshot_invalidator is None:
            return
        try:
            self.snapshot_invalidator(
                server,
                restic_cfg,
                server_tr(server, "snapshot.cache.reason.staging_copy_finished"),
            )
        except Exception as exc:
            # 缓存失效失败只影响下次状态页多读一次仓库，但不能完全无声
            server.logger.debug(
                tr(
                    get_mcdr_language(server),
                    "debug.staging.cache_invalidation_failed",
                    error=exc,
                )
            )

    def _watch_restore(self, monitor_stop: threading.Event, slot: Any):
        while not monitor_stop.wait(RESTORE_CHECK_SECONDS):
            if not self._running():
                return
            if self.restore_running_provider(self.app_runtime):
                slot.cancel.set()
                return

    def _wait_for_next_round(self, seconds: Optional[float]) -> bool:
        state = self.app_runtime.staging
        deadline = None if seconds is None else time.monotonic() + max(0.0, seconds)
        while self._running():
            if state.copy_trigger.is_set():
                state.copy_trigger.clear()
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return True
            if self._stop.wait(WAIT_SLICE_SECONDS):
                return False
        return False

    def _current_settings(self) -> Optional[StagingSettings]:
        try:
            return get_staging_settings(get_config_snapshot(self.app_runtime))
        except Exception as exc:
            # 配置读取失败不能静默：复制会停摆，排查时需要知道原因
            server = self.app_runtime.service.server
            if server is not None:
                server.logger.debug(
                    tr(
                        get_mcdr_language(server),
                        "debug.staging.worker_settings_failed",
                        error=exc,
                    )
                )
            return None

    def _report_worker_failure(self, exc: Exception):
        server = self.app_runtime.service.server
        if server is None:
            return
        server.logger.error(
            "{}\n{}".format(
                tr(
                    get_mcdr_language(server),
                    "error.staging.worker_failed",
                    error=exc,
                ),
                traceback.format_exc(),
            )
        )

    def _log_restore_yield(self, server: PluginServerInterface):
        language = config_language(
            get_config_snapshot(self.app_runtime), get_mcdr_language(server)
        )
        server.logger.debug(tr(language, "info.staging.copy_restore_yield"))


def request_copy(app_runtime: PluginRuntime, manual: bool = False) -> bool:
    worker = app_runtime.staging.worker
    if worker is None:
        return False
    worker.trigger(manual=manual)
    return True


__all__ = [
    "StagingCopyWorker",
    "request_copy",
]
