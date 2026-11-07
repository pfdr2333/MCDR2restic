# -*- coding: utf-8 -*-
import json
import os

try:
    from .support import unittest
except ImportError:
    from support import unittest

from mcdr2restic.config.schema import SECTIONS, derive_base_default_config
from mcdr2restic.defaults.default_config import build_default_config

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_golden(language: str):
    path = os.path.join(GOLDEN_DIR, "default_config_golden_{}.json".format(language))
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


class DeriveDefaultConfigTest(unittest.TestCase):
    """schema 推导的默认值必须与重构前固化的 golden 基线完全一致。"""

    def test_build_default_config_matches_golden(self):
        for language in ("zh_cn", "en_us"):
            with self.subTest(language=language):
                self.assertEqual(build_default_config(language), load_golden(language))

    def test_derive_base_default_config_matches_golden(self):
        self.assertEqual(derive_base_default_config(), load_golden("zh_cn"))


class SchemaStructureTest(unittest.TestCase):
    def test_section_and_field_keys_unique(self):
        section_keys = [section.key for section in SECTIONS]
        self.assertEqual(len(section_keys), len(set(section_keys)))
        for section in SECTIONS:
            field_keys = [item.key for item in section.fields]
            self.assertEqual(
                len(field_keys),
                len(set(field_keys)),
                "duplicate field in section {}".format(section.key),
            )


if __name__ == "__main__":
    unittest.main()
