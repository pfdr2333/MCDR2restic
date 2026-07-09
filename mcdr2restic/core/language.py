# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional

from mcdreforged.api.all import PluginServerInterface


def is_zh_language(language: str) -> bool:
    normalized = str(language or '').lower().replace('-', '_')
    return normalized.startswith('zh')


def get_mcdr_language(server: Optional[PluginServerInterface]) -> str:
    if server is None:
        return 'zh_cn'
    try:
        return str(server.get_mcdr_language())
    except Exception:
        return 'zh_cn'
