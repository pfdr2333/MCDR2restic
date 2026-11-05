import os
try:
    from .support import (
        REPO_ROOT,
        BackupProblem,
        FakePluginServer,
        RestoreSession,
        clear_restore_tasks,
        create_runtime,
        mock,
        normalize_restore_include_path,
        restore_paths,
        restore_workflow,
        tempfile,
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
        mock,
        normalize_restore_include_path,
        restore_paths,
        restore_workflow,
        tempfile,
        unittest,
    )


def build_restore_session(**overrides):
    fields = {
        "tasks": [],
        "cfg": {},
        "snapshot_cfg": {},
        "cache_key": "cache",
        "language": "zh_cn",
        "phase": "restoring",
    }
    fields.update(overrides)
    return RestoreSession(**fields)


class RestoreTargetDirectoryTests(unittest.TestCase):
    def test_normalize_target_resolves_relative_to_workdir(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        result = restore_paths.normalize_restore_target_directory(
            "restore-out", restic_cfg
        )

        self.assertEqual(
            result, str(REPO_ROOT.joinpath("restore-out").resolve())
        )

    def test_normalize_target_accepts_absolute_path_outside_workdir(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with tempfile.TemporaryDirectory() as target:
            result = restore_paths.normalize_restore_target_directory(
                target, restic_cfg
            )

            self.assertEqual(
                result, str(REPO_ROOT.joinpath(target).resolve())
            )

    def test_normalize_target_rejects_empty_value(self):
        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "  ", {"working_directory": str(REPO_ROOT)}
            )

        self.assertEqual(error.exception.i18n_key, "error.restore.target_empty")

    def test_normalize_target_rejects_working_directory_itself(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                str(REPO_ROOT), restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key,
            "error.restore.target_is_working_directory",
        )

    def test_normalize_target_rejects_target_inside_repository(self):
        restic_cfg = {
            "working_directory": str(REPO_ROOT),
            "repository": "./restic-repo",
        }
        target = REPO_ROOT.joinpath("restic-repo", "data")

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                str(target), restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_inside_repository"
        )

    def test_normalize_target_rejects_target_inside_backup_source(self):
        restic_cfg = {
            "working_directory": str(REPO_ROOT),
            "backup_command": ["backup", "./server/world"],
        }
        target = REPO_ROOT.joinpath("server", "world", "restore-out")

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                str(target), restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key,
            "error.restore.target_inside_backup_sources",
        )

    def test_normalize_target_allows_sibling_directory_near_repository(self):
        restic_cfg = {
            "working_directory": str(REPO_ROOT),
            "repository": "./restic-repo",
        }

        result = restore_paths.normalize_restore_target_directory(
            "restore-out", restic_cfg
        )

        self.assertEqual(
            result, str(REPO_ROOT.joinpath("restore-out").resolve())
        )

    def test_normalize_target_rejects_parent_of_working_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory("..", restic_cfg)

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_contains_protected"
        )

    @unittest.skipUnless(os.name == "nt", "drive root only exists on Windows")
    def test_normalize_target_rejects_drive_root(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory("C:\\", restic_cfg)

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_contains_protected"
        )

    @unittest.skipUnless(os.name == "nt", "verbatim prefix only exists on Windows")
    def test_normalize_target_rejects_verbatim_working_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "\\\\?\\" + str(REPO_ROOT), restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_is_working_directory"
        )

    @unittest.skipUnless(os.name == "nt", "verbatim prefix only exists on Windows")
    def test_normalize_target_rejects_verbatim_parent_of_working_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "\\\\?\\" + str(REPO_ROOT.parent), restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_contains_protected"
        )

    @unittest.skipUnless(os.name == "nt", "verbatim prefix only exists on Windows")
    def test_normalize_target_rejects_verbatim_drive_root(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "\\\\?\\C:\\", restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_contains_protected"
        )

    @unittest.skipUnless(os.name == "nt", "admin share UNC is Windows-specific")
    def test_normalize_target_rejects_admin_share_unc_of_working_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "\\\\localhost\\c$\\" + str(REPO_ROOT)[3:], restic_cfg
            )

        # 管理共享写法整体拒绝（P0-6），不再映射回盘符后做等价比较
        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_admin_share_unc"
        )

    @unittest.skipUnless(os.name == "nt", "admin share UNC is Windows-specific")
    def test_normalize_target_rejects_foreign_host_admin_share_unc(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        # 主机名枚举无法覆盖局域网 IP/FQDN 别名（P0-6 残余），按写法拒绝
        for host in ("192.168.255.254", "unknown-host.example.com"):
            with self.assertRaises(BackupProblem) as error:
                restore_paths.normalize_restore_target_directory(
                    "\\\\" + host + "\\c$\\" + str(REPO_ROOT)[3:], restic_cfg
                )

            self.assertEqual(
                error.exception.i18n_key, "error.restore.target_admin_share_unc"
            )

    @unittest.skipUnless(os.name == "nt", "admin share UNC is Windows-specific")
    def test_normalize_target_rejects_admin_share_unc_of_unrelated_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        # 即使目标与受保护目录无关，管理共享写法本身也不放行
        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "\\\\localhost\\c$\\unrelated-restore-target", restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_admin_share_unc"
        )

    @unittest.skipUnless(os.name == "nt", "verbatim UNC prefix is Windows-specific")
    def test_normalize_target_rejects_verbatim_unc_of_working_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "\\\\?\\UNC\\localhost\\c$\\" + str(REPO_ROOT)[3:], restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_admin_share_unc"
        )

    @unittest.skipUnless(os.name == "nt", "verbatim UNC prefix is Windows-specific")
    def test_normalize_target_rejects_verbatim_unc_foreign_host(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                "\\\\?\\UNC\\192.168.255.254\\c$\\" + str(REPO_ROOT)[3:], restic_cfg
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_admin_share_unc"
        )

    @unittest.skipUnless(os.name == "nt", "UNC share is Windows-specific")
    def test_normalize_target_accepts_regular_unc_share(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        # 普通共享（非 X$ 管理共享形式）不受写法规则影响
        result = restore_paths.normalize_restore_target_directory(
            "\\\\fileserver\\backup-target\\restore-here", restic_cfg
        )

        self.assertTrue(result)

    @unittest.skipUnless(os.name == "nt", "verbatim prefix only exists on Windows")
    def test_normalize_target_accepts_verbatim_unrelated_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        with tempfile.TemporaryDirectory() as target:
            result = restore_paths.normalize_restore_target_directory(
                "\\\\?\\" + target, restic_cfg
            )

            self.assertEqual(result, "\\\\?\\" + target)

    def test_normalize_target_rejects_target_containing_data_dir(self):
        restic_cfg = {"working_directory": str(REPO_ROOT.joinpath("restic"))}
        data_dir = REPO_ROOT.joinpath("config", "mcdr2restic")

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                str(REPO_ROOT.joinpath("config")), restic_cfg, data_dir=str(data_dir)
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_contains_protected"
        )

    def test_normalize_target_rejects_data_dir_itself(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}
        data_dir = REPO_ROOT.joinpath("config", "mcdr2restic")

        with self.assertRaises(BackupProblem) as error:
            restore_paths.normalize_restore_target_directory(
                str(data_dir), restic_cfg, data_dir=str(data_dir)
            )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_contains_protected"
        )

    def test_normalize_target_allows_unrelated_directory_with_data_dir(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}
        data_dir = REPO_ROOT.joinpath("config", "mcdr2restic")

        with tempfile.TemporaryDirectory() as target:
            result = restore_paths.normalize_restore_target_directory(
                target, restic_cfg, data_dir=str(data_dir)
            )

            self.assertEqual(
                result, str(REPO_ROOT.joinpath(target).resolve())
            )


class RestoreTargetCommandTests(unittest.TestCase):
    def test_build_restore_command_uses_task_target_directory(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        self.assertEqual(
            restore_workflow.build_restore_command(
                restic_cfg,
                {
                    "snapshot": "abc",
                    "item_type": "full",
                    "target_directory": r"D:\restore-out",
                },
            ),
            ["restore", "abc", "--target", r"D:\restore-out"],
        )

    def test_build_restore_command_keeps_workdir_without_target(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        self.assertEqual(
            restore_workflow.build_restore_command(
                restic_cfg, {"snapshot": "abc", "item_type": "full"}
            ),
            ["restore", "abc", "--target", str(REPO_ROOT)],
        )

    def test_build_restore_command_keeps_include_for_file_tasks(self):
        restic_cfg = {"working_directory": str(REPO_ROOT)}

        args = restore_workflow.build_restore_command(
            restic_cfg,
            {
                "snapshot": "abc",
                "item_type": "file",
                "include_path": "/world/level.dat",
                "target_directory": r"D:\restore-out",
            },
        )

        self.assertEqual(
            args[-4:], ["--target", r"D:\restore-out", "--include", "/world/level.dat"]
        )

    def test_is_direct_restore_session_requires_target_on_every_task(self):
        targeted = {"snapshot": "abc", "item_type": "full", "target_directory": "/out"}
        plain = {"snapshot": "abc", "item_type": "full"}

        self.assertFalse(restore_workflow.is_direct_restore_session([]))
        self.assertFalse(restore_workflow.is_direct_restore_session([plain]))
        self.assertFalse(
            restore_workflow.is_direct_restore_session([targeted, plain])
        )
        self.assertTrue(restore_workflow.is_direct_restore_session([targeted]))

    def test_execute_restore_tasks_rechecks_target_with_current_config(self):
        runtime = create_runtime()
        server = FakePluginServer()
        executed = mock.Mock()

        with tempfile.TemporaryDirectory() as changed_source:
            session = build_restore_session(
                tasks=[
                    {
                        "id": 1,
                        "snapshot": "abc",
                        "item_type": "full",
                        "include_path": "/",
                        "target_directory": changed_source,
                    }
                ]
            )
            restic_cfg = {
                "working_directory": str(REPO_ROOT),
                "backup_command": ["backup", changed_source],
            }
            with mock.patch.object(
                restore_workflow, "run_restic_command_with_lock_recovery", executed
            ), mock.patch.object(
                restore_workflow, "ensure_default_restic_executable_available"
            ), self.assertRaises(BackupProblem) as error:
                restore_workflow.execute_restore_tasks(
                    runtime, server, restic_cfg, session
                )

        self.assertEqual(
            error.exception.i18n_key, "error.restore.target_inside_backup_sources"
        )
        executed.assert_not_called()


class RestoreDirectStageTests(unittest.TestCase):
    def test_direct_restore_success_clears_session_and_queue(self):
        runtime = create_runtime()
        server = FakePluginServer()
        session = build_restore_session(
            tasks=[
                {
                    "id": 1,
                    "snapshot": "abc",
                    "item_type": "full",
                    "include_path": "/",
                    "target_directory": r"D:\restore-out",
                }
            ]
        )
        runtime.restore.lock.acquire()
        runtime.restore.session = session
        clear_func = mock.Mock()

        with mock.patch.object(restore_workflow, "execute_restore_tasks"):
            restore_workflow.run_direct_restore_stage(
                runtime, server, session, clear_func
            )

        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        clear_func.assert_called_once_with(server, session.snapshot_cfg, "cache")
        self.assertIn("恢复任务全部完成", server.logger.info_messages[-1])

    def test_direct_restore_failure_keeps_queue_and_releases_lock(self):
        runtime = create_runtime()
        server = FakePluginServer()
        session = build_restore_session(
            tasks=[
                {
                    "id": 1,
                    "snapshot": "abc",
                    "item_type": "full",
                    "include_path": "/",
                    "target_directory": r"D:\restore-out",
                }
            ]
        )
        runtime.restore.lock.acquire()
        runtime.restore.session = session
        clear_func = mock.Mock()

        with mock.patch.object(
            restore_workflow, "execute_restore_tasks", side_effect=RuntimeError("boom")
        ):
            restore_workflow.run_direct_restore_stage(
                runtime, server, session, clear_func
            )

        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        clear_func.assert_not_called()
        self.assertIn("恢复任务执行失败", server.logger.warning_messages[0])
        self.assertIn("boom", server.logger.warning_messages[0])

    def test_restore_after_stop_logs_starting_server_after_tasks(self):
        runtime = create_runtime()
        server = FakePluginServer()
        session = build_restore_session(
            cfg={"restore": {}},
            tasks=[
                {"id": 1, "snapshot": "abc", "item_type": "full", "include_path": "/"}
            ],
        )
        runtime.restore.lock.acquire()
        runtime.restore.session = session
        clear_func = mock.Mock()

        with mock.patch.object(
            restore_workflow,
            "execute_restore_after_stop",
            return_value=restore_workflow.RestoreStageResult(),
        ), mock.patch.object(
            restore_workflow, "start_server_after_restore"
        ):
            restore_workflow.run_restore_after_stop_stage(
                runtime, server, clear_func
            )

        self.assertTrue(
            any(
                "正在启动 Minecraft 服务端" in message
                for message in server.logger.info_messages
            )
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

        def translate(key):
            return key

        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime,
                server,
                translate,
                [],
                restore_workflow.RestoreReadinessProviders(
                    backup_running=lambda _: False, mc_ready=lambda *_: True
                ),
            ),
            "error.restore.no_tasks",
        )
        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime,
                server,
                translate,
                [{}],
                restore_workflow.RestoreReadinessProviders(
                    backup_running=lambda _: True, mc_ready=lambda *_: True
                ),
            ),
            "error.restore.backup_running",
        )
        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime,
                server,
                translate,
                [{}],
                restore_workflow.RestoreReadinessProviders(
                    backup_running=lambda _: False, mc_ready=lambda *_: False
                ),
            ),
            "error.restore.minecraft_not_ready",
        )
        self.assertEqual(
            restore_workflow.get_restore_apply_rejection(
                runtime,
                server,
                translate,
                [{}],
                restore_workflow.RestoreReadinessProviders(
                    backup_running=lambda _: False, mc_ready=lambda *_: True
                ),
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
            [], {"restore": {}}, {}, "cache", "zh_cn", "starting"
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
            [], {"restore": {}}, {}, "cache-old", "zh_cn", "starting"
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
            [], {}, {}, "cache", "zh_cn", "starting"
        )

        handled = restore_workflow.handle_restore_server_stop(
            runtime, server, 1, clear_restore_tasks
        )

        self.assertTrue(handled)
        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())
        self.assertIn("再次停止", server.logger.warning_messages[0])


class RestoreSkipSafetyTests(unittest.TestCase):
    def test_pre_stop_stage_skips_safety_snapshot_and_backup_slot(self):
        runtime = create_runtime()
        server = FakePluginServer()
        session = build_restore_session(skip_safety_snapshot=True)
        runtime.restore.lock.acquire()
        runtime.restore.session = session

        with mock.patch.object(
            restore_workflow, "create_restore_safety_snapshot"
        ) as create_snapshot, mock.patch.object(
            restore_workflow, "request_minecraft_stop_for_restore"
        ) as request_stop:
            restore_workflow.run_restore_pre_stop_stage(
                runtime, server, mock.Mock(), mock.Mock()
            )

        create_snapshot.assert_not_called()
        request_stop.assert_called_once()
        self.assertEqual(request_stop.call_args.args[3], "")
        self.assertFalse(runtime.backup.lock.locked())
        self.assertIn("跳过恢复前保护快照", server.logger.warning_messages[0])

    def test_pre_stop_stage_still_creates_safety_snapshot_by_default(self):
        runtime = create_runtime()
        server = FakePluginServer()
        session = build_restore_session()
        runtime.restore.lock.acquire()
        runtime.restore.session = session

        with mock.patch.object(
            restore_workflow,
            "create_restore_safety_snapshot",
            return_value="abcdef12",
        ) as create_snapshot, mock.patch.object(
            restore_workflow, "request_minecraft_stop_for_restore"
        ) as request_stop, mock.patch.object(
            type(runtime), "release_backup_slot"
        ) as release_slot:
            restore_workflow.run_restore_pre_stop_stage(
                runtime, server, mock.Mock(), mock.Mock()
            )

        create_snapshot.assert_called_once()
        self.assertEqual(request_stop.call_args.args[3], "abcdef12")
        release_slot.assert_called_once()

    def test_rollback_skipped_when_safety_snapshot_skipped(self):
        runtime = create_runtime()
        server = FakePluginServer()
        session = build_restore_session(skip_safety_snapshot=True)

        with mock.patch.object(
            restore_workflow, "rollback_to_safety_snapshot"
        ) as rollback:
            rollback_error = restore_workflow.try_rollback_after_restore_failure(
                runtime, server, {}, session, "boom"
            )

        rollback.assert_not_called()
        self.assertEqual(rollback_error, "")
        self.assertIn("不执行回滚", server.logger.warning_messages[0])

    def test_startup_message_reports_missing_rollback_when_safety_skipped(self):
        runtime = create_runtime()
        server = FakePluginServer()
        session = build_restore_session(skip_safety_snapshot=True)
        session.error = "boom"
        session.phase = restore_workflow.RestorePhase.STARTING
        runtime.restore.lock.acquire()
        runtime.restore.session = session

        restore_workflow.handle_restore_server_startup(runtime, server)

        self.assertIsNone(runtime.restore.session)
        self.assertIn("未创建保护快照", server.logger.warning_messages[0])


class RestoreApplyRollbackTests(unittest.TestCase):
    def make_commands(self, runtime):
        from mcdr2restic.commands.command_context import CommandContext
        from mcdr2restic.commands.restore_commands import RestoreCommands

        context = CommandContext(
            app_runtime=runtime,
            backup_runner_factory=lambda: None,
            maintenance_runner_factory=lambda: None,
            reload_services=mock.Mock(),
            wake_scheduler=mock.Mock(),
            snapshot_invalidator=mock.Mock(),
        )
        return RestoreCommands(context)

    def test_start_restore_apply_rolls_back_session_and_lock_on_failure(self):
        runtime = create_runtime()
        server = FakePluginServer()
        commands = self.make_commands(runtime)
        session = build_restore_session()
        runtime.restore.lock.acquire()
        with mock.patch(
            "mcdr2restic.commands.restore_commands.start_restore_pre_stop_thread",
            side_effect=RuntimeError("cannot start new thread"),
        ), self.assertRaises(RuntimeError):
            commands.start_restore_apply(server, session)

        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())

    def test_start_direct_restore_apply_rolls_back_on_failure(self):
        runtime = create_runtime()
        server = FakePluginServer()
        commands = self.make_commands(runtime)
        session = build_restore_session(
            tasks=[
                {
                    "type": "snapshot",
                    "snapshot": "abc123",
                    "target_directory": "D:/restore-target",
                }
            ]
        )
        runtime.restore.lock.acquire()
        with mock.patch(
            "mcdr2restic.commands.restore_commands.start_direct_restore_thread",
            side_effect=RuntimeError("cannot start new thread"),
        ), self.assertRaises(RuntimeError):
            commands.start_restore_apply(server, session)

        self.assertIsNone(runtime.restore.session)
        self.assertFalse(runtime.restore.lock.locked())


if __name__ == "__main__":
    unittest.main()
