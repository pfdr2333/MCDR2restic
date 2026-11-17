# -*- coding: utf-8 -*-
try:
    from .support import (
        FakePluginServer,
        SimpleNamespace,
        build_default_config,
        create_runtime,
        datetime,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        FakePluginServer,
        SimpleNamespace,
        build_default_config,
        create_runtime,
        datetime,
        mock,
        unittest,
    )

from mcdr2restic.backup import backup_runner
from mcdr2restic.backup.backup_runner import BackupRunner
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.minecraft.player_gate_service import GateWaitStatus
from mcdr2restic.staging import staging_hint


def hint_config(notify_on_skip=True):
    cfg = build_default_config()
    cfg["runtime"] = build_default_runtime()
    cfg["notification"]["notify_on_skip"] = notify_on_skip
    return cfg


class StagingHintTest(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime()
        self.runtime.config_state.config = hint_config()
        self.server = FakePluginServer()
        self.notifier = mock.Mock()

    def test_hint_is_shown_once_then_cools_down(self):
        with mock.patch.object(
            staging_hint,
            "update_staging_runtime",
            side_effect=lambda runtime, server=None, **values: runtime.config_state.config[
                "runtime"
            ].update(values),
        ) as update:
            shown = staging_hint.maybe_show_staging_hint(
                self.runtime,
                self.server,
                self.runtime.config_state.config,
                self.notifier,
            )
            shown_again = staging_hint.maybe_show_staging_hint(
                self.runtime,
                self.server,
                self.runtime.config_state.config,
                self.notifier,
            )

        self.assertTrue(shown)
        self.assertFalse(shown_again)
        update.assert_called_once()
        self.assertTrue(self.server.logger.warning_messages)
        self.assertIn("beta", self.server.logger.warning_messages[0])
        self.assertIn("m2r.pfdr.cc/staging", self.server.logger.warning_messages[0])
        self.notifier.assert_called_once()
        self.assertFalse(self.notifier.call_args.kwargs["log"])

    def test_enabled_staging_never_hints(self):
        cfg = hint_config()
        cfg["staging"]["enabled"] = True

        self.assertFalse(
            staging_hint.maybe_show_staging_hint(
                self.runtime, self.server, cfg, self.notifier
            )
        )
        self.assertFalse(self.server.logger.warning_messages)

    def test_notify_on_skip_false_only_logs(self):
        self.runtime.config_state.config = hint_config(notify_on_skip=False)

        with mock.patch.object(
            staging_hint,
            "update_staging_runtime",
        ):
            shown = staging_hint.maybe_show_staging_hint(
                self.runtime,
                self.server,
                self.runtime.config_state.config,
                self.notifier,
            )

        self.assertTrue(shown)
        self.notifier.assert_not_called()
        self.assertTrue(self.server.logger.warning_messages)

    def test_cooldown_boundary(self):
        self.assertFalse(
            staging_hint.hint_cooldown_passed(
                {"staging_hint_last_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            )
        )
        self.assertTrue(
            staging_hint.hint_cooldown_passed(
                {"staging_hint_last_at": "2020-01-01 00:00:00"}
            )
        )
        self.assertTrue(staging_hint.hint_cooldown_passed({}))


class BackupRunnerHintBranchTest(unittest.TestCase):
    def make_runner(self):
        runtime = create_runtime()
        runtime.config_state.config = hint_config()
        return BackupRunner(
            runtime,
            lambda _runtime: False,
            mock.Mock(),
            mock.Mock(),
        )

    def occurrence(self):
        return SimpleNamespace(
            scheduled_at="2026-01-01 00:00:00",
            next_due=datetime.now(),
        )

    def test_deadline_branch_emits_hint(self):
        runner = self.make_runner()
        server = FakePluginServer()

        with (
            mock.patch.object(runner, "_skip_scheduled"),
            mock.patch.object(
                backup_runner, "maybe_show_staging_hint"
            ) as hint,
        ):
            runner._handle_gate_abort(
                server, self.occurrence(), GateWaitStatus.DEADLINE
            )

        hint.assert_called_once()

    def test_non_deadline_branches_do_not_emit_hint(self):
        runner = self.make_runner()
        server = FakePluginServer()

        with (
            mock.patch.object(runner, "_skip_scheduled"),
            mock.patch.object(
                backup_runner, "maybe_show_staging_hint"
            ) as hint,
        ):
            for status in (
                GateWaitStatus.CANCELED,
                GateWaitStatus.DISABLED,
                GateWaitStatus.STOPPING,
            ):
                runner._handle_gate_abort(server, self.occurrence(), status)

        hint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
