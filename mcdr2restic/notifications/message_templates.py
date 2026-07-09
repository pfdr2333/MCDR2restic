# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Optional

from mcdr2restic.core.i18n import tr
from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.message_defaults import get_default_message_template


class SafeFormatDict(dict):
    def __missing__(self, key):
        return '{' + str(key) + '}'


def render_message(
    template_key: str,
    values: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    prefix: Optional[str] = None,
    logger: Optional[Any] = None,
    language: str = 'zh_cn'
) -> str:
    cfg = cfg or build_default_config()
    onebot_cfg = cfg.get('onebot', {})
    template = get_message_template(template_key, cfg)
    data = build_message_values(template_key, values, onebot_cfg, prefix)
    try:
        return template.format_map(data)
    except Exception as exc:
        if logger is not None:
            logger.warning(tr(language, 'warn.notify.message_template_format_failed', template_key=template_key, error=exc))
        return '{} {}'.format(data['prefix'], template_key)


def get_message_template(template_key: str, cfg: Dict[str, Any]) -> str:
    messages = cfg.get('messages', {})
    template = messages.get(template_key) if isinstance(messages, dict) else None
    if isinstance(template, str):
        return template
    return get_default_message_template(template_key)


def build_message_values(
    template_key: str,
    values: Optional[Dict[str, Any]],
    onebot_cfg: Dict[str, Any],
    prefix: Optional[str]
) -> SafeFormatDict:
    data = SafeFormatDict()
    data.update({
        'prefix': str(prefix if prefix is not None else onebot_cfg.get('message_prefix', '[MCDR2Restic]')),
        'plugin': 'MCDR2Restic',
        'template_key': template_key
    })
    if values:
        for key, value in values.items():
            data[str(key)] = '' if value is None else str(value)
    return data
