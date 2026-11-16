# -*- coding: utf-8 -*-
try:
    from .support import FakePluginServer, SimpleNamespace, mock, threading, unittest
except ImportError:
    from support import FakePluginServer, SimpleNamespace, mock, threading, unittest

import time
from datetime import datetime, timedelta

from mcdr2restic.backup import stale_backup_warning
from mcdr2restic.backup.stale_backup_warning import maybe_warn_stale_backup


def make_runtime(cfg):
    return SimpleNamespace(
        config_state=SimpleNamespace(
            config=cfg, state={}, lock=threading.RLock()
        ),
        service=SimpleNamespace(server_ready=True, stopping=threading.Event()),
        backup=SimpleNamespace(cancel=threading.Event()),
    )


def make_cfg(hours_ago=30.0, threshold=24, gate_count=3):
    last_success = datetime.now() - timedelta(hours=hours_ago)
    return {
        "command": {"root": "!!restic"},
        "notification": {
            "notify_on_skip": False,
            "stale_backup_warning_hours": threshold,
        },
        "runtime": {
            "last_success_backup_end_time": last_success.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "backup_running_seconds_since_success": int(hours_ago * 3600),
            "server_running_since_epoch": None,
            "gate_deferral_count_since_success": gate_count,
            "stale_backup_warned_at": None,
        },
    }


class StaleBackupWarningTests(unittest.TestCase):
    def call(self, cfg, current=6, max_online=5):
        runtime = make_runtime(cfg)
        server = FakePluginServer()
        notifier = mock.Mock()
        with mock.patch.object(
            stale_backup_warning, "save_config_unlocked"
        ):
            warned = maybe_warn_stale_backup(
                runtime, server, cfg, notifier, (current, max_online)
            )
        return warned, notifier, cfg, server

    def test_warns_once_after_threshold(self):
        warned, notifier, cfg, server = self.call(make_cfg())

        self.assertTrue(warned)
        notifier.assert_called_once()
        self.assertEqual(notifier.call_args.args[0], "backup_stale_warning")
        self.assertTrue(notifier.call_args.args[3])
        self.assertTrue(server.logger.warning_messages)
        self.assertIsNotNone(
            cfg["runtime"]["stale_backup_warned_at"]
        )

    def test_cooldown_suppresses_repeat_within_24_hours(self):
        cfg = make_cfg()
        self.call(cfg)
        warned, notifier, _, _ = self.call(cfg)

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_no_warning_before_threshold(self):
        warned, notifier, _, _ = self.call(make_cfg(hours_ago=2))

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_zero_hours_disables_warning(self):
        warned, notifier, _, _ = self.call(make_cfg(threshold=0))

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_stopped_server_never_warns(self):
        runtime = make_runtime(make_cfg())
        runtime.service.server_ready = False
        server = FakePluginServer()
        notifier = mock.Mock()

        warned = maybe_warn_stale_backup(
            runtime, server, runtime.config_state.config, notifier, (6, 5)
        )

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_downtime_is_not_counted(self):
        cfg = make_cfg(hours_ago=100)
        cfg["runtime"]["backup_running_seconds_since_success"] = 0

        warned, notifier, _, _ = self.call(cfg)

        self.assertFalse(warned)
        notifier.assert_not_called()

    def test_active_running_segment_counts(self):
        cfg = make_cfg(hours_ago=0)
        cfg["runtime"]["backup_running_seconds_since_success"] = 0
        cfg["runtime"]["server_running_since_epoch"] = time.time() - 25 * 3600

        warned, notifier, _, _ = self.call(cfg)

        self.assertTrue(warned)
        notifier.assert_called_once()

    def test_resolves_success_anchor_from_legacy_status(self):
        cfg = make_cfg()
        cfg["runtime"].pop("last_success_backup_end_time")
        cfg["runtime"]["last_backup_status"] = "success"
        cfg["runtime"]["last_backup_end_time"] = cfg["runtime"].get(
            "last_backup_end_time"
        ) or (datetime.now() - timedelta(hours=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        warned, notifier, _, _ = self.call(cfg)

        self.assertTrue(warned)
        notifier.assert_called_once()


if __name__ == "__main__":
    unittest.main()
