try:
    from .support import (
        BackupProblem,
        TerminateResult,
        io,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        BackupProblem,
        TerminateResult,
        io,
        mock,
        unittest,
    )

import threading

from mcdr2restic.core.models import BackupCanceled
from mcdr2restic.restic import restic_runner


class AssertResticProcessCompletedTests(unittest.TestCase):
    @staticmethod
    def _runtime_with_cancel(set_cancel: bool):
        runtime = mock.Mock()
        runtime.backup.cancel = threading.Event()
        if set_cancel:
            runtime.backup.cancel.set()
        return runtime

    def test_cancel_arriving_after_successful_finish_stays_success(self):
        # restic 已成功收尾时取消才刚到达：快照已落库，不得标 canceled（P5-1）
        runtime = self._runtime_with_cancel(set_cancel=True)
        outcome = restic_runner.ResticProcessOutcome("", "", 0, False)
        restic_runner.assert_restic_process_completed(
            runtime, {}, "backup", 0.0, outcome
        )

    def test_cancel_with_interrupted_process_raises_backup_canceled(self):
        runtime = self._runtime_with_cancel(set_cancel=True)
        outcome = restic_runner.ResticProcessOutcome("", "", 1, False)
        with self.assertRaises(BackupCanceled):
            restic_runner.assert_restic_process_completed(
                runtime, {}, "backup", 0.0, outcome
            )

    def test_without_cancel_timeout_still_raises_timeout_problem(self):
        runtime = self._runtime_with_cancel(set_cancel=False)
        outcome = restic_runner.ResticProcessOutcome("", "", 1, True)
        with self.assertRaises(BackupProblem):
            restic_runner.assert_restic_process_completed(
                runtime, {}, "backup", 0.0, outcome
            )


class ResticRunnerTests(unittest.TestCase):
    def test_prepare_json_args_inserts_before_separator(self):
        self.assertEqual(
            restic_runner.prepare_restic_args_for_phase(
                ["backup", "world", "--", "literal"], "backup"
            ),
            ["backup", "world", "--json", "--", "literal"],
        )
        self.assertEqual(
            restic_runner.prepare_restic_args_for_phase(
                ["restore", "--json", "snapshot"], "restore"
            ),
            ["restore", "--json", "snapshot"],
        )
        self.assertEqual(
            restic_runner.prepare_restic_args_for_phase(["forget"], "maintenance"),
            ["forget"],
        )

    def test_progress_interval_is_clamped_and_recovers_from_invalid_value(self):
        self.assertEqual(
            restic_runner.get_restic_progress_interval(
                {"progress_interval_seconds": -1}
            ),
            1.0,
        )
        self.assertEqual(
            restic_runner.get_restic_progress_interval(
                {"progress_interval_seconds": "invalid"}
            ),
            5.0,
        )

    def test_read_restic_stream_preserves_lines_and_end_marker(self):
        output = restic_runner.queue.Queue()

        restic_runner.read_restic_stream("stdout", io.StringIO("one\ntwo\n"), output)

        self.assertEqual(output.get_nowait(), ("stdout", "one\n"))
        self.assertEqual(output.get_nowait(), ("stdout", "two\n"))
        self.assertEqual(output.get_nowait(), ("stdout", None))

    def test_wait_for_restic_exit_terminates_process_after_timeout(self):
        process = mock.Mock()
        process.wait.side_effect = restic_runner.subprocess.TimeoutExpired("restic", 5)

        with mock.patch.object(
            restic_runner,
            "terminate_process",
            return_value=TerminateResult(graceful=True),
        ) as terminate, self.assertRaises(BackupProblem) as error:
            restic_runner.wait_for_restic_exit(process, "en_us")

        terminate.assert_called_once_with(process)
        self.assertEqual(
            error.exception.i18n_key,
            "error.restic.process_still_running_after_output",
        )

    def test_append_output_line_routes_stderr_separately(self):
        stdout = []
        stderr = []

        restic_runner.append_restic_output_line("stdout", "out\n", stdout, stderr)
        restic_runner.append_restic_output_line("stderr", "err\n", stdout, stderr)

        self.assertEqual(stdout, ["out\n"])
        self.assertEqual(stderr, ["err\n"])
