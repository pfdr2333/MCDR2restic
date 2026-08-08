try:
    from .support import (
        build_discord_mentions,
        render_message,
        truncate_discord_content,
        unittest,
    )
except ImportError:
    from support import (
        build_discord_mentions,
        render_message,
        truncate_discord_content,
        unittest,
    )
class NotificationTests(unittest.TestCase):
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
