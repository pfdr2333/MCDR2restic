try:
    from .support import (
        CustomWebhookClient,
        build_default_config,
        DiscordWebhookClient,
        FakePluginServer,
        NotificationDispatcher,
        build_discord_mentions,
        build_discord_request,
        build_webhook_request,
        create_runtime,
        json,
        mock,
        render_message,
        render_payload_leaves,
        truncate_discord_content,
        unittest,
    )
except ImportError:
    from support import (
        CustomWebhookClient,
        build_default_config,
        DiscordWebhookClient,
        FakePluginServer,
        NotificationDispatcher,
        build_discord_mentions,
        build_discord_request,
        build_webhook_request,
        create_runtime,
        json,
        mock,
        render_message,
        render_payload_leaves,
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
            "mcdr2restic.notifications.webhook_base.threading.Thread"
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


class CustomWebhookTests(unittest.TestCase):
    def test_webhook_request_carries_headers_and_json(self):
        request = build_webhook_request(
            "https://example.test/hook",
            {"content": "hello"},
            {"headers": {"Authorization": "Bearer t"}},
        )

        self.assertEqual(request.full_url, "https://example.test/hook")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(request.headers["Authorization"], "Bearer t")
        self.assertEqual(json.loads(request.data)["content"], "hello")

    def test_webhook_send_posts_rendered_payload_without_real_network(self):
        server = FakePluginServer()
        client = CustomWebhookClient(
            server,
            {
                "enabled": True,
                "url": "https://example.test/hook",
                "payload": {"msg_type": "text", "content": {"text": "{message}"}},
            },
        )
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response

        with mock.patch(
            "mcdr2restic.notifications.custom_webhook.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            client._send_message("备份成功", {"label": "正常"})

        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["msg_type"], "text")
        self.assertEqual(payload["content"]["text"], "备份成功")
        self.assertFalse(server.logger.warning_messages)

    def test_webhook_payload_uses_default_when_not_configured(self):
        client = CustomWebhookClient(FakePluginServer(), {"enabled": True})

        payload = client._build_payload("备份完成", {"label": "强制"})

        self.assertEqual(payload, {"content": "备份完成"})

    def test_webhook_payload_keeps_unknown_placeholders(self):
        payload = render_payload_leaves(
            {"content": "{message} {label} {missing}"}, {"message": "ok"}
        )

        self.assertEqual(payload["content"], "ok {label} {missing}")

    def test_webhook_payload_values_override_defaults(self):
        client = CustomWebhookClient(
            FakePluginServer(),
            {"enabled": True, "payload": {"content": "{label} {message}"}},
        )

        payload = client._build_payload("正文", {"label": "强制"})

        self.assertEqual(payload, {"content": "强制 正文"})

    def test_disabled_webhook_client_does_not_start_thread(self):
        server = FakePluginServer()
        client = CustomWebhookClient(server, {"enabled": False})

        with mock.patch(
            "mcdr2restic.notifications.webhook_base.threading.Thread"
        ) as thread:
            client.send_message("hello")

        thread.assert_not_called()

    def test_webhook_send_warns_on_empty_url(self):
        server = FakePluginServer()
        client = CustomWebhookClient(server, {"enabled": True})

        client._send_message("hello", {})

        self.assertIn("url 为空", server.logger.warning_messages[0])

    def test_webhook_send_logs_network_failure(self):
        server = FakePluginServer()
        client = CustomWebhookClient(
            server, {"enabled": True, "url": "https://example.test/hook"}
        )

        with mock.patch(
            "mcdr2restic.notifications.custom_webhook.urllib.request.urlopen",
            side_effect=OSError("offline"),
        ):
            client._send_message("hello", {})

        self.assertIn("发送失败", server.logger.warning_messages[0])

    def test_default_config_contains_custom_webhook_block(self):
        self.assertEqual(
            build_default_config()["custom_webhook"]["payload"], {"content": "{message}"}
        )

    def test_dispatcher_skips_webhook_when_disabled(self):
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, dict)
        app_runtime.service.server = FakePluginServer()
        client = mock.Mock()
        app_runtime.service.custom_webhook = client

        dispatcher._notify_channel(
            dispatcher._channel_specs()[2],
            "backup_start",
            {"label": "正常"},
            {"custom_webhook": {"enabled": False}},
            "zh_cn",
        )

        client.send_message.assert_not_called()

    def test_dispatcher_sends_webhook_when_enabled(self):
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, dict)
        app_runtime.service.server = FakePluginServer()
        client = mock.Mock()
        app_runtime.service.custom_webhook = client

        dispatcher._notify_channel(
            dispatcher._channel_specs()[2],
            "backup_start",
            {"label": "正常", "start_time": "10:00"},
            {"custom_webhook": {"enabled": True}},
            "zh_cn",
        )

        client.send_message.assert_called_once()
        text = client.send_message.call_args.args[0]
        self.assertIn("备份开始", text)

    def test_notify_admins_tolerates_scalar_channel_sections(self):
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, dict)
        server = FakePluginServer()
        app_runtime.service.server = server

        dispatcher.notify_admins(
            "backup_start",
            {"label": "manual"},
            {"onebot": "xx", "discord": 1, "custom_webhook": True},
        )

        self.assertTrue(server.logger.info_messages)
        self.assertEqual(server.logger.warning_messages, [])
