try:
    from .support import FakePluginServer, create_runtime, mock, unittest
except ImportError:
    from support import FakePluginServer, create_runtime, mock, unittest

import threading

from mcdr2restic.core.models import BackupCanceled
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

    def test_run_waiting_executes_and_releases_slot(self):
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "en_us"}
        server = FakePluginServer(language="en_us")

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body"
        ) as run_body:
            result = runner.run_waiting(server)

        self.assertTrue(result)
        run_body.assert_called_once()
        self.assertFalse(runtime.backup.lock.locked())
        self.assertIsNone(runtime.backup.thread)
        self.assertEqual(runtime.backup.label, None)

    def test_run_waiting_gives_up_when_plugin_stopping(self):
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "en_us"}
        server = FakePluginServer(language="en_us")
        runtime.backup.lock.acquire()
        runtime.service.stopping.set()
        try:
            with mock.patch(
                "mcdr2restic.restic.maintenance_runner.run_maintenance_body"
            ) as run_body:
                self.assertFalse(runner.run_waiting(server))
            run_body.assert_not_called()
        finally:
            runtime.backup.lock.release()
        self.assertTrue(any("stopping" in m for m in server.logger.info_messages))

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

    def test_label_set_before_config_snapshot(self):
        # 等待方轮询 give_up_when 依赖 label，取快照窗口内必须已标记 maintenance（P1-6）
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "en_us"}
        server = FakePluginServer(language="en_us")
        labels_at_snapshot = []

        def fake_snapshot(app_runtime):
            labels_at_snapshot.append(app_runtime.backup.label)
            return {"language": "en_us"}

        with mock.patch(
            "mcdr2restic.restic.maintenance_runner.get_config_snapshot",
            side_effect=fake_snapshot,
        ), mock.patch(
            "mcdr2restic.restic.maintenance_runner.run_maintenance_body"
        ):
            self.assertTrue(runner.run_waiting(server))

        # 首次快照发生在拿锁前（run_waiting 读语言），拿锁后的快照必须已带标记
        self.assertEqual(labels_at_snapshot[-1], "maintenance")

    def test_waiting_maintenance_thread_never_runs_after_stop(self):
        runtime, runner = self.make_runner()
        runtime.config_state.config = {"language": "en_us"}
        server = FakePluginServer(language="en_us")
        # 模拟长备份占用槽位，卸载发生在维护线程等待期间
        runtime.backup.lock.acquire()
        result = {}

        def waiting():
            with mock.patch(
                "mcdr2restic.restic.maintenance_runner.run_maintenance_body"
            ) as run_body:
                result["value"] = runner.run_waiting(server)
                result["body_calls"] = run_body.call_count

        worker = threading.Thread(target=waiting, daemon=True)
        worker.start()
        runtime.service.stopping.set()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result["value"], False)
        self.assertEqual(result["body_calls"], 0)
        # 锁始终未被维护线程抢走
        self.assertTrue(runtime.backup.lock.locked())
        runtime.backup.lock.release()
