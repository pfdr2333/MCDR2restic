try:
    from .support import FakePluginServer, mock, unittest
except ImportError:
    from support import FakePluginServer, mock, unittest

import time
from datetime import datetime, timedelta

from mcdr2restic.backup.backup_scheduler import BackupScheduler, SchedulerDependencies
from mcdr2restic.backup.scheduling import ScheduleSpec
from mcdr2restic.core.models import BackupTrigger


def due_times():
    now = datetime.now()
    return now, now + timedelta(hours=3)


class BackupSchedulerTests(unittest.TestCase):
    def make_scheduler(self, cfg=None, ready=True, skip=False):
        config = cfg or {"enabled": True, "notification": {"notify_on_skip": True}}
        config_provider = mock.Mock(return_value=config)
        backup_runner = mock.Mock()
        scheduled_runner = mock.Mock()
        maintenance_runner = mock.Mock()
        ready_provider = mock.Mock(return_value=ready)
        skip_predicate = mock.Mock(return_value=skip)
        notifier = mock.Mock()
        record_skip = mock.Mock()
        scheduler = BackupScheduler(
            FakePluginServer(),
            SchedulerDependencies(
                config_provider=config_provider,
                backup_runner=backup_runner,
                scheduled_backup_runner=scheduled_runner,
                maintenance_runner=maintenance_runner,
                mc_ready_provider=ready_provider,
                skip_predicate=skip_predicate,
                admin_notifier=notifier,
                record_backup_skip=record_skip,
            ),
        )
        return scheduler, scheduled_runner, backup_runner, maintenance_runner, notifier, record_skip

    def test_normal_trigger_rejects_not_ready_server(self):
        scheduler, scheduled_runner, _, _, notifier, record_skip = self.make_scheduler(
            ready=False
        )

        scheduler._trigger_normal_backup(*due_times())

        scheduled_runner.assert_not_called()
        self.assertEqual(notifier.call_args.args[0], "backup_not_ready")
        self.assertTrue(notifier.call_args.args[3])
        self.assertEqual(record_skip.call_args.args[1], "not_ready")

    def test_normal_trigger_skips_idle_period_and_notifies(self):
        scheduler, scheduled_runner, _, _, notifier, record_skip = self.make_scheduler(
            skip=True
        )

        scheduler._trigger_normal_backup(*due_times())

        scheduled_runner.assert_not_called()
        self.assertEqual(notifier.call_args.args[0], "backup_skipped")
        self.assertFalse(notifier.call_args.args[3])
        self.assertEqual(record_skip.call_args.args[1], "no_activity")

    def test_normal_trigger_starts_deferred_occurrence(self):
        scheduler, scheduled_runner, _, _, _, _ = self.make_scheduler()

        due, next_due = due_times()
        scheduler._trigger_normal_backup(due, next_due)

        scheduled_runner.assert_called_once()
        occurrence = scheduled_runner.call_args.args[1]
        self.assertEqual(occurrence.scheduled_datetime, due)
        self.assertEqual(occurrence.next_due, next_due)
        self.assertIsNotNone(occurrence.deadline_monotonic)

    def test_normal_trigger_skips_within_min_gap(self):
        now = datetime.now()
        cfg = {
            "enabled": True,
            "schedule": {"min_gap_seconds": 1800},
            "notification": {"notify_on_skip": True},
            "runtime": {
                "last_backup_end_time": (
                    now - timedelta(seconds=60)
                ).strftime("%Y-%m-%d %H:%M:%S")
            },
        }
        scheduler, scheduled_runner, _, _, notifier, record_skip = self.make_scheduler(
            cfg=cfg
        )

        scheduler._trigger_normal_backup(now, now + timedelta(hours=3))

        scheduled_runner.assert_not_called()
        self.assertEqual(record_skip.call_args.args[1], "min_gap")
        self.assertEqual(notifier.call_args.args[0], "backup_skipped")

    def test_forced_trigger_runs_even_when_normal_skip_predicate_is_true(self):
        scheduler, _, backup_runner, _, _, _ = self.make_scheduler(skip=True)

        scheduler._trigger_forced_backup(*due_times())

        backup_runner.assert_called_once_with(scheduler.server, BackupTrigger.FORCED)

    def test_maintenance_trigger_respects_disable_and_backup_priority(self):
        scheduler, _, _, maintenance_runner, _, _ = self.make_scheduler(
            cfg={"enabled": False}
        )
        scheduler._trigger_maintenance(*due_times())
        maintenance_runner.assert_not_called()

        scheduler, _, _, maintenance_runner, _, _ = self.make_scheduler()
        with mock.patch.object(scheduler, "_wait", return_value=True):
            scheduler._trigger_maintenance(*due_times())
        maintenance_runner.assert_not_called()

        with mock.patch.object(scheduler, "_wait", return_value=False):
            due, next_due = due_times()
            scheduler._trigger_maintenance(due, next_due)
        maintenance_runner.assert_called_once_with(
            scheduler.server, due.strftime("%Y-%m-%d %H:%M:%S")
        )

    def test_schedule_error_notifies_and_returns_no_spec(self):
        scheduler, _, _, _, notifier, _ = self.make_scheduler()

        with mock.patch(
            "mcdr2restic.backup.backup_scheduler.parse_schedule_spec",
            side_effect=ValueError("invalid schedule"),
        ):
            result = scheduler._normal_spec({})

        self.assertIsNone(result)
        self.assertEqual(notifier.call_args.args[0], "schedule_config_error")
        self.assertTrue(notifier.call_args.args[3])

    def test_schedule_loop_survives_trigger_crash(self):
        scheduler, _, _, _, _, _ = self.make_scheduler(
            cfg={"enabled": True, "notification": True}
        )
        spec = ScheduleSpec(interval_seconds=1)
        calls = {"count": 0}

        def trigger(due, next_due):
            calls["count"] += 1
            raise AttributeError("'bool' object has no attribute 'get'")

        def fake_wait(seconds):
            if calls["count"] >= 1:
                scheduler.stop_event.set()
            return False

        with mock.patch.object(scheduler, "_wait", side_effect=fake_wait):
            scheduler._run_schedule_loop(
                "schedule.normal", lambda cfg: spec, trigger
            )

        self.assertEqual(calls["count"], 1)
        self.assertTrue(scheduler.server.logger.error_messages)

    def test_handle_no_player_activity_skip_tolerates_scalar_notification(self):
        scheduler, _, _, _, notifier, record_skip = self.make_scheduler(
            cfg={"enabled": True, "notification": True}
        )

        scheduler._handle_no_player_activity_skip({"notification": True}, "2026-01-01 00:00:00")

        notifier.assert_not_called()
        record_skip.assert_called_once()

    def test_wait_honors_wakeup_set_before_wait_starts(self):
        # 两次 _wait 之间到达的 wakeup 不得被随后的 clear 抹掉
        scheduler, _, _, _, _, _ = self.make_scheduler()

        scheduler.wakeup()

        normal_event = scheduler._wakeup_events["MCDR2Restic-Scheduler-Normal"]
        started = time.monotonic()
        self.assertTrue(scheduler._wait(30.0, normal_event))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(normal_event.is_set())

    def test_wakeup_sets_every_scheduler_thread_event(self):
        # 一次 wakeup 必须同时唤醒全部三个调度线程，而非被其中一个独占（P5-3）
        scheduler, _, _, _, _, _ = self.make_scheduler()

        scheduler.wakeup()

        started = time.monotonic()
        for event in scheduler._wakeup_events.values():
            self.assertTrue(scheduler._wait(30.0, event))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_wait_returns_false_when_deadline_reached_without_wakeup(self):
        scheduler, _, _, _, _, _ = self.make_scheduler()

        started = time.monotonic()
        self.assertFalse(scheduler._wait(0.05))
        self.assertGreaterEqual(time.monotonic() - started, 0.05)
        self.assertFalse(scheduler._foreign_wakeup_event.is_set())
