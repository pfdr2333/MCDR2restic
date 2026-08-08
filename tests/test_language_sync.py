try:
    from .support import mock, tempfile, unittest
except ImportError:
    from support import mock, tempfile, unittest

from tools import sync_language_resources


class LanguageSyncTests(unittest.TestCase):
    def test_traditional_conversion_uses_opencc_taiwan_dictionary(self):
        self.assertEqual(
            sync_language_resources.to_traditional_chinese("配置文件和服务端消息模板"),
            "配置檔案和服務端訊息模板",
        )

    def test_config_templates_are_written_only_to_package_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = sync_language_resources.Path(temp_dir) / "package"
            root_dir = sync_language_resources.Path(temp_dir) / "root"
            package_dir.mkdir()

            with (
                mock.patch.object(
                    sync_language_resources,
                    "PACKAGE_CONFIG_TEMPLATE_DIR",
                    package_dir,
                ),
                mock.patch.object(
                    sync_language_resources,
                    "ROOT_LANG_DIR",
                    root_dir,
                ),
            ):
                sync_language_resources.write_config_template_language(
                    "en_us", "enabled: true\n", {"template.message.test": "hello"}
                )

            self.assertTrue((package_dir / "en_us.yml").is_file())
            self.assertFalse((root_dir / "en_us.yml").exists())
            self.assertFalse(
                hasattr(sync_language_resources, "ROOT_CONFIG_TEMPLATE_DIR")
            )
