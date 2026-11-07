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
    """模板渲染入口：允许占位标记与手工排版值。"""

    pad = " " * indent
    if field.value_marker:
        return ["{}{}: {}".format(pad, field.key, field.value_marker)]
    if field.raw_value_lines is not None:
        return ["{}{}:".format(pad, field.key), *field.raw_value_lines]
    return generic_value_lines(field, field.default, pad)


def generic_value_lines(field: Field, value: Any, pad: str) -> List[str]:
    """通用键值行渲染：迁移补写与模板共用的标量/集合格式。"""

    key_line = "{}{}:".format(pad, field.key)
    if isinstance(value, (list, dict)):
        return collection_value_lines(field, value, pad, key_line)
    return ["{}{}: {}".format(pad, field.key, yaml_scalar(value))]


def collection_value_lines(field: Field, value: Any, pad: str, key_line: str) -> List[str]:
    if isinstance(value, list):
        if not value:
            return ["{} []".format(key_line)]
        items = ["{}  - {}".format(pad, sequence_item_text(item, field)) for item in value]
    else:
        if not value:
            return ["{} {{}}".format(key_line)]
        items = ["{}  {}: {}".format(pad, key, yaml_scalar(item)) for key, item in value.items()]
    return [key_line, *items]


def migration_field_entry_lines(field: Field, value: Any, language: str, indent: int = 2) -> List[str]:
    """渲染迁移补写条目：注释来自模板文本，值取运行时合并结果。

    与模板渲染不同：不输出占位标记与手工排版值（迁移插入的必须是
    可解析的具体值），占位标记类字段按通用标量格式落盘。返回行均带
    换行符，可直接拼入按行组织的配置文件。
    """

    pad = " " * indent
    lines: List[str] = []
    if field.comment_key and not field.comment_inline:
        lines.extend(comment_lines(language, field.comment_key, indent))
    value_part = generic_value_lines(field, value, pad)
    if field.comment_key and field.comment_inline:
        comment = config_template_text(language, field.comment_key)
        value_part[-1] = "{} {}".format(value_part[-1], comment.strip())
    lines.extend(value_part)
    return ["{}\n".format(line) if line else "\n" for line in lines]


def sequence_item_text(item: Any, field: Field) -> str:
    if isinstance(item, (list, dict)):
        # 嵌套集合用 JSON 流式语法（JSON 是合法 YAML flow），如 maintenance_commands
        return json.dumps(item, ensure_ascii=False)
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
