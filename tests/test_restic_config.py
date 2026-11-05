import os

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

    def test_extract_backup_sources_consumes_short_value_options(self):
        args = ["backup", "-r", "./restic-repo", "-H", "srv", "-o", "key=value", "world"]

        self.assertEqual(restic_config.extract_restic_backup_sources(args), ["world"])

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

    def test_command_string_keeps_backslash_paths_on_windows(self):
        with mock.patch("os.name", "nt"):
            args = restic_config.normalize_command_args("backup C:\\server\\world")

        self.assertEqual(args, ["backup", "C:\\server\\world"])

    def test_command_string_strips_quotes_but_keeps_inner_backslashes_on_windows(self):
        with mock.patch("os.name", "nt"):
            args = restic_config.normalize_command_args(
                'backup "C:\\Program Files\\world"'
            )

        self.assertEqual(args, ["backup", "C:\\Program Files\\world"])

    def test_command_string_uses_posix_semantics_off_windows(self):
        with mock.patch("os.name", "posix"):
            args = restic_config.normalize_command_args("backup ./server/world")

        self.assertEqual(args, ["backup", "./server/world"])

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

    def test_repository_passed_via_short_option_is_not_a_backup_source(self):
        cfg = {
            "working_directory": "/server",
            "repository": "/repo",
            "backup_command": ["backup", "-r", "/repo", "./world"],
        }

        restic_config.assert_backup_sources_do_not_contain_repository(cfg)


@unittest.skipUnless(os.name == "nt", "verbatim/UNC forms are Windows-specific")
class CanonicalPathComparisonTests(unittest.TestCase):
    def test_verbatim_drive_prefix_is_stripped(self):
        self.assertEqual(
            restic_config.canonical_path_for_comparison(r"\\?\C:\dir"),
            r"C:\dir",
        )

    def test_verbatim_prefix_dotdot_collapses_after_strip(self):
        # ".." 在 verbatim 内不会被 realpath/normpath 折叠，剥除后必须补折叠
        self.assertEqual(
            restic_config.canonical_path_for_comparison(r"\\?\C:\dir\other\.."),
            r"C:\dir",
        )

    def test_verbatim_unc_prefix_becomes_plain_unc(self):
        self.assertEqual(
            restic_config.canonical_path_for_comparison(r"\\?\UNC\server\share\dir"),
            r"\\server\share\dir",
        )

    def test_local_admin_share_unc_maps_to_drive(self):
        self.assertEqual(
            restic_config.canonical_path_for_comparison(r"\\LOCALHOST\c$\Users\me"),
            r"C:\Users\me",
        )

    def test_remote_admin_share_unc_is_not_mapped(self):
        self.assertEqual(
            restic_config.canonical_path_for_comparison(r"\\fileserver\c$\dir"),
            r"\\fileserver\c$\dir",
        )

    def test_path_contains_or_equals_accepts_equivalent_forms(self):
        contains = restic_config.path_contains_or_equals

        self.assertTrue(contains(r"C:\base", r"\\?\C:\base\sub"))
        self.assertTrue(contains(r"\\localhost\c$\base", r"C:\base\sub"))
        self.assertTrue(contains(r"C:\base", r"C:\base"))
        self.assertFalse(contains(r"C:\base", r"C:\basement"))


class AdminShareDetectionTests(unittest.TestCase):
    # is_admin_share_unc_path 是纯字符串判定（探测与主机无关），无平台门控
    def test_detects_admin_share_form_on_any_host(self):
        is_admin_share = restic_config.is_admin_share_unc_path

        self.assertTrue(is_admin_share(r"\\localhost\c$\dir"))
        self.assertTrue(is_admin_share(r"\\192.168.1.5\d$\dir"))
        self.assertTrue(is_admin_share(r"\\unknown-host.example.com\e$"))
        self.assertTrue(is_admin_share(r"\\LOCALHOST\C$"))

    def test_detects_admin_share_behind_verbatim_prefix(self):
        is_admin_share = restic_config.is_admin_share_unc_path

        self.assertTrue(is_admin_share(r"\\?\UNC\fileserver\c$\dir"))
        self.assertTrue(is_admin_share(r"\\?\UNC\192.168.1.5\c$\dir"))

    def test_regular_shares_and_drive_paths_are_not_admin_share(self):
        is_admin_share = restic_config.is_admin_share_unc_path

        self.assertFalse(is_admin_share(r"\\fileserver\backup\dir"))
        self.assertFalse(is_admin_share(r"\\?\UNC\fileserver\backup\dir"))
        self.assertFalse(is_admin_share(r"C:\dir"))
        self.assertFalse(is_admin_share(r"\\?\C:\dir"))
        self.assertFalse(is_admin_share(r"/srv/backup"))
        self.assertFalse(is_admin_share(""))
        self.assertFalse(is_admin_share(None))
