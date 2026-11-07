try:
    from .support import (
        CustomWebhookClient,
        build_default_config,
        DiscordWebhookClient,
        FakePluginServer,
        NotificationDispatcher,
        MessageRenderOptions,
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
        MessageRenderOptions,
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

import inspect
import threading
from datetime import date
from typing import ClassVar



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

    def test_message_prefix_null_falls_back_to_default(self):
        # message_prefix 显式置空（YAML null）时不得渲染成字面 "None"（P3-5）
        cfg = {
            "onebot": {"message_prefix": None},
            "messages": {"custom": "{prefix} {name}"},
        }
        self.assertEqual(
            render_message("custom", {"name": "ok"}, cfg), "[MCDR2Restic] ok"
        )

    def test_missing_message_key_falls_back_to_render_language(self):
        # messages 删键后兜底模板须跟随渲染语言，en_us 不得收到中文（P3-6）
        cfg = {"onebot": {"message_prefix": "[T]"}, "messages": {}}
        rendered = render_message(
            "backup_failure",
            {"label": "manual", "status": "success", "message": "m", "detail": "d"},
            cfg,
            MessageRenderOptions(language="en_us"),
        )
        self.assertTrue(rendered.startswith("[T] Backup problem"))

    def test_render_failure_falls_back_to_translated_text(self):
        # 模板格式化失败时兜底不得直出内部键名（P3-19）
        cfg = {
            "onebot": {"message_prefix": "[T]"},
            "messages": {"custom": "{prefix} {value.dotted}"},
        }
        rendered = render_message(
            "custom", {"value": {"dotted": "x"}}, cfg
        )
        self.assertEqual(rendered, "[T] 通知消息渲染失败")

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

    def test_webhook_payload_invalid_template_warns_and_falls_back(self):
        server = FakePluginServer()
        broken_template = {"content": "{message"}
        client = CustomWebhookClient(server, {"enabled": True, "payload": broken_template})

        payload = client._build_payload("正文", {})

        self.assertEqual(payload, {"content": "{message"})
        self.assertTrue(
            any("模板渲染失败" in message for message in server.logger.warning_messages)
        )

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

    def test_webhook_send_survives_garbage_timeout(self):
        # 超时解析原来在 try 之外，垃圾值会让 daemon 线程静默死亡、通知无声丢失（P1-1）
        server = FakePluginServer()
        client = CustomWebhookClient(
            server,
            {"enabled": True, "url": "https://example.test/hook",
             "send_timeout_seconds": "10s"},
        )

        client._send_message("hello", {})

        self.assertEqual(len(server.logger.warning_messages), 1)
        self.assertIn("发送失败", server.logger.warning_messages[0])

    def test_webhook_send_survives_scalar_headers(self):
        server = FakePluginServer()
        client = CustomWebhookClient(
            server,
            {"enabled": True, "url": "https://example.test/hook", "headers": "x"},
        )

        client._send_message("hello", {})

        self.assertEqual(len(server.logger.warning_messages), 1)
        self.assertIn("发送失败", server.logger.warning_messages[0])

    def test_webhook_send_survives_unserializable_payload_value(self):
        # YAML 日期等值无法 JSON 序列化，构造请求阶段抛 TypeError 必须转为告警
        server = FakePluginServer()
        client = CustomWebhookClient(
            server,
            {"enabled": True, "url": "https://example.test/hook",
             "payload": {"content": "{message}", "since": date(2026, 1, 1)}},
        )

        client._send_message("hello", {})

        self.assertEqual(len(server.logger.warning_messages), 1)
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

    def test_onebot_admin_qqs_invalid_type_warns_without_sending(self):
        # 字符串会被逐字符拆分并向错误 QQ 号真实发送，非列表必须告警跳过（P0-4）
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, dict)
        server = FakePluginServer()
        app_runtime.service.server = server
        onebot = mock.Mock()
        app_runtime.service.onebot = onebot

        for bad in ("123456789", 123456789, {"1": "2"}):
            dispatcher._deliver_onebot(
                onebot, {"enabled": True, "admin_qqs": bad}, "text", None
            )

        onebot.send_private_msg.assert_not_called()
        self.assertEqual(len(server.logger.warning_messages), 3)
        self.assertTrue(
            all("admin_qqs" in m for m in server.logger.warning_messages)
        )

    def test_onebot_admin_qqs_list_sends_to_each_number(self):
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, dict)
        app_runtime.service.server = FakePluginServer()
        onebot = mock.Mock()
        app_runtime.service.onebot = onebot

        dispatcher._deliver_onebot(
            onebot, {"admin_qqs": [111, 222]}, "text", None
        )

        onebot.send_private_msg.assert_has_calls(
            [mock.call(111, "text"), mock.call(222, "text")]
        )


class ChannelContractTests(unittest.TestCase):
    """dispatcher 与三通道客户端之间的签名契约（P0-1 回归防护）。"""

    def test_deliver_specs_accept_uniform_arguments(self):
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, dict)

        for spec in dispatcher._channel_specs():
            with self.subTest(channel=spec.section):
                inspect.signature(spec.deliver).bind(None, {}, "text", {})

    def test_webhook_clients_accept_text_and_values(self):
        for client_cls in (DiscordWebhookClient, CustomWebhookClient):
            with self.subTest(client=client_cls.__name__):
                signature = inspect.signature(client_cls.send_message)
                signature.bind(mock.Mock(), "text", {"label": "x"})

    def test_dispatcher_delivers_real_discord_client_with_payload_intact(self):
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, dict)
        server = FakePluginServer()
        app_runtime.service.server = server
        client = DiscordWebhookClient(
            server,
            {"enabled": True, "webhook_url": "https://example.test/discord"},
        )
        app_runtime.service.discord = client
        response = mock.MagicMock(status=204)
        response.__enter__.return_value = response
        requests = []
        delivered = threading.Event()

        def record(request, timeout=None):
            requests.append(request)
            delivered.set()
            return response

        with mock.patch(
            "mcdr2restic.notifications.webhook_base.urllib.request.urlopen",
            side_effect=record,
        ):
            dispatcher._notify_channel(
                dispatcher._channel_specs()[1],
                "backup_start",
                {"label": "正常"},
                {"discord": {"enabled": True}},
                "zh_cn",
            )
            self.assertTrue(delivered.wait(timeout=5))

        self.assertEqual(server.logger.warning_messages, [])
        payload = json.loads(requests[0].data)
        self.assertIn("备份开始", payload["content"])
        self.assertIn("allowed_mentions", payload)


class _FakeOneBotClient:
    """带真实就绪信号的 OneBot 客户端替身，用于验证提交前的就绪探测。"""

    def __init__(self, connected: bool):
        self.connected_event = threading.Event()
        if connected:
            self.connected_event.set()
        self.sent = []

    def send_private_msg(self, user_id, text):
        self.sent.append((user_id, text))


class NotifyTestTests(unittest.TestCase):
    CHANNEL_CFG: ClassVar[dict] = {
        "onebot": {"enabled": True, "admin_qqs": [123]},
        "discord": {"enabled": True},
        "custom_webhook": {"enabled": True},
    }

    @staticmethod
    def make_dispatcher(cfg):
        app_runtime = create_runtime()
        dispatcher = NotificationDispatcher(app_runtime, lambda: cfg)
        app_runtime.service.server = FakePluginServer()
        return app_runtime, dispatcher

    @staticmethod
    def status_by_section(results):
        return {result.section: result.status for result in results}

    def test_reports_sent_for_ready_channels(self):
        app_runtime, dispatcher = self.make_dispatcher(self.CHANNEL_CFG)
        app_runtime.service.onebot = mock.Mock()
        app_runtime.service.discord = mock.Mock()
        app_runtime.service.custom_webhook = mock.Mock()

        statuses = self.status_by_section(
            dispatcher.send_test_notification(self.CHANNEL_CFG)
        )

        self.assertEqual(
            statuses,
            {"onebot": "sent", "discord": "sent", "custom_webhook": "sent"},
        )
        app_runtime.service.onebot.send_private_msg.assert_called_once()

    def test_reports_not_started_when_onebot_ws_not_connected(self):
        # OneBot 发送是先起线程再等连接，客户端非空不代表已连上：
        # notify-test 须同步探测就绪信号，不得恒报"已发送"（P2-1）
        app_runtime, dispatcher = self.make_dispatcher(self.CHANNEL_CFG)
        client = _FakeOneBotClient(connected=False)
        app_runtime.service.onebot = client

        statuses = self.status_by_section(
            dispatcher.send_test_notification(self.CHANNEL_CFG)
        )

        self.assertEqual(statuses["onebot"], "not_started")
        self.assertEqual(client.sent, [])

    def test_reports_sent_when_onebot_ws_connected(self):
        app_runtime, dispatcher = self.make_dispatcher(self.CHANNEL_CFG)
        client = _FakeOneBotClient(connected=True)
        app_runtime.service.onebot = client

        statuses = self.status_by_section(
            dispatcher.send_test_notification(self.CHANNEL_CFG)
        )

        self.assertEqual(statuses["onebot"], "sent")
        self.assertEqual([user_id for user_id, _ in client.sent], [123])
        text = client.sent[0][1]
        # 前缀与正文之间须有分隔，且模板正文不再自带插件名（P3-2）
        self.assertTrue(text.startswith("[MCDR2Restic] "))
        self.assertNotIn("MCDR2Restic]MCDR2Restic", text)

    def test_reports_disabled_for_unenabled_channel(self):
        cfg = dict(self.CHANNEL_CFG, discord={"enabled": False})
        app_runtime, dispatcher = self.make_dispatcher(cfg)
        app_runtime.service.onebot = mock.Mock()
        app_runtime.service.discord = mock.Mock()
        app_runtime.service.custom_webhook = mock.Mock()

        statuses = self.status_by_section(dispatcher.send_test_notification(cfg))

        self.assertEqual(statuses["discord"], "disabled")
        app_runtime.service.discord.send_message.assert_not_called()

    def test_reports_not_started_when_client_missing(self):
        app_runtime, dispatcher = self.make_dispatcher(self.CHANNEL_CFG)
        app_runtime.service.onebot = mock.Mock()
        # discord/custom_webhook 客户端保持 None

        statuses = self.status_by_section(dispatcher.send_test_notification(self.CHANNEL_CFG))

        self.assertEqual(statuses["discord"], "not_started")
        self.assertEqual(statuses["custom_webhook"], "not_started")

    def test_reports_failed_when_deliver_raises(self):
        app_runtime, dispatcher = self.make_dispatcher(self.CHANNEL_CFG)
        app_runtime.service.onebot = mock.Mock()
        app_runtime.service.discord = mock.Mock()
        failing = mock.Mock()
        failing.send_message.side_effect = RuntimeError("boom")
        app_runtime.service.custom_webhook = failing

        results = dispatcher.send_test_notification(self.CHANNEL_CFG)
        statuses = self.status_by_section(results)

        self.assertEqual(statuses["custom_webhook"], "failed")
        failed = next(r for r in results if r.section == "custom_webhook")
        self.assertEqual(str(failed.error), "boom")
