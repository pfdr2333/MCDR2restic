try:
    from .support import FakePluginServer, create_runtime, mock, tempfile, unittest
except ImportError:
    from support import FakePluginServer, create_runtime, mock, tempfile, unittest

import threading

from mcdr2restic.backup.backup_runner import BackupRunner
from mcdr2restic.core.models import (
    BackupCanceled,
    BackupProblem,
    BackupRunOutcome,
    BackupRunStatus,
    build_canceled_error,
)
from mcdr2restic.minecraft.minecraft_service import (
    check_canceled,
    request_cancel_current_backup,
)


class BackupRunnerTests(unittest.TestCase):
    def make_runner(self, runtime=None, restore_running=False):
        runtime = runtime or create_runtime()
        return runtime, BackupRunner(
            runtime,
            lambda _runtime: restore_running,
            mock.Mock(),
            mock.Mock(),
        )

    def test_start_thread_rejects_restore_and_busy_backup(self):
        runtime, runner = self.make_runner(restore_running=True)
        server = FakePluginServer()

        self.assertFalse(runner.start_thread(server, "manual"))
        self.assertFalse(runtime.backup.lock.locked())

        runtime, runner = self.make_runner()
        runtime.backup.lock.acquire()
        try:
            self.assertFalse(runner.start_thread(server, "manual"))
        finally:
            runtime.backup.lock.release()

    def test_start_thread_starts_daemon_and_releases_slot(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        fake_thread = mock.Mock()

        with mock.patch.object(
            __import__(
                "mcdr2restic.backup.backup_runner", fromlist=["threading"]
            ).threading,
            "Thread",
            return_value=fake_thread,
        ) as thread_factory:
            self.assertTrue(runner.start_thread(server, "manual"))

        thread_factory.assert_called_once()
        self.assertTrue(thread_factory.call_args.kwargs["daemon"])
        fake_thread.start.assert_called_once_with()
        self.assertTrue(runtime.backup.lock.locked())
        runtime.release_backup_slot()
        self.assertFalse(runtime.backup.lock.locked())

    def test_start_thread_rolls_back_slot_when_thread_start_fails(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        fake_thread = mock.Mock()
        fake_thread.start.side_effect = RuntimeError("cannot start new thread")

        with mock.patch(
            "mcdr2restic.backup.backup_runner.threading.Thread",
            return_value=fake_thread,
        ), self.assertRaises(RuntimeError):
            runner.start_thread(server, "manual")

        self.assertFalse(runtime.backup.lock.locked())
        self.assertIsNone(runtime.backup.thread)
        self.assertIsNone(runtime.backup.label)

    def test_execute_backup_run_classifies_success_cancel_and_failure(self):
        _, runner = self.make_runner()
        server = FakePluginServer()

        with mock.patch("mcdr2restic.backup.backup_runner.run_backup_body"):
            success = runner._execute_backup_run(server, {}, "manual", 0)
        self.assertEqual(success.status, BackupRunStatus.SUCCESS)

        with mock.patch(
            "mcdr2restic.backup.backup_runner.run_backup_body",
            side_effect=BackupCanceled(i18n_key="error.backup.cancel_requested"),
        ):
            canceled = runner._execute_backup_run(server, {}, "manual", 0)
        self.assertEqual(canceled.status, BackupRunStatus.CANCELED)

        with mock.patch(
            "mcdr2restic.backup.backup_runner.run_backup_body",
            side_effect=RuntimeError("failed"),
        ):
            failed = runner._execute_backup_run(server, {}, "manual", 0)
        self.assertEqual(failed.status, BackupRunStatus.FAILED)
        self.assertEqual(failed.detail, "failed")

    def test_expected_failure_logs_clean_message_without_traceback(self):
        # 磁盘不足等预期业务错误只记干净消息，不刷堆栈（P2-2）
        _, runner = self.make_runner()
        server = FakePluginServer()

        with mock.patch(
            "mcdr2restic.backup.backup_runner.run_backup_body",
            side_effect=BackupProblem(
                i18n_key="error.backup.disk_space_low",
                path="repo",
                free_percent=9,
                min_free_percent=10,
            ),
        ):
            outcome = runner._execute_backup_run(server, {}, "manual", 0)

        self.assertEqual(outcome.status, BackupRunStatus.FAILED)
        self.assertEqual(len(server.logger.error_messages), 1)
        self.assertNotIn("Traceback", server.logger.error_messages[0])
        self.assertIn("剩余空间不足", server.logger.error_messages[0])

    def test_unexpected_failure_still_logs_traceback(self):
        # 非预期异常保留完整堆栈便于排查（P2-2）
        _, runner = self.make_runner()
        server = FakePluginServer()

        with mock.patch(
            "mcdr2restic.backup.backup_runner.run_backup_body",
            side_effect=RuntimeError("boom"),
        ):
            outcome = runner._execute_backup_run(server, {}, "manual", 0)

        self.assertEqual(outcome.status, BackupRunStatus.FAILED)
        self.assertIn("Traceback", server.logger.error_messages[-1])

    def test_save_on_failure_changes_success_to_failed(self):
        _, runner = self.make_runner()
        server = FakePluginServer()
        outcome = BackupRunOutcome(BackupRunStatus.SUCCESS, "done", "", 2)

        with mock.patch(
            "mcdr2restic.backup.backup_runner.try_force_save_on",
            side_effect=RuntimeError("save-on failed"),
        ):
            result = runner._include_save_on_result(server, outcome)

        self.assertEqual(result.status, BackupRunStatus.FAILED)
        self.assertIn("save-on failed", result.detail)

    def test_save_on_failure_preserves_non_success_status_and_merges_detail(self):
        _, runner = self.make_runner()
        server = FakePluginServer()
        outcome = BackupRunOutcome(BackupRunStatus.CANCELED, "canceled", "original", 2)

        with mock.patch(
            "mcdr2restic.backup.backup_runner.try_force_save_on",
            side_effect=RuntimeError("save-on failed"),
        ):
            result = runner._include_save_on_result(server, outcome)

        self.assertEqual(result.status, BackupRunStatus.CANCELED)
        self.assertTrue(result.detail.startswith("original; "))
        self.assertIn("save-on failed", result.detail)

    def test_run_locked_registers_running_thread_for_cancel(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        seen = {}

        def fake_body(*args, **kwargs):
            seen["thread"] = runtime.backup.thread

        with tempfile.TemporaryDirectory() as data_dir:
            server.get_data_folder = lambda: data_dir
            with mock.patch(
                "mcdr2restic.backup.backup_runner.run_backup_body",
                side_effect=fake_body,
            ):
                self.assertTrue(runner.run_locked(server, "manual"))

        self.assertIs(seen["thread"], threading.current_thread())
        self.assertFalse(runtime.backup.lock.locked())
        self.assertIsNone(runtime.backup.thread)

    def test_acquire_backup_slot_gives_up_when_plugin_stopping(self):
        runtime, runner = self.make_runner()
        runtime.backup.lock.acquire()
        runtime.backup.label = "maintenance"
        runtime.service.stopping.set()
        try:
            self.assertFalse(runner._acquire_backup_slot(wait_for_maintenance=True))
        finally:
            runtime.backup.lock.release()

    def test_run_locked_skips_when_stopping_while_waiting_for_maintenance(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        runtime.backup.lock.acquire()
        runtime.backup.label = "maintenance"
        runtime.service.stopping.set()
        try:
            with mock.patch("mcdr2restic.backup.backup_runner.run_backup_body") as body:
                self.assertFalse(runner.run_locked(server, "scheduled", wait_for_slot=True))
            body.assert_not_called()
        finally:
            runtime.backup.lock.release()
        self.assertTrue(
            any("插件正在停止" in m for m in server.logger.info_messages)
        )

    def test_unload_cancels_long_running_deferred_backup(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        started = threading.Event()

        def long_body(*args, **kwargs):
            started.set()
            self.assertTrue(runtime.backup.cancel.wait(timeout=10))
            raise BackupCanceled(i18n_key="error.backup.cancel_requested")

        with tempfile.TemporaryDirectory() as data_dir:
            server.get_data_folder = lambda: data_dir
            with mock.patch(
                "mcdr2restic.backup.backup_runner.run_backup_body",
                side_effect=long_body,
            ):
                self.assertTrue(runner.start_deferred_thread(server, "scheduled"))
                self.assertTrue(started.wait(timeout=5))
                # 模拟卸载流程：先置停止事件，再请求取消并等待备份线程退出
                runtime.service.stopping.set()
                request_cancel_current_backup(runtime, "unload")
                thread = runtime.backup.thread
                self.assertIsNotNone(thread)
                thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(runtime.backup.lock.locked())
        self.assertIsNone(runtime.backup.label)
        self.assertEqual(
            runtime.config_state.config["runtime"]["last_backup_status"],
            BackupRunStatus.CANCELED.value,
        )

    def test_entry_does_not_erase_cancel_arrived_before_thread_start(self):
        # cancel_running_backup 先 set 再 join，线程入口 clear 不得抹掉窗口内的取消（P1-7）
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        runtime.backup.lock.acquire()
        runtime.backup.cancel.set()

        def fake_body(*args, **kwargs):
            # 入口不得清除取消标记，备份体首个检查点应立即放弃执行
            self.assertTrue(runtime.backup.cancel.is_set())
            raise BackupCanceled(i18n_key="error.backup.cancel_requested")

        with tempfile.TemporaryDirectory() as data_dir:
            server.get_data_folder = lambda: data_dir
            with mock.patch(
                "mcdr2restic.backup.backup_runner.run_backup_body",
                side_effect=fake_body,
            ):
                runner._run_with_acquired_lock(server, "manual")

        self.assertFalse(runtime.backup.lock.locked())
        self.assertEqual(
            runtime.config_state.config["runtime"]["last_backup_status"],
            BackupRunStatus.CANCELED.value,
        )

    def test_run_with_acquired_lock_survives_notify_crash(self):
        runtime, runner = self.make_runner()
        runner.admin_notifier = mock.Mock(side_effect=RuntimeError("onebot broken"))
        server = FakePluginServer()

        with tempfile.TemporaryDirectory() as data_dir:
            server.get_data_folder = lambda: data_dir
            runtime.backup.lock.acquire()
            try:
                with mock.patch("mcdr2restic.backup.backup_runner.run_backup_body"):
                    runner._run_with_acquired_lock(server, "manual")
            finally:
                pass

        self.assertFalse(runtime.backup.lock.locked())
        self.assertEqual(
            runtime.config_state.config["runtime"]["last_backup_status"],
            BackupRunStatus.SUCCESS.value,
        )
        self.assertTrue(
            any("onebot broken" in m for m in server.logger.warning_messages)
        )

    def test_scheduled_start_requeues_when_gate_reopens_after_slot_wait(self):
        # 等槽位期间人数回升：不得直接进入 save-off，而是归还槽位回到门控等待（P2-1）
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        occurrence = mock.Mock(scheduled_at="2026-01-01 00:00:00")
        pending = runtime.register_pending_backup(occurrence.scheduled_at, None)

        with (
            mock.patch.object(
                runner, "_prepare_scheduled_start", side_effect=[True, False]
            ) as prepare,
            mock.patch.object(runner, "_wait_for_backup_slot", return_value=None),
            mock.patch.object(runner, "_player_gate_reopened", return_value=True),
            mock.patch.object(runner, "_run_with_acquired_lock") as run,
        ):
            self.assertFalse(
                runner._await_slot_with_gate(server, occurrence, pending)
            )

        self.assertEqual(prepare.call_count, 2)
        run.assert_not_called()

    def test_gate_recheck_exception_releases_backup_slot(self):
        # 拿到槽位后复检抛错（如状态落盘失败）必须释放槽位，否则后续备份永久卡死
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        occurrence = mock.Mock(scheduled_at="2026-01-01 00:00:00")
        pending = runtime.register_pending_backup(occurrence.scheduled_at, None)

        def acquire_slot(_server, _pending):
            runtime.backup.lock.acquire()

        with (
            mock.patch.object(runner, "_prepare_scheduled_start", return_value=True),
            mock.patch.object(
                runner, "_wait_for_backup_slot", side_effect=acquire_slot
            ),
            mock.patch.object(
                runner,
                "_player_gate_reopened",
                side_effect=OSError("state write failed"),
            ),
            self.assertRaises(OSError),
        ):
            runner._await_slot_with_gate(server, occurrence, pending)

        self.assertFalse(runtime.backup.lock.locked())

    def test_scheduled_start_proceeds_when_gate_still_open(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        occurrence = mock.Mock(scheduled_at="2026-01-01 00:00:00")
        pending = runtime.register_pending_backup(occurrence.scheduled_at, None)

        with (
            mock.patch.object(runner, "_prepare_scheduled_start", return_value=True),
            mock.patch.object(runner, "_wait_for_backup_slot", return_value=None),
            mock.patch.object(runner, "_player_gate_reopened", return_value=False),
            mock.patch.object(runner, "_scheduled_start_allowed", return_value=True),
        ):
            self.assertTrue(
                runner._await_slot_with_gate(server, occurrence, pending)
            )

    def test_player_gate_reopened_compares_current_count(self):
        from mcdr2restic.backup import backup_runner

        runtime, runner = self.make_runner()
        runtime.config_state.config = {
            "schedule": {"backup_player_gate_max_online": 5}
        }
        server = FakePluginServer()

        with mock.patch.object(
            backup_runner, "resolve_online_player_count", return_value=3
        ):
            self.assertFalse(runner._player_gate_reopened(server))
        with mock.patch.object(
            backup_runner, "resolve_online_player_count", return_value=5
        ):
            self.assertTrue(runner._player_gate_reopened(server))
        self.assertTrue(
            any("门控阈值" in message for message in server.logger.info_messages)
        )

    def test_success_records_success_anchor_and_resets_gate_warning(self):
        runtime, runner = self.make_runner()
        runtime.config_state.config = {
            "runtime": {
                "last_success_backup_end_time": "2026-01-01 00:00:00",
                "stale_backup_warned_at": "2026-01-01 01:00:00",
                "gate_deferral_count_since_success": 4,
                "gate_stall_baseline_seconds": 1200,
                "gate_stall_started_at": "2026-01-01 01:00:00",
            }
        }
        server = FakePluginServer()

        with mock.patch("mcdr2restic.backup.backup_runner.save_config_unlocked"):
            runner._record_backup_finished(
                server,
                BackupRunOutcome(BackupRunStatus.SUCCESS, "ok", "", 12),
                "2026-01-02 03:04:05",
            )

        runtime_state = runtime.config_state.config["runtime"]
        self.assertEqual(
            runtime_state["last_success_backup_end_time"], "2026-01-02 03:04:05"
        )
        self.assertEqual(runtime_state["last_backup_duration_seconds"], 12)
        self.assertIsNone(runtime_state["stale_backup_warned_at"])
        self.assertEqual(runtime_state["gate_deferral_count_since_success"], 0)
        self.assertIsNone(runtime_state["gate_stall_baseline_seconds"])
        self.assertIsNone(runtime_state["gate_stall_started_at"])
        self.assertEqual(runtime_state["backup_running_seconds_since_success"], 0)
        self.assertIsNotNone(runtime_state["server_running_since_epoch"])

    def test_failed_run_keeps_previous_success_anchor(self):
        runtime, runner = self.make_runner()
        runtime.config_state.config = {
            "runtime": {"last_success_backup_end_time": "2026-01-01 00:00:00"}
        }
        server = FakePluginServer()

        with mock.patch("mcdr2restic.backup.backup_runner.save_config_unlocked"):
            runner._record_backup_finished(
                server,
                BackupRunOutcome(BackupRunStatus.FAILED, "boom", "detail", 3),
                "2026-01-02 03:04:05",
            )

        runtime_state = runtime.config_state.config["runtime"]
        self.assertEqual(
            runtime_state["last_success_backup_end_time"], "2026-01-01 00:00:00"
        )
        self.assertEqual(runtime_state["last_backup_status"], "failed")

    def test_record_backup_skip_does_not_pollute_last_backup_status(self):
        runtime, runner = self.make_runner()
        runtime.config_state.config = {
            "runtime": {"last_backup_status": "success"}
        }

        with mock.patch("mcdr2restic.backup.backup_runner.save_config_unlocked"):
            runner.record_backup_skip("2026-01-02 03:00:00", "min_gap", "too soon")

        runtime_state = runtime.config_state.config["runtime"]
        self.assertEqual(runtime_state["last_backup_status"], "success")
        self.assertEqual(runtime_state["last_skip_scheduled_time"], "2026-01-02 03:00:00")
        self.assertEqual(runtime_state["last_skip_reason"], "too soon")

    def test_gate_interrupt_reason_reaches_canceled_outcome_and_notification(self):
        # 门控中断曾只留下"收到停止请求"，通知无法说明备份为何被取消
        runtime, runner = self.make_runner()
        runtime.service.server = FakePluginServer()
        runtime.config_state.config = {
            "notification": {"notify_on_failure": True},
            "runtime": {},
        }
        gate_reason = "在线人数持续超过门控阈值（8/5 达 600 秒），中断备份以恢复世界自动保存"

        with mock.patch(
            "mcdr2restic.minecraft.minecraft_service.terminate_process"
        ), mock.patch(
            "mcdr2restic.minecraft.minecraft_service.warn_if_termination_failed"
        ):
            request_cancel_current_backup(runtime, gate_reason)

        with self.assertRaises(BackupCanceled) as raised:
            check_canceled(runtime)
        self.assertEqual(str(raised.exception), gate_reason)

        with mock.patch(
            "mcdr2restic.backup.backup_runner.run_backup_body",
            side_effect=build_canceled_error(runtime.backup),
        ):
            outcome = runner._execute_backup_run(
                runtime.service.server, runtime.config_state.config, "定时", 0
            )

        self.assertEqual(outcome.status, BackupRunStatus.CANCELED)
        self.assertEqual(outcome.detail, gate_reason)
        self.assertIn(gate_reason, outcome.message)

        runner._notify_backup_finished(
            runtime.config_state.config,
            "定时",
            "2026-01-02 03:00:00",
            "2026-01-02 03:10:00",
            outcome,
        )
        template_key, values = runner.admin_notifier.call_args.args[:2]
        self.assertEqual(template_key, "backup_failure")
        self.assertEqual(values["detail"], gate_reason)

    def test_canceled_outcome_falls_back_to_generic_reason_without_recorded_cause(self):
        _, runner = self.make_runner()
        server = FakePluginServer()

        with mock.patch(
            "mcdr2restic.backup.backup_runner.run_backup_body",
            side_effect=BackupCanceled(i18n_key="error.backup.cancel_requested"),
        ):
            outcome = runner._execute_backup_run(server, {}, "manual", 0)

        self.assertEqual(outcome.status, BackupRunStatus.CANCELED)
        self.assertEqual(outcome.detail, "收到停止请求")

