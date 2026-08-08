try:
    from .support import (
        BackupScheduler,
        CronExpression,
        FakePluginServer,
        compute_maintenance_wait_seconds,
        datetime,
        parse_daily_time,
        tr_error,
        unittest,
    )
except ImportError:
    from support import (
        BackupScheduler,
        CronExpression,
        FakePluginServer,
        compute_maintenance_wait_seconds,
        datetime,
        parse_daily_time,
        tr_error,
        unittest,
    )
class SchedulingTests(unittest.TestCase):
    def test_parse_daily_time(self):
        self.assertEqual(parse_daily_time("07:30"), (7, 30))

    def test_parse_daily_time_fails_fast(self):
        with self.assertRaises(ValueError):
            parse_daily_time("25:00")

    def test_maintenance_schedule_empty_cron_uses_default(self):
        wait_seconds, due_text = compute_maintenance_wait_seconds(
            {"maintenance_schedule": {"interval_seconds": 0, "cron_expression": ""}}
        )

        self.assertGreater(wait_seconds, 0)
        self.assertRegex(due_text, r"\d{4}-\d{2}-\d{2} 03:00:00")

    def test_scheduler_loop_triggers_ready_schedule_once(self):
        server = FakePluginServer()
        scheduler = BackupScheduler(
            server,
            lambda: {"enabled": True},
            lambda target, label: True,
            lambda target: True,
            lambda target: True,
            lambda cfg: False,
            lambda key, data, cfg, important: None,
        )
        triggered = []

        def trigger():
            triggered.append(True)
            scheduler.stop_event.set()

        scheduler._run_schedule_loop("测试", lambda: (0, "now"), trigger)

        self.assertEqual(triggered, [True])
        self.assertIn("MCDR2Restic 测试调度线程已启动", server.logger.info_messages)

class CronTests(unittest.TestCase):
    def test_next_after_skips_current_second(self):
        cron = CronExpression("0 0 3 * * *")

        self.assertEqual(
            cron.next_after(datetime(2024, 1, 1, 3, 0, 0)),
            datetime(2024, 1, 2, 3, 0, 0),
        )

    def test_next_after_uses_step_seconds(self):
        cron = CronExpression("*/15 * * * * *")

        self.assertEqual(
            cron.next_after(datetime(2024, 1, 1, 0, 0, 14)),
            datetime(2024, 1, 1, 0, 0, 15),
        )

    def test_next_after_maps_sunday_seven_to_zero(self):
        cron = CronExpression("0 0 0 * * 7")

        self.assertEqual(
            cron.next_after(datetime(2024, 1, 6, 23, 59, 59)),
            datetime(2024, 1, 7, 0, 0, 0),
        )

    def test_cron_error_carries_i18n_key(self):
        with self.assertRaises(Exception) as error:
            CronExpression("* * *")

        self.assertEqual(error.exception.i18n_key, "error.cron.fields")
        self.assertIn(
            "Cron expression must have 6 fields", tr_error("en_us", error.exception)
        )
