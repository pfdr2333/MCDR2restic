# -*- coding: utf-8 -*-
"""从 schema 与模板文本资源渲染配置模板。

取代手写的 config_templates 模板主体：键序与默认值来自 schema，
注释来自 lang 资源（``template.comment.*``）。渲染结果与重构前的
模板 blob 逐字节一致，由 golden 测试保障。
"""

from __future__ import annotations

import json
from typing import Any, List

from mcdr2restic.config import schema
from mcdr2restic.config.schema.model import Field
from mcdr2restic.defaults.config_template_resources import config_template_text
from mcdr2restic.defaults.default_constants import CONFIG_VERSION
from mcdr2restic.defaults.message_defaults import DEFAULT_MESSAGE_KEYS

# 以下标记由 default_config_templates 的渲染管线在运行时替换
LANGUAGE_MARKER = "__MCDR2RESTIC_LANGUAGE__"
EXECUTABLE_MARKER = "__MCDR2RESTIC_EXECUTABLE__"
REPOSITORY_MARKER = "__MCDR2RESTIC_REPOSITORY__"
CONFIG_VERSION_MARKER_COMMENT = "__MCDR2RESTIC_CONFIG_VERSION_MARKER_COMMENT__"


def render_config_template_text(language: str) -> str:
    """渲染原始配置模板文本（含占位标记，与历史模板 blob 同构）。"""

    lines: List[str] = []
    lines.extend(comment_lines(language, "template.header", indent=0))
    lines.append("")
    for item in schema.TOP_LEVEL_FIELDS:
        lines.extend(field_entry_lines(item, indent=0, language=language))
        lines.append("")
    for section in schema.SECTIONS:
        lines.extend(section_block_lines(section, language))
        lines.append("")
    lines.extend(messages_block_lines(language))
    lines.append("")
    lines.append(CONFIG_VERSION_MARKER_COMMENT)
    lines.append("config_version: {}".format(CONFIG_VERSION))
    return "\n".join(lines)


def section_block_lines(section, language: str) -> List[str]:
    lines = ["{}:".format(section.key)]
    intro_key = getattr(section, "intro_comment_key", None)
    if intro_key:
        lines.extend(comment_lines(language, intro_key, indent=2))
    for item in section.fields:
        lines.extend(field_entry_lines(item, indent=2, language=language))
    return lines


def messages_block_lines(language: str) -> List[str]:
    lines = ["messages:"]
    lines.extend(comment_lines(language, "template.comment.messages", indent=2))
    for key in DEFAULT_MESSAGE_KEYS:
        lines.append("  {}: |-".format(key))
        lines.append("    __MCDR2RESTIC_MESSAGE_{}__".format(key))
    return lines


def field_entry_lines(field: Field, indent: int, language: str) -> List[str]:
    """渲染单个条目：可选标记行、上方注释、键值行、行尾注释。"""

    lines: List[str] = []
    if field.comment_key and not field.comment_inline:
        lines.extend(comment_lines(language, field.comment_key, indent))
    for marker in field.comment_markers:
        lines.append(marker)
    lines.extend(value_lines(field, indent))
    if field.comment_key and field.comment_inline:
        comment = config_template_text(language, field.comment_key)
        lines[-1] = "{} {}".format(lines[-1], comment.strip())
    return lines


def value_lines(field: Field, indent: int) -> List[str]:
    pad = " " * indent
    key_line = "{}{}:".format(pad, field.key)
    if field.value_marker:
        return ["{}{}: {}".format(pad, field.key, field.value_marker)]
    if field.raw_value_lines is not None:
        return [key_line, *field.raw_value_lines]
    default = field.default
    if isinstance(default, (list, dict)):
        return collection_value_lines(field, default, pad, key_line)
    return ["{}{}: {}".format(pad, field.key, yaml_scalar(default))]


def collection_value_lines(field: Field, default: Any, pad: str, key_line: str) -> List[str]:
    if isinstance(default, list):
        if not default:
            return ["{} []".format(key_line)]
        items = ["{}  - {}".format(pad, sequence_item_text(item, field)) for item in default]
    else:
        if not default:
            return ["{} {{}}".format(key_line)]
        items = ["{}  {}: {}".format(pad, key, yaml_scalar(value)) for key, value in default.items()]
    return [key_line, *items]


def sequence_item_text(item: Any, field: Field) -> str:
    if field.quote_single_items:
        return "'{}'".format(str(item).replace("'", "''"))
    return yaml_scalar(item)


def comment_lines(language: str, key: str, indent: int) -> List[str]:
    text = config_template_text(language, key)
    pad = " " * indent
    return ["{}{}".format(pad, line) if line else "" for line in text.split("\n")]


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    return json.dumps(str(value), ensure_ascii=False)
