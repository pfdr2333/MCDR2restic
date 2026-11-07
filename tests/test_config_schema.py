# -*- coding: utf-8 -*-
import json
import os
import re

import yaml

try:
    from .support import unittest
except ImportError:
    from support import unittest

from mcdr2restic.config.config_migration import apply_config_file_migrations
from mcdr2restic.config.config_lines import template_top_level_keys
from mcdr2restic.config.schema import SECTIONS, derive_base_default_config
from mcdr2restic.config.template_render import render_config_template_text
from mcdr2restic.defaults.default_config import build_default_config

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_golden(language: str):
    path = os.path.join(GOLDEN_DIR, "default_config_golden_{}.json".format(language))
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_template_golden(language: str) -> str:
    path = os.path.join(GOLDEN_DIR, "config_template_golden_{}.txt".format(language))
    with open(path, "r", encoding="utf-8", newline="") as file:
        return file.read()


class RenderConfigTemplateTest(unittest.TestCase):
    """schema 渲染的模板必须与重构前固化的模板 blob 逐字节一致。"""

    def test_rendered_template_matches_golden(self):
        for language in ("zh_cn", "en_us"):
            with self.subTest(language=language):
                rendered = render_config_template_text(language)
                self.assertEqual(rendered, load_template_golden(language))


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

    def test_schema_sections_match_template_order(self):
        section_keys = {section.key for section in SECTIONS}
        template_keys = template_top_level_keys("zh_cn")
        self.assertEqual(
            [key for key in template_keys if key in section_keys],
            [section.key for section in SECTIONS],
        )


class MigrationCoverageTest(unittest.TestCase):
    """schema 内每个键都必须能被文件侧迁移补写，杜绝新增键静默缺失。

    这是替代"新增配置键四件套"人工清单的结构性守卫：以后加键只要
    进了 schema，本测试自动要求迁移能补写它，测试红即提示。
    """

    MINIMAL_OLD_CONFIG_LINES = ["enabled: true\n"] + ["{}:\n".format(section.key) for section in SECTIONS]

    def test_every_schema_field_is_backfilled_by_migration(self):
        for language in ("zh_cn", "en_us"):
            with self.subTest(language=language):
                migrated = "".join(
                    apply_config_file_migrations(
                        list(self.MINIMAL_OLD_CONFIG_LINES),
                        language,
                        build_default_config(language),
                    )
                )
                migrated_cfg = yaml.safe_load(migrated)
                for section in SECTIONS:
                    block = self._block_lines(migrated, section.key)
                    for field in section.fields:
                        key_pattern = re.compile(r"^  {}:".format(re.escape(field.key)), re.MULTILINE)
                        self.assertEqual(
                            len(key_pattern.findall(block)),
                            1,
                            "field {}.{} not backfilled exactly once".format(section.key, field.key),
                        )
                        self.assertEqual(migrated_cfg[section.key][field.key], field.default)

    @staticmethod
    def _block_lines(text: str, section_key: str) -> str:
        lines = text.splitlines(keepends=True)
        start = next(index for index, line in enumerate(lines) if line.rstrip("\n") == "{}:".format(section_key))
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index] and not lines[index][0].isspace() and not lines[index].startswith("#"):
                end = index
                break
        return "".join(lines[start:end])


if __name__ == "__main__":
    unittest.main()
