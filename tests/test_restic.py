import threading

try:
    from .support import (
        REPO_ROOT,
        BackupProblem,
        CommandContext,
        CommandServer,
        FakeLogger,
        FakePluginServer,
        PermissiveCommandSource,
        ResticCommandResult,
        ResticCommands,
        ResticProgressState,
        SaveFlushWatcher,
        SimpleNamespace,
        TerminateResult,
        assert_restic_success,
        terminate_process,
        classify_restic_failure_output,
        create_runtime,
        detect_error_lines,
        format_restic_status,
        format_restic_summary,
        is_default_restic_executable_path,
        mock,
        os,
        resolve_popen_executable,
        resolve_restic_executable_path,
        restic_context,
        restic_lock_recovery,
        restic_service,
        start_restic_process,
        termination_failure_suffix,
        unittest,
    )
except ImportError:
    from support import (
        REPO_ROOT,
        BackupProblem,
        CommandContext,
        CommandServer,
        FakeLogger,
        FakePluginServer,
        PermissiveCommandSource,
        ResticCommandResult,
        ResticCommands,
        ResticProgressState,
        SaveFlushWatcher,
        SimpleNamespace,
        TerminateResult,
        assert_restic_success,
        terminate_process,
        classify_restic_failure_output,
        create_runtime,
        detect_error_lines,
        format_restic_status,
        format_restic_summary,
        is_default_restic_executable_path,
        mock,
        os,
        resolve_popen_executable,
        resolve_restic_executable_path,
        restic_context,
        restic_lock_recovery,
        restic_service,
        start_restic_process,
        termination_failure_suffix,
        unittest,
    )


class ResticResultTests(unittest.TestCase):
    def test_detect_error_lines_honors_ignore_patterns(self):
        lines = detect_error_lines(
            "ok\nerror: failed\nerror: ignored\n", [r"error:"], [r"ignored"]
        )
        self.assertEqual(lines, ["error: failed"])

    def test_success_exit_codes_invalid_entries_fall_back_with_warning(self):
        # P3-2：非法值不得抛裸异常，回退默认 {0} 并记 warn
        logger = FakeLogger()
        result = ResticCommandResult("snapshots", ["snapshots"], 0, "", "", 2)

        for bad in ([0, "abc"], [0, None], 0, [True]):
            with self.subTest(value=bad):
                assert_restic_success(
                    {"success_exit_codes": bad}, result, "!!backup", logger
                )

        self.assertEqual(len(logger.warning_messages), 4)
        self.assertTrue(
            all(
                "success_exit_codes" in message
                for message in logger.warning_messages
            )
        )

    def test_success_exit_codes_valid_custom_codes_still_honored(self):
        result = ResticCommandResult("snapshots", ["snapshots"], 3, "", "", 2)

        assert_restic_success({"success_exit_codes": [0, 3]}, result, "!!backup")

    def test_invalid_error_regex_is_skipped_with_warning(self):
        logger = FakeLogger()
        lines = detect_error_lines(
            "ok\nerror: failed\n",
            ["[unclosed", r"error:"],
            ["(?"],
            logger,
        )

        self.assertEqual(lines, ["error: failed"])
        self.assertEqual(len(logger.warning_messages), 2)
        self.assertTrue(
            all(
                "error_regex" in message for message in logger.warning_messages
            )
        )

    def test_return_code_error_guides_repository_initialization(self):
        result = ResticCommandResult(
            "snapshots",
            ["snapshots"],
            1,
            "",
            "Fatal: unable to open config file: config file does not exist",
            2,
        )

        with self.assertRaises(BackupProblem) as error:
            assert_restic_success({}, result, "!!backup")

        self.assertEqual(
            error.exception.i18n_key,
            "error.restic.return_code.repository_not_initialized",
        )
        self.assertEqual(error.exception.i18n_params["init_command"], "!!backup init")

    def test_return_code_error_guides_manual_unlock_with_risk_text(self):
        result = ResticCommandResult(
            "backup",
            ["backup", "world"],
            1,
            "",
            "unable to create lock in backend: repository is already locked",
            1,
        )

        with self.assertRaises(BackupProblem) as error:
            assert_restic_success({}, result, "!!backup")

        self.assertEqual(error.exception.i18n_key, "error.restic.return_code.locked")
        self.assertEqual(
            error.exception.i18n_params["unlock_command"], "!!backup unlock"
        )

    def test_classify_restic_failure_output(self):
        self.assertEqual(
            classify_restic_failure_output("repository is already locked"),
            "locked",
        )
        self.assertEqual(
            classify_restic_failure_output(
                "Is there a repository at the following location?"
            ),
            "repository_not_initialized",
        )

    def test_config_folder_default_restic_path_is_recognized_and_cwd_relative(self):
        executable = "./config/mcdr2restic/restic"

        self.assertTrue(is_default_restic_executable_path(executable))
        self.assertEqual(
            resolve_restic_executable_path(
                {"working_directory": os.path.join(str(REPO_ROOT), "server")},
                executable,
            ),
            os.path.abspath(executable),
        )
        self.assertEqual(
            resolve_popen_executable(
                executable, os.path.join(str(REPO_ROOT), "server")
            ),
            os.path.abspath(executable),
        )

    def test_bare_restic_name_resolves_identically_for_download_and_execution(self):
        platform_defaults = {
            "posix": "./config/mcdr2restic/restic",
            "nt": ".\\config\\mcdr2restic\\restic.exe",
        }
        for platform_name, default_path in platform_defaults.items():
            with mock.patch("os.name", platform_name):
                download_path = resolve_restic_executable_path(
                    {"working_directory": os.path.join(str(REPO_ROOT), "server")},
                    "restic",
                )
                execution_path = resolve_popen_executable(
                    "restic", os.path.join(str(REPO_ROOT), "server")
                )

            self.assertEqual(download_path, os.path.abspath(default_path))
            self.assertEqual(execution_path, download_path)

    def test_prefer_system_restic_resolves_to_path_hit(self):
        with mock.patch(
            "mcdr2restic.restic.restic_runner.find_system_restic",
            return_value="/usr/bin/restic",
        ):
            self.assertEqual(
                resolve_popen_executable("restic", None, prefer_system=True),
                "/usr/bin/restic",
            )

    def test_prefer_system_restic_falls_back_to_config_folder_on_path_miss(self):
        platform_defaults = {
            "posix": "./config/mcdr2restic/restic",
            "nt": ".\\config\\mcdr2restic\\restic.exe",
        }
        for platform_name, default_path in platform_defaults.items():
            with mock.patch("os.name", platform_name), mock.patch(
                "mcdr2restic.restic.restic_runner.find_system_restic",
                return_value=None,
            ):
                resolved = resolve_popen_executable(
                    "restic", None, prefer_system=True
                )

            self.assertEqual(resolved, os.path.abspath(default_path))

    def test_explicit_executable_path_ignores_system_restic_preference(self):
        with mock.patch(
            "mcdr2restic.restic.restic_runner.find_system_restic",
            return_value="/usr/bin/restic",
        ):
            resolved = resolve_popen_executable(
                "./custom-restic", str(REPO_ROOT), prefer_system=True
            )

        self.assertEqual(
            resolved, os.path.abspath(os.path.join(str(REPO_ROOT), "custom-restic"))
        )


class ResticLockRecoveryTests(unittest.TestCase):
    LOCK_ERROR_OUTPUT = (
        "repo already locked, waiting up to 0s for the lock\n\n"
        "unable to create lock in backend: repository is already locked by PID 69916 "
        "on T6A-x by T6A-X\\mo182 (UID 0, GID 0)\n"
        "lock was created at 2026-07-09 09:16:29 (1h22m52.1074711s ago)\n"
        "storage ID 53ac25a7\n"
    )

    def test_extract_restic_lock_info_parses_pid_and_host(self):
        info = restic_lock_recovery.extract_restic_lock_info(self.LOCK_ERROR_OUTPUT)

        self.assertEqual(
            info, restic_lock_recovery.ResticLockInfo(pid=69916, host="T6A-x")
        )

    def test_recoverable_stale_lock_info_accepts_dead_local_process(self):
        result = ResticCommandResult(
            "maintenance", ["forget"], 11, "", self.LOCK_ERROR_OUTPUT, 1
        )

        with (
            mock.patch.object(
                restic_lock_recovery,
                "lock_belongs_to_current_host",
                return_value=True,
            ),
            mock.patch.object(
                restic_lock_recovery, "process_exists", return_value=False
            ),
        ):
            info = restic_lock_recovery.recoverable_stale_lock_info(result)

        self.assertEqual(
            info, restic_lock_recovery.ResticLockInfo(pid=69916, host="T6A-x")
        )

    def test_recoverable_stale_lock_info_ignores_successful_command(self):
        # 成功命令的输出即使提及锁也不允许触发解锁重跑，防止同一命令执行两次
        result = ResticCommandResult(
            "maintenance", ["forget"], 0, self.LOCK_ERROR_OUTPUT, "", 1
        )

        with (
            mock.patch.object(
                restic_lock_recovery,
                "lock_belongs_to_current_host",
                return_value=True,
            ),
            mock.patch.object(
                restic_lock_recovery, "process_exists", return_value=False
            ),
        ):
            info = restic_lock_recovery.recoverable_stale_lock_info(result)

        self.assertIsNone(info)

    def test_run_restic_command_with_lock_recovery_unlocks_and_retries_once(self):
        runtime = create_runtime()
        server = FakePluginServer()
        locked_result = ResticCommandResult(
            "maintenance", ["forget"], 11, "", self.LOCK_ERROR_OUTPUT, 1
        )
        unlock_result = ResticCommandResult("unlock", ["unlock"], 0, "", "", 0)
        retry_result = ResticCommandResult("maintenance", ["forget"], 0, "", "", 0)
        calls = []

        def fake_run(_runtime, _restic_cfg, args, phase, _deadline):
            calls.append((phase, list(args)))
            if len(calls) == 1:
                return locked_result
            if len(calls) == 2:
                return unlock_result
            return retry_result

        with (
            mock.patch.object(
                restic_lock_recovery,
                "recoverable_stale_lock_info",
                return_value=restic_lock_recovery.ResticLockInfo(
                    pid=69916, host="T6A-x"
                ),
            ),
            mock.patch.object(
                restic_lock_recovery, "run_restic_command", side_effect=fake_run
            ),
            mock.patch.object(restic_lock_recovery, "assert_restic_success"),
        ):
            result = restic_lock_recovery.run_restic_command_with_lock_recovery(
                restic_context.ResticOperationContext(runtime, server, {}),
                ["forget"],
                "maintenance",
            )

        self.assertIs(result, retry_result)
        self.assertEqual(
            calls,
            [
                ("maintenance", ["forget"]),
                ("unlock", ["unlock"]),
                ("maintenance", ["forget"]),
            ],
        )
        self.assertTrue(server.logger.warning_messages)
        self.assertTrue(server.logger.info_messages)


class ResticCommandTests(unittest.TestCase):
    def test_start_restic_process_maps_missing_executable(self):
        # P3-5：报错须显示 Popen 实际尝试的解析路径，而非配置原文裸名
        resolved = os.path.join("config", "mcdr2restic", "restic.exe")
        with mock.patch(
            "mcdr2restic.restic.restic_runner.subprocess.Popen",
            side_effect=FileNotFoundError,
        ), self.assertRaises(BackupProblem) as error:
            start_restic_process(
                [resolved, "backup"], None, {}, "backup"
            )

        self.assertEqual(error.exception.i18n_key, "error.restic.executable_not_found")
        self.assertEqual(error.exception.i18n_params["executable"], resolved)

    def test_start_restic_process_maps_start_failure(self):
        with mock.patch(
            "mcdr2restic.restic.restic_runner.subprocess.Popen",
            side_effect=OSError("permission denied"),
        ), self.assertRaises(BackupProblem) as error:
            start_restic_process(["restic"], None, {}, "backup")

        self.assertEqual(error.exception.i18n_key, "error.restic.start_failed")

    def test_manual_init_runs_restic_init_and_invalidates_snapshot_cache(self):
        runtime = create_runtime()
        runtime.config_state.config = {
            "command": {"root": "!!backup", "permission_level": 3},
            "restic": {"timeout_seconds": 0},
        }
        server = FakePluginServer()
        runtime.service.server = server
        source = PermissiveCommandSource(server)
        invalidations = []
        commands = ResticCommands(
            CommandContext(
                runtime,
                lambda: None,
                lambda: None,
                lambda _server: None,
                lambda: None,
                lambda *args: invalidations.append(args),
            )
        )

        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                return_value=ResticCommandResult("init", ["init"], 0, "", "", 0),
            ) as run_restic,
        ):
            commands.command_init(source)

        self.assertEqual(run_restic.call_args.args[2], ["init"])
        self.assertEqual(run_restic.call_args.args[3], "init")
        self.assertTrue(invalidations)
        self.assertIn("restic init", source.replies[0])

    def test_manual_init_registers_command_thread_for_shutdown_join(self):
        # P2-2：init/unlock 同步跑在命令线程，必须登记 backup.thread 供卸载 join
        runtime = create_runtime()
        runtime.config_state.config = {
            "command": {"root": "!!backup", "permission_level": 3},
            "restic": {"timeout_seconds": 0},
        }
        server = FakePluginServer()
        runtime.service.server = server
        source = PermissiveCommandSource(server)
        observed = {}
        commands = ResticCommands(
            CommandContext(
                runtime,
                lambda: None,
                lambda: None,
                lambda _server: None,
                lambda: None,
                lambda *_args: None,
            )
        )

        def fake_run(app_runtime, *_args, **_kwargs):
            observed["thread"] = app_runtime.backup.thread
            return ResticCommandResult("init", ["init"], 0, "", "", 0)

        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                side_effect=fake_run,
            ),
        ):
            commands.command_init(source)

        self.assertIs(observed["thread"], threading.current_thread())
        self.assertIsNone(runtime.backup.thread)
        self.assertFalse(runtime.backup.lock.locked())

    def test_manual_maintenance_starts_runner_and_replies(self):
        runtime = create_runtime()
        runtime.config_state.config = {"command": {"root": "!!restic", "permission_level": 3}}
        server = FakePluginServer()
        runtime.service.server = server
        source = PermissiveCommandSource(server)
        maintenance_runner = mock.Mock()
        maintenance_runner.start_thread.return_value = True
        commands = ResticCommands(
            CommandContext(
                runtime,
                lambda: None,
                lambda: maintenance_runner,
                lambda _server: None,
                lambda: None,
                lambda *_args: None,
            )
        )

        commands.command_maintenance(source)

        maintenance_runner.start_thread.assert_called_once_with(server)
        self.assertEqual(len(source.replies), 1)
        self.assertIn("维护", source.replies[0])

    def test_manual_maintenance_rejects_restore_and_busy_slot(self):
        runtime = create_runtime()
        runtime.config_state.config = {"command": {"root": "!!restic", "permission_level": 3}}
        server = FakePluginServer()
        runtime.service.server = server
        source = PermissiveCommandSource(server)
        maintenance_runner = mock.Mock()
        maintenance_runner.start_thread.return_value = False
        commands = ResticCommands(
            CommandContext(
                runtime,
                lambda: None,
                lambda: maintenance_runner,
                lambda _server: None,
                lambda: None,
                lambda *_args: None,
            )
        )

        with mock.patch(
            "mcdr2restic.commands.restic_commands.is_restore_running",
            return_value=True,
        ):
            commands.command_maintenance(source)
        self.assertIn("恢复流程", source.replies[-1])

        with mock.patch(
            "mcdr2restic.commands.restic_commands.is_restore_running",
            return_value=False,
        ):
            commands.command_maintenance(source)
        self.assertIn("已有备份", source.replies[-1])


class BackupFlowTests(unittest.TestCase):
    def test_run_backup_body_executes_minecraft_then_backup_without_maintenance(self):
        runtime = create_runtime()
        runtime.service.server_ready = True
        server = CommandServer()
        cfg = {
            "minecraft": {
                "save_off_command": "save-off",
                "save_all_command": "save-all",
                "wait_after_save_off_seconds": 0,
                "wait_after_save_all_seconds": 0,
            },
            "restic": {
                "maintenance_commands": [["forget"]],
                "backup_command": ["backup", "world"],
                "timeout_seconds": 0,
            },
        }
        restic_calls = []

        def fake_run_restic(_ctx, args, phase):
            restic_calls.append((phase, list(args)))
            return ResticCommandResult(
                phase, list(args), 0, "", "", 0, snapshot_id="abc123"
            )

        with (
            mock.patch.object(restic_service, "is_mc_ready", return_value=True),
            mock.patch.object(
                restic_service, "assert_backup_sources_do_not_contain_repository"
            ),
            mock.patch.object(
                restic_service, "ensure_default_restic_executable_available"
            ),
            mock.patch.object(
                restic_service,
                "ensure_restic_repository_initialized",
                return_value=False,
            ),
            mock.patch.object(
                restic_service,
                "run_restic_command_with_lock_recovery",
                side_effect=fake_run_restic,
            ),
            mock.patch.object(restic_service, "assert_restic_success"),
        ):
            snapshot_id = restic_service.run_backup_body(
                runtime, server, cfg, lambda *_args: None
            )

        self.assertEqual(snapshot_id, "abc123")
        self.assertEqual(restic_calls, [("backup", ["backup", "world"])])
        self.assertEqual(server.commands, ["save-off", "save-all"])

    def test_run_maintenance_body_executes_maintenance_without_minecraft_commands(self):
        runtime = create_runtime()
        runtime.service.server_ready = True
        server = CommandServer()
        cfg = {
            "restic": {
                "maintenance_commands": [["forget"]],
                "backup_command": ["backup", "world"],
                "timeout_seconds": 0,
            },
        }
        restic_calls = []

        def fake_run_restic(_ctx, args, phase):
            restic_calls.append((phase, list(args)))
            return ResticCommandResult(phase, list(args), 0, "", "", 0)

        with (
            mock.patch.object(
                restic_service, "ensure_default_restic_executable_available"
            ),
            mock.patch.object(
                restic_service,
                "ensure_restic_repository_initialized",
                return_value=False,
            ),
            mock.patch.object(
                restic_service,
                "run_restic_command_with_lock_recovery",
                side_effect=fake_run_restic,
            ),
            mock.patch.object(restic_service, "assert_restic_success"),
        ):
            restic_service.run_maintenance_body(
                runtime, server, cfg, lambda *_args: None
            )

        self.assertEqual(restic_calls, [("maintenance", ["forget"])])
        self.assertEqual(server.commands, [])


class ResticProgressTests(unittest.TestCase):
    def test_format_status_uses_progress_values(self):
        progress = ResticProgressState(
            phase="backup",
            language="en_us",
            status={
                "percent_done": 0.5,
                "files_done": 1,
                "total_files": 2,
                "bytes_done": 1024,
                "total_bytes": 2048,
            },
        )

        text = format_restic_status(progress)

        self.assertIn("50.0%", text)
        self.assertIn("files 1/2", text)
        self.assertIn("1.0 KiB/2.0 KiB", text)

    def test_format_summary_reports_snapshot_id(self):
        progress = ResticProgressState(
            phase="backup",
            language="en_us",
            summary={"snapshot_id": "abcdef123456", "total_files_processed": 3},
        )

        self.assertIn("snapshot abcdef12", format_restic_summary(progress))

    def test_run_restic_command_skips_final_progress_without_json_output(self):
        # P3-3：init 等非 JSON 阶段结束后不得再报"仍在执行"
        from mcdr2restic.restic import restic_runner

        runtime = create_runtime()
        runtime.service.server = FakePluginServer()

        def fake_read(app_runtime, process, progress, interval, deadline):
            return restic_runner.ResticProcessOutcome("", "", 0, False)

        with (
            mock.patch(
                "mcdr2restic.restic.restic_runner.build_restic_process_command",
                return_value=("restic", None, {}, ["restic", "init"]),
            ),
            mock.patch(
                "mcdr2restic.restic.restic_runner.start_restic_process",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "mcdr2restic.restic.restic_runner.read_restic_process_output",
                side_effect=fake_read,
            ),
            mock.patch(
                "mcdr2restic.restic.restic_runner.maybe_emit_restic_progress"
            ) as emit,
        ):
            restic_runner.run_restic_command(
                runtime, {"timeout_seconds": 0}, ["init"], "init", None
            )

        emit.assert_not_called()

    def test_run_restic_command_emits_summary_for_json_phases(self):
        from mcdr2restic.restic import restic_runner

        runtime = create_runtime()
        runtime.service.server = FakePluginServer()

        def fake_read(app_runtime, process, progress, interval, deadline):
            progress.summary = {"snapshot_id": "abcdef123456"}
            return restic_runner.ResticProcessOutcome("", "", 0, False)

        with (
            mock.patch(
                "mcdr2restic.restic.restic_runner.build_restic_process_command",
                return_value=("restic", None, {}, ["restic", "backup"]),
            ),
            mock.patch(
                "mcdr2restic.restic.restic_runner.start_restic_process",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "mcdr2restic.restic.restic_runner.read_restic_process_output",
                side_effect=fake_read,
            ),
            mock.patch(
                "mcdr2restic.restic.restic_runner.maybe_emit_restic_progress"
            ) as emit,
        ):
            restic_runner.run_restic_command(
                runtime, {"timeout_seconds": 0}, ["backup"], "backup", None
            )

        emit.assert_called_once()
        self.assertTrue(emit.call_args.kwargs.get("force"))


class ResticTerminationTests(unittest.TestCase):
    def test_terminate_result_reports_successful_paths(self):
        self.assertTrue(TerminateResult(graceful=True).terminated)
        self.assertTrue(TerminateResult(killed=True).terminated)
        self.assertFalse(TerminateResult(error="failed").terminated)

    def test_termination_failure_suffix_reports_error(self):
        self.assertEqual(termination_failure_suffix(TerminateResult(graceful=True)), "")
        self.assertIn(
            "kill failed",
            termination_failure_suffix(TerminateResult(error="kill failed")),
        )

    def test_posix_process_already_exited_counts_as_terminated(self):
        # 进程自然退出后 killpg/getpgid 抛 ProcessLookupError，不应误报终止失败
        process = mock.Mock()
        process.pid = 1234

        with (
            mock.patch("mcdr2restic.restic.restic_termination.os.name", "posix"),
            mock.patch(
                "mcdr2restic.restic.restic_termination.os.getpgid",
                side_effect=ProcessLookupError,
                create=True,
            ),
            mock.patch(
                "mcdr2restic.restic.restic_termination.os.killpg",
                return_value=None,
                create=True,
            ),
        ):
            result = terminate_process(process)

        self.assertTrue(result.terminated)
        self.assertEqual(result.error, "")


class SaveFlushWatcherTests(unittest.TestCase):
    def test_notify_output_sets_event_on_completion_match(self):
        watcher = SaveFlushWatcher()
        watcher.arm(["Saved the game"])

        watcher.notify_output(
            SimpleNamespace(
                is_user=False,
                content="[23:49:23 INFO]: Saved the game",
            )
        )

        self.assertTrue(watcher.wait(0.0))

    def test_notify_output_ignores_user_input_and_unmatched_lines(self):
        watcher = SaveFlushWatcher()
        watcher.arm(["Saved the game"])

        watcher.notify_output(SimpleNamespace(is_user=True, content=">save-all flush"))
        watcher.notify_output(SimpleNamespace(is_user=False, content="Saving chunks"))
        self.assertFalse(watcher.wait(0.0))

        watcher.notify_output(SimpleNamespace(is_user=False, content="Saved the game"))
        self.assertTrue(watcher.wait(0.0))

    def test_notify_output_does_nothing_when_not_armed(self):
        watcher = SaveFlushWatcher()

        watcher.notify_output(SimpleNamespace(is_user=False, content="Saved the game"))

        self.assertFalse(watcher.wait(0.0))
        self.assertFalse(watcher.is_armed())

    def test_disarm_clears_patterns_and_event(self):
        watcher = SaveFlushWatcher()
        watcher.arm(["Saved the game"])

        watcher.disarm()

        self.assertFalse(watcher.is_armed())
        watcher.notify_output(SimpleNamespace(is_user=False, content="Saved the game"))
        self.assertFalse(watcher.wait(0.0))

    def test_wait_returns_false_after_timeout(self):
        watcher = SaveFlushWatcher()
        watcher.arm(["Saved the game"])

        self.assertFalse(watcher.wait(0.05))


class WaitForSaveAllFlushTests(unittest.TestCase):
    def build_cfg(self, **overrides):
        cfg = {
            "save_all_command": "save-all flush",
            "wait_after_save_off_seconds": 0,
            "wait_after_save_all_seconds": 10,
            "wait_for_save_all_completion": True,
            "save_all_done_patterns": ["Saved the game"],
        }
        cfg.update(overrides)
        return cfg

    def test_flush_completion_releases_backup_immediately(self):
        runtime = create_runtime()
        server = CommandServer()
        executed = []

        def fake_execute(_runtime, _server, command, label):
            executed.append(command)
            if label == "save-all":
                runtime.service.save_flush_watcher.notify_output(
                    SimpleNamespace(is_user=False, content="Saved the game")
                )

        with mock.patch.object(
            restic_service, "execute_mc_command", side_effect=fake_execute
        ):
            restic_service.wait_for_save_all_flush(
                runtime, server, self.build_cfg()
            )

        self.assertEqual(executed, ["save-all flush"])
        self.assertIn("已完成", server.logger.info_messages[0])
        self.assertFalse(runtime.service.save_flush_watcher.is_armed())

    def test_flush_wait_timeout_falls_back_with_warning(self):
        runtime = create_runtime()
        server = CommandServer()

        with mock.patch.object(restic_service, "execute_mc_command"):
            restic_service.wait_for_save_all_flush(
                runtime, server, self.build_cfg(wait_after_save_all_seconds=0.05)
            )

        self.assertIn("未检测到完成提示", server.logger.warning_messages[0])

    def test_disabled_completion_uses_fixed_sleep(self):
        runtime = create_runtime()
        server = CommandServer()
        slept = []

        with mock.patch.object(restic_service, "execute_mc_command"), mock.patch.object(
            restic_service,
            "sleep_or_cancel",
            side_effect=lambda _runtime, seconds: slept.append(seconds),
        ):
            restic_service.wait_for_save_all_flush(
                runtime, server, self.build_cfg(wait_for_save_all_completion=False)
            )

        self.assertEqual(slept, [10])
        self.assertFalse(runtime.service.save_flush_watcher.is_armed())

    def test_zero_timeout_still_sends_save_all_without_waiting(self):
        runtime = create_runtime()
        server = CommandServer()
        executed = []

        with mock.patch.object(
            restic_service,
            "execute_mc_command",
            side_effect=lambda _r, _s, command, _label: executed.append(command),
        ), mock.patch.object(restic_service, "sleep_or_cancel"):
            restic_service.wait_for_save_all_flush(
                runtime, server, self.build_cfg(wait_after_save_all_seconds=0)
            )

        self.assertEqual(executed, ["save-all flush"])
        self.assertFalse(runtime.service.save_flush_watcher.is_armed())

    def test_empty_patterns_uses_fixed_sleep(self):
        runtime = create_runtime()
        server = CommandServer()
        slept = []

        with mock.patch.object(restic_service, "execute_mc_command"), mock.patch.object(
            restic_service,
            "sleep_or_cancel",
            side_effect=lambda _runtime, seconds: slept.append(seconds),
        ):
            restic_service.wait_for_save_all_flush(
                runtime, server, self.build_cfg(save_all_done_patterns=[])
            )

        self.assertEqual(slept, [10])
