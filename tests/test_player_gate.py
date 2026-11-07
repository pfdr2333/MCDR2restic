# -*- coding: utf-8 -*-
try:
    from .support import SimpleNamespace, mock, threading, unittest
except ImportError:
    from support import SimpleNamespace, mock, threading, unittest

import time

from mcdr2restic.minecraft import player_gate_service
from mcdr2restic.minecraft import player_activity_service
from mcdr2restic.minecraft.player_gate_service import (
    PlayerGateMonitor,
    player_gate_settings,
    resolve_online_player_count,
    start_backup_player_gate_monitor,
    wait_for_player_gate,
)


def make_app_runtime(cfg=None, stopping=None):
    runtime_cfg = cfg or {"schedule": {}, "notification": {}}
    return SimpleNamespace(
        config_state=SimpleNamespace(
            config=runtime_cfg,
            lock=threading.Lock(),
            state={},
            player_state_persisted_at=0.0,
        ),
        service=SimpleNamespace(
            stopping=stopping or threading.Event(), server_ready=False
        ),
        backup=SimpleNamespace(cancel=threading.Event()),
    )


def make_server(rcon_output=None, running=True):
    server = mock.MagicMock()
    server.logger = mock.MagicMock()
    server.get_mcdr_language.return_value = "zh_cn"
    server.is_server_startup.return_value = running
    server.is_server_running.return_value = running
    if rcon_output is None:
        server.rcon_query = None
    else:
        server.rcon_query = mock.Mock(return_value=rcon_output)
    return server


class PlayerGateTestBase(unittest.TestCase):
    def setUp(self):
        # 门控采样会刷新玩家状态；单测不落盘，屏蔽文件写入副作用
        persist_patcher = mock.patch.object(
            player_activity_service, "persist_player_state", lambda *a, **k: None
        )
        persist_patcher.start()
        self.addCleanup(persist_patcher.stop)


class PlayerGateSettingsTest(PlayerGateTestBase):
    def test_settings_default_to_disabled(self):
        enabled, max_online, overlimit = player_gate_settings({"schedule": {}})

        self.assertFalse(enabled)
        self.assertEqual(max_online, 5)
        self.assertEqual(overlimit, 600)

    def test_settings_fall_back_on_invalid_values(self):
        cfg = {
            "schedule": {
                "backup_player_gate_enabled": True,
                "backup_player_gate_max_online": "not-a-number",
                "backup_player_gate_overlimit_seconds": -3,
            }
        }

        enabled, max_online, overlimit = player_gate_settings(cfg)

        self.assertTrue(enabled)
        self.assertEqual(max_online, 0)
        self.assertEqual(overlimit, 0)


class ResolveOnlinePlayerCountTest(PlayerGateTestBase):
    def test_stopped_server_counts_as_zero(self):
        app_runtime = make_app_runtime()
        server = make_server(
            "There are 3 of a max of 20 players online: a, b, c", running=False
        )

        self.assertEqual(resolve_online_player_count(app_runtime, server, {}), 0)

    def test_rcon_sample_wins_when_available(self):
        app_runtime = make_app_runtime()
        server = make_server("There are 3 of a max of 20 players online: a, b, c")

        self.assertEqual(
            resolve_online_player_count(
                app_runtime, server, {"schedule": {"online_check_command": "list"}}
            ),
            3,
        )

    def test_falls_back_to_event_counter_without_rcon(self):
        app_runtime = make_app_runtime()
        app_runtime.config_state.config["runtime"] = {"current_online_players": 2}
        server = make_server(None)

        self.assertEqual(
            resolve_online_player_count(
                app_runtime, server, {"schedule": {"online_check_command": "list"}}
            ),
            2,
        )


class WaitForPlayerGateTest(PlayerGateTestBase):
    def setUp(self):
        # 缩短轮询间隔，等待循环的测试才能在毫秒级完成
        self._patcher = mock.patch.object(
            player_gate_service, "PLAYER_GATE_POLL_SECONDS", 0.01
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_returns_true_immediately_when_disabled(self):
        app_runtime = make_app_runtime({"schedule": {"backup_player_gate_enabled": False}})
        server = make_server("There are 3 of a max of 20 players online: a, b, c")

        self.assertTrue(wait_for_player_gate(app_runtime, server, mock.Mock()))

    def test_returns_true_when_count_below_threshold(self):
        app_runtime = make_app_runtime(
            {"schedule": {"backup_player_gate_enabled": True, "backup_player_gate_max_online": 5}}
        )
        server = make_server("There are 3 of a max of 20 players online: a, b, c")
        notifier = mock.Mock()

        self.assertTrue(wait_for_player_gate(app_runtime, server, notifier))
        notifier.assert_not_called()

    def test_waits_until_players_drop_below_threshold(self):
        app_runtime = make_app_runtime(
            {
                "schedule": {
                    "backup_player_gate_enabled": True,
                    "backup_player_gate_max_online": 5,
                },
                "notification": {"notify_on_skip": True},
            }
        )
        server = make_server("There are 6 of a max of 20 players online: a, b, c, d, e, f")
        notifier = mock.Mock()
        result = {}

        def release_gate():
            time.sleep(0.05)
            server.rcon_query.return_value = "There are 0 of a max of 20 players online"

        thread = threading.Thread(target=release_gate)
        thread.start()
        try:
            result["passed"] = wait_for_player_gate(app_runtime, server, notifier)
        finally:
            thread.join()

        self.assertTrue(result["passed"])
        self.assertEqual(notifier.call_args.args[0], "backup_player_gate_waiting")
        self.assertTrue(server.logger.warning.called)

    def test_returns_false_when_plugin_stopping(self):
        stopping = threading.Event()
        app_runtime = make_app_runtime(
            {
                "schedule": {
                    "backup_player_gate_enabled": True,
                    "backup_player_gate_max_online": 5,
                }
            },
            stopping=stopping,
        )
        server = make_server("There are 6 of a max of 20 players online: a, b, c, d, e, f")
        notifier = mock.Mock()

        def stop_soon():
            time.sleep(0.05)
            stopping.set()

        thread = threading.Thread(target=stop_soon)
        thread.start()
        try:
            self.assertFalse(wait_for_player_gate(app_runtime, server, notifier))
        finally:
            thread.join()


class PlayerGateMonitorTest(PlayerGateTestBase):
    def setUp(self):
        self._patcher = mock.patch.object(
            player_gate_service, "PLAYER_GATE_POLL_SECONDS", 0.01
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def make_monitor(self, rcon_output, cfg=None):
        app_runtime = make_app_runtime(
            cfg
            or {
                "schedule": {
                    "backup_player_gate_enabled": True,
                    "backup_player_gate_max_online": 5,
                    "backup_player_gate_overlimit_seconds": 1,
                }
            }
        )
        server = make_server(rcon_output)
        monitor = PlayerGateMonitor(app_runtime, server, app_runtime.config_state.config)
        return monitor, app_runtime, server

    def test_factory_skips_disabled_gate(self):
        monitor = start_backup_player_gate_monitor(
            make_app_runtime(), make_server(), {"schedule": {}}
        )

        self.assertIsNone(monitor)

    def test_interrupts_backup_after_sustained_overlimit(self):
        monitor, _app_runtime, _server = self.make_monitor(
            "There are 6 of a max of 20 players online: a, b, c, d, e, f"
        )
        with mock.patch.object(
            player_gate_service, "request_cancel_current_backup", return_value=None
        ) as cancel:
            monitor.start()
            deadline = time.monotonic() + 5
            while cancel.call_count == 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            monitor.stop()

        cancel.assert_called_once()
        reason = cancel.call_args.args[1]
        self.assertIsInstance(reason, str)
        self.assertIn("阈值", reason)

    def test_resets_timer_when_players_drop_back(self):
        # 持续超阈不足容忍时长：取消不应被触发
        monitor, _app_runtime, _server = self.make_monitor(
            "There are 6 of a max of 20 players online: a, b, c, d, e, f",
            cfg={
                "schedule": {
                    "backup_player_gate_enabled": True,
                    "backup_player_gate_max_online": 5,
                    # 容忍时长远大于轮询周期，几轮采样内不应触发
                    "backup_player_gate_overlimit_seconds": 600,
                }
            },
        )
        with mock.patch.object(
            player_gate_service, "request_cancel_current_backup", return_value=None
        ) as cancel:
            monitor.start()
            time.sleep(0.1)
            monitor.stop()

        cancel.assert_not_called()

    def test_exits_silently_when_backup_already_canceling(self):
        monitor, app_runtime, _server = self.make_monitor(
            "There are 6 of a max of 20 players online: a, b, c, d, e, f"
        )
        app_runtime.backup.cancel.set()
        with mock.patch.object(
            player_gate_service, "request_cancel_current_backup", return_value=None
        ) as cancel:
            monitor.start()
            time.sleep(0.1)
            monitor.stop()

        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
