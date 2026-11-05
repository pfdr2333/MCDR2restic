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
