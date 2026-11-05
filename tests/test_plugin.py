try:
    from .support import (
        FakePluginServer,
        RestoreSession,
        create_runtime,
        mock,
        restore_workflow,
        unittest,
    )
except ImportError:
    from support import (
        FakePluginServer,
        RestoreSession,
        create_runtime,
        mock,
        restore_workflow,
        unittest,
    )

import threading
import time

from mcdr2restic.core.bootstrap import BootstrapResult
from mcdr2restic.core.plugin import PluginEntrypoint


def build_restore_session(phase):
    return RestoreSession(
        tasks=[],
        cfg={},
        snapshot_cfg={},
        cache_key="cache",
        language="zh_cn",
        phase=phase,
    )


class PluginUnloadRestoreTests(unittest.TestCase):
    def make_entrypoint(self):
        runtime = create_runtime()
        return runtime, PluginEntrypoint(runtime, BootstrapResult(logs=[]))

    def test_on_unload_without_restore_proceeds_immediately(self):
        runtime, entrypoint = self.make_entrypoint()
        server = FakePluginServer()

        entrypoint.on_unload(server)

        self.assertTrue(runtime.service.stopping.is_set())
        self.assertEqual(server.logger.warning_messages, [])

    def test_unload_wait_skipped_when_mcdr_stopping(self):
        runtime, entrypoint = self.make_entrypoint()
        runtime.service.stopping.set()
        runtime.restore.lock.acquire()
        runtime.restore.session = build_restore_session("stopping")
        server = FakePluginServer()

        entrypoint.wait_for_active_restore(server)

        self.assertEqual(server.logger.warning_messages, [])
        self.assertTrue(runtime.restore.lock.locked())
        runtime.restore.lock.release()

    def test_on_unload_waits_for_restore_driven_by_server_stop(self):
        runtime, entrypoint = self.make_entrypoint()
        server = FakePluginServer()
        session = build_restore_session("stopping")
        runtime.restore.lock.acquire()
        runtime.restore.session = session
        clear_func = mock.Mock()

        def server_stops_during_unload():
            time.sleep(0.2)
            with mock.patch.object(
                restore_workflow,
                "execute_restore_after_stop",
                return_value=restore_workflow.RestoreStageResult(),
            ), mock.patch.object(
                restore_workflow,
                "start_server_after_restore",
                side_effect=lambda rt, srv, sess, result: (
                    restore_workflow.handle_restore_server_startup(rt, srv)
                ),
            ):
                restore_workflow.handle_restore_server_stop(
                    runtime, server, 0, clear_func
                )

        worker = threading.Thread(target=server_stops_during_unload, daemon=True)
        worker.start()
        entrypoint.on_unload(server)
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIsNone(runtime.restore.session)
        self.assertTrue(runtime.service.stopping.is_set())
        self.assertTrue(
            any("恢复流程进行中" in m for m in server.logger.warning_messages)
        )


class ShutdownGuardTests(unittest.TestCase):
    def test_shutdown_runtime_continues_after_save_on_failure(self):
        runtime = create_runtime()
        entrypoint = PluginEntrypoint(runtime, BootstrapResult(logs=[]))
        server = FakePluginServer()

        with mock.patch(
            "mcdr2restic.core.plugin.try_force_save_on",
            side_effect=RuntimeError("boom"),
        ), mock.patch.object(entrypoint, "stop_onebot") as stop_onebot:
            entrypoint.shutdown_runtime(server, "reason")

        stop_onebot.assert_called_once()
        self.assertTrue(
            any("save-on" in m for m in server.logger.warning_messages)
        )
