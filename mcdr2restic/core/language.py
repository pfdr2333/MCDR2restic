# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Optional

from mcdreforged.api.all import CommandSource, PluginServerInterface


def is_zh_language(language: str) -> bool:
    normalized = str(language or "").lower().replace("-", "_")
    return normalized.startswith("zh")


def get_mcdr_language(server: Optional[PluginServerInterface]) -> str:
    if server is None:
        return "zh_cn"
    try:
        return str(server.get_mcdr_language())
    except Exception:
        return "zh_cn"


def get_source_language(
    source: Optional[CommandSource], server: Optional[PluginServerInterface] = None
) -> str:
    if source is not None:
        language = preferred_language_from_source(source)
        if language:
            return language
        server = server or safe_source_server(source)
    return get_mcdr_language(server)


def preferred_language_from_source(source: CommandSource) -> str:
    try:
        preference = source.get_preference()
    except Exception:
        return ""
    language = getattr(preference, "language", "")
    return str(language or "").strip()


def safe_source_server(source: CommandSource) -> Optional[PluginServerInterface]:
    try:
        return source.get_server()
    except Exception:
        return None
