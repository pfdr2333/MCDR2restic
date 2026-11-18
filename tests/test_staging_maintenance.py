# -*- coding: utf-8 -*-
try:
    from .support import (
        FakePluginServer,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )
except ImportError:
    from support import (
        FakePluginServer,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )

import os
import tempfile
from datetime import datetime

from mcdr2restic.core.models import BackupCanceled, BackupProblem, MaintenanceRunStatus
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.restic import restic_service
from mcdr2restic.staging import staging_maintenance, staging_purge
from mcdr2restic.staging.staging_config import (
    get_staging_settings,
    resolve_staging_paths,
)
from mcdr2restic.staging.staging_copy import CopyCycleResult


def staging_config(local_repo, remote_repo=""):
    cfg = build_default_config()
    cfg["staging"].update(
        {"enabled": True, "repository": local_repo, "password": "pass"}
    )
    cfg["restic"]["repository"] = remote_repo or "./restic-repo"
    cfg["runtime"] = build_default_runtime()
    return cfg


def snapshot(snapshot_id, time_text):
    return {"id": snapshot_id, "short_id": snapshot_id[:8], "time": time_text}


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime()
        self.server = FakePluginServer()
        self.cfg = staging_config("/data/staging")
        self.settings = get_staging_settings(self.cfg)
        self.paths = resolve_staging_paths(self.settings, self.cfg["restic"])

    def run_maintenance(self, copy_result, order=None):
        order = order if order is not None else []

        def record(name):
            def inner(*_args, **_kwargs):
                order.append(name)

            return inner

        with (
            mock.patch.object(
                staging_maintenance,
                "ensure_default_restic_executable_available",
            ),
            mock.patch.object(
                staging_maintenance,
                "validate_staging_settings",
                return_value=self.paths,
            ),
            mock.patch.object(
                staging_maintenance, "acquire_staging_io_lock", return_value=True
            ),
            mock.patch.object(
                staging_maintenance, "release_staging_io_lock"
            ),
            mock.patch.object(
                staging_maintenance,
                "run_copy_cycle",
                return_value=copy_result,
            ) as copy,
            mock.patch.object(
                staging_maintenance,
                "run_remote_maintenance_commands",
                side_effect=record("remote"),
            ),
            mock.patch.object(
                staging_maintenance,
                "run_local_prune",
                side_effect=record("local_prune"),
            ),
            mock.patch.object(
                staging_maintenance,
                "cleanup_restic_cache",
                side_effect=record("cache"),
            ),
            mock.patch.object(
                staging_maintenance, "should_deep_prune", return_value=False
            ),
        ):
            outcome = staging_maintenance.run_staging_maintenance_body(
                self.runtime, self.server, self.cfg
            )
        return outcome, order, copy

    def test_copy_runs_before_remote_and_local_maintenance(self):
        outcome, order, copy = self.run_maintenance(
            CopyCycleResult("success", copied_count=1)
        )

        self.assertEqual(outcome.status, MaintenanceRunStatus.SUCCESS)
        self.assertEqual(order, ["remote", "local_prune", "cache"])
        copy.assert_called_once()

    def test_copy_backlog_skips_entire_maintenance(self):
        outcome, order, _copy = self.run_maintenance(
            CopyCycleResult(
                "failed", message="network down", pending_count=2, retryable=True
            )
        )

        self.assertEqual(outcome.status, MaintenanceRunStatus.SKIPPED)
        self.assertEqual(order, [])
        # 文案区分"确有未复制快照"与"复制失败"（E2E P3-3）
        self.assertIn("2", outcome.message)

    def test_copy_failure_without_backlog_uses_copy_failed_message(self):
        # E2E 现场：backlog=0 但复制步骤失败，旧文案误称"仍有未复制快照"
        outcome, _order, _copy = self.run_maintenance(
            CopyCycleResult("failed", message="remote down", retryable=True)
        )

        self.assertEqual(outcome.status, MaintenanceRunStatus.SKIPPED)
        self.assertIn("复制未成功", outcome.message)
        self.assertNotIn("未复制", outcome.message.split("：")[0])

    def test_remote_initialized_skips_remote_commands_but_still_prunes_local(self):
        outcome, order, _copy = self.run_maintenance(
            CopyCycleResult("success", remote_initialized=True)
        )

        self.assertEqual(outcome.status, MaintenanceRunStatus.SUCCESS)
        self.assertEqual(order, ["local_prune", "cache"])

    def test_cursor_anomaly_skips_local_prune(self):
        # 游标不可信时本轮已全量重放，仍保守跳过本地 prune（P1-1）
        outcome, order, _copy = self.run_maintenance(
            CopyCycleResult("success", cursor_untrusted=True)
        )

        self.assertEqual(outcome.status, MaintenanceRunStatus.SUCCESS)
        self.assertEqual(order, ["remote", "cache"])
        self.assertIn("跳过本地 prune", outcome.message)

    def test_deep_prune_is_due_when_never_run_or_interval_elapsed(self):
        self.assertTrue(
            staging_maintenance.should_deep_prune(self.runtime, self.settings)
        )
        self.runtime.config_state.config = self.cfg
        self.runtime.config_state.config["runtime"][
            "staging_last_deep_prune_time"
        ] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.assertFalse(
            staging_maintenance.should_deep_prune(
                self.runtime,
                self.settings,
            )
        )


class MaintenanceBranchTest(unittest.TestCase):
    def test_enabled_staging_delegates_maintenance(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = staging_config("/data/staging")
        outcome = MaintenanceRunStatus.SUCCESS

        with (
            mock.patch.object(restic_service, "staging_enabled", return_value=True),
            mock.patch(
                "mcdr2restic.staging.staging_maintenance.run_staging_maintenance_body",
                return_value=outcome,
            ) as staging_run,
        ):
            result = restic_service.run_maintenance_body(
                runtime, server, cfg, None
            )

        self.assertEqual(result, outcome)
        staging_run.assert_called_once()


class PurgeTest(unittest.TestCase):
    def setUp(self):
        self.runtime = create_runtime()
        self.server = FakePluginServer()

    def test_purge_rejects_remote_newer_than_local(self):
        with self.assertRaises(BackupProblem) as raised:
            staging_purge.validate_purge_direction(
                "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", force=False
            )

        self.assertEqual(
            raised.exception.i18n_key, "error.staging.purge_remote_newer"
        )
        staging_purge.validate_purge_direction(
            "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", force=True
        )

    def test_purge_rejects_remote_snapshots_when_local_is_empty(self):
        with self.assertRaises(BackupProblem):
            staging_purge.validate_purge_direction(
                None, "2026-01-02T00:00:00Z", force=False
            )

    def test_purge_rejects_non_filesystem_remote(self):
        cfg = staging_config("/data/staging", "sftp:user@host:/backup")

        with self.assertRaises(BackupProblem) as raised:
            staging_purge.purge_staging_remote(
                self.runtime, self.server, cfg, force=True
            )

        self.assertEqual(
            raised.exception.i18n_key, "error.staging.purge_requires_filesystem"
        )

    def test_purge_rejects_overlapping_local_and_remote_paths(self):
        with tempfile.TemporaryDirectory() as root:
            local_repo = os.path.join(root, "staging-repo")
            os.makedirs(local_repo)
            cfg = staging_config(local_repo, root)

            with mock.patch.object(
                staging_purge, "ensure_default_restic_executable_available"
            ), self.assertRaises(BackupProblem) as raised:
                staging_purge.purge_staging_remote(
                    self.runtime, self.server, cfg, force=True
                )

            self.assertEqual(
                raised.exception.i18n_key,
                # 路径重叠现在在 validate_staging_settings 统一拦截（P2-7）
                "error.staging.repository_overlaps_remote",
            )

    def test_purge_rejects_remote_that_contains_backup_sources(self):
        with tempfile.TemporaryDirectory() as root:
            local_repo = os.path.join(root, "staging-repo")
            remote_repo = os.path.join(root, "remote-repo")
            source = os.path.join(remote_repo, "world")
            os.makedirs(local_repo)
            os.makedirs(source)
            cfg = staging_config(local_repo, remote_repo)
            cfg["restic"]["backup_command"] = ["backup", source]

            with mock.patch.object(
                staging_purge, "ensure_default_restic_executable_available"
            ), self.assertRaises(BackupProblem) as raised:
                staging_purge.purge_staging_remote(
                    self.runtime, self.server, cfg, force=True
                )

            self.assertEqual(
                raised.exception.i18n_key,
                "error.staging.purge_remote_contains_sources",
            )

    def test_purge_requires_force_when_existing_remote_is_unreadable(self):
        with tempfile.TemporaryDirectory() as root:
            local_repo = os.path.join(root, "staging-repo")
            remote_repo = os.path.join(root, "remote-repo")
            os.makedirs(local_repo)
            os.makedirs(remote_repo)
            cfg = staging_config(local_repo, remote_repo)
            probes = [
                mock.Mock(ok=True, missing=False, error="", config={}),
                mock.Mock(
                    ok=False,
                    missing=False,
                    status="auth",
                    error="wrong password",
                ),
            ]

            with (
                mock.patch.object(
                    staging_purge, "ensure_default_restic_executable_available"
                ),
                mock.patch.object(
                    staging_purge, "probe_repository", side_effect=probes
                ),
                mock.patch.object(
                    staging_purge,
                    "read_snapshots",
                    return_value=[
                        snapshot("a" * 64, "2026-01-02T00:00:00Z")
                    ],
                ),self.assertRaises(BackupProblem) as raised
            ):
                staging_purge.purge_staging_remote(
                    self.runtime, self.server, cfg, force=False
                )

            self.assertEqual(
                raised.exception.i18n_key,
                "error.staging.purge_remote_unreadable",
            )

    def test_purge_renames_remote_initializes_from_local_and_resets_cursor(self):
        with tempfile.TemporaryDirectory() as root:
            local_repo = os.path.join(root, "staging-repo")
            remote_repo = os.path.join(root, "remote-repo")
            os.makedirs(local_repo)
            os.makedirs(remote_repo)
            with open(os.path.join(remote_repo, "config"), "w", encoding="utf8") as file:
                file.write("old")

            cfg = staging_config(local_repo, remote_repo)
            cfg["runtime"].update(
                {
                    "staging_cursor_time": "2026-01-01T00:00:00Z",
                    "staging_cursor_ids": ["abc"],
                    "staging_copy_backlog": 3,
                    "staging_remote_latest_time": "2026-01-01T00:00:00Z",
                }
            )
            health = mock.Mock(ok=True, missing=False, error="", config={})
            local_snapshot = snapshot("a" * 64, "2026-01-02T00:00:00Z")
            remote_snapshot = snapshot("b" * 64, "2026-01-01T00:00:00Z")

            with (
                mock.patch.object(
                    staging_purge, "ensure_default_restic_executable_available"
                ),
                mock.patch.object(
                    staging_purge, "probe_repository", return_value=health
                ),
                mock.patch.object(
                    staging_purge,
                    "read_snapshots",
                    side_effect=[[local_snapshot], [remote_snapshot]],
                ),
                mock.patch.object(
                    staging_purge, "run_initialization"
                ) as initialize,
                mock.patch.object(
                    staging_purge, "request_copy"
                ) as request_copy,
                mock.patch.object(
                    staging_purge, "update_staging_runtime"
                ) as update,
            ):
                message = staging_purge.purge_staging_remote(
                    self.runtime, self.server, cfg, force=False
                )

            initialize.assert_called_once()
            request_copy.assert_called_once()
            update.assert_called_once()
            self.assertIn(".purged-", message)
            self.assertFalse(os.path.exists(remote_repo))
            backups = [
                name for name in os.listdir(root) if ".purged-" in name
            ]
            self.assertEqual(len(backups), 1)
            with open(
                os.path.join(root, backups[0], "config"), encoding="utf8"
            ) as file:
                self.assertEqual(file.read(), "old")
            reset_values = update.call_args.kwargs
            self.assertIsNone(reset_values["staging_cursor_time"])
            self.assertEqual(reset_values["staging_cursor_ids"], [])
            self.assertEqual(reset_values["staging_copy_backlog"], 0)

    def test_purge_rolls_back_when_remote_initialization_fails(self):
        with tempfile.TemporaryDirectory() as root:
            local_repo = os.path.join(root, "staging-repo")
            remote_repo = os.path.join(root, "remote-repo")
            os.makedirs(local_repo)
            os.makedirs(remote_repo)
            with open(os.path.join(remote_repo, "config"), "w", encoding="utf8") as file:
                file.write("old")

            cfg = staging_config(local_repo, remote_repo)
            health = mock.Mock(ok=True, missing=False, error="", config={})

            with (
                mock.patch.object(
                    staging_purge, "ensure_default_restic_executable_available"
                ),
                mock.patch.object(
                    staging_purge, "probe_repository", return_value=health
                ),
                mock.patch.object(
                    staging_purge,
                    "read_snapshots",
                    side_effect=[
                        [snapshot("a" * 64, "2026-01-02T00:00:00Z")],
                        [snapshot("b" * 64, "2026-01-01T00:00:00Z")],
                    ],
                ),
                mock.patch.object(
                    staging_purge,
                    "run_initialization",
                    side_effect=BackupProblem(
                        i18n_key="error.staging.purge_failed", error="boom"
                    ),
                ),
                mock.patch.object(
                    staging_purge, "request_copy"
                ) as request_copy,
                self.assertRaises(BackupProblem),
            ):
                staging_purge.purge_staging_remote(
                    self.runtime, self.server, cfg, force=False
                )

            request_copy.assert_not_called()
            self.assertTrue(os.path.exists(remote_repo))
            with open(
                os.path.join(remote_repo, "config"), encoding="utf8"
            ) as file:
                self.assertEqual(file.read(), "old")

    def test_purge_cancel_between_phases_rolls_back_old_remote(self):
        with tempfile.TemporaryDirectory() as root:
            local_repo = os.path.join(root, "staging-repo")
            remote_repo = os.path.join(root, "remote-repo")
            os.makedirs(local_repo)
            os.makedirs(remote_repo)
            with open(os.path.join(remote_repo, "config"), "w", encoding="utf8") as file:
                file.write("old")

            cfg = staging_config(local_repo, remote_repo)
            health = mock.Mock(ok=True, missing=False, error="", config={})

            def initialize_then_cancel(*_args, **_kwargs):
                # 初始化期间收到取消：下一个阶段检查点应中止并回滚
                self.runtime.staging.copy_slot.cancel.set()

            with (
                mock.patch.object(
                    staging_purge, "ensure_default_restic_executable_available"
                ),
                mock.patch.object(
                    staging_purge, "probe_repository", return_value=health
                ),
                mock.patch.object(
                    staging_purge,
                    "read_snapshots",
                    side_effect=[
                        [snapshot("a" * 64, "2026-01-02T00:00:00Z")],
                        [snapshot("b" * 64, "2026-01-01T00:00:00Z")],
                    ],
                ),
                mock.patch.object(
                    staging_purge,
                    "run_initialization",
                    side_effect=initialize_then_cancel,
                ),
                mock.patch.object(
                    staging_purge, "request_copy"
                ) as request_copy,
                self.assertRaises(BackupCanceled),
            ):
                staging_purge.purge_staging_remote(
                    self.runtime, self.server, cfg, force=True
                )

            request_copy.assert_not_called()
            self.assertTrue(os.path.exists(remote_repo))
            self.assertFalse(
                any(".purged-" in name for name in os.listdir(root))
            )
            with open(
                os.path.join(remote_repo, "config"), encoding="utf8"
            ) as file:
                self.assertEqual(file.read(), "old")


if __name__ == "__main__":
    unittest.main()
