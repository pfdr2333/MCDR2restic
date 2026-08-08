try:
    from .support import (
        FakeCommandSource,
        FakePluginServer,
        config_template_resources,
        get_default_config_template,
        get_default_message_template,
        i18n,
        json,
        load_yaml_mapping_with_text_repair,
        make_source_translate,
        mock,
        normalize_language,
        os,
        repair_inconsistent_block_scalar_indentation,
        tempfile,
        tr,
        tr_error,
        unittest,
        yaml,
    )
except ImportError:
    from support import (
        FakeCommandSource,
        FakePluginServer,
        config_template_resources,
        get_default_config_template,
        get_default_message_template,
        i18n,
        json,
        load_yaml_mapping_with_text_repair,
        make_source_translate,
        mock,
        normalize_language,
        os,
        repair_inconsistent_block_scalar_indentation,
        tempfile,
        tr,
        tr_error,
        unittest,
        yaml,
    )


class I18nTests(unittest.TestCase):
    def test_make_source_translate_prefers_source_language_over_server_default(self):
        server = FakePluginServer(language="en_us")
        source = FakeCommandSource(language="zh_cn")

        translate = make_source_translate(source, server)

        self.assertEqual(translate("info.backup.enabled"), "MCDR2Restic 定时备份已启用")

    def test_normalize_language_uses_supported_fallbacks(self):
        self.assertEqual(normalize_language("zh-TW"), "zh_tw")
        self.assertEqual(normalize_language("fr_fr"), "en_us")

    def test_translation_formats_named_parameters(self):
        self.assertEqual(
            tr("en_us", "info.backup.success", label="manual", duration_seconds=3),
            "manual backup completed in 3s",
        )

    def test_translation_accepts_prefixed_mcdr_key(self):
        self.assertEqual(
            tr(
                "en_us",
                "mcdr2restic.info.backup.success",
                label="manual",
                duration_seconds=3,
            ),
            "manual backup completed in 3s",
        )

    def test_translation_keeps_missing_placeholders_visible(self):
        self.assertIn("{level}", tr("zh_cn", "error.permission.denied"))

    def test_translation_missing_language_key_falls_back_to_english(self):
        with (
            mock.patch.object(
                i18n,
                "available_language_codes",
                return_value=frozenset({"xx", "en_us"}),
            ),
            mock.patch.object(i18n, "load_language_messages") as loader,
        ):
            loader.side_effect = lambda language: {
                "xx": {"info.backup.enabled": "custom enabled"},
                "en_us": {
                    "info.backup.enabled": "English enabled",
                    "info.backup.disabled": "English disabled",
                },
            }.get(language, {})

            self.assertEqual(tr("xx", "info.backup.enabled"), "custom enabled")
            self.assertEqual(tr("xx", "info.backup.disabled"), "English disabled")

    def test_root_lang_files_match_prefixed_package_lang_files(self):
        for name in ("zh_cn", "zh_tw", "en_us"):
            with open(
                os.path.join("mcdr2restic", "lang", "{}.json".format(name)),
                "r",
                encoding="utf8",
            ) as file:
                package_lang = json.load(file)
            with open(
                os.path.join("lang", "{}.json".format(name)), "r", encoding="utf8"
            ) as file:
                root_lang = json.load(file)

            expected = {
                "mcdr2restic.{}".format(key): value
                for key, value in package_lang.items()
            }
            self.assertEqual(root_lang, expected)

    def test_default_message_templates_come_from_config_template_resources(self):
        self.assertIn(
            "Backup started", get_default_message_template("backup_start", "en_us")
        )
        self.assertIn("备份开始", get_default_message_template("backup_start", "zh_cn"))
        self.assertIn("備份", get_default_message_template("backup_start", "zh_tw"))

    def test_config_template_text_missing_language_key_falls_back_to_english(self):
        def fake_loader(language):
            return {
                "xx": {"texts": {"template.message.backup_start": "custom start"}},
                "en_us": {
                    "texts": {
                        "template.message.backup_start": "English start",
                        "template.message.backup_success": "English success",
                    }
                },
            }.get(language, {})

        with mock.patch.object(
            config_template_resources, "load_config_template_resource", fake_loader
        ):
            self.assertEqual(
                config_template_resources.config_template_text(
                    "xx", "template.message.backup_start"
                ),
                "custom start",
            )
            self.assertEqual(
                config_template_resources.config_template_text(
                    "xx", "template.message.backup_success"
                ),
                "English success",
            )

    def test_config_file_template_text_is_not_stored_in_language_resources(self):
        for name in ("zh_cn", "zh_tw", "en_us"):
            with open(
                os.path.join("mcdr2restic", "lang", "{}.json".format(name)),
                "r",
                encoding="utf8",
            ) as file:
                package_lang = json.load(file)
            with open(
                os.path.join("mcdr2restic", "config_templates", "{}.yml".format(name)),
                "r",
                encoding="utf8",
            ) as file:
                template = file.read()

            self.assertNotIn("template.default_config", package_lang)
            self.assertFalse(
                any(key.startswith("template.message.") for key in package_lang)
            )
            self.assertFalse(
                any(key.startswith("template.snippet.") for key in package_lang)
            )
            self.assertIn("config_template:", template)
            self.assertIn("texts:", template)
            self.assertIn("template.message.backup_start", template)
            self.assertIn("__MCDR2RESTIC_PLATFORM_", template)
            self.assertIn("__MCDR2RESTIC_DEFAULT_BACKUP_SOURCES__", template)

    def test_default_config_template_renders_placeholders(self):
        for language in ("zh_cn", "zh_tw", "en_us", "fr_fr"):
            template = get_default_config_template(language, os.getcwd())

            expected_language = "fr_fr" if language == "fr_fr" else language
            loaded = yaml.safe_load(template)
            self.assertEqual(loaded["language"], expected_language)
            self.assertIn("messages:", template)
            self.assertIn("maintenance_schedule:", template)
            self.assertIn("config_version: 10", template)
            self.assertNotIn("__MCDR2RESTIC_", template)
            self.assertIsInstance(loaded, dict)

    def test_load_yaml_mapping_repairs_inconsistent_block_scalar_indentation(self):
        broken_config = (
            "messages:\n"
            "  backup_start: |-\n"
            "        first line\n"
            "    second line\n"
            "config_version: 9\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yml")
            with open(config_path, "w", encoding="utf8") as file:
                file.write(broken_config)

            load_result = load_yaml_mapping_with_text_repair(
                config_path,
                repair_inconsistent_block_scalar_indentation,
            )

        self.assertEqual(
            load_result.mapping["messages"]["backup_start"], "first line\nsecond line"
        )
        self.assertIsNotNone(load_result.repaired_text)
