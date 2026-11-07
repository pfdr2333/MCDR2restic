# -*- coding: utf-8 -*-
"""!!restic exec 透传命令：管线解析、过滤阶段与命令处理器行为。"""
from __future__ import annotations

try:
    from .support import (
        BackupProblem,
        FakePluginServer,
        ResticCommandResult,
        create_runtime,
        mock,
        os,
        PermissiveCommandSource,
        threading,
        unittest,
    )
except ImportError:
    from support import (
        BackupProblem,
        FakePluginServer,
        ResticCommandResult,
        create_runtime,
        mock,
        os,
        PermissiveCommandSource,
        threading,
        unittest,
    )

from mcdr2restic.commands.exec_pipeline import (
    DEFAULT_WINDOW_LINES,
    EXEC_DISPLAY_MAX_LINES,
    EXEC_DISPLAY_MAX_LINE_CHARS,
    ExecOutputPipeline,
    GrepStage,
    HeadStage,
    TailStage,
    parse_exec_pipeline,
)
from mcdr2restic.commands.restic_commands import EXEC_PHASE, ResticCommands


def make_exec_commands(runtime=None):
    from mcdr2restic.commands.command_context import CommandContext

    runtime = runtime or create_runtime()
    runtime.config_state.config = {
        "command": {"root": "!!restic", "permission_level": 3},
        "restic": {},
    }
    server = FakePluginServer()
    runtime.service.server = server
    source = PermissiveCommandSource(server)
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
    return commands, runtime, server, source


def ok_exec_result(args):
    return ResticCommandResult("exec", args, 0, "", "", 1.234)


class ExecPipelineParseTests(unittest.TestCase):
    def test_parse_splits_restic_args_and_stages(self):
        request = parse_exec_pipeline("ls latest /world | grep grove | head 30")
        self.assertEqual(request.restic_args, ["ls", "latest", "/world"])
        self.assertEqual(
            [type(stage).__name__ for stage in request.stages],
            ["GrepStage", "HeadStage"],
        )

    def test_parse_without_pipeline_keeps_all_args(self):
        request = parse_exec_pipeline("snapshots --json --compact")
        self.assertEqual(request.restic_args, ["snapshots", "--json", "--compact"])
        self.assertEqual(request.stages, [])

    def test_quoted_pipe_is_not_a_separator(self):
        request = parse_exec_pipeline('snapshots --tag "a|b"')
        self.assertEqual(request.restic_args, ["snapshots", "--tag", "a|b"])
        self.assertEqual(request.stages, [])

    @unittest.skipUnless(os.name == "nt", "Windows 非 POSIX 分词语义")
    def test_windows_backslash_path_is_preserved(self):
        request = parse_exec_pipeline(r"ls latest C:\world\backup")
        self.assertEqual(request.restic_args, ["ls", "latest", r"C:\world\backup"])

    def test_empty_restic_args_raise_usage(self):
        for raw in ("", "   ", "| grep x"):
            with self.assertRaises(BackupProblem) as ctx:
                parse_exec_pipeline(raw)
            self.assertEqual(ctx.exception.i18n_key, "error.exec.usage")

    def test_unknown_stage_raises(self):
        with self.assertRaises(BackupProblem) as ctx:
            parse_exec_pipeline("ls | wc -l")
        self.assertEqual(ctx.exception.i18n_key, "error.exec.unknown_stage")

    def test_invalid_window_count_raises(self):
        for raw in ("ls | head abc", "ls | tail 0", "ls | head -5"):
            with self.assertRaises(BackupProblem) as ctx:
                parse_exec_pipeline(raw)
            self.assertEqual(ctx.exception.i18n_key, "error.exec.invalid_count")

    def test_invalid_grep_pattern_raises(self):
        with self.assertRaises(BackupProblem) as ctx:
            parse_exec_pipeline("ls | grep ([")
        self.assertEqual(ctx.exception.i18n_key, "error.exec.invalid_pattern")

    def test_grep_ignore_case_flag(self):
        stage = parse_exec_pipeline("ls | grep -i GROVE").stages[0]
        self.assertTrue(stage.feed("a grove line\n"))

    def test_default_window_lines(self):
        head = parse_exec_pipeline("ls | head").stages[0]
        tail = parse_exec_pipeline("ls | tail").stages[0]
        self.assertEqual(head.remaining, DEFAULT_WINDOW_LINES)
        self.assertEqual(tail.buffer.maxlen, DEFAULT_WINDOW_LINES)


class ExecPipelineFilterTests(unittest.TestCase):
    def test_grep_filters_lines(self):
        pipeline = ExecOutputPipeline([GrepStage("grove")])
        out = []
        for line in ["alpha\n", "grove 1\n", "beta\n", "grove 2\n"]:
            out.extend(pipeline.feed(line))
        self.assertEqual(out, ["grove 1", "grove 2"])
        self.assertEqual((pipeline.total_lines, pipeline.shown_lines), (4, 2))

    def test_head_stops_after_limit(self):
        pipeline = ExecOutputPipeline([HeadStage(2)])
        out = []
        for line in ["1\n", "2\n", "3\n", "4\n"]:
            out.extend(pipeline.feed(line))
        self.assertEqual(out, ["1", "2"])

    def test_tail_buffers_and_flushes_last_lines(self):
        pipeline = ExecOutputPipeline([TailStage(2)])
        for line in ["a\n", "b\n", "c\n", "d\n"]:
            self.assertEqual(pipeline.feed(line), [])
        self.assertEqual(pipeline.flush(), ["c", "d"])

    def test_chained_grep_then_tail(self):
        pipeline = ExecOutputPipeline(
            parse_exec_pipeline("ls x | grep grove | tail 2").stages
        )
        for line in ["grove 1\n", "grove 2\n", "grove 3\n", "other\n", "grove 4\n"]:
            pipeline.feed(line)
        self.assertEqual(pipeline.flush(), ["grove 3", "grove 4"])

    def test_display_cap_suppresses_extra_lines(self):
        pipeline = ExecOutputPipeline([])
        out = []
        for index in range(EXEC_DISPLAY_MAX_LINES + 10):
            out.extend(pipeline.feed("{}\n".format(index)))
        self.assertEqual(len(out), EXEC_DISPLAY_MAX_LINES)
        self.assertEqual(pipeline.suppressed_lines, 10)

    def test_long_line_is_truncated(self):
        pipeline = ExecOutputPipeline([])
        out = pipeline.feed("x" * (EXEC_DISPLAY_MAX_LINE_CHARS + 50) + "\n")
        self.assertEqual(len(out[0]), EXEC_DISPLAY_MAX_LINE_CHARS + 1)
        self.assertTrue(out[0].endswith("…"))

    def test_flush_respects_display_cap(self):
        pipeline = ExecOutputPipeline([TailStage(EXEC_DISPLAY_MAX_LINES + 10)])
        for index in range(EXEC_DISPLAY_MAX_LINES + 10):
            pipeline.feed("{}\n".format(index))
        flushed = pipeline.flush()
        self.assertEqual(len(flushed), EXEC_DISPLAY_MAX_LINES)
        self.assertEqual(pipeline.suppressed_lines, 10)


class ExecCommandHandlerTests(unittest.TestCase):
    def test_exec_runs_parsed_args_with_exec_phase_and_no_deadline(self):
        commands, runtime, _server, source = make_exec_commands()
        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                return_value=ok_exec_result(["version"]),
            ) as run_restic,
        ):
            commands.command_exec(source, {"args": "version"})

        self.assertEqual(run_restic.call_args.args[2], ["version"])
        self.assertEqual(run_restic.call_args.args[3], EXEC_PHASE)
        self.assertIsNone(run_restic.call_args.args[4])
        self.assertIn("version", source.replies[0])
        self.assertIn("退出码 0", source.replies[-1])
        self.assertFalse(runtime.backup.lock.locked())

    def test_exec_streams_filtered_lines_via_line_sink(self):
        commands, _runtime, server, source = make_exec_commands()

        def fake_run(app_runtime, _cfg, args, phase, deadline, line_sink=None):
            self.assertIsNotNone(line_sink)
            for line in ["alpha\n", "grove 1\n", "grove 2\n", "beta\n"]:
                line_sink("stdout", line)
            return ok_exec_result(args)

        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                side_effect=fake_run,
            ),
        ):
            commands.command_exec(source, {"args": "ls x | grep grove"})

        self.assertEqual(source.replies[1:-1], ["grove 1", "grove 2"])
        # 全量不过滤输出进服务端日志
        logged = server.logger.info_messages
        self.assertTrue(any("alpha" in item for item in logged))
        self.assertTrue(any("beta" in item for item in logged))

    def test_exec_flushes_tail_lines_after_success(self):
        commands, _runtime, _server, source = make_exec_commands()

        def fake_run(_app_runtime, _cfg, args, _phase, _deadline, line_sink=None):
            for line in ["a\n", "b\n", "c\n", "d\n"]:
                line_sink("stdout", line)
            return ok_exec_result(args)

        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                side_effect=fake_run,
            ),
        ):
            commands.command_exec(source, {"args": "ls x | tail 2"})

        self.assertEqual(source.replies[1:-1], ["c", "d"])

    def test_exec_registers_thread_and_clears_cancel(self):
        commands, runtime, _server, source = make_exec_commands()
        observed = {}

        def fake_run(app_runtime, _cfg, args, _phase, _deadline, line_sink=None):
            observed["thread"] = app_runtime.backup.thread
            observed["cancel_set"] = app_runtime.backup.cancel.is_set()
            observed["label"] = app_runtime.backup.label
            return ok_exec_result(args)

        runtime.backup.cancel.set()
        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                side_effect=fake_run,
            ),
        ):
            commands.command_exec(source, {"args": "version"})

        self.assertIs(observed["thread"], threading.current_thread())
        self.assertFalse(observed["cancel_set"])
        self.assertEqual(observed["label"], "exec-version")
        # 槽位释放后线程与标签一并清理
        self.assertIsNone(runtime.backup.thread)
        self.assertIsNone(runtime.backup.label)

    def test_exec_rejects_while_backup_running(self):
        commands, runtime, _server, source = make_exec_commands()
        runtime.backup.lock.acquire()
        try:
            with mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command"
            ) as run_restic:
                commands.command_exec(source, {"args": "version"})
        finally:
            runtime.backup.lock.release()

        run_restic.assert_not_called()
        self.assertIn("备份", source.replies[-1])
        self.assertIn("暂不能执行", source.replies[-1])

    def test_exec_rejects_while_restore_running(self):
        commands, _runtime, _server, source = make_exec_commands()
        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.is_restore_running",
                return_value=True,
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command"
            ) as run_restic,
        ):
            commands.command_exec(source, {"args": "version"})

        run_restic.assert_not_called()
        self.assertIn("恢复", source.replies[-1])

    def test_exec_invalid_stage_replies_error_without_running(self):
        commands, runtime, _server, source = make_exec_commands()
        with mock.patch(
            "mcdr2restic.commands.restic_commands.run_restic_command"
        ) as run_restic:
            commands.command_exec(source, {"args": "ls | wc -l"})

        run_restic.assert_not_called()
        self.assertIn("wc", source.replies[-1])
        self.assertFalse(runtime.backup.lock.locked())

    def test_exec_empty_args_replies_usage(self):
        commands, _runtime, _server, source = make_exec_commands()
        with mock.patch(
            "mcdr2restic.commands.restic_commands.run_restic_command"
        ) as run_restic:
            commands.command_exec(source, {"args": ""})

        run_restic.assert_not_called()
        self.assertIn("!!restic exec", source.replies[-1])

    def test_exec_usage_handler_replies_usage(self):
        commands, _runtime, _server, source = make_exec_commands()
        commands.command_exec_usage(source)
        self.assertIn("!!restic exec", source.replies[-1])

    def test_exec_failure_replies_error_and_releases_slot(self):
        commands, runtime, _server, source = make_exec_commands()
        failed = ResticCommandResult("exec", ["version"], 1, "", "boom", 1.0)
        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                return_value=failed,
            ),
        ):
            commands.command_exec(source, {"args": "version"})

        self.assertIn("退出码异常", source.replies[-1])
        self.assertFalse(runtime.backup.lock.locked())
        self.assertIsNone(runtime.backup.thread)

    def test_exec_canceled_replies_cancel_error_and_releases_slot(self):
        commands, runtime, _server, source = make_exec_commands()
        with (
            mock.patch(
                "mcdr2restic.commands.restic_commands.ensure_default_restic_executable_available"
            ),
            mock.patch(
                "mcdr2restic.commands.restic_commands.run_restic_command",
                side_effect=BackupProblem(i18n_key="error.backup.cancel_requested"),
            ),
        ):
            commands.command_exec(source, {"args": "version"})

        self.assertIn("停止请求", source.replies[-1])
        self.assertFalse(runtime.backup.lock.locked())


if __name__ == "__main__":
    unittest.main()
