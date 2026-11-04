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


if __name__ == "__main__":
    unittest.main()
