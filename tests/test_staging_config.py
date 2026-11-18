# -*- coding: utf-8 -*-
try:
    from .support import BackupProblem, tempfile, unittest, os
except ImportError:
    from support import BackupProblem, tempfile, unittest, os

from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.staging.staging_config import (
    build_backup_args,
    build_cache_cleanup_args,
    build_copy_args,
    build_copy_source_config,
    build_emergency_prune_args,
    build_init_args,
    build_local_forget_args,
    build_local_restic_config,
    build_snapshots_args,
    get_staging_settings,
    resolve_staging_paths,
    validate_staging_settings,
    with_cache_dir,
)


def staging_cfg(repository="", **overrides):
    cfg = build_default_config()
    cfg["staging"].update(
        {
            "enabled": True,
            "repository": repository,
            "password": "staging-pass",
            "password_file": "",
        }
    )
    cfg["staging"].update(overrides)
    return cfg


class StagingSettingsTest(unittest.TestCase):
    def test_defaults_are_disabled_and_parse_clamped_values(self):
        settings = get_staging_settings(build_default_config())

        self.assertFalse(settings.enabled)
        self.assertEqual(settings.keep_last, 2)
        self.assertEqual(settings.copy_retry_backoff_seconds, (60, 300, 900))
        self.assertEqual(settings.min_free_percent_after, 10.0)

    def test_bad_values_fall_back_instead_of_killing_backup(self):
        cfg = staging_cfg(
            min_free_percent_after="not-a-number",
            keep_last=0,
            copy_retry_backoff_seconds=[],
            copy_retry_max=-2,
        )

        settings = get_staging_settings(cfg)

        self.assertEqual(settings.min_free_percent_after, 10.0)
        self.assertEqual(settings.keep_last, 1)
        self.assertEqual(settings.copy_retry_backoff_seconds, (60, 300, 900))
        self.assertEqual(settings.copy_retry_max, 0)

    def test_disabled_validation_returns_none_without_touching_filesystem(self):
        settings = get_staging_settings(build_default_config())

        self.assertIsNone(
            validate_staging_settings(settings, build_default_config()["restic"])
        )


class StagingValidationTest(unittest.TestCase):
    def test_empty_repository_is_rejected(self):
        cfg = staging_cfg("")

        with self.assertRaises(BackupProblem) as raised:
            validate_staging_settings(
                get_staging_settings(cfg), cfg["restic"]
            )

        self.assertEqual(raised.exception.i18n_key, "error.staging.repository_empty")

    def test_remote_repository_is_rejected(self):
        cfg = staging_cfg("sftp:user@host:/staging")

        with self.assertRaises(BackupProblem) as raised:
            validate_staging_settings(
                get_staging_settings(cfg), cfg["restic"]
            )

        self.assertEqual(
            raised.exception.i18n_key, "error.staging.repository_not_local"
        )

    def test_password_or_password_file_is_required(self):
        cfg = staging_cfg("/data/staging-repo", password="", password_file="")

        with self.assertRaises(BackupProblem) as raised:
            validate_staging_settings(
                get_staging_settings(cfg), cfg["restic"]
            )

        self.assertEqual(raised.exception.i18n_key, "error.staging.password_missing")

    def test_repository_cache_and_password_file_must_be_outside_backup_sources(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "server", "world")
            os.makedirs(source)

            cases = (
                ("repository", os.path.join(source, "staging-repo"), {}),
                (
                    "cache_dir",
                    os.path.join(root, "staging-repo"),
                    {"cache_dir": os.path.join(source, "cache")},
                ),
                (
                    "password_file",
                    os.path.join(root, "staging-repo"),
                    {
                        "password": "",
                        "password_file": os.path.join(source, "password.txt"),
                    },
                ),
            )
            for label, repository, overrides in cases:
                with self.subTest(label=label):
                    cfg = staging_cfg(repository, **overrides)
                    cfg["restic"]["backup_command"] = ["backup", source]
                    with self.assertRaises(BackupProblem) as raised:
                        validate_staging_settings(
                            get_staging_settings(cfg), cfg["restic"]
                        )
                    self.assertEqual(
                        raised.exception.i18n_key,
                        "error.staging.path_inside_backup_sources",
                    )
                    self.assertEqual(
                        raised.exception.i18n_params["config_key"], label
                    )

    def test_backup_command_must_not_contain_repository_options(self):
        cfg = staging_cfg("/tmp/staging-repo")
        cfg["restic"]["backup_command"] = ["backup", "-r", "/other/repo", "./world"]

        with self.assertRaises(BackupProblem) as raised:
            validate_staging_settings(get_staging_settings(cfg), cfg["restic"])

        self.assertEqual(
            raised.exception.i18n_key,
            "error.staging.backup_command_has_repository",
        )

    def test_valid_configuration_resolves_repository_and_default_cache(self):
        with tempfile.TemporaryDirectory() as root:
            repository = os.path.join(root, "staging-repo")
            cfg = staging_cfg(repository)
            cfg["restic"]["backup_command"] = ["backup", os.path.join(root, "world")]

            settings = get_staging_settings(cfg)
            paths = validate_staging_settings(settings, cfg["restic"])

            self.assertTrue(paths.repository_path.endswith("staging-repo"))
            self.assertTrue(paths.cache_dir.endswith("cache"))
            self.assertEqual(
                os.path.dirname(paths.cache_dir),
                os.path.dirname(paths.repository_path),
            )

    def test_staging_repository_must_not_overlap_local_remote_repository(self):
        with tempfile.TemporaryDirectory() as root:
            staging_repo = os.path.join(root, "staging-repo")
            os.makedirs(staging_repo)
            cases = (
                (staging_repo, staging_repo),
                (staging_repo, root),
                (os.path.join(staging_repo, "remote"), staging_repo),
            )
            for remote, expected_local in cases:
                with self.subTest(remote=remote):
                    cfg = staging_cfg(expected_local)
                    cfg["restic"]["repository"] = remote
                    with self.assertRaises(BackupProblem) as raised:
                        validate_staging_settings(
                            get_staging_settings(cfg), cfg["restic"]
                        )
                    self.assertEqual(
                        raised.exception.i18n_key,
                        "error.staging.repository_overlaps_remote",
                    )
                    self.assertIn(
                        "local_path", raised.exception.i18n_params
                    )
                    self.assertIn(
                        "remote_path", raised.exception.i18n_params
                    )

    def test_network_remote_is_exempt_from_overlap_check(self):
        cfg = staging_cfg("/data/staging-repo")
        cfg["restic"]["repository"] = "s3:https://s3.example.com/mcdr-backup"

        paths = validate_staging_settings(
            get_staging_settings(cfg), cfg["restic"]
        )

        self.assertTrue(paths.repository_path.endswith("staging-repo"))

    def test_missing_password_file_is_reported_before_restic_runs(self):
        with tempfile.TemporaryDirectory() as root:
            missing = os.path.join(root, "missing-password.txt")
            cfg = staging_cfg(
                os.path.join(root, "staging-repo"),
                password="",
                password_file=missing,
            )

            with self.assertRaises(BackupProblem) as raised:
                validate_staging_settings(
                    get_staging_settings(cfg), cfg["restic"]
                )

            self.assertEqual(
                raised.exception.i18n_key,
                "error.staging.password_file_missing",
            )
            # Windows 临时目录可能是 8.3 短路径，插件返回的是真实路径
            self.assertEqual(
                os.path.normcase(
                    os.path.realpath(raised.exception.i18n_params["path"])
                ),
                os.path.normcase(os.path.realpath(missing)),
            )

    def test_password_takes_priority_so_missing_file_is_not_checked(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = staging_cfg(
                os.path.join(root, "staging-repo"),
                password="inline-pass",
                password_file=os.path.join(root, "missing.txt"),
            )

            paths = validate_staging_settings(
                get_staging_settings(cfg), cfg["restic"]
            )

            self.assertTrue(paths.repository_path.endswith("staging-repo"))


class StagingCommandTest(unittest.TestCase):
    def test_local_restic_config_uses_staging_repository_and_password(self):
        cfg = staging_cfg("/data/staging-repo")
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])

        local = build_local_restic_config(cfg["restic"], settings, paths)

        self.assertEqual(local["repository"], paths.repository_path)
        self.assertEqual(local["password"], "staging-pass")
        self.assertEqual(local["password_file"], "")

    def test_password_takes_priority_over_default_password_file(self):
        # 只填 password 时默认 password_file 路径不得抢走凭据（E2E P2-1）
        cfg = staging_cfg("/data/staging-repo", password="wins")
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])

        local = build_local_restic_config(cfg["restic"], settings, paths)

        self.assertEqual(local["password"], "wins")
        self.assertEqual(local["password_file"], "")

    def test_password_file_used_only_when_password_empty(self):
        cfg = staging_cfg(
            "/data/staging-repo", password="", password_file="/data/pass.txt"
        )
        settings = get_staging_settings(cfg)
        paths = resolve_staging_paths(settings, cfg["restic"])

        local = build_local_restic_config(cfg["restic"], settings, paths)

        self.assertEqual(local["password"], "")
        self.assertEqual(local["password_file"], paths.password_file)

    def test_copy_source_config_uses_from_password_environment(self):
        cfg = staging_cfg("./staging-repo")
        remote_cfg = build_copy_source_config(
            cfg["restic"], "staging-pass", "/data/staging-password.txt"
        )

        self.assertEqual(
            remote_cfg["environment"]["RESTIC_FROM_PASSWORD"], "staging-pass"
        )
        self.assertNotIn(
            "RESTIC_FROM_PASSWORD_FILE", remote_cfg["environment"]
        )

        file_cfg = build_copy_source_config(
            cfg["restic"], "", "/data/staging-password.txt"
        )
        self.assertEqual(
            file_cfg["environment"]["RESTIC_FROM_PASSWORD_FILE"],
            "/data/staging-password.txt",
        )

    def test_backup_args_replace_cache_dir(self):
        cfg = staging_cfg("/data/staging-repo")
        cfg["restic"]["backup_command"] = [
            "backup",
            "./world",
            "--cache-dir",
            "/old/cache",
        ]

        args = build_backup_args(cfg["restic"], "/new/cache")

        self.assertNotIn("/old/cache", args)
        self.assertEqual(args[args.index("--cache-dir") + 1], "/new/cache")

    def test_copy_args_include_explicit_ids_from_repo_and_limits(self):
        args = build_copy_args(
            "/data/staging-repo", ["abc123"], "/data/cache", 512
        )

        self.assertEqual(args[:4], ["copy", "--from-repo", "/data/staging-repo", "abc123"])
        self.assertIn("--retry-lock", args)
        self.assertEqual(args[args.index("--cache-dir") + 1], "/data/cache")
        self.assertEqual(args[args.index("--limit-upload") + 1], "512")

    def test_init_and_snapshot_arguments(self):
        self.assertEqual(
            build_init_args("/remote/repo", copy_chunker_params=True),
            [
                "init",
                "--from-repo",
                "/remote/repo",
                "--copy-chunker-params",
            ],
        )
        self.assertEqual(
            build_snapshots_args(latest_only=True),
            ["snapshots", "--json", "--latest", "1"],
        )

    def test_maintenance_arguments(self):
        cfg = staging_cfg("/data/staging-repo", keep_last=3)
        settings = get_staging_settings(cfg)

        daily = build_local_forget_args(settings, "/data/cache", deep=False)
        deep = build_local_forget_args(settings, "/data/cache", deep=True)
        emergency = build_emergency_prune_args(settings, "/data/cache")
        cleanup = build_cache_cleanup_args("/data/cache")

        self.assertIn("--keep-last", daily)
        self.assertEqual(daily[daily.index("--keep-last") + 1], "3")
        self.assertEqual(daily[daily.index("--max-unused") + 1], "5%")
        self.assertNotIn("--max-repack-size", daily)
        self.assertEqual(deep[deep.index("--max-unused") + 1], "0")
        self.assertEqual(deep[deep.index("--max-repack-size") + 1], "20G")
        self.assertEqual(emergency[emergency.index("--max-repack-size") + 1], "0")
        self.assertEqual(cleanup[:2], ["cache", "--cleanup"])
        self.assertIn("--cache-dir", cleanup)

    def test_with_cache_dir_keeps_double_dash_boundary(self):
        args = with_cache_dir(["backup", "--", "./world"], "/data/cache")

        self.assertEqual(args, ["backup", "--cache-dir", "/data/cache", "--", "./world"])


if __name__ == "__main__":
    unittest.main()
