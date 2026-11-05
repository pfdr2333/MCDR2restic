try:
    from .support import FakePluginServer, mock, unittest
except ImportError:
    from support import FakePluginServer, mock, unittest

import time

from mcdr2restic.backup.backup_scheduler import BackupScheduler, SchedulerDependencies
from mcdr2restic.core.models import BackupTrigger


class BackupSchedulerTests(unittest.TestCase):
    def make_scheduler(self, cfg=None, ready=True, skip=False):
        config = cfg or {"enabled": True, "notification": {"notify_on_skip": True}}
        config_provider = mock.Mock(return_value=config)
        backup_runner = mock.Mock()
        maintenance_runner = mock.Mock()
        ready_provider = mock.Mock(return_value=ready)
        skip_predicate = mock.Mock(return_value=skip)
        notifier = mock.Mock()
        scheduler = BackupScheduler(
            FakePluginServer(),
            SchedulerDependencies(
                config_provider=config_provider,
                backup_runner=backup_runner,
                maintenance_runner=maintenance_runner,
                mc_ready_provider=ready_provider,
                skip_predicate=skip_predicate,
                admin_notifier=notifier,
            ),
        )
        return scheduler, backup_runner, maintenance_runner, notifier

    def test_normal_trigger_rejects_not_ready_server(self):
        scheduler, backup_runner, _, notifier = self.make_scheduler(ready=False)

        scheduler._trigger_normal_backup()

        backup_runner.assert_not_called()
        self.assertEqual(notifier.call_args.args[0], "backup_not_ready")
        self.assertTrue(notifier.call_args.args[3])

    def test_normal_trigger_skips_idle_period_and_notifies(self):
        scheduler, backup_runner, _, notifier = self.make_scheduler(skip=True)

        scheduler._trigger_normal_backup()

        backup_runner.assert_not_called()
        self.assertEqual(notifier.call_args.args[0], "backup_skip_no_player")
        self.assertFalse(notifier.call_args.args[3])

    def test_forced_trigger_runs_even_when_normal_skip_predicate_is_true(self):
        scheduler, backup_runner, _, _ = self.make_scheduler(skip=True)

        scheduler._trigger_forced_backup()

        backup_runner.assert_called_once_with(scheduler.server, BackupTrigger.FORCED)

    def test_maintenance_trigger_respects_disable_and_backup_priority(self):
        scheduler, _, maintenance_runner, _ = self.make_scheduler(
            cfg={"enabled": False}
        )
        scheduler._trigger_maintenance()
        maintenance_runner.assert_not_called()

        scheduler, _, maintenance_runner, _ = self.make_scheduler()
        with mock.patch.object(scheduler, "_wait", return_value=True):
            scheduler._trigger_maintenance()
        maintenance_runner.assert_not_called()

        with mock.patch.object(scheduler, "_wait", return_value=False):
            scheduler._trigger_maintenance()
        maintenance_runner.assert_called_once_with(scheduler.server)

    def test_schedule_error_notifies_and_returns_no_schedule(self):
        scheduler, _, _, notifier = self.make_scheduler()

        with mock.patch(
            "mcdr2restic.backup.backup_scheduler.compute_wait_seconds",
            side_effect=ValueError("invalid schedule"),
        ), mock.patch.object(scheduler, "_wait") as wait:
            result = scheduler._next_normal_schedule()

        self.assertIsNone(result)
        self.assertEqual(notifier.call_args.args[0], "schedule_config_error")
        self.assertTrue(notifier.call_args.args[3])
        wait.assert_called_once_with(60)

    def test_schedule_loop_survives_trigger_crash(self):
        scheduler, _, _, _ = self.make_scheduler(
            cfg={"enabled": True, "notification": True}
        )
        state = {"calls": 0}

        def provider():
            state["calls"] += 1
            if state["calls"] == 1:
                return (0.01, "soon")
            scheduler.stop_event.set()
            return None

        trigger = mock.Mock(
            side_effect=AttributeError("'bool' object has no attribute 'get'")
        )
        with mock.patch.object(
            scheduler, "_next_normal_schedule", side_effect=provider
        ), mock.patch.object(
            scheduler, "_trigger_normal_backup", trigger
        ), mock.patch.object(scheduler, "_wait", return_value=False):
            scheduler._run_schedule_loop(
                "schedule.normal",
                scheduler._next_normal_schedule,
                scheduler._trigger_normal_backup,
            )

        trigger.assert_called_once()
        self.assertTrue(scheduler.server.logger.error_messages)

    def test_handle_no_player_activity_skip_tolerates_scalar_notification(self):
        scheduler, _, _, notifier = self.make_scheduler(
            cfg={"enabled": True, "notification": True}
        )

        scheduler._handle_no_player_activity_skip({"notification": True})

        notifier.assert_not_called()

    def test_wait_honors_wakeup_set_before_wait_starts(self):
        # 两次 _wait 之间到达的 wakeup 不得被随后的 clear 抹掉
        scheduler, _, _, _ = self.make_scheduler()

        scheduler.wakeup()

        started = time.monotonic()
        self.assertTrue(scheduler._wait(30.0))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(scheduler.wakeup_event.is_set())

    def test_wait_returns_false_when_deadline_reached_without_wakeup(self):
        scheduler, _, _, _ = self.make_scheduler()

        started = time.monotonic()
        self.assertFalse(scheduler._wait(0.05))
        self.assertGreaterEqual(time.monotonic() - started, 0.05)
        self.assertFalse(scheduler.wakeup_event.is_set())
