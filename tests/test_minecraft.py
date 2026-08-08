try:
    from .support import (
        FakePluginServer,
        ProbeServer,
        create_runtime,
        has_recent_player_activity,
        parse_online_list_output,
        resolve_known_online_players,
        runtime_player_set,
        server_is_running,
        try_call_bool,
        unittest,
    )
except ImportError:
    from support import (
        FakePluginServer,
        ProbeServer,
        create_runtime,
        has_recent_player_activity,
        parse_online_list_output,
        resolve_known_online_players,
        runtime_player_set,
        server_is_running,
        try_call_bool,
        unittest,
    )


class PlayerActivityTests(unittest.TestCase):
    def test_parse_english_list_output(self):
        count, names = parse_online_list_output(
            "There are 2 of a max of 20 players online: Steve, Alex"
        )
        self.assertEqual(count, 2)
        self.assertEqual(names, ["Steve", "Alex"])

    def test_parse_chinese_list_output(self):
        count, names = parse_online_list_output("当前有 3 个玩家在线")
        self.assertEqual(count, 3)
        self.assertEqual(names, [])

    def test_runtime_player_set_defends_against_invalid_shape(self):
        self.assertEqual(runtime_player_set({"known_online_players": object()}), set())

    def test_resolve_known_online_players_prefers_sample_names(self):
        runtime_state = {"known_online_players": ["Steve"]}
        self.assertEqual(
            resolve_known_online_players(runtime_state, 2, ["Alex", "Steve"]),
            ["Alex", "Steve"],
        )

    def test_has_recent_player_activity_detects_join(self):
        self.assertTrue(
            has_recent_player_activity({"player_joined_since_last_check": True})
        )

    def test_has_recent_player_activity_is_false_when_idle(self):
        self.assertFalse(has_recent_player_activity({"current_online_players": 0}))


class MinecraftServiceTests(unittest.TestCase):
    def test_try_call_bool_logs_probe_failure(self):
        server = ProbeServer()

        result = try_call_bool(server, server.is_server_startup, "is_server_startup")

        self.assertIsNone(result)
        self.assertIn("is_server_startup", server.logger.debug_messages[0])

    def test_server_is_running_falls_back_to_cached_ready_after_probe_failure(self):
        runtime = create_runtime()
        runtime.service.server_ready = True
        server = ProbeServer()

        self.assertTrue(server_is_running(runtime, server))
        self.assertEqual(server.startup_calls, 1)
