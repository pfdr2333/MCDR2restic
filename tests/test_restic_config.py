try:
    from .support import BackupProblem, mock, unittest
except ImportError:
    from support import BackupProblem, mock, unittest

from mcdr2restic.restic import restic_config


class ResticConfigTests(unittest.TestCase):
    def test_password_takes_precedence_over_password_file(self):
        with mock.patch.dict(
            restic_config.os.environ,
            {"RESTIC_PASSWORD_FILE": "inherited", "RESTIC_PASSWORD_COMMAND": "cmd"},
            clear=False,
        ):
            env = restic_config.build_restic_environment(
                {
                    "environment": {"RESTIC_TEST": 123},
                    "repository": "./repo",
                    "password": "secret",
                    "password_file": "./password-file",
                }
            )

        self.assertEqual(env["RESTIC_REPOSITORY"], "./repo")
        self.assertEqual(env["RESTIC_PASSWORD"], "secret")
        self.assertNotIn("RESTIC_PASSWORD_FILE", env)
        self.assertNotIn("RESTIC_PASSWORD_COMMAND", env)
        self.assertEqual(env["RESTIC_TEST"], "123")

    def test_environment_none_removes_inherited_value(self):
        with mock.patch.dict(
            restic_config.os.environ, {"RESTIC_TEST": "old"}, clear=False
        ):
            env = restic_config.build_restic_environment(
                {"environment": {"RESTIC_TEST": None}}
            )

        self.assertNotIn("RESTIC_TEST", env)

    def test_local_repository_detection_distinguishes_remote_schemes(self):
        self.assertTrue(restic_config.is_local_restic_repository("./repo"))
        self.assertTrue(restic_config.is_local_restic_repository(r"C:\repo"))
        self.assertFalse(restic_config.is_local_restic_repository("s3:bucket/repo"))
        self.assertFalse(restic_config.is_local_restic_repository("https://repo"))
        self.assertFalse(restic_config.is_local_restic_repository(""))

    def test_extract_backup_sources_skips_options_and_honors_separator(self):
        args = [
            "backup",
            "--tag",
            "minecraft",
            "--exclude=*.lock",
            "world",
            "--",
            "literal-source",
        ]

        self.assertEqual(
            restic_config.extract_restic_backup_sources(args),
            ["world", "literal-source"],
        )

    def test_effective_repository_prefers_environment_over_command_args(self):
        cfg = {
            "repository": "./environment-repo",
            "backup_command": ["backup", "--repo", "./command-repo", "world"],
        }

        self.assertEqual(
            restic_config.get_effective_restic_repository(cfg), "./environment-repo"
        )

    def test_command_argument_normalization_supports_shell_strings(self):
        self.assertEqual(
            restic_config.normalize_command_args('backup "server world"'),
            ["backup", "server world"],
        )
        self.assertEqual(
            restic_config.normalize_command_args(["backup", 1]), ["backup", "1"]
        )

    def test_invalid_command_arguments_raise_backup_problem(self):
        with self.assertRaises(BackupProblem) as error:
            restic_config.normalize_command_args({"backup": True})

        self.assertEqual(error.exception.i18n_key, "error.restic.command_args_invalid")

    def test_repository_inside_backup_source_is_rejected(self):
        cfg = {
            "working_directory": "/server",
            "repository": "./world/restic-repo",
            "backup_command": ["backup", "./world"],
        }

        with self.assertRaises(BackupProblem) as error:
            restic_config.assert_backup_sources_do_not_contain_repository(cfg)

        self.assertEqual(
            error.exception.i18n_key,
            "error.restic.repository_inside_backup_sources",
        )

    def test_remote_repository_is_not_checked_against_backup_sources(self):
        cfg = {
            "working_directory": "/server",
            "repository": "s3:bucket/repo",
            "backup_command": ["backup", "."],
        }

        restic_config.assert_backup_sources_do_not_contain_repository(cfg)
