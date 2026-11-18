# -*- coding: utf-8 -*-
import time

try:
    from .support import (
        BackupProblem,
        FakePluginServer,
        PermissiveCommandSource,
        SimpleNamespace,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        BackupProblem,
        FakePluginServer,
        PermissiveCommandSource,
        SimpleNamespace,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.commands.restic_commands import ResticCommands
from mcdr2restic.commands.staging_commands import StagingCommands
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.staging.staging_config import (
    get_staging_settings,
    resolve_staging_paths,
)
from mcdr2restic.staging.staging_repository import RepoProbe, RepoProbeStatus


def staging_config():
    cfg = build_default_config()
    cfg["staging"].update(
        {"enabled": True, "repository": "/data/staging", "password": "pass"}
    )
    return cfg


def make_context(runtime, server):
    return CommandContext(
        runtime,
        lambda: mock.Mock(),
        lambda: mock.Mock(),
        lambda _server: None,
        lambda: None,
        mock.Mock(),
    )


class StagingCommandTest(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime()
        self.server = FakePluginServer()
        self.runtime.service.server = self.server
        self.source = PermissiveCommandSource(self.server)
        self.context = make_context(self.runtime, self.server)
        self.commands = StagingCommands(self.context)

    def test_status_reports_disabled_without_touching_restic(self):
        self.runtime.config_state.config = build_default_config()

        self.commands.command_status(self.source)

        self.assertEqual(len(self.source.replies), 1)
        self.assertIn("中转库未启用", self.source.replies[0])

    def test_copy_rejects_disabled(self):
        self.runtime.config_state.config = build_default_config()

        self.commands.command_copy(self.source)

        self.assertIn("中转库未启用", self.source.replies[0])

    def test_copy_triggers_worker(self):
        self.runtime.config_state.config = staging_config()
        with mock.patch(
            "mcdr2restic.commands.staging_commands.request_copy",
            return_value=True,
        ) as request:
            self.commands.command_copy(self.source)

        request.assert_called_once_with(self.runtime, manual=True)
        self.assertIn("已触发中转库复制", self.source.replies[0])

    def test_copy_yields_to_restore(self):
        self.runtime.config_state.config = staging_config()
        with mock.patch(
            "mcdr2restic.commands.staging_commands.is_restore_running",
            return_value=True,
        ):
            self.commands.command_copy(self.source)

        self.assertIn("恢复流程正在运行", self.source.replies[0])

    def test_purge_rejects_when_io_lock_busy(self):
        self.runtime.config_state.config = staging_config()
        self.runtime.staging.io_lock.acquire()
        try:
            self.commands.command_purge(self.source)
        finally:
            self.runtime.staging.io_lock.release()

        self.assertIn("另一个中转库任务", self.source.replies[0])

    def test_purge_reports_success_message(self):
        self.runtime.config_state.config = staging_config()
        with mock.patch(
            "mcdr2restic.commands.staging_commands.purge_staging_remote",
            return_value="purged",
        ):
            self.commands.command_purge_force(self.source)

        self.assertEqual(self.source.replies[-1], "purged")

    def test_purge_rechecks_restore_after_acquiring_lock(self):
        # 检查与拿锁之间恢复可能刚好启动；拿锁后必须复检并归还锁（P2-3）
        self.runtime.config_state.config = staging_config()
        with (
            mock.patch(
                "mcdr2restic.commands.staging_commands.is_restore_running",
                side_effect=[False, True],
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.purge_staging_remote"
            ) as purge,
        ):
            self.commands.command_purge(self.source)

        purge.assert_not_called()
        self.assertFalse(self.runtime.staging.io_lock.locked())
        self.assertFalse(self.runtime.staging.purge_active)
        self.assertIn("恢复流程正在运行", self.source.replies[-1])

    def test_purge_marks_purge_active_while_running(self):
        self.runtime.config_state.config = staging_config()
        seen = {}

        def fake_purge(*_args, **_kwargs):
            seen["active"] = self.runtime.staging.purge_active
            return "purged"

        with mock.patch(
            "mcdr2restic.commands.staging_commands.purge_staging_remote",
            side_effect=fake_purge,
        ):
            self.commands.command_purge(self.source)

        self.assertTrue(seen["active"])
        self.assertFalse(self.runtime.staging.purge_active)

    def test_status_renders_mode_snapshots_disk_and_chunker(self):
        cfg = staging_config()
        self.runtime.config_state.config = cfg
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        probes = RepoProbe(RepoProbeStatus.OK, {"chunker_polynomial": "poly"})
        snapshots = [
            {"id": "a" * 64, "short_id": "a" * 8, "time": "2026-01-01T00:00:00Z"}
        ]

        with (
            mock.patch(
                "mcdr2restic.commands.staging_commands.validate_staging_settings",
                return_value=paths,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.read_snapshots",
                return_value=snapshots,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.probe_repository",
                return_value=probes,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.shutil.disk_usage",
                return_value=SimpleNamespace(total=1000, free=500),
            ),
        ):
            self.commands.command_status(self.source)

        output = self.source.replies[0]
        self.assertIn("中转库 (beta)", output)
        self.assertIn("本地最新快照", output)
        self.assertIn("远端最新快照", output)
        self.assertIn("磁盘剩余", output)
        self.assertIn("chunker 参数：一致", output)

    def test_status_shares_one_remote_deadline_across_remote_calls(self):
        # 远端黑洞下 status 曾先后两次各等一个探测周期；现在共用同一个 deadline（P2-6）
        cfg = staging_config()
        self.runtime.config_state.config = cfg
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        probes = RepoProbe(RepoProbeStatus.OK, {"chunker_polynomial": "poly"})
        snapshots = [
            {"id": "a" * 64, "short_id": "a" * 8, "time": "2026-01-01T00:00:00Z"}
        ]

        with (
            mock.patch(
                "mcdr2restic.commands.staging_commands.validate_staging_settings",
                return_value=paths,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.read_snapshots",
                return_value=snapshots,
            ) as read_snapshots_mock,
            mock.patch(
                "mcdr2restic.commands.staging_commands.probe_repository",
                return_value=probes,
            ) as probe_mock,
            mock.patch(
                "mcdr2restic.commands.staging_commands.shutil.disk_usage",
                return_value=SimpleNamespace(total=1000, free=500),
            ),
        ):
            self.commands.command_status(self.source)

        remote_read_deadline = read_snapshots_mock.call_args_list[1].kwargs[
            "deadline"
        ]
        remote_probe_deadline = probe_mock.call_args_list[1].kwargs["deadline"]
        self.assertIsNotNone(remote_read_deadline)
        self.assertEqual(remote_read_deadline, remote_probe_deadline)

    def test_status_surfaces_cursor_anomaly_and_unknown_backlog(self):
        # 游标异常必须可见，否则状态页会显示 backlog=0，等于声称远端已完整（P1-1）
        cfg = staging_config()
        cfg["runtime"] = build_default_runtime()
        cfg["runtime"].update(
            {"staging_cursor_anomaly": "ahead", "staging_copy_backlog": -1}
        )
        self.runtime.config_state.config = cfg
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        probes = RepoProbe(RepoProbeStatus.OK, {"chunker_polynomial": "poly"})
        snapshots = [
            {"id": "a" * 64, "short_id": "a" * 8, "time": "2026-01-01T00:00:00Z"}
        ]

        with (
            mock.patch(
                "mcdr2restic.commands.staging_commands.validate_staging_settings",
                return_value=paths,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.read_snapshots",
                return_value=snapshots,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.probe_repository",
                return_value=probes,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.shutil.disk_usage",
                return_value=SimpleNamespace(total=1000, free=500),
            ),
        ):
            self.commands.command_status(self.source)

        output = self.source.replies[0]
        self.assertIn("待复制快照：未知", output)
        self.assertIn("游标状态：异常", output)
        self.assertIn("游标晚于本地最新快照", output)

    def test_status_remote_read_fails_fast_and_falls_back_to_cache(self):
        # 远端 snapshots 读取必须带短超时并回退上次已知值，
        # 否则不可达远端会把 TaskExecutor 挂住数分钟（E2E NEW-P3）
        cfg = staging_config()
        cfg["runtime"] = build_default_runtime()
        cfg["runtime"]["staging_remote_latest_time"] = "2026-01-02T00:00:00Z"
        self.runtime.config_state.config = cfg
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        probes = RepoProbe(RepoProbeStatus.OK, {"chunker_polynomial": "poly"})
        snapshots = [
            {"id": "a" * 64, "short_id": "a" * 8, "time": "2026-01-01T00:00:00Z"}
        ]
        timeout_problem = BackupProblem(
            i18n_key="error.restic.timeout",
            phase="staging-snapshots",
            seconds=30,
            output="",
        )

        with (
            mock.patch(
                "mcdr2restic.commands.staging_commands.validate_staging_settings",
                return_value=paths,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.read_snapshots",
                side_effect=[snapshots, timeout_problem],
            ) as read_snapshots_mock,
            mock.patch(
                "mcdr2restic.commands.staging_commands.probe_repository",
                return_value=probes,
            ),
            mock.patch(
                "mcdr2restic.commands.staging_commands.shutil.disk_usage",
                return_value=SimpleNamespace(total=1000, free=500),
            ),
        ):
            self.commands.command_status(self.source)

        output = self.source.replies[0]
        self.assertIn("上次已知", output)
        self.assertIn("2026-01-02T00:00:00Z", output)
        remote_deadline = read_snapshots_mock.call_args_list[1].kwargs["deadline"]
        self.assertIsNotNone(remote_deadline)
        self.assertLessEqual(remote_deadline - time.monotonic(), 30.0)


class ManualResticStagingLockTest(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime()
        self.server = FakePluginServer()
        self.runtime.service.server = self.server
        self.source = PermissiveCommandSource(self.server)
        self.commands = ResticCommands(make_context(self.runtime, self.server))

    def test_disabled_returns_false_without_locking(self):
        self.runtime.config_state.config = build_default_config()

        self.assertFalse(self.commands.try_acquire_staging_io(self.source))
        self.assertFalse(self.runtime.staging.io_lock.locked())

    def test_enabled_locks_and_busy_returns_none(self):
        self.runtime.config_state.config = staging_config()

        self.assertTrue(self.commands.try_acquire_staging_io(self.source))
        self.assertTrue(self.runtime.staging.io_lock.locked())
        self.commands.release_staging_io()

        self.runtime.staging.io_lock.acquire()
        try:
            self.assertIsNone(
                self.commands.try_acquire_staging_io(self.source)
            )
        finally:
            self.runtime.staging.io_lock.release()
        self.assertIn("另一个中转库任务", self.source.replies[0])


if __name__ == "__main__":
    unittest.main()
