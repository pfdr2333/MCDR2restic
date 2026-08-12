try:
    from .support import FakePluginServer, create_runtime, mock, unittest
except ImportError:
    from support import FakePluginServer, create_runtime, mock, unittest

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
        runner._release_slot()
        self.assertIsNone(runtime.backup.thread)
        self.assertFalse(runtime.backup.lock.locked())

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
