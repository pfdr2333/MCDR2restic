try:
    from .support import (
        BackupScheduler,
        CronExpression,
        FakePluginServer,
        SchedulerDependencies,
        compute_maintenance_wait_seconds,
        datetime,
        mock,
        parse_daily_time,
        tr_error,
        unittest,
    )
except ImportError:
    from support import (
        BackupScheduler,
        CronExpression,
        FakePluginServer,
        SchedulerDependencies,
        compute_maintenance_wait_seconds,
        datetime,
        mock,
        parse_daily_time,
        tr_error,
        unittest,
    )

from mcdr2restic.backup.cron import parse_field_part
from mcdr2restic.backup.scheduling import (
    ScheduleSpec,
    compute_defer_deadline,
)


class SchedulingTests(unittest.TestCase):
    def test_parse_daily_time(self):
        self.assertEqual(parse_daily_time("07:30"), (7, 30))

    def test_parse_daily_time_fails_fast(self):
        with self.assertRaises(ValueError):
            parse_daily_time("25:00")

    def test_parse_daily_time_non_integer_is_localized(self):
        with self.assertRaises(Exception) as error:
            parse_daily_time("aa:30")

        self.assertEqual(
            error.exception.i18n_key, "error.schedule.daily_time_not_integer"
        )
        self.assertIn("aa:30", tr_error("en_us", error.exception))

    def test_interval_seconds_non_integer_is_localized(self):
        with self.assertRaises(Exception) as error:
            compute_maintenance_wait_seconds(
                {"maintenance_schedule": {"interval_seconds": "abc"}}
            )

        self.assertEqual(
            error.exception.i18n_key, "error.schedule.interval_not_integer"
        )
        self.assertIn("abc", tr_error("en_us", error.exception))

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
            SchedulerDependencies(
                config_provider=lambda: {"enabled": True},
                backup_runner=lambda target, label: True,
                scheduled_backup_runner=lambda target, occurrence: True,
                maintenance_runner=lambda target, scheduled_at: True,
                mc_ready_provider=lambda target: True,
                skip_predicate=lambda cfg: False,
                admin_notifier=lambda key, data, cfg, important: None,
                record_backup_skip=lambda scheduled, reason, message: None,
            ),
        )
        triggered = []
        spec = type(
            "FakeSpec",
            (),
            {"next_after": lambda self, after: after + datetime.resolution},
        )()

        def trigger(due, next_due):
            triggered.append((due, next_due))
            scheduler.stop_event.set()

        with mock.patch.object(scheduler, "_wait", return_value=False):
            scheduler._run_schedule_loop("schedule.normal", lambda cfg: spec, trigger)

        self.assertEqual(len(triggered), 1)
        self.assertIn("MCDR2Restic 正常调度线程已启动", server.logger.info_messages)


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

    def test_cron_value_not_integer_is_localized(self):
        with self.assertRaises(Exception) as error:
            parse_field_part("abc", 0, 59)

        self.assertEqual(error.exception.i18n_key, "error.cron.value_not_integer")
        self.assertIn("abc", tr_error("en_us", error.exception))

    def test_cron_error_carries_i18n_key(self):
        with self.assertRaises(Exception) as error:
            CronExpression("* * *")

        self.assertEqual(error.exception.i18n_key, "error.cron.fields")
        self.assertIn(
            "Cron expression must have 6 fields", tr_error("en_us", error.exception)
        )

    def test_interval_spec_advances_from_anchor(self):
        anchor = datetime(2026, 1, 1, 6, 0, 0)
        spec = ScheduleSpec(interval_seconds=3600)

        self.assertEqual(
            spec.next_after(anchor), datetime(2026, 1, 1, 7, 0, 0)
        )

    def test_cron_spec_advances_from_previous_plan_point(self):
        spec = ScheduleSpec(cron=CronExpression("0 0 6,9 * * *"))

        self.assertEqual(
            spec.next_after(datetime(2026, 1, 1, 6, 0, 0)),
            datetime(2026, 1, 1, 9, 0, 0),
        )

    def test_auto_defer_deadline_uses_next_due_minus_reserve(self):
        scheduled = datetime(2026, 1, 1, 6, 0, 0)
        next_due = datetime(2026, 1, 1, 9, 0, 0)

        deadline = compute_defer_deadline(
            scheduled, next_due, last_duration_seconds=1200, min_gap_seconds=1800,
            max_defer_seconds=0,
        )

        self.assertEqual(deadline, datetime(2026, 1, 1, 8, 30, 0))

    def test_explicit_max_defer_overrides_auto_deadline(self):
        scheduled = datetime(2026, 1, 1, 6, 0, 0)
        next_due = datetime(2026, 1, 1, 9, 0, 0)

        deadline = compute_defer_deadline(
            scheduled, next_due, last_duration_seconds=0, min_gap_seconds=0,
            max_defer_seconds=600,
        )

        self.assertEqual(deadline, datetime(2026, 1, 1, 6, 10, 0))

