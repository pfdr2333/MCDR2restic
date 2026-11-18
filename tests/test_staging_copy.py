# -*- coding: utf-8 -*-
import threading

try:
    from .support import (
        BackupProblem,
        FakePluginServer,
        ResticCommandResult,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        BackupProblem,
        FakePluginServer,
        ResticCommandResult,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )

from mcdr2restic.core.models import BackupCanceled
from mcdr2restic.core.runtime import ResticExecutionSlot
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.staging import staging_copy, staging_repository, staging_warning, staging_worker
from mcdr2restic.staging.staging_config import (
    get_staging_settings,
    resolve_staging_paths,
)
from mcdr2restic.staging.staging_copy import CopyCycleResult, run_copy_cycle
from mcdr2restic.staging.staging_state import (
    STAGING_COPY_STATUS_CANCELED,
    STAGING_COPY_STATUS_FAILED,
    STAGING_COPY_STATUS_SKIPPED,
    STAGING_COPY_STATUS_SUCCESS,
)


def staging_config():
    cfg = build_default_config()
    cfg["staging"].update(
        {"enabled": True, "repository": "/data/staging", "password": "pass"}
    )
    cfg["runtime"] = build_default_runtime()
    return cfg


def result(return_code=0, stderr=""):
    return ResticCommandResult("staging-copy", [], return_code, "", stderr, 1.0)


def snapshot(snapshot_id, time_text):
    return {"id": snapshot_id, "short_id": snapshot_id[:8], "time": time_text}


class CopyCycleTest(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime()
        self.runtime.config_state.config = staging_config()
        self.server = FakePluginServer()
        self.cfg = staging_config()
        self.settings = get_staging_settings(self.cfg)
        self.paths = resolve_staging_paths(self.settings, self.cfg["restic"])

    def run_cycle(self, **patches):
        defaults = {
            "probe_repository": mock.Mock(return_value=mock.Mock(ok=True, error="")),
            "ensure_remote_repository_for_copy": mock.Mock(
                return_value=(self.cfg["restic"], False)
            ),
            "assert_matching_chunkers": mock.Mock(return_value="poly"),
            "read_snapshots": mock.Mock(return_value=[]),
            "run_restic_command": mock.Mock(return_value=result(0)),
            "assert_restic_success": mock.Mock(),
            "update_staging_runtime": mock.Mock(
                side_effect=lambda runtime, server=None, **values: runtime.config_state.config[
                    "runtime"
                ].update(values)
            ),
        }
        defaults.update(patches)
        patchers = [
            mock.patch.object(staging_copy, name, value)
            for name, value in defaults.items()
        ]
        for patcher in patchers:
            patcher.start()
        try:
            return run_copy_cycle(
                self.runtime,
                self.server,
                self.cfg,
                self.settings,
                self.paths,
                slot=None,
            )
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

    def test_copies_only_pending_snapshots_and_advances_cursor(self):
        local = [snapshot("a" * 64, "2026-01-01T00:00:00Z")]
        run_command = mock.Mock(return_value=result(0))
        read = mock.Mock(side_effect=[local, local])

        cycle = self.run_cycle(
            read_snapshots=read,
            run_restic_command=run_command,
        )

        self.assertEqual(cycle.status, STAGING_COPY_STATUS_SUCCESS)
        self.assertEqual(cycle.copied_count, 1)
        run_command.assert_called_once()
        args = run_command.call_args.args[2]
        self.assertEqual(args[:3], ["copy", "--from-repo", self.paths.repository_path])
        self.assertIn("a" * 64, args)
        runtime = self.runtime.config_state.config["runtime"]
        self.assertEqual(runtime["staging_cursor_time"], "2026-01-01T00:00:00Z")
        self.assertEqual(runtime["staging_cursor_ids"], ["a" * 64])
        self.assertEqual(runtime["staging_copy_backlog"], 0)

    def test_cursor_prevents_recopy_when_nothing_new(self):
        local = [snapshot("a" * 64, "2026-01-01T00:00:00Z")]
        self.runtime.config_state.config["runtime"].update(
            {
                "staging_cursor_time": "2026-01-01T00:00:00Z",
                "staging_cursor_ids": ["a" * 64],
            }
        )
        run_command = mock.Mock()

        cycle = self.run_cycle(
            read_snapshots=mock.Mock(side_effect=[local, local]),
            run_restic_command=run_command,
        )

        self.assertEqual(cycle.status, STAGING_COPY_STATUS_SUCCESS)
        self.assertEqual(cycle.copied_count, 0)
        run_command.assert_not_called()

    def test_chunker_mismatch_is_permanent_and_recorded(self):
        mismatch = BackupProblem(
            i18n_key="error.staging.chunker_mismatch",
            local_chunker="a",
            remote_chunker="b",
        )
        cycle = self.run_cycle(
            assert_matching_chunkers=mock.Mock(side_effect=mismatch)
        )

        self.assertTrue(cycle.permanent)
        self.assertTrue(cycle.chunker_mismatch)
        runtime = self.runtime.config_state.config["runtime"]
        self.assertTrue(runtime["staging_chunker_mismatch"])
        self.assertEqual(runtime["staging_last_copy_status"], "permanent")

    def test_auth_failure_is_not_retried(self):
        local = [snapshot("a" * 64, "2026-01-01T00:00:00Z")]
        cycle = self.run_cycle(
            read_snapshots=mock.Mock(side_effect=[local, local]),
            run_restic_command=mock.Mock(
                return_value=result(1, stderr="Fatal: wrong password or no key found")
            ),
            assert_restic_success=mock.Mock(
                side_effect=BackupProblem(
                    i18n_key="error.restic.return_code",
                    phase="staging-copy",
                    return_code=1,
                    duration_seconds=1,
                    output="Fatal: wrong password or no key found",
                )
            ),
        )

        self.assertTrue(cycle.auth_error)
        self.assertFalse(cycle.retryable)
        self.assertFalse(cycle.success)

    def test_quota_failure_is_not_retried(self):
        local = [snapshot("a" * 64, "2026-01-01T00:00:00Z")]
        cycle = self.run_cycle(
            read_snapshots=mock.Mock(side_effect=[local, local]),
            run_restic_command=mock.Mock(
                return_value=result(1, stderr="quota exceeded")
            ),
            assert_restic_success=mock.Mock(
                side_effect=BackupProblem(
                    i18n_key="error.restic.return_code",
                    phase="staging-copy",
                    return_code=1,
                    duration_seconds=1,
                    output="quota exceeded",
                )
            ),
        )

        self.assertTrue(cycle.quota_error)
        self.assertFalse(cycle.retryable)

    def test_numbers_in_output_are_not_auth_errors(self):
        # 裸数字 401/403 标记会命中输出里的随机数字（退避时长等），
        # 把网络故障误判成鉴权失败停止重试（E2E NEW-P2）
        local = [snapshot("a" * 64, "2026-01-01T00:00:00Z")]
        cycle = self.run_cycle(
            read_snapshots=mock.Mock(side_effect=[local, local]),
            run_restic_command=mock.Mock(
                return_value=result(1, stderr="retried 2.640454019s")
            ),
            assert_restic_success=mock.Mock(
                side_effect=BackupProblem(
                    i18n_key="error.restic.return_code",
                    phase="staging-copy",
                    return_code=1,
                    duration_seconds=1,
                    output="retried 2.640454019s",
                )
            ),
        )

        self.assertFalse(cycle.auth_error)
        self.assertFalse(cycle.quota_error)
        self.assertTrue(cycle.retryable)

    def test_canceled_copy_is_retryable_and_not_a_failure(self):
        local = [snapshot("a" * 64, "2026-01-01T00:00:00Z")]
        cycle = self.run_cycle(
            read_snapshots=mock.Mock(side_effect=[local, local]),
            run_restic_command=mock.Mock(
                side_effect=BackupCanceled(i18n_key="error.backup.cancel_requested")
            ),
        )

        self.assertEqual(cycle.status, STAGING_COPY_STATUS_CANCELED)
        self.assertTrue(cycle.canceled)
        self.assertTrue(cycle.retryable)

    def test_cursor_ahead_replays_every_local_snapshot(self):
        # 游标晚于本地最新快照时不能相信"没有待复制快照"：必须全量重放，
        # 否则复制假成功 + backlog=0 会让维护删掉未复制的本地快照（P1-1）
        local = [
            snapshot("a" * 64, "2026-01-01T00:00:00Z"),
            snapshot("b" * 64, "2026-01-01T01:00:00Z"),
            snapshot("c" * 64, "2026-01-01T02:00:00Z"),
        ]
        self.runtime.config_state.config["runtime"].update(
            {
                "staging_cursor_time": "2026-01-02T00:00:00Z",
                "staging_cursor_ids": ["c" * 64],
            }
        )
        run_command = mock.Mock(return_value=result(0))

        cycle = self.run_cycle(
            read_snapshots=mock.Mock(side_effect=[local, local]),
            run_restic_command=run_command,
        )

        self.assertTrue(cycle.cursor_untrusted)
        self.assertTrue(cycle.success)
        copied = run_command.call_args.args[2]
        for item in local:
            self.assertIn(item["id"], copied)
        self.assertTrue(self.server.logger.warning_messages)
        runtime = self.runtime.config_state.config["runtime"]
        self.assertEqual(runtime["staging_cursor_anomaly"], "")
        self.assertEqual(runtime["staging_cursor_time"], "2026-01-01T02:00:00Z")
        self.assertEqual(runtime["staging_copy_backlog"], 0)

    def test_cursor_ahead_failure_reports_unknown_backlog(self):
        local = [
            snapshot("a" * 64, "2026-01-01T00:00:00Z"),
            snapshot("b" * 64, "2026-01-01T01:00:00Z"),
        ]
        self.runtime.config_state.config["runtime"].update(
            {"staging_cursor_time": "2026-01-02T00:00:00Z"}
        )

        cycle = self.run_cycle(
            read_snapshots=mock.Mock(side_effect=[local]),
            run_restic_command=mock.Mock(
                return_value=result(1, stderr="network down")
            ),
            assert_restic_success=mock.Mock(
                side_effect=BackupProblem(
                    i18n_key="error.restic.return_code",
                    phase="staging-copy",
                    return_code=1,
                    duration_seconds=1,
                    output="network down",
                )
            ),
        )

        self.assertTrue(cycle.retryable)
        self.assertTrue(cycle.backlog_unknown)
        runtime = self.runtime.config_state.config["runtime"]
        # 未知 backlog 用 -1 表示，状态页与维护都不能当成"已补齐"
        self.assertEqual(runtime["staging_copy_backlog"], -1)
        self.assertEqual(runtime["staging_cursor_anomaly"], "ahead")

    def test_remote_probe_failure_keeps_last_known_remote_latest(self):
        # 探测失败时用本地时间顶替远端时间，会让"远端落后"被误判为已追平（P1-1）
        local = [snapshot("a" * 64, "2026-01-01T00:00:00Z")]
        self.runtime.config_state.config["runtime"].update(
            {"staging_remote_latest_time": "2025-12-31T00:00:00Z"}
        )

        cycle = self.run_cycle(
            read_snapshots=mock.Mock(
                side_effect=[
                    local,
                    BackupProblem(i18n_key="error.restic.command_timeout"),
                ]
            ),
            run_restic_command=mock.Mock(return_value=result(0)),
        )

        self.assertTrue(cycle.success)
        runtime = self.runtime.config_state.config["runtime"]
        self.assertEqual(
            runtime["staging_remote_latest_time"], "2025-12-31T00:00:00Z"
        )
        self.assertTrue(runtime["staging_remote_probe_failed"])

    def test_local_repository_missing_is_retryable(self):
        cycle = self.run_cycle(
            probe_repository=mock.Mock(
                return_value=mock.Mock(ok=False, error="missing")
            )
        )

        self.assertEqual(cycle.status, STAGING_COPY_STATUS_FAILED)
        self.assertTrue(cycle.retryable)

    def test_local_missing_without_cursor_waits_for_first_backup(self):
        # 刚启用、首备未跑：静默等待而不是连环 WARNING（E2E P3-2）
        cycle = self.run_cycle(
            probe_repository=mock.Mock(
                return_value=mock.Mock(
                    ok=False,
                    status=staging_repository.RepoProbeStatus.MISSING,
                    error="repository does not exist",
                )
            )
        )

        self.assertEqual(cycle.status, STAGING_COPY_STATUS_SKIPPED)
        self.assertTrue(cycle.retryable)
        self.assertEqual(self.server.logger.warning_messages, [])
        self.assertTrue(self.server.logger.info_messages)

    def test_local_missing_with_cursor_still_warns(self):
        # 有游标说明此前成功备份过，本地库突然缺失是真故障，保持告警
        self.runtime.config_state.config["runtime"].update(
            {"staging_cursor_time": "2026-01-01T00:00:00Z"}
        )
        cycle = self.run_cycle(
            probe_repository=mock.Mock(
                return_value=mock.Mock(
                    ok=False,
                    status=staging_repository.RepoProbeStatus.MISSING,
                    error="repository does not exist",
                )
            )
        )

        self.assertEqual(cycle.status, STAGING_COPY_STATUS_FAILED)
        self.assertTrue(self.server.logger.warning_messages)


class WorkerTest(unittest.TestCase):
    def make_worker(self, cfg=None):
        runtime = create_runtime()
        runtime.config_state.config = cfg or staging_config()
        return runtime, staging_worker.StagingCopyWorker(
            runtime, lambda _runtime: False
        )

    def test_retry_plan_uses_backoff_then_gives_up(self):
        _, worker = self.make_worker()
        failure = CopyCycleResult(
            STAGING_COPY_STATUS_FAILED, pending_count=1, retryable=True
        )

        self.assertEqual(worker._plan_next_wait(failure), 60.0)
        self.assertEqual(worker._plan_next_wait(failure), 300.0)
        self.assertEqual(worker._plan_next_wait(failure), 900.0)
        self.assertIsNone(worker._plan_next_wait(failure))

    def test_success_resets_wait_and_attempt(self):
        _, worker = self.make_worker()
        worker._attempt = 2

        self.assertIsNone(
            worker._plan_next_wait(
                CopyCycleResult(STAGING_COPY_STATUS_SUCCESS)
            )
        )
        self.assertEqual(worker._attempt, 0)

    def test_restore_yield_retries_later(self):
        _, worker = self.make_worker()
        result = CopyCycleResult(
            STAGING_COPY_STATUS_SKIPPED, retryable=True, canceled=True
        )

        self.assertEqual(
            worker._plan_next_wait(result),
            float(staging_worker.RESTORE_RETRY_SECONDS),
        )

    def test_request_copy_requires_a_worker(self):
        runtime = create_runtime()

        self.assertFalse(staging_worker.request_copy(runtime))
        worker = mock.Mock()
        runtime.staging.worker = worker

        self.assertTrue(staging_worker.request_copy(runtime, manual=True))
        worker.trigger.assert_called_once_with(manual=True)

    def test_wait_for_next_round_wakes_on_trigger_and_stops(self):
        runtime, worker = self.make_worker()

        runtime.staging.copy_trigger.set()
        self.assertTrue(worker._wait_for_next_round(None))

        worker._stop.set()
        self.assertFalse(worker._wait_for_next_round(None))

    def test_stop_only_signals_its_own_events(self):
        # stop() 不再清理/置位共享事件，否则新实例的取消信号会被旧线程抹掉（P2-4）
        runtime, worker = self.make_worker()

        worker.stop()

        self.assertTrue(worker._stop.is_set())
        self.assertFalse(runtime.staging.copy_slot.cancel.is_set())
        self.assertIsNone(runtime.staging.worker)

    def test_stop_cancels_active_run_slot(self):
        _, worker = self.make_worker()
        slot = ResticExecutionSlot()
        worker._active_slot = slot

        worker.stop()

        self.assertTrue(slot.cancel.is_set())

    def test_new_worker_retires_previous_generation(self):
        runtime, old = self.make_worker()

        with mock.patch.object(
            staging_worker.StagingCopyWorker, "_run", lambda _self: None
        ):
            old.start()
            old_generation = old._generation
            new = staging_worker.StagingCopyWorker(runtime, lambda _runtime: False)
            new.start()

        try:
            self.assertEqual(
                runtime.staging.worker_generation, old_generation + 1
            )
            self.assertFalse(old._running())
            self.assertTrue(new._running())
        finally:
            new.stop()

    def test_start_stop_leaves_no_running_thread(self):
        _, worker = self.make_worker()
        started = threading.Event()

        def fake_run(_self):
            started.set()
            _self._stop.wait(5)

        with mock.patch.object(
            staging_worker.StagingCopyWorker, "_run", fake_run
        ):
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertTrue(worker.is_alive())
            worker.stop()

        self.assertFalse(worker.is_alive())


class RemoteStaleWarningTest(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime()
        self.runtime.config_state.config = staging_config()
        self.server = FakePluginServer()
        self.cfg = staging_config()
        self.settings = get_staging_settings(self.cfg)

    def test_no_warning_before_threshold(self):
        self.runtime.config_state.config["runtime"].update(
            {
                "staging_local_latest_time": staging_warning.now_text(),
                "staging_remote_latest_time": staging_warning.now_text(),
                "staging_copy_backlog": 1,
            }
        )
        notifier = mock.Mock()

        warned = staging_warning.maybe_warn_remote_stale(
            self.runtime, self.server, self.cfg, self.settings, notifier
        )

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_warning_fires_once_then_cools_down(self):
        self.runtime.config_state.config["runtime"].update(
            {
                "staging_local_latest_time": "2026-01-03 00:00:00",
                "staging_remote_latest_time": "2026-01-01T00:00:00Z",
                "staging_copy_backlog": 1,
            }
        )
        notifier = mock.Mock()
        with mock.patch.object(
            staging_warning, "update_staging_runtime"
        ) as update:
            warned = staging_warning.maybe_warn_remote_stale(
                self.runtime, self.server, self.cfg, self.settings, notifier
            )

        self.assertTrue(warned)
        notifier.assert_called_once()
        self.assertFalse(notifier.call_args.kwargs["log"])
        update.assert_called_once()
        self.assertTrue(self.server.logger.warning_messages)

    def test_success_resets_cooldown(self):
        state = {
            "staging_remote_stale_warned_at": staging_warning.now_text(),
        }

        self.assertFalse(staging_warning.stale_warning_due(state, 24))
        self.assertTrue(staging_warning.stale_warning_due({}, 24))

    def test_probe_failure_does_not_suppress_stale_warning(self):
        # 复制成功但远端探测失败：backlog=0 也不能当作"远端已追平"（P1-1）
        self.runtime.config_state.config["runtime"].update(
            {
                "staging_local_latest_time": "2026-01-03 00:00:00",
                "staging_remote_latest_time": "2026-01-01T00:00:00Z",
                "staging_copy_backlog": 0,
                "staging_remote_probe_failed": True,
            }
        )
        notifier = mock.Mock()
        with mock.patch.object(staging_warning, "update_staging_runtime"):
            warned = staging_warning.maybe_warn_remote_stale(
                self.runtime, self.server, self.cfg, self.settings, notifier
            )

        self.assertTrue(warned)
        notifier.assert_called_once()

    def test_unknown_backlog_does_not_suppress_stale_warning(self):
        self.runtime.config_state.config["runtime"].update(
            {
                "staging_local_latest_time": "2026-01-03 00:00:00",
                "staging_remote_latest_time": "2026-01-01T00:00:00Z",
                "staging_copy_backlog": -1,
            }
        )
        notifier = mock.Mock()
        with mock.patch.object(staging_warning, "update_staging_runtime"):
            warned = staging_warning.maybe_warn_remote_stale(
                self.runtime, self.server, self.cfg, self.settings, notifier
            )

        self.assertTrue(warned)


if __name__ == "__main__":
    unittest.main()
