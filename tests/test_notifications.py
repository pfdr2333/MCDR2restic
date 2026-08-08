try:
    from .support import (
        DiscordWebhookClient,
        FakePluginServer,
        build_discord_mentions,
        build_discord_request,
        json,
        mock,
        render_message,
        truncate_discord_content,
        unittest,
    )
except ImportError:
    from support import (
        DiscordWebhookClient,
        FakePluginServer,
        build_discord_mentions,
        build_discord_request,
        json,
        mock,
        render_message,
        truncate_discord_content,
        unittest,
    )


class NotificationTests(unittest.TestCase):
    def test_discord_request_contains_json_payload(self):
        request = build_discord_request(
            "https://example.test/webhook", {"content": "hello"}
        )

        self.assertEqual(request.full_url, "https://example.test/webhook")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b'{"content": "hello"}')

    def test_discord_send_posts_payload_without_real_network(self):
        server = FakePluginServer()
        client = DiscordWebhookClient(
            server,
            {
                "enabled": True,
                "webhook_url": "https://example.test/webhook",
                "send_timeout_seconds": 7,
            },
        )
        response = mock.MagicMock(status=204)
        response.__enter__.return_value = response

        with mock.patch(
            "mcdr2restic.notifications.discord_webhook.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            client._send_message("hello")

        urlopen.assert_called_once()
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        self.assertEqual(json.loads(urlopen.call_args.args[0].data)["content"], "hello")
        self.assertFalse(server.logger.warning_messages)

    def test_discord_send_logs_network_failure(self):
        server = FakePluginServer()
        client = DiscordWebhookClient(
            server,
            {"enabled": True, "webhook_url": "https://example.test/webhook"},
        )

        with mock.patch(
            "mcdr2restic.notifications.discord_webhook.urllib.request.urlopen",
            side_effect=OSError("offline"),
        ):
            client._send_message("hello")

        self.assertEqual(len(server.logger.warning_messages), 1)

    def test_disabled_discord_client_does_not_start_thread(self):
        client = DiscordWebhookClient(FakePluginServer(), {"enabled": False})

        with mock.patch(
            "mcdr2restic.notifications.discord_webhook.threading.Thread"
        ) as thread:
            client.send_message("hello")

        thread.assert_not_called()

    def test_render_message_keeps_unknown_placeholders(self):
        cfg = {
            "onebot": {"message_prefix": "[T]"},
            "messages": {"custom": "{prefix} {name} {missing}"},
        }
        self.assertEqual(
            render_message("custom", {"name": "ok"}, cfg), "[T] ok {missing}"
        )

    def test_discord_mentions_are_explicit_and_ordered(self):
        cfg = {
            "mention_everyone": True,
            "mention_role_ids": [" 1 ", ""],
            "mention_user_ids": ["2"],
        }

        self.assertEqual(build_discord_mentions(cfg), ["@everyone", "<@&1>", "<@2>"])

    def test_discord_content_truncates_to_webhook_limit(self):
        self.assertEqual(len(truncate_discord_content("x" * 2100)), 2000)
