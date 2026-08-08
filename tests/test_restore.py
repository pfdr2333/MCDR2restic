try:
    from .support import (
        FakePluginServer,
        RestoreSession,
        clear_restore_tasks,
        create_runtime,
        normalize_restore_include_path,
        os,
        restore_workflow,
        unittest,
    )
except ImportError:
    from support import (
        FakePluginServer,
        RestoreSession,
        clear_restore_tasks,
        create_runtime,
        normalize_restore_include_path,
        os,
        restore_workflow,
        unittest,
    )
class RestoreWorkflowTests(unittest.TestCase):
    def test_normalize_restore_include_path_returns_restic_absolute_path(self):
        restic_cfg = {"working_directory": os.getcwd()}
        self.assertEqual(
            normalize_restore_include_path("world/region", restic_cfg, "!!backup"),
            "/world/region",
        )

    def test_restore_startup_finishes_session_and_releases_lock(self):
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.restore.lock.acquire()
        runtime.restore.session = RestoreSession(
            tasks=[],
            cfg={},
            snapshot_cfg={},
            cache_key="cache",
            language="zh_cn",
            phase="starting",
            started_at="now",
        )

        restore_workflow.handle_restore_server_startup(runtime, server)

        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIn("恢复流程完成", server.logger.info_messages[0])

    def test_restore_startup_timeout_finishes_session_and_releases_lock(self):
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.restore.lock.acquire()
        runtime.restore.session = RestoreSession(
            [], {"restore": {}}, {}, "cache", "zh_cn", "starting", "now"
        )

        finished = restore_workflow.finish_restore_start_timeout_if_still_starting(
            runtime, server, restore_workflow.RestoreStageResult(), 1
        )

        self.assertTrue(finished)
        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIn("未完成", server.logger.warning_messages[0])

    def test_restore_startup_timeout_ignores_newer_session(self):
        runtime = create_runtime()
        server = FakePluginServer()
        old_session = RestoreSession(
            [], {"restore": {}}, {}, "cache-old", "zh_cn", "starting", "old"
        )
        new_session = RestoreSession(
            [], {"restore": {}}, {}, "cache-new", "zh_cn", "starting", "new"
        )
        runtime.restore.lock.acquire()
        runtime.restore.session = new_session

        finished = restore_workflow.finish_restore_start_timeout_if_still_starting(
            runtime,
            server,
            restore_workflow.RestoreStageResult(),
            1,
            expected_session=old_session,
        )

        self.assertFalse(finished)
        self.assertIs(runtime.restore.session, new_session)
        self.assertTrue(runtime.restore.lock.locked())
        self.assertEqual(server.logger.warning_messages, [])

    def test_restore_server_stop_during_starting_finishes_session(self):
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.restore.lock.acquire()
        runtime.restore.session = RestoreSession(
            [], {}, {}, "cache", "zh_cn", "starting", "now"
        )

        handled = restore_workflow.handle_restore_server_stop(
            runtime, server, 1, clear_restore_tasks
        )

        self.assertTrue(handled)
        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIn("再次停止", server.logger.warning_messages[0])
