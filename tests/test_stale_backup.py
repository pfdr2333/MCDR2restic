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
    reset_gate_stall_on_disabled,
)
from mcdr2restic.backup.stale_backup_watchdog import (
    StaleBackupWatchdog,
    maybe_warn_stale_backup,
    parse_stale_warning_hours,
    warn_invalid_stale_warning_config,
)
from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.notifications.message_templates import (
    MessageRenderOptions,
    render_message,
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

    def test_notification_body_uses_new_stall_semantics(self):
        # 通知正文曾停留在旧模板：缺首次推迟时间，且把停滞时长写成累计运行（P2-1）
        cfg = make_cfg()
        cfg["messages"] = build_default_config("zh_cn")["messages"]

        warned, notifier, cfg, _ = self.call(cfg)
        values = notifier.call_args.args[1]
        body = render_message(
            "backup_stale_warning",
            values,
            cfg,
            MessageRenderOptions(language="zh_cn"),
        )

        self.assertTrue(warned)
        self.assertIn("2026-01-01 00:00:00", body)
        self.assertIn("已等待 25 小时", body)
        self.assertNotIn("已累计运行", body)

    def test_notification_is_not_logged_twice(self):
        # 控制台已输出新版文案，通知渠道不再重复打一条旧正文（P3-1）
        _, notifier, _, _ = self.call(make_cfg())

        self.assertFalse(notifier.call_args.kwargs["log"])

    def test_future_cooldown_timestamp_does_not_suppress_warning(self):
        cfg = make_cfg(
            warned_at=format_time(datetime.now() + timedelta(hours=2))
        )

        warned, notifier, _, server = self.call(cfg)

        self.assertTrue(warned)
        notifier.assert_called_once()
        self.assertTrue(
            any(
                "晚于当前时间" in message
                for message in server.logger.warning_messages
            )
        )

    def test_zero_deferral_count_is_reported_as_zero(self):
        # 手工改状态时计数可能为 0，不得虚报成“推迟 1 次”（P3-6）
        cfg = make_cfg(gate_count=0)

        warned, notifier, _, _ = self.call(cfg)

        self.assertTrue(warned)
        self.assertEqual(notifier.call_args.args[1]["skips"], 0)


class StaleWarningThresholdTests(unittest.TestCase):
    def test_valid_threshold_is_used_as_is(self):
        self.assertEqual(parse_stale_warning_hours(30), (30, False))
        self.assertEqual(parse_stale_warning_hours(0), (0, False))

    def test_missing_threshold_falls_back_silently(self):
        self.assertEqual(parse_stale_warning_hours(None), (24, False))

    def test_non_numeric_threshold_falls_back_and_flags(self):
        self.assertEqual(parse_stale_warning_hours("abc"), (24, True))

    def test_negative_threshold_disables_and_flags(self):
        self.assertEqual(parse_stale_warning_hours(-1), (0, True))

    def test_invalid_threshold_warns_on_load(self):
        server = FakePluginServer()
        cfg = make_cfg(threshold="abc")

        warn_invalid_stale_warning_config(server, cfg)

        self.assertTrue(
            any(
                "stale_backup_warning_hours" in message
                for message in server.logger.warning_messages
            )
        )

    def test_valid_threshold_does_not_warn_on_load(self):
        server = FakePluginServer()

        warn_invalid_stale_warning_config(server, make_cfg(threshold=12))

        self.assertFalse(server.logger.warning_messages)


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

    def test_disable_drops_stall_clock_and_cooldown(self):
        # 停用期间继续计时会让重新启用后立刻按停用期告警（P3-2）
        state = {
            "gate_stall_baseline_seconds": 100,
            "gate_stall_started_at": "2026-01-01 00:00:00",
            "stale_backup_warned_at": "2026-01-01 01:00:00",
        }

        self.assertTrue(reset_gate_stall_on_disabled(state))

        self.assertIsNone(state["gate_stall_baseline_seconds"])
        self.assertIsNone(state["gate_stall_started_at"])
        self.assertIsNone(state["stale_backup_warned_at"])

    def test_disable_without_stall_is_noop(self):
        state = {
            "gate_stall_baseline_seconds": None,
            "gate_stall_started_at": None,
            "stale_backup_warned_at": None,
        }

        self.assertFalse(reset_gate_stall_on_disabled(state))


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
