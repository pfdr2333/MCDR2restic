try:
    from .support import FakePluginServer, create_runtime, mock, unittest
except ImportError:
    from support import FakePluginServer, create_runtime, mock, unittest


from mcdr2restic.core.models import (
    BackupCanceled,
    BackupProblem,
    MaintenanceOutcome,
    MaintenanceRunStatus,
)
from mcdr2restic.restic.maintenance_runner import MaintenanceRunner


class MaintenanceRunnerTests(unittest.TestCase):
    def make_runner(self, runtime=None, restore_running=False):
        runtime = runtime or create_runtime()
        return runtime, MaintenanceRunner(
            runtime,
            lambda _runtime: restore_running,
            mock.Mock(),
        )

    def test_start_thread_rejects_restore_and_busy_slot(self):
        runtime, runner = self.make_runner(restore_running=True)
        server = FakePluginServer()

        self.assertFalse(runner.start_thread(server))
        self.assertFalse(runtime.backup.lock.locked())

        runtime, runner = self.make_runner()
        runtime.backup.lock.acquire()
        try:
            self.assertFalse(runner.start_thread(server))
        finally:
            runtime.backup.lock.release()

    def test_start_thread_starts_daemon_and_tracks_thread(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        fake_thread = mock.Mock()

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.threading.Thread",
            return_value=fake_thread,
        ) as thread_factory:
            self.assertTrue(runner.start_thread(server))

        thread_factory.assert_called_once()
        self.assertTrue(thread_factory.call_args.kwargs["daemon"])
        fake_thread.start.assert_called_once_with()
        self.assertIs(runtime.backup.thread, fake_thread)
        self.assertTrue(runtime.backup.lock.locked())
        runtime.release_backup_slot()
        self.assertIsNone(runtime.backup.thread)
        self.assertFalse(runtime.backup.lock.locked())

    def test_start_thread_rolls_back_slot_when_thread_start_fails(self):
        runtime, runner = self.make_runner()
        server = FakePluginServer()
        fake_thread = mock.Mock()
        fake_thread.start.side_effect = RuntimeError("cannot start new thread")

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.threading.Thread",
            return_value=fake_thread,
        ), self.assertRaises(RuntimeError):
            runner.start_thread(server)

        self.assertFalse(runtime.backup.lock.locked())
        self.assertIsNone(runtime.backup.thread)
        self.assertIsNone(runtime.backup.label)

    def test_locked_run_executes_and_releases_slot(self):
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "en_us"}
        server = FakePluginServer(language="en_us")
        runtime.backup.lock.acquire()

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body",
            return_value=MaintenanceOutcome(MaintenanceRunStatus.SUCCESS, "ok"),
        ) as run_body:
            result = runner._run_with_acquired_lock(server)

        self.assertTrue(result)
        run_body.assert_called_once()
        self.assertFalse(runtime.backup.lock.locked())
        self.assertIsNone(runtime.backup.thread)
        self.assertEqual(runtime.backup.label, None)

    def test_entry_does_not_erase_cancel_arrived_before_thread_start(self):
        # 线程入口 clear 不得抹掉拿锁与线程启动之间到达的取消请求（P1-7）
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "en_us"}
        server = FakePluginServer(language="en_us")
        runtime.backup.lock.acquire()
        runtime.backup.cancel.set()

        def fake_body(*args, **kwargs):
            self.assertTrue(runtime.backup.cancel.is_set())
            raise BackupCanceled(i18n_key="error.backup.cancel_requested")

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body",
            side_effect=fake_body,
        ):
            self.assertFalse(runner._run_with_acquired_lock(server))

        self.assertFalse(runtime.backup.lock.locked())

    def test_expected_failure_logs_clean_message_without_traceback(self):
        # 仓库冲突等预期业务错误只记干净消息，不刷堆栈（P2-2）
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "zh_cn"}
        server = FakePluginServer()

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body",
            side_effect=BackupProblem(
                i18n_key="error.backup.disk_space_low",
                path="repo",
                free_percent=9,
                min_free_percent=10,
            ),
        ):
            self.assertFalse(runner._run_with_acquired_lock(server))

        self.assertEqual(len(server.logger.error_messages), 1)
        self.assertNotIn("Traceback", server.logger.error_messages[0])

    def test_unexpected_failure_still_logs_traceback(self):
        # 非预期异常保留完整堆栈便于排查（P2-2）
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "zh_cn"}
        server = FakePluginServer()

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body",
            side_effect=RuntimeError("boom"),
        ):
            self.assertFalse(runner._run_with_acquired_lock(server))

        self.assertIn("Traceback", server.logger.error_messages[-1])

    def test_label_set_before_config_snapshot(self):
        # 等待方轮询 give_up_when 依赖 label，取快照窗口内必须已标记 maintenance（P1-6）
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "en_us"}
        server = FakePluginServer(language="en_us")
        labels_at_snapshot = []

        def fake_snapshot(app_runtime):
            labels_at_snapshot.append(app_runtime.backup.label)
            return {"language": "en_us"}

        runtime.backup.lock.acquire()
        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.get_config_snapshot",
            side_effect=fake_snapshot,
        ), mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body",
            return_value=MaintenanceOutcome(MaintenanceRunStatus.SUCCESS, "ok"),
        ):
            self.assertTrue(runner._run_with_acquired_lock(server))

        # 取配置快照时 maintenance 标记必须已就位
        self.assertEqual(labels_at_snapshot[-1], "maintenance")

    def test_run_scheduled_skips_without_blocking_when_slot_busy(self):
        runtime, runner = self.make_runner()
        runner.admin_notifier = mock.Mock()
        runtime.config_state.config = {
            "language": "en_us",
            "notification": {"notify_on_skip": True},
        }
        server = FakePluginServer(language="en_us")
        runtime.backup.lock.acquire()
        try:
            with mock.patch(
                "mcdr2restic.restic.maintenance_runner.save_config_unlocked"
            ):
                result = runner.run_scheduled(server, "2026-01-01 03:00:00")
        finally:
            runtime.backup.lock.release()

        self.assertFalse(result)
        self.assertEqual(
            runtime.config_state.config["runtime"]["last_maintenance_status"],
            "skipped",
        )
        self.assertEqual(
            runner.admin_notifier.call_args.args[0], "maintenance_skipped"
        )

    def test_failed_maintenance_notifies_and_records_status(self):
        runtime, runner = self.make_runner()
        runner.admin_notifier = mock.Mock()
        runtime.config_state.config = {
            "language": "zh_cn",
            "notification": {"notify_on_failure": True},
        }
        server = FakePluginServer()

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body",
            side_effect=BackupProblem(i18n_key="error.maintenance.failed", error="boom"),
        ), mock.patch(
            "mcdr2restic.restic.maintenance_runner.save_config_unlocked"
        ):
            result = runner._run_with_acquired_lock(server)

        self.assertFalse(result)
        self.assertEqual(
            runtime.config_state.config["runtime"]["last_maintenance_status"],
            "failed",
        )
        self.assertEqual(
            runner.admin_notifier.call_args.args[0], "maintenance_failed"
        )
        self.assertTrue(runner.admin_notifier.call_args.args[3])

