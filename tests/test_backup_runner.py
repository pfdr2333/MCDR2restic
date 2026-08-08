try:
    from .support import FakePluginServer, create_runtime, mock, unittest
except ImportError:
    from support import FakePluginServer, create_runtime, mock, unittest

from mcdr2restic.backup.backup_runner import BackupRunner
from mcdr2restic.core.models import BackupCanceled, BackupRunOutcome, BackupRunStatus


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
        runner._release_backup_slot()
        self.assertFalse(runtime.backup.lock.locked())

    def test_execute_backup_run_classifies_success_cancel_and_failure(self):
        runtime, runner = self.make_runner()
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
        runtime, runner = self.make_runner()
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
        runtime, runner = self.make_runner()
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
