# -*- coding: utf-8 -*-
try:
    from .support import (
        BackupProblem,
        FakePluginServer,
        ResticCommandResult,
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
        ResticCommandResult,
        SimpleNamespace,
        build_default_config,
        create_runtime,
        mock,
        unittest,
    )

from mcdr2restic.core.models import RestorePhase, RestoreSession
from mcdr2restic.restic import restic_service
from mcdr2restic.restore import restore_workflow
from mcdr2restic.staging import staging_backup
from mcdr2restic.staging.staging_config import (
    get_staging_settings,
    resolve_staging_paths,
)


def restic_result(return_code, stderr="", summary=None):
    return ResticCommandResult(
        "backup",
        [],
        return_code,
        "",
        stderr,
        1.0,
        summary=summary or {},
    )


class DiskSpaceTest(unittest.TestCase):
    def setUp(self):
        self.server = FakePluginServer()
        self.cfg = build_default_config()
        self.cfg["staging"].update({"enabled": True, "repository": "/data/staging"})
        self.settings = get_staging_settings(self.cfg)

    def test_projected_free_below_reserve_is_rejected(self):
        with mock.patch.object(
            staging_backup.shutil,
            "disk_usage",
            return_value=SimpleNamespace(total=1000, free=200),
        ), self.assertRaises(BackupProblem) as raised:
            staging_backup.assert_staging_disk_space(
                self.server, "/data/staging", 150, self.settings
            )

        self.assertEqual(
            raised.exception.i18n_key, "error.staging.disk_insufficient"
        )
        self.assertEqual(raised.exception.i18n_params["reserve"], "100.0 B")

    def test_projected_free_meeting_reserve_is_accepted(self):
        with mock.patch.object(
            staging_backup.shutil,
            "disk_usage",
            return_value=SimpleNamespace(total=1000, free=200),
        ):
            staging_backup.assert_staging_disk_space(
                self.server, "/data/staging", 50, self.settings
            )

    def test_negative_projected_free_is_rejected_even_with_zero_reserve(self):
        settings = get_staging_settings(
            {
                **self.cfg,
                "staging": {**self.cfg["staging"], "min_free_percent_after": 0},
            }
        )
        with mock.patch.object(
            staging_backup.shutil,
            "disk_usage",
            return_value=SimpleNamespace(total=1000, free=10),
        ), self.assertRaises(BackupProblem):
            staging_backup.assert_staging_disk_space(
                self.server, "/data/staging", 20, settings
            )


class DiskPreflightTest(unittest.TestCase):
    def test_dry_run_adds_flags_and_reads_data_added_packed(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        captured = {}

        def fake_run(_runtime, _cfg, args, phase, deadline):
            captured["args"] = list(args)
            captured["phase"] = phase
            return restic_result(0, summary={"data_added_packed": 4096})

        with mock.patch.object(
            staging_backup, "run_restic_command", side_effect=fake_run
        ), mock.patch.object(staging_backup, "assert_restic_success"):
            estimated = staging_backup.run_disk_preflight(
                runtime, server, cfg, cfg["restic"], paths, None
            )

        self.assertEqual(estimated, 4096)
        self.assertIn("--dry-run", captured["args"])
        self.assertIn("--json", captured["args"])
        self.assertIn("--cache-dir", captured["args"])


class LocalBackupDiskFullTest(unittest.TestCase):
    def test_disk_full_runs_emergency_prune_then_reraises(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()
        cfg["staging"].update({"enabled": True, "repository": "/data/staging"})
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])
        calls = []

        def fake_run(_ctx, args, phase):
            calls.append((list(args), phase))
            if len(calls) == 1:
                return restic_result(1, stderr="write: no space left on device")
            return restic_result(0)

        with mock.patch.object(
            staging_backup,
            "run_restic_command_with_lock_recovery",
            side_effect=fake_run,
        ), self.assertRaises(BackupProblem):
            staging_backup.run_local_backup(
                runtime, server, cfg, cfg["restic"], settings, paths, None
            )

        self.assertEqual(len(calls), 2)
        prune_args = calls[1][0]
        self.assertIn("forget", prune_args)
        self.assertIn("--max-repack-size", prune_args)
        self.assertEqual(
            prune_args[prune_args.index("--max-repack-size") + 1], "0"
        )

    def test_non_disk_failure_does_not_prune(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])

        with mock.patch.object(
            staging_backup,
            "run_restic_command_with_lock_recovery",
            return_value=restic_result(1, stderr="repository is already locked"),
        ) as run, self.assertRaises(BackupProblem):
            staging_backup.run_local_backup(
                runtime, server, cfg, cfg["restic"], settings, paths, None
            )

        self.assertEqual(run.call_count, 1)


class StagingBackupPreflightTest(unittest.TestCase):
    def test_preflight_failure_never_enters_save_off(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()
        cfg["staging"].update({"enabled": True, "repository": "/data/staging"})

        with (
            mock.patch.object(staging_backup, "is_mc_ready", return_value=True),
            mock.patch.object(
                staging_backup,
                "validate_staging_settings",
                return_value=SimpleNamespace(repository_path="/data/staging"),
            ),
            mock.patch.object(
                staging_backup, "ensure_default_restic_executable_available"
            ),
            mock.patch.object(
                staging_backup, "ensure_local_repository", return_value={}
            ),
            mock.patch.object(staging_backup, "preflight_chunker_parameters"),
            mock.patch.object(
                staging_backup,
                "run_disk_preflight",
                side_effect=BackupProblem(i18n_key="error.staging.disk_insufficient"),
            ),
            mock.patch.object(
                staging_backup, "prepare_minecraft_for_backup"
            ) as prepare_minecraft,self.assertRaises(BackupProblem)
        ):
            staging_backup.run_staging_backup_body(runtime, server, cfg)

        prepare_minecraft.assert_not_called()

    def test_remote_repository_inside_backup_source_fails_before_save_off(self):
        import os
        import tempfile

        runtime = create_runtime()
        server = FakePluginServer()
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "world")
            os.makedirs(source)
            cfg = build_default_config()
            cfg["staging"].update(
                {"enabled": True, "repository": os.path.join(root, "staging")}
            )
            cfg["restic"]["backup_command"] = ["backup", source]
            cfg["restic"]["repository"] = os.path.join(source, "remote-repo")

            with (
                mock.patch.object(staging_backup, "is_mc_ready", return_value=True),
                mock.patch.object(
                    staging_backup,
                    "validate_staging_settings",
                    return_value=SimpleNamespace(repository_path="/data/staging"),
                ),
                mock.patch.object(
                    staging_backup, "ensure_default_restic_executable_available"
                ),
                mock.patch.object(
                    staging_backup, "prepare_minecraft_for_backup"
                ) as prepare_minecraft,
                self.assertRaises(BackupProblem) as raised,
            ):
                staging_backup.run_staging_backup_body(runtime, server, cfg)

        self.assertEqual(
            raised.exception.i18n_key,
            "error.restic.repository_inside_backup_sources",
        )
        prepare_minecraft.assert_not_called()

    def test_chunker_mismatch_fails_before_save_off(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()
        cfg["staging"].update({"enabled": True, "repository": "/data/staging"})

        with (
            mock.patch.object(staging_backup, "is_mc_ready", return_value=True),
            mock.patch.object(
                staging_backup,
                "validate_staging_settings",
                return_value=SimpleNamespace(repository_path="/data/staging"),
            ),
            mock.patch.object(
                staging_backup, "ensure_default_restic_executable_available"
            ),
            mock.patch.object(
                staging_backup, "ensure_local_repository", return_value={}
            ),
            mock.patch.object(
                staging_backup,
                "preflight_chunker_parameters",
                side_effect=BackupProblem(
                    i18n_key="error.staging.chunker_mismatch",
                    local_chunker="a",
                    remote_chunker="b",
                ),
            ),
            mock.patch.object(
                staging_backup, "prepare_minecraft_for_backup"
            ) as prepare_minecraft,
            self.assertRaises(BackupProblem),
        ):
            staging_backup.run_staging_backup_body(runtime, server, cfg)

        prepare_minecraft.assert_not_called()


class RestoreSafetyBypassTest(unittest.TestCase):
    def test_restore_safety_snapshot_bypasses_staging(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()
        session = RestoreSession(
            tasks=[],
            cfg=cfg,
            snapshot_cfg={},
            cache_key="key",
            language="zh_cn",
            phase=RestorePhase.PRE_BACKUP,
        )

        with mock.patch.object(
            restore_workflow, "run_backup_body", return_value="snap123"
        ) as run_body:
            safety_id = restore_workflow.create_restore_safety_snapshot(
                runtime, server, session, mock.Mock()
            )

        self.assertEqual(safety_id, "snap123")
        self.assertEqual(run_body.call_args.kwargs["use_staging"], False)


class BackupBodyBranchTest(unittest.TestCase):
    def test_disabled_staging_uses_direct_path(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()

        with (
            mock.patch.object(restic_service, "staging_enabled", return_value=False),
            mock.patch.object(
                restic_service, "run_direct_backup_body", return_value="direct"
            ) as direct,
        ):
            result = restic_service.run_backup_body(runtime, server, cfg, None)

        self.assertEqual(result, "direct")
        direct.assert_called_once()

    def test_enabled_staging_uses_staging_path(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()

        with (
            mock.patch.object(restic_service, "staging_enabled", return_value=True),
            mock.patch(
                "mcdr2restic.staging.staging_backup.run_staging_backup_body",
                return_value="staging",
            ) as staging_run,
        ):
            result = restic_service.run_backup_body(runtime, server, cfg, None)

        self.assertEqual(result, "staging")
        staging_run.assert_called_once()

    def test_use_staging_false_bypasses_staging_even_when_enabled(self):
        runtime = create_runtime()
        server = FakePluginServer()
        cfg = build_default_config()

        with (
            mock.patch.object(restic_service, "staging_enabled", return_value=True),
            mock.patch.object(
                restic_service, "run_direct_backup_body", return_value="direct"
            ) as direct,
        ):
            result = restic_service.run_backup_body(
                runtime, server, cfg, None, use_staging=False
            )

        self.assertEqual(result, "direct")
        direct.assert_called_once()


if __name__ == "__main__":
    unittest.main()
