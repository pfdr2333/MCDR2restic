import os

try:
    from .support import (
        DEFAULT_CONFIG,
        apply_config_file_migrations,
        build_default_config,
        ConfigError,
        FakeLogger,
        FakeServer,
        get_default_config_template,
        load_config_file_mapping,
        migrate_config_file,
        migrate_legacy_config,
        replace_or_append_enabled_line,
        tempfile,
        unittest,
        WHOLE_BLOCK_SECTIONS,
        yaml,
    )
except ImportError:
    from support import (
        DEFAULT_CONFIG,
        apply_config_file_migrations,
        build_default_config,
        ConfigError,
        FakeLogger,
        FakeServer,
        get_default_config_template,
        load_config_file_mapping,
        migrate_config_file,
        migrate_legacy_config,
        replace_or_append_enabled_line,
        tempfile,
        unittest,
        WHOLE_BLOCK_SECTIONS,
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

    def test_apply_config_file_migrations_inserts_all_registered_top_level_blocks(
        self,
    ):
        lines = ["enabled: true\n", "schedule:\n", "  interval_seconds: 0\n"]
        cfg = build_default_config("en_us")

        migrated = "".join(apply_config_file_migrations(lines, "en_us", cfg))
        migrated_cfg = yaml.safe_load(migrated)

        for section in WHOLE_BLOCK_SECTIONS:
            self.assertIn(section, migrated_cfg)
        self.assertEqual(
            migrated_cfg["custom_webhook"],
            build_default_config("en_us")["custom_webhook"],
        )

    def test_config_template_registered_sections_match_default_config(self):
        for language in ("zh_cn", "en_us"):
            template_cfg = yaml.safe_load(get_default_config_template(language))
            default_cfg = build_default_config(language)
            for section in WHOLE_BLOCK_SECTIONS:
                self.assertEqual(
                    template_cfg[section],
                    default_cfg[section],
                    "template drift for {} in {}".format(section, language),
                )

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

    def test_config_migration_warns_about_deprecated_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write(
                    "schedule:\n"
                    "  require_player_joined_in_wait_period: true\n"
                    "  online_check_interval_seconds: 60\n"
                    "config_version: 8\n"
                )

            migrate_config_file(server, "en_us", build_default_config("en_us"))

        warnings = "\n".join(server.logger.warning_messages)
        self.assertIn("schedule.require_player_joined_in_wait_period", warnings)
        self.assertIn("schedule.online_check_interval_seconds", warnings)

    def test_config_migration_stays_silent_on_clean_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write("enabled: true\nschedule:\n  interval_seconds: 0\n")

            migrate_config_file(server, "en_us", build_default_config("en_us"))

        self.assertEqual(server.logger.warning_messages, [])

    def test_config_migration_creates_versioned_backup_and_replaces_atomically(self):
        original = "enabled: true\nconfig_version: 8\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write(original)

            migrate_config_file(server, "en_us", build_default_config("en_us"))

            with open(config_path, "r", encoding="utf8") as file:
                migrated = file.read()
            backup_path = os.path.join(temp_dir, "config.yml.v8.bak")
            with open(backup_path, "r", encoding="utf8") as file:
                backup = file.read()

            self.assertEqual(backup, original)
            self.assertEqual(yaml.safe_load(migrated)["config_version"], 10)
            self.assertFalse(os.path.exists(config_path + ".temp"))

    def test_invalid_config_fails_without_automatic_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write("messages:\n  backup_start: |-\n        first\n    second\n")

            with self.assertRaises(ConfigError) as error:
                load_config_file_mapping(server)

        self.assertEqual(error.exception.i18n_key, "error.config.yaml_invalid")
