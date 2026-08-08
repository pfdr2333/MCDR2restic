try:
    from .support import (
        REPO_ROOT,
        BackupProblem,
        CommandContext,
        CommandServer,
        FakePluginServer,
        PermissiveCommandSource,
        ResticCommandResult,
        ResticCommands,
        ResticProgressState,
        TerminateResult,
        assert_restic_success,
        classify_restic_failure_output,
        create_runtime,
        detect_error_lines,
        format_restic_status,
        format_restic_summary,
        is_default_restic_executable_path,
        mock,
        normalize_restore_include_path,
        os,
        resolve_popen_executable,
        resolve_restic_executable_path,
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
        FakePluginServer,
        PermissiveCommandSource,
        ResticCommandResult,
        ResticCommands,
        ResticProgressState,
        TerminateResult,
        assert_restic_success,
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
                runtime,
                server,
                {},
                ["forget"],
                "maintenance",
                None,
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
        with mock.patch(
            "mcdr2restic.restic.restic_runner.subprocess.Popen",
            side_effect=FileNotFoundError,
        ):
            with self.assertRaises(BackupProblem) as error:
                start_restic_process(
                    ["missing-restic"], None, {}, "missing-restic", "backup"
                )

        self.assertEqual(error.exception.i18n_key, "error.restic.executable_not_found")

    def test_start_restic_process_maps_start_failure(self):
        with mock.patch(
            "mcdr2restic.restic.restic_runner.subprocess.Popen",
            side_effect=OSError("permission denied"),
        ):
            with self.assertRaises(BackupProblem) as error:
                start_restic_process(["restic"], None, {}, "restic", "backup")

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

        def fake_run_restic(_runtime, _server, _restic_cfg, args, phase, _deadline):
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
                runtime, server, cfg, "manual", lambda *_args: None
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

        def fake_run_restic(_runtime, _server, _restic_cfg, args, phase, _deadline):
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
