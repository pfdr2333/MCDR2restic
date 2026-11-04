# -*- coding: utf-8 -*-
"""Synchronize runtime language files and package config-template resources."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import yaml
from opencc import OpenCC

PLUGIN_ID = "mcdr2restic"
PACKAGE_LANG_DIR = Path("mcdr2restic/lang")
ROOT_LANG_DIR = Path("lang")
PACKAGE_CONFIG_TEMPLATE_DIR = Path("mcdr2restic/config_templates")
CONFIG_TEXT_PREFIXES = ("template.message.", "template.snippet.")
PLATFORM_SUFFIXES = (".posix", ".windows")
BASE_LANGUAGES = ("zh_cn", "en_us")
GENERATED_LANGUAGE = "zh_tw"

OPENCC_CONVERTER = OpenCC("s2twp")


def main() -> int:
    ensure_directories()
    for language in BASE_LANGUAGES:
        sync_base_language(language)
    sync_traditional_chinese()
    return 0


def ensure_directories():
    for directory in [
        PACKAGE_LANG_DIR,
        ROOT_LANG_DIR,
        PACKAGE_CONFIG_TEMPLATE_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def sync_base_language(language: str):
    package_lang = load_json(PACKAGE_LANG_DIR / "{}.json".format(language))
    template = ensure_language_config_block(
        load_existing_config_template(language), language
    )
    config_texts = collect_config_texts(package_lang)
    if not config_texts:
        config_texts = load_existing_config_texts(language)
    if not config_texts:
        config_texts = collect_config_texts(load_git_baseline_language(language))
    runtime_lang = remove_config_texts(package_lang)

    write_config_template_language(language, template, config_texts)
    write_package_lang(language, runtime_lang)
    write_root_lang(language, runtime_lang)


def sync_traditional_chinese():
    zh_lang = load_json(PACKAGE_LANG_DIR / "zh_cn.json")
    zh_template = load_config_template_language("zh_cn")
    runtime_lang = convert_value(zh_lang)
    template_lang = convert_value(zh_template)

    write_config_template_language(
        GENERATED_LANGUAGE,
        str(template_lang.get("config_template", "")),
        dict(template_lang.get("texts", {})),
    )
    write_package_lang(GENERATED_LANGUAGE, runtime_lang)
    write_root_lang(GENERATED_LANGUAGE, runtime_lang)


def load_existing_config_template(language: str) -> str:
    path = PACKAGE_CONFIG_TEMPLATE_DIR / "{}.yml".format(language)
    text = path.read_text(encoding="utf8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return text
    if isinstance(data, dict) and isinstance(data.get("config_template"), str):
        return data["config_template"]
    return text


def load_config_template_language(language: str) -> Dict[str, Any]:
    path = PACKAGE_CONFIG_TEMPLATE_DIR / "{}.yml".format(language)
    data = yaml.safe_load(path.read_text(encoding="utf8")) or {}
    return data if isinstance(data, dict) else {}


def load_existing_config_texts(language: str) -> Dict[str, str]:
    path = PACKAGE_CONFIG_TEMPLATE_DIR / "{}.yml".format(language)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf8")) or {}
    except yaml.YAMLError:
        return {}
    texts = data.get("texts") if isinstance(data, dict) else None
    if not isinstance(texts, dict):
        return {}
    return {str(key): str(value) for key, value in texts.items()}


def load_git_baseline_language(language: str) -> Dict[str, str]:
    path = "mcdr2restic/lang/{}.json".format(language)
    try:
        completed = subprocess.run(
            ["git", "show", "HEAD:{}".format(path.replace("\\", "/"))],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf8",
        )
    except Exception:
        return {}
    data = json.loads(completed.stdout)
    return (
        {str(key): str(value) for key, value in data.items()}
        if isinstance(data, dict)
        else {}
    )


def collect_config_texts(messages: Dict[str, str]) -> Dict[str, str]:
    texts: Dict[str, str] = {}
    platform_entries: Dict[str, Dict[str, str]] = {}
    for key, value in messages.items():
        if not is_config_text_key(key):
            continue
        base_key, platform = split_platform_key(key)
        if platform:
            platform_entries.setdefault(base_key, {})[platform] = value
            continue
        texts[key] = value

    for base_key, values in platform_entries.items():
        texts[base_key] = render_platform_text(values)
    return texts


def is_config_text_key(key: str) -> bool:
    return any(key.startswith(prefix) for prefix in CONFIG_TEXT_PREFIXES)


def split_platform_key(key: str) -> Tuple[str, str]:
    for suffix in PLATFORM_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)], suffix[1:].upper()
    return key, ""


def render_platform_text(values: Dict[str, str]) -> str:
    parts = []
    if "POSIX" in values:
        parts.extend(["__MCDR2RESTIC_PLATFORM_POSIX__", values["POSIX"]])
    if "WINDOWS" in values:
        parts.extend(["__MCDR2RESTIC_PLATFORM_WINDOWS__", values["WINDOWS"]])
    return "\n".join(parts)


def remove_config_texts(messages: Dict[str, str]) -> Dict[str, str]:
    return {
        key: value for key, value in messages.items() if not is_config_text_key(key)
    }


def write_config_template_language(
    language: str, config_template: str, texts: Dict[str, str]
):
    text = render_config_template_language(config_template, texts)
    (PACKAGE_CONFIG_TEMPLATE_DIR / "{}.yml".format(language)).write_text(
        text, encoding="utf8"
    )


def render_config_template_language(config_template: str, texts: Dict[str, str]) -> str:
    lines = ["config_template: |-\n"]
    lines.extend(indent_block(config_template))
    lines.append("\ntexts:\n")
    for key in sorted(texts):
        lines.append("  {}: |-\n".format(yaml_key(key)))
        lines.extend(indent_block(str(texts[key]), "    "))
    return "".join(lines)


def ensure_language_config_block(template: str, language: str) -> str:
    if "\nlanguage:" in "\n" + template:
        return template
    marker = "enabled: true\n\n"
    if marker not in template:
        return template
    return template.replace(marker, marker + language_config_block(language), 1)


def language_config_block(language: str) -> str:
    if language.startswith("zh"):
        return (
            "# 插件语言。默认会写入生成配置时 MCDR 传入的语言。\n"
            "# 可填写已有语言文件名；未匹配时回退 en_us。\n"
            "language: __MCDR2RESTIC_LANGUAGE__\n\n"
        )
    return (
        "# Plugin language. The generated default uses the language passed by MCDR.\n"
        "# Use an existing language file name; unknown values fall back to en_us.\n"
        "language: __MCDR2RESTIC_LANGUAGE__\n\n"
    )


def yaml_key(key: str) -> str:
    return json.dumps(key, ensure_ascii=False)


def indent_block(text: str, indent: str = "  ") -> Iterable[str]:
    lines = str(text).splitlines()
    if not lines:
        return [indent + "\n"]
    return [indent + line + "\n" for line in lines]


def write_package_lang(language: str, messages: Dict[str, str]):
    write_json(PACKAGE_LANG_DIR / "{}.json".format(language), messages)


def write_root_lang(language: str, messages: Dict[str, str]):
    prefixed = {
        "{}.{}".format(PLUGIN_ID, key): value for key, value in messages.items()
    }
    write_json(ROOT_LANG_DIR / "{}.json".format(language), prefixed)


def load_json(path: Path) -> Dict[str, str]:
    data = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def write_json(path: Path, data: Dict[str, str]):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf8",
    )


def convert_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: convert_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [convert_value(item) for item in value]
    if isinstance(value, str):
        return to_traditional_chinese(value)
    return value


def to_traditional_chinese(text: str) -> str:
    return OPENCC_CONVERTER.convert(text)


if __name__ == "__main__":
    raise SystemExit(main())
