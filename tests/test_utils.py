try:
    from .support import (
        FakeCommandSource,
        FakePluginServer,
        RuntimeStatusProviders,
        StatusViewContext,
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
        RuntimeStatusProviders,
        StatusViewContext,
        make_source_translate,
        non_negative_int,
        render_status_output,
        safe_int,
        schedule_status_text,
        tail_text,
        threading,
        unittest,
    )

from mcdr2restic.core.presentation import (
    append_snapshot_cache_notes,
    format_snapshot_paths_preview,
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

        # 固定间隔模式直接返回间隔文案，不再"X 秒后（固定间隔 X 秒）"重复（P3-18）
        self.assertEqual(schedule_status_text(cfg, False, "zh_cn"), "固定间隔 60 秒")
        self.assertEqual(schedule_status_text(cfg, True, "zh_cn"), "关闭")

    def test_schedule_status_text_disabled_when_plugin_stopped(self):
        # 插件停用后调度线程并不排程，状态页不得再显示倒计时（P3-3）
        cfg = {
            "enabled": False,
            "schedule": {"interval_seconds": 60, "cron_expression": "0"},
            "force_schedule": {"interval_seconds": 0, "cron_expression": "0 3 * * *"},
        }

        self.assertEqual(schedule_status_text(cfg, False, "zh_cn"), "关闭")
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
            StatusViewContext.create(
                cfg, "zh_cn", server, make_source_translate(source, server)
            ),
            RuntimeStatusProviders(
                backup_running=lambda: False,
                restore_running=lambda: False,
                mc_ready=lambda _: True,
            ),
            threading.Lock(),
            1,
        )

        self.assertIn("MCDR2Restic 状态", output)
        self.assertIn("正常备份：固定间隔 60 秒", output)

    def test_status_output_includes_success_maintenance_and_pending_rows(self):
        class Pending:
            scheduled_time_text = "2026-01-01 00:00:00"

        source = FakeCommandSource(language="zh_cn")
        server = FakePluginServer(language="en_us")
        cfg = {
            "enabled": True,
            "runtime": {"last_backup_status": "never"},
            "schedule": {"interval_seconds": 60, "cron_expression": "0"},
            "force_schedule": {"interval_seconds": 0, "cron_expression": "0"},
            "snapshot_cache": {"enabled": False},
        }

        output = render_status_output(
            StatusViewContext.create(
                cfg, "zh_cn", server, make_source_translate(source, server)
            ),
            RuntimeStatusProviders(
                backup_running=lambda: False,
                restore_running=lambda: False,
                mc_ready=lambda _: True,
                pending_backup=lambda: Pending(),
            ),
            threading.Lock(),
            1,
        )

        self.assertIn("距上次成功备份（服务器运行时间）：从未成功备份", output)
        self.assertIn("上次仓库维护：从未执行", output)
        self.assertIn("推迟中的定时备份：原计划 2026-01-01 00:00:00", output)

    def test_status_output_uses_server_running_hours_for_last_success(self):
        source = FakeCommandSource(language="zh_cn")
        server = FakePluginServer(language="en_us")
        cfg = {
            "enabled": True,
            "runtime": {
                "last_backup_status": "success",
                "last_success_backup_end_time": "2026-01-01 00:00:00",
                "backup_running_seconds_since_success": 25 * 3600,
                "server_running_since_epoch": None,
            },
            "schedule": {"interval_seconds": 60, "cron_expression": "0"},
            "force_schedule": {"interval_seconds": 0, "cron_expression": "0"},
            "snapshot_cache": {"enabled": False},
        }

        output = render_status_output(
            StatusViewContext.create(
                cfg, "zh_cn", server, make_source_translate(source, server)
            ),
            RuntimeStatusProviders(
                backup_running=lambda: False,
                restore_running=lambda: False,
                mc_ready=lambda _: True,
            ),
            threading.Lock(),
            1,
        )

        self.assertIn(
            "距上次成功备份（服务器运行时间）：25 小时（上次成功：2026-01-01 00:00:00）",
            output,
        )

    def test_snapshot_paths_show_directory_names_instead_of_full_paths(self):
        preview = format_snapshot_paths_preview(
            "C:\\MinecraftServer\\iris\\world, C:\\MinecraftServer\\iris\\world_nether"
        )

        self.assertEqual(preview, "world, world_nether")

    def test_snapshot_paths_accept_json_list_and_keep_unknown_shapes(self):
        self.assertEqual(
            format_snapshot_paths_preview(
                '["/srv/mc/world", "/srv/mc/plugins"]'
            ),
            "world, plugins",
        )
        self.assertEqual(
            format_snapshot_paths_preview("relative/tail/"), "tail"
        )

    def test_first_refresh_failure_does_not_claim_old_cache(self):
        def translate(key, **params):
            return "{}:{}".format(key, params.get("error", ""))

        lines = []

        append_snapshot_cache_notes(
            lines,
            translate,
            {"error": "restic not found", "updated_at_text": None},
            "",
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("snapshot.cache.refresh_failed", lines[0])
        self.assertNotIn("snapshot.cache.stale_error", lines[0])

    def test_refresh_failure_with_existing_cache_reports_stale_cache(self):
        def translate(key, **params):
            return "{}:{}".format(key, params.get("error", ""))

        lines = []

        append_snapshot_cache_notes(
            lines,
            translate,
            {"error": "timeout", "updated_at_text": "2026-01-01 00:00:00"},
            "",
        )

        self.assertIn("snapshot.cache.stale_error", lines[0])

