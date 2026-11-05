try:
    from .support import (
        FakePluginServer,
        OneBotClient,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        FakePluginServer,
        OneBotClient,
        mock,
        unittest,
    )


def build_client(server, **overrides):
    cfg = {
        "enabled": True,
        "ws_url": "ws://127.0.0.1:8777",
        "access_token": "secret-token",
        "use_header_auth": False,
    }
    cfg.update(overrides)
    return OneBotClient(server, cfg, websocket_module=mock.Mock())


class OneBotAuthTests(unittest.TestCase):
    def test_query_auth_appends_token_to_url(self):
        server = FakePluginServer()
        client = build_client(server)

        url, headers = client._build_connect_auth()

        self.assertIn("access_token=secret-token", url)
        self.assertIsNone(headers)

    def test_header_auth_keeps_url_clean(self):
        server = FakePluginServer()
        client = build_client(server, use_header_auth=True)

        url, headers = client._build_connect_auth()

        self.assertNotIn("access_token", url)
        self.assertEqual(headers, {"Authorization": "Bearer secret-token"})


class OneBotInvalidNumberSettingTests(unittest.TestCase):
    def test_reconnect_loop_survives_invalid_interval(self):
        # 重连线程死亡后永不重启，垃圾间隔值必须回退默认并告警（P1-2）
        server = FakePluginServer()
        client = build_client(server, reconnect_interval_seconds="soon")
        client.websocket_client.WebSocketApp.return_value.run_forever = mock.Mock()
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            client.stop_event.set()

        with mock.patch.object(client, "_sleep", side_effect=fake_sleep):
            client._thread_main()

        self.assertEqual(sleeps, [5])
        self.assertTrue(
            any(
                "reconnect_interval_seconds" in message
                for message in server.logger.warning_messages
            )
        )

    def test_send_private_msg_survives_invalid_timeout(self):
        server = FakePluginServer()
        client = build_client(server, send_timeout_seconds="fast")

        with mock.patch.object(client.connected_event, "wait", return_value=False):
            client._send_private_msg(123, "hello")

        self.assertTrue(
            any(
                "reconnect_interval_seconds" not in message
                and "send_timeout_seconds" in message
                for message in server.logger.warning_messages
            )
        )
        self.assertTrue(
            any("未连接" in message for message in server.logger.warning_messages)
        )

    def test_valid_number_setting_keeps_silent(self):
        server = FakePluginServer()
        client = build_client(server, reconnect_interval_seconds=7)

        self.assertEqual(client._float_setting("reconnect_interval_seconds", 5), 7.0)
        self.assertEqual(server.logger.warning_messages, [])


class OneBotErrorMaskingTests(unittest.TestCase):
    def test_connection_error_log_masks_token(self):
        server = FakePluginServer()
        client = build_client(server)

        client._handle_connection_error(
            None, Exception("connect to ws://127.0.0.1:8777/?access_token=secret-token failed")
        )

        message = server.logger.warning_messages[0]
        self.assertNotIn("secret-token", message)
        self.assertIn("access_token=***", message)

    def test_connect_once_exception_log_masks_token(self):
        server = FakePluginServer()
        client = build_client(server)
        client.websocket_client.setdefaulttimeout = mock.Mock()
        client.websocket_client.WebSocketApp.side_effect = Exception(
            "handshake failed for ws://127.0.0.1:8777/?access_token=secret-token"
        )

        client._connect_once()

        message = server.logger.warning_messages[0]
        self.assertNotIn("secret-token", message)
        self.assertIn("access_token=***", message)

    def test_safe_url_masks_token_but_keeps_rest(self):
        masked = OneBotClient._safe_url(
            "ws://host:8777/?access_token=secret-token&foo=bar"
        )

        self.assertEqual(masked, "ws://host:8777/?access_token=***&foo=bar")


if __name__ == "__main__":
    unittest.main()
