# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from functools import lru_cache
from importlib import resources
from typing import Any, Dict

import yaml

from mcdr2restic.core.i18n import FALLBACK_LANGUAGE


CONFIG_TEMPLATE_PACKAGE = "mcdr2restic.config_templates"
CONFIG_TEMPLATE_SUFFIX = ".yml"
CONFIG_TEMPLATE_KEY = "config_template"
TEXTS_KEY = "texts"
PLATFORM_MARKER_PATTERN = re.compile(
    r"^__MCDR2RESTIC_PLATFORM_(WINDOWS|POSIX)__\s*$"
)


@lru_cache(maxsize=None)
def available_config_template_languages() -> frozenset[str]:
    try:
        entries = resources.files(CONFIG_TEMPLATE_PACKAGE).iterdir()
    except Exception:
        return frozenset({FALLBACK_LANGUAGE})
    languages = [
        entry.name[: -len(CONFIG_TEMPLATE_SUFFIX)]
        for entry in entries
        if entry.name.endswith(CONFIG_TEMPLATE_SUFFIX)
    ]
    return frozenset(languages) or frozenset({FALLBACK_LANGUAGE})


@lru_cache(maxsize=None)
def load_config_template_resource(language: str) -> Dict[str, Any]:
    resource_name = "{}{}".format(
        resolve_config_template_language(language), CONFIG_TEMPLATE_SUFFIX
    )
    try:
        with resources.open_text(
            CONFIG_TEMPLATE_PACKAGE, resource_name, encoding="utf8"
        ) as file:
            data = yaml.safe_load(file) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_config_template_language(language: str) -> str:
    requested = normalize_language_name(language)
    if config_template_resource_exists(requested):
        return requested
    if requested in {"zh_tw", "zh_hant", "zh_hk", "zh_mo"} and config_template_resource_exists(
        "zh_tw"
    ):
        return "zh_tw"
    if (
        requested == "zh"
        or requested.startswith("zh_cn")
        or requested.startswith("zh_hans")
    ) and config_template_resource_exists("zh_cn"):
        return "zh_cn"
    return FALLBACK_LANGUAGE


def normalize_language_name(language: str) -> str:
    return str(language or "").strip().lower().replace("-", "_")


def config_template_resource_exists(language: str) -> bool:
    return str(language or "").strip() in available_config_template_languages()


def load_base_config_template(language: str) -> str:
    value = load_config_template_resource(language).get(CONFIG_TEMPLATE_KEY, "")
    return str(value or "")


def config_template_text(language: str, key: str) -> str:
    texts = load_config_template_resource(language).get(TEXTS_KEY, {})
    if isinstance(texts, dict) and key in texts:
        return render_platform_markers(str(texts[key]))

    fallback = load_config_template_resource(FALLBACK_LANGUAGE).get(TEXTS_KEY, {})
    if isinstance(fallback, dict):
        return render_platform_markers(str(fallback.get(key, key)))
    return key


def render_platform_markers(text: str) -> str:
    blocks = split_platform_marker_blocks(text)
    if not blocks:
        return text
    target = "WINDOWS" if os.name == "nt" else "POSIX"
    selected = (
        blocks.get(target) or blocks.get("POSIX") or next(iter(blocks.values()), "")
    )
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
