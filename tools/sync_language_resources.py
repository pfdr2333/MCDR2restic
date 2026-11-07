# -*- coding: utf-8 -*-
"""Synchronize runtime language files and config-text resources.

语言资源的权威来源是 ``mcdr2restic/lang/*.json``（运行时文案）与
``mcdr2restic/config_texts/*.json``（配置模板文本）；本工具负责：
1. 把包内语言文件镜像到根目录 ``lang/``（加插件前缀，供 MCDR 加载），
   并保证语言资源中不混入模板文本键；
2. 由 zh_cn 经 OpenCC 生成 zh_tw。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from opencc import OpenCC

PLUGIN_ID = "mcdr2restic"
PACKAGE_LANG_DIR = Path("mcdr2restic/lang")
ROOT_LANG_DIR = Path("lang")
PACKAGE_CONFIG_TEXTS_DIR = Path("mcdr2restic/config_texts")
BASE_LANGUAGES = ("zh_cn", "en_us")
GENERATED_LANGUAGE = "zh_tw"
TEMPLATE_TEXT_KEY_PREFIX = "template."

OPENCC_CONVERTER = OpenCC("s2twp")


def main() -> int:
    ensure_directories()
    for language in BASE_LANGUAGES:
        sync_base_language(language)
    sync_traditional_chinese()
    return 0


def ensure_directories():
    for directory in [PACKAGE_LANG_DIR, ROOT_LANG_DIR, PACKAGE_CONFIG_TEXTS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


def sync_base_language(language: str):
    package_lang = load_json(PACKAGE_LANG_DIR / "{}.json".format(language))
    runtime_lang = remove_template_texts(package_lang)
    write_package_lang(language, runtime_lang)
    write_root_lang(language, runtime_lang)


def sync_traditional_chinese():
    zh_lang = load_json(PACKAGE_LANG_DIR / "zh_cn.json")
    zh_texts = load_json(PACKAGE_CONFIG_TEXTS_DIR / "zh_cn.json")
    runtime_lang = convert_value(zh_lang)
    write_package_lang(GENERATED_LANGUAGE, runtime_lang)
    write_root_lang(GENERATED_LANGUAGE, runtime_lang)
    write_config_texts(GENERATED_LANGUAGE, convert_value(zh_texts))


def remove_template_texts(messages: Dict[str, str]) -> Dict[str, str]:
    """模板文本只允许存在于 config_texts，避免污染运行时与 MCDR 语言资源。"""

    return {key: value for key, value in messages.items() if not key.startswith(TEMPLATE_TEXT_KEY_PREFIX)}


def write_package_lang(language: str, messages: Dict[str, str]):
    write_json(PACKAGE_LANG_DIR / "{}.json".format(language), messages)


def write_root_lang(language: str, messages: Dict[str, str]):
    prefixed = {"{}.{}".format(PLUGIN_ID, key): value for key, value in messages.items()}
    write_json(ROOT_LANG_DIR / "{}.json".format(language), prefixed)


def write_config_texts(language: str, texts: Dict[str, str]):
    write_json(PACKAGE_CONFIG_TEXTS_DIR / "{}.json".format(language), texts)


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
        return OPENCC_CONVERTER.convert(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
