import json
import os
import sys

try:
    from .support import REPO_ROOT, tempfile, unittest
except ImportError:
    from support import REPO_ROOT, tempfile, unittest

sys.path.insert(0, str(REPO_ROOT))

from tools.check_language_divergence import (
    load_semantic_messages,
    normalize_base_sha,
)


class LanguageDivergenceCheckTests(unittest.TestCase):
    def test_load_semantic_messages_strips_plugin_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root_file = os.path.join(temp_dir, "root.json")
            package_file = os.path.join(temp_dir, "package.json")
            with open(root_file, "w", encoding="utf8") as file:
                json.dump({"mcdr2restic.info.restore.no_tasks": "暂无任务"}, file)
            with open(package_file, "w", encoding="utf8") as file:
                json.dump({"info.restore.no_tasks": "暂无任务"}, file)

            self.assertEqual(
                load_semantic_messages(root_file),
                load_semantic_messages(package_file),
            )

    def test_load_semantic_messages_detects_value_divergence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root_file = os.path.join(temp_dir, "root.json")
            package_file = os.path.join(temp_dir, "package.json")
            with open(root_file, "w", encoding="utf8") as file:
                json.dump({"mcdr2restic.key": "root 版本"}, file)
            with open(package_file, "w", encoding="utf8") as file:
                json.dump({"key": "package 版本"}, file)

            self.assertNotEqual(
                load_semantic_messages(root_file),
                load_semantic_messages(package_file),
            )

    def test_normalize_base_sha_falls_back_for_empty_and_zero(self):
        zero_sha = "0" * 40

        self.assertEqual(normalize_base_sha(""), "HEAD~1")
        self.assertEqual(normalize_base_sha(zero_sha), "HEAD~1")
        self.assertEqual(normalize_base_sha("abc123"), "abc123")


class ConfigTextsSyncTests(unittest.TestCase):
    """zh_tw 配置模板文本由同步工具生成，必须与 zh_cn 键集对齐。"""

    def test_zh_tw_config_texts_cover_all_zh_cn_keys(self):
        def load(language):
            path = os.path.join(
                REPO_ROOT, "mcdr2restic", "config_texts", "{}.json".format(language)
            )
            with open(path, "r", encoding="utf8") as file:
                return json.load(file)

        zh_cn = load("zh_cn")
        zh_tw = load("zh_tw")
        self.assertEqual(set(zh_tw), set(zh_cn))
        for key, value in zh_tw.items():
            self.assertTrue(value.strip(), "empty zh_tw template text: {}".format(key))


if __name__ == "__main__":
    unittest.main()
