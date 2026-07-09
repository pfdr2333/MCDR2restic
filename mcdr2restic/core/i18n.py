# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from contextlib import nullcontext
from functools import lru_cache
from importlib import resources
from string import Formatter
from typing import Any, Callable, Dict

from mcdreforged.api.all import CommandSource, PluginServerInterface

from mcdr2restic.core.language import get_mcdr_language, get_source_language
from mcdr2restic.defaults.default_constants import PLUGIN_ID


DEFAULT_LANGUAGE = 'zh_cn'
FALLBACK_LANGUAGE = 'en_us'
SUPPORTED_LANGUAGES = frozenset({DEFAULT_LANGUAGE, FALLBACK_LANGUAGE})
LANG_PACKAGE = 'mcdr2restic.lang'
TRANSLATION_KEY_PREFIX = '{}.'.format(PLUGIN_ID)
TranslateFunc = Callable[..., str]


def normalize_language(language: str) -> str:
    text = str(language or '').lower().replace('-', '_')
    if text in SUPPORTED_LANGUAGES:
        return text
    if text.startswith('zh'):
        return DEFAULT_LANGUAGE
    return FALLBACK_LANGUAGE


def tr(language: str, key: str, **params: Any) -> str:
    template = translation_template(normalize_language(language), key)
    return format_translation(template, params)


def server_tr(server: PluginServerInterface, key: str, **params: Any) -> str:
    translate = getattr(server, 'tr', None)
    if callable(translate):
        try:
            return str(translate(plugin_translation_key(key), **params))
        except Exception:
            pass
    return tr(get_mcdr_language(server), key, **params)


def server_rtr(server: PluginServerInterface, key: str, **params: Any) -> Any:
    translate = getattr(server, 'rtr', None)
    if callable(translate):
        try:
            return translate(plugin_translation_key(key), **params)
        except Exception:
            pass
    return tr(get_mcdr_language(server), key, **params)


def reply_tr(source: CommandSource, server: PluginServerInterface, key: str, **params: Any):
    source.reply(server_rtr(server, key, **params))


def source_tr(source: CommandSource, server: PluginServerInterface, key: str, **params: Any) -> str:
    translate = getattr(server, 'rtr', None)
    if callable(translate):
        try:
            text = translate(plugin_translation_key(key), **params)
            return render_text_for_source(source, text)
        except Exception:
            pass
    return tr(get_source_language(source, server), key, **params)


def make_source_translate(source: CommandSource, server: PluginServerInterface) -> TranslateFunc:
    return lambda key, **params: source_tr(source, server, key, **params)


def normalize_translate(translate_or_language: Any) -> TranslateFunc:
    if callable(translate_or_language):
        return translate_or_language
    language = normalize_language(str(translate_or_language or DEFAULT_LANGUAGE))
    return lambda key, **params: tr(language, key, **params)


def render_text_for_source(source: CommandSource, text: Any) -> str:
    context = preferred_language_context(source)
    try:
        with context:
            return text_to_plain_text(text)
    except Exception:
        return text_to_plain_text(text)


def preferred_language_context(source: CommandSource):
    context_factory = getattr(source, 'preferred_language_context', None)
    if callable(context_factory):
        try:
            return context_factory()
        except Exception:
            return nullcontext()
    return nullcontext()


def text_to_plain_text(text: Any) -> str:
    to_plain_text = getattr(text, 'to_plain_text', None)
    if callable(to_plain_text):
        try:
            return str(to_plain_text())
        except Exception:
            pass
    return str(text)


def source_error_text(source: CommandSource, server: PluginServerInterface, error: Exception) -> str:
    return tr_error(get_source_language(source, server), error)


def tr_error(language: str, error: Exception) -> str:
    key = str(getattr(error, 'i18n_key', '') or '')
    params = getattr(error, 'i18n_params', {})
    if key:
        return tr(language, key, **params)
    return str(error)


def translation_template(language: str, key: str) -> str:
    normalized_key = unprefixed_translation_key(key)
    messages = load_language_messages(language)
    if normalized_key in messages:
        return messages[normalized_key]
    fallback = load_language_messages(FALLBACK_LANGUAGE)
    return fallback.get(normalized_key, normalized_key)


@lru_cache(maxsize=None)
def load_language_messages(language: str) -> Dict[str, str]:
    resource_name = '{}.json'.format(normalize_language(language))
    try:
        with resources.open_text(LANG_PACKAGE, resource_name, encoding='utf8') as file:
            data = json.load(file)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def plugin_translation_key(key: str) -> str:
    text = str(key or '').strip()
    if text.startswith(TRANSLATION_KEY_PREFIX):
        return text
    return '{}{}'.format(TRANSLATION_KEY_PREFIX, text)


def unprefixed_translation_key(key: str) -> str:
    text = str(key or '').strip()
    if text.startswith(TRANSLATION_KEY_PREFIX):
        return text[len(TRANSLATION_KEY_PREFIX):]
    return text


def format_translation(template: str, params: Dict[str, Any]) -> str:
    try:
        needed = formatter_field_names(template)
        values = {name: params.get(name, '{' + name + '}') for name in needed}
        return template.format(**values)
    except Exception:
        return template


def formatter_field_names(template: str) -> set:
    names = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            names.add(field_name.split('.', 1)[0].split('[', 1)[0])
    return names
