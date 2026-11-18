# -*- coding: utf-8 -*-
try:
    from .support import FakePluginServer, SimpleNamespace, mock, threading, unittest
except ImportError:
    from support import FakePluginServer, SimpleNamespace, mock, threading, unittest

import time
from datetime import datetime, timedelta

from mcdr2restic.backup import gate_stall, stale_backup_watchdog
from mcdr2restic.backup.gate_stall import (
    clear_gate_stall,
    gate_stall_elapsed_seconds,
    mark_gate_stall_started,
)
from mcdr2restic.backup.stale_backup_watchdog import (
    StaleBackupWatchdog,
    maybe_warn_stale_backup,
)


def format_time(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def make_runtime(cfg, server_ready=True):
    return SimpleNamespace(
        config_state=SimpleNamespace(
            config=cfg, state={}, lock=threading.RLock()
        ),
        service=SimpleNamespace(
            server_ready=server_ready, stopping=threading.Event()
        ),
        backup=SimpleNamespace(cancel=threading.Event()),
    )


def make_cfg(  # noqa: PLR0913 测试夹具一次性给全场景参数
    elapsed_hours=25.0,
    threshold=24,
    gate_count=3,
    started_at="2026-01-01 00:00:00",
    last_success_hours_ago=30.0,
    warned_at=None,
    current=6,
    max_online=5,
    stalled=True,
):
    last_success = datetime.now() - timedelta(hours=last_success_hours_ago)
    return {
        "command": {"root": "!!restic"},
        "notification": {
            "notify_on_skip": False,
            "stale_backup_warning_hours": threshold,
        },
        "schedule": {"backup_player_gate_max_online": max_online},
        "runtime": {
            "last_success_backup_end_time": format_time(last_success),
            "backup_running_seconds_since_success": int(elapsed_hours * 3600),
            "server_running_since_epoch": None,
            "gate_stall_baseline_seconds": 0 if stalled else None,
            "gate_stall_started_at": started_at if stalled else None,
            "gate_deferral_count_since_success": gate_count,
            "stale_backup_warned_at": warned_at,
            "current_online_players": current,
        },
    }


class StaleBackupWarningTests(unittest.TestCase):
    def call(self, cfg, server_ready=True):
        runtime = make_runtime(cfg, server_ready=server_ready)
        server = FakePluginServer()
        if not server_ready:
            server.is_server_startup = lambda: False
        notifier = mock.Mock()
        with mock.patch.object(
            stale_backup_watchdog, "save_config_unlocked"
        ):
            warned = maybe_warn_stale_backup(
                runtime, server, cfg, notifier
            )
        return warned, notifier, cfg, server

    def test_warns_once_after_threshold(self):
        warned, notifier, cfg, server = self.call(make_cfg())

        self.assertTrue(warned)
        notifier.assert_called_once()
        self.assertEqual(notifier.call_args.args[0], "backup_stale_warning")
        self.assertTrue(notifier.call_args.args[3])
        self.assertTrue(server.logger.warning_messages)
        self.assertIn(
            "2026-01-01 00:00:00",
            server.logger.warning_messages[0],
        )
        self.assertIsNotNone(cfg["runtime"]["stale_backup_warned_at"])

    def test_no_warning_before_threshold(self):
        warned, notifier, _, _ = self.call(make_cfg(elapsed_hours=2))

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_no_warning_without_gate_stall_start(self):
        warned, notifier, _, _ = self.call(make_cfg(stalled=False))

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_zero_hours_disables_warning(self):
        warned, notifier, _, _ = self.call(make_cfg(threshold=0))

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_disabled_backups_never_warn(self):
        cfg = make_cfg()
        cfg["enabled"] = False

        warned, notifier, _, _ = self.call(cfg)

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_stopping_plugin_never_warns(self):
        cfg = make_cfg()
        runtime = make_runtime(cfg)
        runtime.service.stopping.set()
        notifier = mock.Mock()

        warned = maybe_warn_stale_backup(
            runtime, FakePluginServer(), cfg, notifier
        )

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_stopped_server_never_warns(self):
        runtime = make_runtime(make_cfg(), server_ready=False)
        server = FakePluginServer()
        server.is_server_startup = lambda: False
        server.is_server_running = lambda: False
        notifier = mock.Mock()

        warned = maybe_warn_stale_backup(
            runtime, server, runtime.config_state.config, notifier
        )

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_cooldown_suppresses_repeat_within_24_hours(self):
        cfg = make_cfg(warned_at=format_time(datetime.now() - timedelta(hours=1)))
        warned, notifier, _, _ = self.call(cfg)

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_reminds_again_after_cooldown(self):
        cfg = make_cfg(
            elapsed_hours=50,
            warned_at=format_time(datetime.now() - timedelta(hours=25)),
        )

        warned, notifier, _, _ = self.call(cfg)

        self.assertTrue(warned)
        notifier.assert_called_once()

    def test_active_running_segment_counts(self):
        cfg = make_cfg(elapsed_hours=0)
        cfg["runtime"]["backup_running_seconds_since_success"] = 0
        cfg["runtime"]["server_running_since_epoch"] = time.time() - 25 * 3600

        warned, notifier, _, _ = self.call(cfg)

        self.assertTrue(warned)
        notifier.assert_called_once()

    def test_resolves_success_anchor_from_legacy_status(self):
        cfg = make_cfg()
        cfg["runtime"].pop("last_success_backup_end_time")
        cfg["runtime"]["last_backup_status"] = "success"
        cfg["runtime"]["last_backup_end_time"] = format_time(
            datetime.now() - timedelta(hours=30)
        )

        warned, notifier, _, _ = self.call(cfg)

        self.assertTrue(warned)
        notifier.assert_called_once()

    def test_downtime_does_not_advance_stall_elapsed(self):
        cfg = make_cfg(elapsed_hours=100)
        cfg["runtime"]["backup_running_seconds_since_success"] = 0
        cfg["runtime"]["gate_stall_baseline_seconds"] = 0

        warned, notifier, _, _ = self.call(cfg)

        self.assertFalse(warned)
        notifier.assert_not_called()


class GateStallStateTests(unittest.TestCase):
    def test_mark_records_baseline_only_once(self):
        cfg = {"runtime": {"backup_running_seconds_since_success": 3600}}
        runtime = make_runtime(cfg)
        server = FakePluginServer()

        with mock.patch.object(gate_stall, "save_config_unlocked") as save:
            self.assertTrue(mark_gate_stall_started(runtime, server))
            self.assertFalse(mark_gate_stall_started(runtime, server))

        state = cfg["runtime"]
        self.assertEqual(state["gate_stall_baseline_seconds"], 3600)
        self.assertIsNotNone(state["gate_stall_started_at"])
        self.assertEqual(save.call_count, 1)

    def test_elapsed_uses_running_clock(self):
        state = {
            "backup_running_seconds_since_success": 7200,
            "server_running_since_epoch": None,
            "gate_stall_baseline_seconds": 3600,
        }

        self.assertEqual(gate_stall_elapsed_seconds(state), 3600)

    def test_elapsed_without_baseline_is_none(self):
        self.assertIsNone(
            gate_stall_elapsed_seconds({"gate_stall_baseline_seconds": None})
        )

    def test_clear_removes_baseline_and_timestamp(self):
        state = {
            "gate_stall_baseline_seconds": 100,
            "gate_stall_started_at": "2026-01-01 00:00:00",
        }

        clear_gate_stall(state)

        self.assertIsNone(state["gate_stall_baseline_seconds"])
        self.assertIsNone(state["gate_stall_started_at"])


class StaleWatchdogThreadTests(unittest.TestCase):
    def test_run_once_delegates_to_warning_check(self):
        cfg = make_cfg()
        runtime = make_runtime(cfg)
        runtime.service.server = FakePluginServer()
        notifier = mock.Mock()
        watchdog = StaleBackupWatchdog(runtime, notifier, poll_seconds=0.01)
        with mock.patch.object(
            stale_backup_watchdog,
            "get_config_snapshot",
            return_value=cfg,
        ), mock.patch.object(
            stale_backup_watchdog, "save_config_unlocked"
        ), mock.patch.object(
            stale_backup_watchdog, "settle_running_clock"
        ) as settle:
            self.assertTrue(watchdog.run_once())

        settle.assert_called_once()
        notifier.assert_called_once()

    def test_stop_terminates_thread(self):
        runtime = make_runtime(make_cfg(stalled=False))
        runtime.service.server = None
        watchdog = StaleBackupWatchdog(runtime, mock.Mock(), poll_seconds=0.01)
        watchdog.start()
        watchdog.stop()

        self.assertFalse(watchdog.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
