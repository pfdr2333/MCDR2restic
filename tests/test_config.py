try:
    from .support import (
        DEFAULT_CONFIG,
        apply_config_file_migrations,
        build_default_config,
        migrate_legacy_config,
        replace_or_append_enabled_line,
        unittest,
        yaml,
    )
except ImportError:
    from support import (
        DEFAULT_CONFIG,
        apply_config_file_migrations,
        build_default_config,
        migrate_legacy_config,
        replace_or_append_enabled_line,
        unittest,
        yaml,
    )
class ConfigurationTests(unittest.TestCase):
    def test_default_config_constructor_returns_independent_copies(self):
        first = build_default_config()
        second = build_default_config()

        first["restic"]["backup_command"].append("--changed")

        self.assertNotIn("--changed", second["restic"]["backup_command"])
        with self.assertRaises(TypeError):
            DEFAULT_CONFIG["enabled"] = False
        with self.assertRaises(TypeError):
            DEFAULT_CONFIG["restic"]["password"] = "changed"

    def test_migrate_legacy_config_moves_restic_environment_secrets(self):
        cfg = {
            "config_version": 1,
            "restic": {
                "environment": {
                    "RESTIC_REPOSITORY": "/repo",
                    "RESTIC_PASSWORD_FILE": "password.txt",
                },
                "timeout_seconds": 3600,
            },
            "schedule": {"require_player_joined_in_wait_period": False},
        }

        migrate_legacy_config(cfg)

        self.assertEqual(cfg["restic"]["repository"], "/repo")
        self.assertEqual(cfg["restic"]["password_file"], "password.txt")
        self.assertEqual(cfg["restic"]["password"], "")
        self.assertEqual(cfg["restic"]["timeout_seconds"], 0)
        self.assertFalse(cfg["schedule"]["require_player_activity_in_wait_period"])

    def test_apply_config_file_migrations_removes_deprecated_schedule_keys(self):
        lines = [
            "enabled: true\n",
            "schedule:\n",
            "  # nobody joined during waiting period\n",
            "  require_player_joined_in_wait_period: true\n",
            "  online_check_interval_seconds: 60\n",
            "restic:\n",
            "  executable: restic\n",
            "messages:\n",
            "  ok: ok\n",
        ]
        cfg = {
            "schedule": {
                "require_player_activity_in_wait_period": True,
                "online_check_command": "list",
            },
            "restic": {},
        }

        migrated = "".join(apply_config_file_migrations(lines, "en_us", cfg))

        self.assertIn("require_player_activity_in_wait_period", migrated)
        self.assertIn("online_check_command", migrated)
        self.assertEqual(yaml.safe_load(migrated)["language"], "en_us")
        self.assertIn("maintenance_schedule:", migrated)
        self.assertNotIn("  require_player_joined_in_wait_period:", migrated)
        self.assertNotIn("  online_check_interval_seconds:", migrated)
        self.assertIn("config_version: 10", migrated)

    def test_apply_config_file_migrations_is_idempotent(self):
        lines = [
            "enabled: true\n",
            "schedule:\n",
            "  interval_seconds: 0\n",
            "restic:\n",
            "  executable: restic\n",
            "minecraft:\n",
            "  save_off_command: save-off\n",
            "notification:\n",
            "  notify_on_success: true\n",
            "messages:\n",
            "  ok: ok\n",
        ]
        cfg = build_default_config("en_us")

        first = apply_config_file_migrations(lines, "en_us", cfg)
        second = apply_config_file_migrations(first, "en_us", cfg)

        self.assertEqual(first, second)
        migrated = "".join(first)
        self.assertEqual(migrated.count("force_schedule:"), 1)
        self.assertEqual(migrated.count("language:"), 1)
        self.assertEqual(migrated.count("maintenance_schedule:"), 1)
        self.assertEqual(migrated.count("update_check:"), 1)
        self.assertEqual(migrated.count("snapshot_cache:"), 1)
        self.assertEqual(migrated.count("restore:"), 1)

    def test_apply_config_file_migrations_keeps_top_level_comments_with_following_block(
        self,
    ):
        lines = [
            "enabled: true\n",
            "schedule:\n",
            "  interval_seconds: 0\n",
            "\n",
            "# Restic settings stay attached\n",
            "restic:\n",
            "  executable: restic\n",
            "\n",
            "# Minecraft commands stay attached\n",
            "minecraft:\n",
            "  save_off_command: save-off\n",
            "notification:\n",
            "  notify_on_success: true\n",
            "messages:\n",
            "  ok: ok\n",
        ]
        cfg = build_default_config("en_us")

        migrated = apply_config_file_migrations(lines, "en_us", cfg)

        restic_comment_index = migrated.index("# Restic settings stay attached\n")
        minecraft_comment_index = migrated.index("# Minecraft commands stay attached\n")
        self.assertEqual(migrated[restic_comment_index + 1], "restic:\n")
        self.assertEqual(migrated[minecraft_comment_index + 1], "minecraft:\n")

    def test_replace_or_append_enabled_line(self):
        self.assertEqual(
            replace_or_append_enabled_line(["name: test\n"], False),
            ["name: test\n", "enabled: false\n"],
        )
        self.assertEqual(
            replace_or_append_enabled_line(["enabled: true\n"], False),
            ["enabled: false\n"],
        )
