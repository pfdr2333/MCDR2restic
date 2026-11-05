try:
    from .support import FakePluginServer, create_runtime, mock, tempfile, unittest
except ImportError:
    from support import FakePluginServer, create_runtime, mock, tempfile, unittest

import threading

from mcdr2restic.backup.backup_runner import BackupRunner
from mcdr2restic.core.models import BackupCanceled, BackupRunOutcome, BackupRunStatus
from mcdr2restic.minecraft.minecraft_service import request_cancel_current_backup


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
