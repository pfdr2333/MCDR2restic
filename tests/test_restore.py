try:
    from .support import (
        REPO_ROOT,
        BackupProblem,
        FakePluginServer,
        RestoreSession,
        clear_restore_tasks,
        create_runtime,
        normalize_restore_include_path,
        restore_workflow,
        unittest,
    )
except ImportError:
    from support import (
        REPO_ROOT,
        BackupProblem,
        FakePluginServer,
        RestoreSession,
        clear_restore_tasks,
        create_runtime,
        normalize_restore_include_path,
        restore_workflow,
        unittest,
    )


class RestoreWorkflowTests(unittest.TestCase):
    def test_normalize_restore_include_path_returns_restic_absolute_path(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}
        self.assertEqual(
            normalize_restore_include_path("world/region", restic_cfg, "!!backup"),
            "/world/region",
        )

    def test_restore_snapshot_rejects_empty_and_whitespace_values(self):
        for value, expected_key in (
            ("", "error.restore.snapshot_empty"),
            ("snapshot with spaces", "error.restore.snapshot_whitespace"),
        ):
            with self.assertRaises(BackupProblem) as error:
                restore_workflow.normalize_restore_snapshot(value)

            self.assertEqual(error.exception.i18n_key, expected_key)

    def test_restore_include_path_rejects_parent_and_external_paths(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        for value, expected_key in (
            ("../outside", "error.restore.path_parent_reference"),
            (r"C:\\outside", "error.restore.path_outside_workdir"),
        ):
            with self.assertRaises(BackupProblem) as error:
                normalize_restore_include_path(value, restic_cfg, "!!restic")

            self.assertEqual(error.exception.i18n_key, expected_key)

    def test_restore_apply_rejection_checks_tasks_backup_and_server_state(self):
        runtime = create_runtime()
        server = FakePluginServer()
        translate = lambda key: key

        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime, server, translate, [], lambda _: False, lambda *_: True
            ),
            "error.restore.no_tasks",
        )
        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime, server, translate, [{}], lambda _: True, lambda *_: True
            ),
            "error.restore.backup_running",
        )
        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime, server, translate, [{}], lambda _: False, lambda *_: False
            ),
            "error.restore.minecraft_not_ready",
        )
        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime, server, translate, [{}], lambda _: False, lambda *_: True
            ),
            "",
        )

    def test_pre_restore_config_adds_tag_without_mutating_original(self):
        cfg = {
            "restic": {
                "backup_command": ["backup", "world"],
                "maintenance_commands": [["forget"]],
            },
            "restore": {"pre_restore_backup_tag": "protect"},
        }

        result = restore_workflow.build_pre_restore_backup_config(cfg)

        self.assertEqual(
            result["restic"]["backup_command"], ["backup", "world", "--tag", "protect"]
        )
        self.assertEqual(result["restic"]["maintenance_commands"], [])
        self.assertEqual(cfg["restic"]["backup_command"], ["backup", "world"])

    def test_build_restore_command_supports_full_and_included_tasks(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        self.assertEqual(
            restore_workflow.build_restore_command(
                restic_cfg, {"snapshot": "abc", "item_type": "full"}
            ),
            ["restore", "abc", "--target", str(REPO_ROOT)],
        )
        self.assertEqual(
            restore_workflow.build_restore_command(
                restic_cfg,
                {
                    "snapshot": "abc",
                    "item_type": "file",
                    "include_path": "/world/region",
                },
            )[-2:],
            ["--include", "/world/region"],
        )

        with self.assertRaises(BackupProblem) as error:
            restore_workflow.build_restore_command(
                restic_cfg, {"snapshot": "abc", "item_type": "unknown"}
            )
        self.assertEqual(error.exception.i18n_key, "error.restore.unknown_task_type")

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
