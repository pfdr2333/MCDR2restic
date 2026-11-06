import os
import threading

try:
    from .support import (
        apply_config_file_migrations,
        build_default_config,
        ConfigError,
        create_runtime,
        FakeLogger,
        FakePluginServer,
        FakeServer,
        get_default_config_template,
        load_config_file_mapping,
        load_state_file,
        load_yaml_mapping_with_text_repair,
        migrate_config_file,
        migrate_legacy_config,
        mock,
        persist_player_state,
        replace_or_append_enabled_line,
        save_yaml_file,
        tempfile,
        unittest,
        WHOLE_BLOCK_SECTIONS,
        yaml,
    )
except ImportError:
    from support import (
        apply_config_file_migrations,
        build_default_config,
        ConfigError,
        create_runtime,
        FakeLogger,
        FakePluginServer,
        FakeServer,
        get_default_config_template,
        load_config_file_mapping,
        load_state_file,
        load_yaml_mapping_with_text_repair,
        migrate_config_file,
        migrate_legacy_config,
        mock,
        persist_player_state,
        replace_or_append_enabled_line,
        save_yaml_file,
        tempfile,
        unittest,
        WHOLE_BLOCK_SECTIONS,
        yaml,
    )

from mcdr2restic.config.config_loader import load_config, save_enabled_unlocked
from mcdr2restic.config.config_migration import (
    atomically_replace_config_with_backup,
)
from mcdr2restic.config.config_paths import ensure_config_file_exists
from mcdr2restic.defaults.message_defaults import build_default_messages


class ConfigurationTests(unittest.TestCase):
    def test_default_config_constructor_returns_independent_copies(self):
        first = build_default_config()
        second = build_default_config()

        first["restic"]["backup_command"].append("--changed")

        self.assertNotIn("--changed", second["restic"]["backup_command"])

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

    def test_apply_config_file_migrations_removes_deprecated_restore_key(self):
        lines = [
            "restore:\n",
            "  stop_timeout_seconds: 120\n",
            "  start_timeout_seconds: 120\n",
        ]
        cfg = build_default_config("en_us")

        migrated = "".join(apply_config_file_migrations(lines, "en_us", cfg))

        self.assertNotIn("stop_timeout_seconds", migrated)
        self.assertIn("start_timeout_seconds: 120", migrated)

    def test_apply_config_file_migrations_removes_deprecated_restic_download_keys(self):
        lines = [
            "restic:\n",
            "  executable: restic\n",
            "  # 找不到 restic 时自动下载。\n",
            "  auto_download: true\n",
            "  download_version: \"v0.19.1\"\n",
            "  download_proxy_prefixes:\n",
            "    - \"https://gh.llkk.cc/\"\n",
            "  download_timeout_seconds: 120\n",
            "  auto_init_local_repository: true\n",
        ]
        cfg = build_default_config("en_us")

        migrated = "".join(apply_config_file_migrations(lines, "en_us", cfg))

        self.assertNotIn("auto_download", migrated)
        self.assertNotIn("download_version", migrated)
        self.assertNotIn("download_proxy_prefixes", migrated)
        self.assertNotIn("download_timeout_seconds", migrated)
        self.assertNotIn("找不到 restic 时自动下载", migrated)
        self.assertIn("executable: restic\n", migrated)
        self.assertIn("use_path_restic: false\n", migrated)
        self.assertIn("auto_init_local_repository: true\n", migrated)

    def test_apply_config_file_migrations_appends_restic_keys_without_comment_duplication(self):
        # 相邻缺失键在模板中共享注释段，提取片段不得吞并后随注释，
        # 否则一次迁移会把同一注释段追加多次
        lines = [
            "enabled: true\n",
            "restic:\n",
            "  executable: restic\n",
            '  repository: "D:/backups/repo"\n',
            '  password: "secret"\n',
        ]
        cfg = build_default_config("zh_cn")

        migrated = "".join(apply_config_file_migrations(lines, "zh_cn", cfg))

        for comment in (
            "优先使用系统 PATH 中安装的 restic",
            "本地仓库缺失时自动执行 restic init",
            "backup/restore --json 进度回显间隔",
        ):
            self.assertEqual(migrated.count(comment), 1, comment)
        self.assertEqual(migrated.count("use_path_restic:"), 1)
        self.assertEqual(
            yaml.safe_load(migrated)["restic"]["use_path_restic"], False
        )

    def test_apply_config_file_migrations_appends_minecraft_save_all_keys(self):
        # v0.6.0 后 minecraft 块新增的 save-all 确认键，必须能追加进老配置既有块
        lines = [
            "enabled: true\n",
            "minecraft:\n",
            '  save_all_command: "save-all flush"\n',
            "  wait_after_save_off_seconds: 2\n",
            "  wait_after_save_all_seconds: 10\n",
        ]
        cfg = build_default_config("zh_cn")

        migrated = "".join(apply_config_file_migrations(lines, "zh_cn", cfg))

        self.assertIn("wait_for_save_all_completion: true\n", migrated)
        self.assertEqual(migrated.count("save_all_done_patterns:"), 1)
        for comment in (
            "是否通过服务端输出确认 save-all 完成",
            "判定 save-all 完成的输出关键词",
        ):
            self.assertEqual(migrated.count(comment), 1, comment)
        parsed = yaml.safe_load(migrated)
        self.assertTrue(parsed["minecraft"]["wait_for_save_all_completion"])
        self.assertEqual(
            parsed["minecraft"]["save_all_done_patterns"],
            cfg["minecraft"]["save_all_done_patterns"],
        )

    def test_config_migration_warns_about_removed_restore_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write(
                    "restore:\n"
                    "  stop_timeout_seconds: 120\n"
                    "  start_timeout_seconds: 120\n"
                    "config_version: 10\n"
                )

            migrate_config_file(server, "en_us", build_default_config("en_us"))

        warnings = "\n".join(server.logger.warning_messages)
        self.assertIn("restore.stop_timeout_seconds", warnings)

    def test_config_migration_warns_about_removed_restic_download_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write(
                    "restic:\n"
                    "  executable: restic\n"
                    "  auto_download: true\n"
                    "  download_timeout_seconds: 120\n"
                    "config_version: 10\n"
                )

            migrate_config_file(server, "en_us", build_default_config("en_us"))

        warnings = "\n".join(server.logger.warning_messages)
        self.assertIn("restic.auto_download", warnings)
        self.assertIn("restic.download_timeout_seconds", warnings)

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

    def test_config_template_maintenance_commands_match_default_config(self):
        # 模板与默认值漂移会让全新安装与缺键补默认的用户维护策略不一致（P1-3）
        for language in ("zh_cn", "en_us", "zh_tw"):
            template_cfg = yaml.safe_load(get_default_config_template(language))
            self.assertEqual(
                template_cfg["restic"]["maintenance_commands"],
                build_default_config(language)["restic"]["maintenance_commands"],
                "maintenance_commands drift for {}".format(language),
            )

    def test_default_template_messages_match_config_texts(self):
        # messages 块必须由标记行从 texts 注入；残留内联文案时 sync 无法自愈（P0-1）
        for language in ("zh_cn", "en_us"):
            template_cfg = yaml.safe_load(get_default_config_template(language))

            for key, text in build_default_messages(language).items():
                self.assertEqual(
                    template_cfg["messages"][key],
                    text,
                    "message drift for {} in {}".format(key, language),
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
                    "runtime:\n"
                    "  last_backup_status: success\n"
                    "config_version: 8\n"
                )

            migrate_config_file(server, "en_us", build_default_config("en_us"))

            with open(config_path, "r", encoding="utf8") as file:
                migrated = file.read()

        warnings = "\n".join(server.logger.warning_messages)
        self.assertIn("schedule.require_player_joined_in_wait_period", warnings)
        self.assertIn("schedule.online_check_interval_seconds", warnings)
        # runtime 顶级块从文件剥离并给出废弃告警（P3-7）
        self.assertIn("runtime", warnings)
        self.assertNotIn("runtime:", migrated)

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

    def test_migration_backup_rotates_when_same_version_backup_exists(self):
        # P0-2：同版本二次迁移不得硬失败，也不得覆盖历史备份
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write("first: true\n")

            atomically_replace_config_with_backup(config_path, "updated: 1\n", 8)
            atomically_replace_config_with_backup(config_path, "updated: 2\n", 8)

            first_backup = os.path.join(temp_dir, "config.yml.v8.bak")
            second_backup = os.path.join(temp_dir, "config.yml.v8.bak.1")
            self.assertTrue(os.path.exists(first_backup))
            self.assertTrue(os.path.exists(second_backup))
            with open(first_backup, "r", encoding="utf8") as file:
                self.assertEqual(file.read(), "first: true\n")
            with open(second_backup, "r", encoding="utf8") as file:
                self.assertEqual(file.read(), "updated: 1\n")
            with open(config_path, "r", encoding="utf8") as file:
                self.assertEqual(file.read(), "updated: 2\n")

    def test_migration_replace_failure_leaves_no_residue(self):
        # P0-2：os.replace 被杀软/文件占用打断时不得留下卡死下次启动的 .bak
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yml")
            original = "enabled: true\n"
            with open(config_path, "w", encoding="utf8") as file:
                file.write(original)

            with mock.patch(
                "mcdr2restic.config.config_migration.os.replace",
                side_effect=OSError("locked by antivirus"),
            ), self.assertRaises(ConfigError) as error:
                atomically_replace_config_with_backup(
                    config_path, "updated: 1\n", 8
                )

            self.assertEqual(
                error.exception.i18n_key, "error.config.migration_write_failed"
            )
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "config.yml.v8.bak")))
            self.assertFalse(os.path.exists(config_path + ".temp"))
            with open(config_path, "r", encoding="utf8") as file:
                self.assertEqual(file.read(), original)

    def test_invalid_config_fails_without_automatic_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write("messages:\n  backup_start: |-\n        first\n    second\n")

            with self.assertRaises(ConfigError) as error:
                load_config_file_mapping(server)

        self.assertEqual(error.exception.i18n_key, "error.config.yaml_invalid")

    @mock.patch("mcdr2restic.core.i18n._ACTIVE_LANGUAGE", "")
    def test_config_migration_serializes_with_enabled_flip(self):
        # 迁移与 enabled 翻转都是整文件替换，必须经 config_state.lock 互斥，
        # 否则并发时最后写入胜出、丢掉对方的改动（P1-9）
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            server.get_mcdr_language = lambda: "en_us"
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write(
                    "enabled: true\n"
                    "schedule:\n"
                    "  interval_seconds: 0\n"
                    "config_version: 8\n"
                )

            app_runtime = create_runtime()
            events = []
            migrate_entered = threading.Event()
            real_migrate = migrate_config_file

            def instrumented_migrate(*args, **kwargs):
                migrate_entered.set()
                real_migrate(*args, **kwargs)
                events.append("migrate_finished")

            def reload_config():
                try:
                    load_config(app_runtime, server)
                except Exception as exc:
                    events.append("load_config error: {}".format(exc))

            with mock.patch(
                "mcdr2restic.config.config_loader.migrate_config_file",
                side_effect=instrumented_migrate,
            ):
                worker = threading.Thread(target=reload_config, daemon=True)
                worker.start()
                self.assertTrue(migrate_entered.wait(timeout=5))
                with app_runtime.config_state.lock:
                    events.append("flip_finished")
                    save_enabled_unlocked(app_runtime, server, False)
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())

            self.assertEqual(events, ["migrate_finished", "flip_finished"])
            with open(config_path, "r", encoding="utf8") as file:
                final = yaml.safe_load(file.read())
            self.assertEqual(final["config_version"], 10)
            self.assertFalse(final["enabled"])

    @mock.patch("mcdr2restic.core.i18n._ACTIVE_LANGUAGE", "")
    def test_load_config_resets_stale_running_backup_status(self):
        # P1-3：进程被杀后 state.yml 残留 running，启动加载必须复位为已中断
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            server.get_mcdr_language = lambda: "en_us"
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write("enabled: true\nconfig_version: 10\n")
            state_path = os.path.join(temp_dir, "state.yml")
            with open(state_path, "w", encoding="utf8") as file:
                file.write("runtime:\n  last_backup_status: running\n")

            app_runtime = create_runtime()
            app_runtime.service.server = server
            load_config(app_runtime, server)

            with app_runtime.config_state.lock:
                runtime_state = app_runtime.config_state.config["runtime"]
            self.assertEqual(runtime_state["last_backup_status"], "interrupted")
            self.assertTrue(runtime_state["last_backup_message"])
            self.assertEqual(len(server.logger.warning_messages), 1)

    @mock.patch("mcdr2restic.core.i18n._ACTIVE_LANGUAGE", "")
    def test_load_config_keeps_running_status_with_live_backup_thread(self):
        # 重载窗口内备份线程仍在跑时不得误改 running 状态
        with tempfile.TemporaryDirectory() as temp_dir:
            server = FakeServer(temp_dir)
            server.logger = FakeLogger()
            server.get_mcdr_language = lambda: "en_us"
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write("enabled: true\nconfig_version: 10\n")
            state_path = os.path.join(temp_dir, "state.yml")
            with open(state_path, "w", encoding="utf8") as file:
                file.write("runtime:\n  last_backup_status: running\n")

            release = threading.Event()
            thread = threading.Thread(
                target=lambda: release.wait(timeout=5), daemon=True
            )
            thread.start()
            app_runtime = create_runtime()
            app_runtime.service.server = server
            app_runtime.backup.thread = thread
            try:
                load_config(app_runtime, server)

                with app_runtime.config_state.lock:
                    runtime_state = app_runtime.config_state.config["runtime"]
                self.assertEqual(runtime_state["last_backup_status"], "running")
                self.assertEqual(server.logger.warning_messages, [])
            finally:
                release.set()
                thread.join()

    def test_first_config_generation_is_atomic(self):
        server = FakePluginServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            server.get_data_folder = lambda: temp_dir

            ensure_config_file_exists(server, "en_us")

            with open(os.path.join(temp_dir, "config.yml"), "r", encoding="utf8") as file:
                self.assertIn("config_version:", file.read())

    def test_first_config_generation_probes_system_restic(self):
        server = FakePluginServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            server.get_data_folder = lambda: temp_dir

            with mock.patch(
                "mcdr2restic.config.config_paths.find_system_restic",
                return_value="/usr/bin/restic",
            ):
                ensure_config_file_exists(server, "en_us")

            with open(os.path.join(temp_dir, "config.yml"), "r", encoding="utf8") as file:
                cfg = yaml.safe_load(file.read())

        self.assertTrue(cfg["restic"]["use_path_restic"])

    def test_first_config_generation_without_system_restic_keeps_switch_off(self):
        server = FakePluginServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            server.get_data_folder = lambda: temp_dir

            with mock.patch(
                "mcdr2restic.config.config_paths.find_system_restic",
                return_value=None,
            ):
                ensure_config_file_exists(server, "en_us")

            with open(os.path.join(temp_dir, "config.yml"), "r", encoding="utf8") as file:
                cfg = yaml.safe_load(file.read())

        self.assertFalse(cfg["restic"]["use_path_restic"])

    def test_interrupted_first_config_generation_leaves_no_partial_file(self):
        # 首次生成 config.yml 中断时不得留下半截 YAML，否则下次启动报 yaml_invalid（P1-8）
        server = FakePluginServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            server.get_data_folder = lambda: temp_dir
            config_path = os.path.join(temp_dir, "config.yml")
            with mock.patch(
                "mcdr2restic.config.state_store.save_text_file_atomically",
                side_effect=RuntimeError("interrupted"),
            ), self.assertRaises(RuntimeError):
                ensure_config_file_exists(server, "en_us")

            self.assertFalse(os.path.exists(config_path))


class StateFileDurabilityTests(unittest.TestCase):
    def test_save_yaml_file_is_atomic_and_tolerates_stale_temp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.yml")
            save_yaml_file(path, {"a": 1})

            with open(path + ".tmp", "w", encoding="utf8") as file:
                file.write("garbage: [")
            self.assertEqual(
                load_yaml_mapping_with_text_repair(path).mapping, {"a": 1}
            )

            save_yaml_file(path, {"b": 2})
            self.assertEqual(
                load_yaml_mapping_with_text_repair(path).mapping, {"b": 2}
            )

    def test_concurrent_atomic_writes_keep_main_file_parseable(self):
        # 持锁写方与修复回写并发时不得因共享 .tmp 名互相截断，主文件始终可解析（P1-4）
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.yml")
            payloads = {
                index: {"index": index, "text": "x" * 500} for index in range(24)
            }
            errors = []

            def write(index):
                try:
                    save_yaml_file(path, payloads[index])
                except OSError as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=write, args=(index,)) for index in payloads
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertIn(
                load_yaml_mapping_with_text_repair(path).mapping["index"], payloads
            )
            self.assertEqual(
                [name for name in os.listdir(temp_dir) if name.endswith(".tmp")], []
            )

    def test_load_state_file_repairs_block_scalar_and_rewrites(self):
        server = FakePluginServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            server.get_data_folder = lambda: temp_dir
            path = os.path.join(temp_dir, "state.yml")
            with open(path, "w", encoding="utf8") as file:
                file.write(
                    "runtime:\n"
                    "  last_backup_message: |-\n"
                    "      first\n"
                    "    second\n"
                )

            state = load_state_file(server)

            self.assertIn("runtime", state)
            self.assertIn("first", state["runtime"]["last_backup_message"])
            with open(path, "r", encoding="utf8") as file:
                self.assertNotIn("      first", file.read())
        self.assertTrue(server.logger.warning_messages)

    def test_load_state_file_repairs_clip_block_scalar_header(self):
        # safe_dump 对以换行结尾的多行值产出 |（clip）头，修复须同样覆盖（P5-2）
        server = FakePluginServer()
        with tempfile.TemporaryDirectory() as temp_dir:
            server.get_data_folder = lambda: temp_dir
            path = os.path.join(temp_dir, "state.yml")
            with open(path, "w", encoding="utf8") as file:
                file.write(
                    "runtime:\n"
                    "  last_backup_message: |\n"
                    "      first\n"
                    "    second\n"
                )

            state = load_state_file(server)

            self.assertIn("first", state["runtime"]["last_backup_message"])
            with open(path, "r", encoding="utf8") as file:
                self.assertNotIn("      first", file.read())
        self.assertTrue(server.logger.warning_messages)

    def test_persist_player_state_throttles_file_writes(self):
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.config_state.config = {
            "runtime": {"last_player_joined": "player @ 2026-01-01 00:00:00"}
        }

        with mock.patch(
            "mcdr2restic.minecraft.player_activity_service.save_config_unlocked"
        ) as save:
            persist_player_state(runtime, server)
            persist_player_state(runtime, server)

        self.assertEqual(save.call_count, 1)
        self.assertEqual(
            runtime.config_state.state["runtime"]["last_player_joined"],
            "player @ 2026-01-01 00:00:00",
        )

    def test_persist_player_state_saves_on_fresh_boot(self):
        # 节流基准是负无穷哨兵：开机不足 30 秒（monotonic 值很小）时首次调用也必须立即落盘
        runtime = create_runtime()
        server = FakePluginServer()
        runtime.config_state.config = {
            "runtime": {"last_player_joined": "player @ 2026-01-01 00:00:00"}
        }

        with mock.patch(
            "mcdr2restic.minecraft.player_activity_service.time.monotonic",
            return_value=10.0,
        ), mock.patch(
            "mcdr2restic.minecraft.player_activity_service.save_config_unlocked"
        ) as save:
            persist_player_state(runtime, server)

        self.assertEqual(save.call_count, 1)
        self.assertEqual(runtime.config_state.player_state_persisted_at, 10.0)
