# -*- coding: utf-8 -*-
try:
    from .support import SimpleNamespace, mock, threading, unittest
except ImportError:
    from support import SimpleNamespace, mock, threading, unittest

from mcdr2restic.backup import running_time
from mcdr2restic.backup.running_time import (
    reset_running_clock_state,
    running_hours_since_success,
    running_seconds_since_success,
    start_server_running_clock,
    stop_server_running_clock,
)


def make_runtime(runtime_state):
    return SimpleNamespace(
        config_state=SimpleNamespace(
            config={"runtime": runtime_state},
            state={},
            lock=threading.RLock(),
        )
    )


class RunningTimeTests(unittest.TestCase):
    def test_counts_accumulated_and_active_segment(self):
        state = {
            "backup_running_seconds_since_success": 3600,
            "server_running_since_epoch": 1000.0,
        }

        self.assertEqual(
            running_seconds_since_success(state, now=4600.0), 3600 + 3600
        )
        self.assertEqual(running_hours_since_success(state, now=4600.0), 2)

    def test_stopped_clock_does_not_advance(self):
        state = {
            "backup_running_seconds_since_success": 7200,
            "server_running_since_epoch": None,
        }

        self.assertEqual(running_seconds_since_success(state, now=999999.0), 7200)

    def test_start_clock_sets_epoch_once(self):
        runtime = make_runtime({})
        with mock.patch.object(
            running_time.time, "time", return_value=1000.0
        ), mock.patch.object(running_time, "save_config_unlocked") as save:
            self.assertTrue(start_server_running_clock(runtime, mock.Mock()))
            self.assertFalse(start_server_running_clock(runtime, mock.Mock()))

        state = runtime.config_state.config["runtime"]
        self.assertEqual(state["server_running_since_epoch"], 1000.0)
        self.assertEqual(save.call_count, 1)

    def test_stop_clock_accumulates_and_clears(self):
        runtime = make_runtime({"server_running_since_epoch": 1000.0})
        with mock.patch.object(
            running_time.time, "time", return_value=4600.0
        ), mock.patch.object(running_time, "save_config_unlocked"):
            self.assertTrue(stop_server_running_clock(runtime, mock.Mock()))
            self.assertFalse(stop_server_running_clock(runtime, mock.Mock()))

        state = runtime.config_state.config["runtime"]
        self.assertEqual(state["backup_running_seconds_since_success"], 3600)
        self.assertIsNone(state["server_running_since_epoch"])

    def test_reset_clears_accumulated_and_restarts_segment(self):
        state = {
            "backup_running_seconds_since_success": 5000,
            "server_running_since_epoch": 1000.0,
        }

        reset_running_clock_state(state, server_running=True, now=8000.0)

        self.assertEqual(state["backup_running_seconds_since_success"], 0)
        self.assertEqual(state["server_running_since_epoch"], 8000.0)

        reset_running_clock_state(state, server_running=False, now=9000.0)
        self.assertEqual(state["backup_running_seconds_since_success"], 0)
        self.assertIsNone(state["server_running_since_epoch"])


if __name__ == "__main__":
    unittest.main()
