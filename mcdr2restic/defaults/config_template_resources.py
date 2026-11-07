# -*- coding: utf-8 -*-
"""配置模板文本资源。

模板注释与片段文本统一存放在 ``mcdr2restic.config_texts`` 包（JSON，
键形如 ``template.comment.*``、``template.snippet.*``、
``template.message.*``），zh_tw 由同步工具生成。本模块负责按语言
解析文本并展开 ``__MCDR2RESTIC_PLATFORM_*__`` 平台分支标记。
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from importlib import resources
from typing import Dict

from mcdr2restic.core.i18n import (
    FALLBACK_LANGUAGE,
    available_language_codes,
    normalize_language,
)


TEMPLATE_TEXTS_PACKAGE = "mcdr2restic.config_texts"
PLATFORM_MARKER_PATTERN = re.compile(r"^__MCDR2RESTIC_PLATFORM_(WINDOWS|POSIX)__\s*$")


@lru_cache(maxsize=None)
def load_template_texts(language: str) -> Dict[str, str]:
    resource_name = "{}.json".format(normalize_language(language))
    try:
        with resources.open_text(TEMPLATE_TEXTS_PACKAGE, resource_name, encoding="utf8") as file:
            data = json.load(file)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if isinstance(key, str) and isinstance(value, str)}


def available_config_template_languages() -> frozenset[str]:
    return available_language_codes()


def config_template_text(language: str, key: str) -> str:
    texts = load_template_texts(language)
    if key in texts:
        return render_platform_markers(texts[key])
    fallback = load_template_texts(FALLBACK_LANGUAGE)
    if key in fallback:
        return render_platform_markers(fallback[key])
    return key


def render_platform_markers(text: str) -> str:
    blocks = split_platform_marker_blocks(text)
    if not blocks:
        return text
    target = "WINDOWS" if os.name == "nt" else "POSIX"
    selected = blocks.get(target) or blocks.get("POSIX") or next(iter(blocks.values()), "")
    return trim_empty_edge_lines(selected)


def split_platform_marker_blocks(text: str) -> Dict[str, str]:
    blocks: Dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        match = PLATFORM_MARKER_PATTERN.match(line.strip())
        if match is not None:
            current = match.group(1)
            blocks.setdefault(current, [])
            continue
        if current:
            blocks.setdefault(current, []).append(line)
    return {key: "\n".join(lines) for key, lines in blocks.items()}


def trim_empty_edge_lines(text: str) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
