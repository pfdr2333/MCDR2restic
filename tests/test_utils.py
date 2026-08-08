try:
    from .support import (
        FakeCommandSource,
        FakePluginServer,
        make_source_translate,
        non_negative_int,
        render_status_output,
        safe_int,
        schedule_status_text,
        tail_text,
        threading,
        unittest,
    )
except ImportError:
    from support import (
        FakeCommandSource,
        FakePluginServer,
        make_source_translate,
        non_negative_int,
        render_status_output,
        safe_int,
        schedule_status_text,
        tail_text,
        threading,
        unittest,
    )


class UtilsTests(unittest.TestCase):
    def test_safe_int_uses_default_on_bad_input(self):
        self.assertEqual(safe_int("oops", 7), 7)

    def test_non_negative_int_clamps_negative_values(self):
        self.assertEqual(non_negative_int(-5), 0)

    def test_tail_text_keeps_short_text(self):
        self.assertEqual(tail_text("abc", 10), "abc")

    def test_schedule_status_text_uses_schedule_helpers(self):
        cfg = {
            "schedule": {"interval_seconds": 60, "cron_expression": "0"},
            "force_schedule": {"interval_seconds": 0, "cron_expression": "0"},
        }

        self.assertEqual(
            schedule_status_text(cfg, False, "zh_cn"), "60 秒后（固定间隔 60 秒）"
        )
        self.assertEqual(schedule_status_text(cfg, True, "zh_cn"), "关闭")

    def test_render_status_output_uses_source_translate(self):
        source = FakeCommandSource(language="zh_cn")
        server = FakePluginServer(language="en_us")
        cfg = {
            "enabled": True,
            "runtime": {
                "current_online_players": 0,
                "last_backup_status": "never",
            },
            "schedule": {"interval_seconds": 60, "cron_expression": "0"},
            "force_schedule": {"interval_seconds": 0, "cron_expression": "0"},
            "snapshot_cache": {"enabled": False},
        }

        output = render_status_output(
            threading.Lock(),
            cfg,
            "zh_cn",
            server,
            1,
            backup_running_provider=lambda: False,
            restore_running_provider=lambda: False,
            mc_ready_provider=lambda _: True,
            translate=make_source_translate(source, server),
        )

        self.assertIn("MCDR2Restic 状态", output)
        self.assertIn("正常备份: 60 秒后（固定间隔 60 秒）", output)
