# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from mcdr2restic.core.i18n import DEFAULT_LANGUAGE, tr
from mcdr2restic.core.utils import get_config_section
from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.message_defaults import get_default_message_template


class SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + str(key) + "}"


@dataclass(frozen=True)
class MessageRenderOptions:
    """渲染通知消息时的输出选项。"""

    prefix: Optional[str] = None
    logger: Optional[Any] = None
    language: str = DEFAULT_LANGUAGE


def render_message(
    template_key: str,
    values: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    options: Optional[MessageRenderOptions] = None,
) -> str:
    options = options or MessageRenderOptions()
    cfg = cfg or build_default_config()
    onebot_cfg = get_config_section(cfg, "onebot")
    template = get_message_template(template_key, cfg, options.language)
    data = build_message_values(template_key, values, onebot_cfg, options.prefix)
    try:
        return template.format_map(data)
    except Exception as exc:
        if options.logger is not None:
            options.logger.warning(
                tr(
                    options.language,
                    "warn.notify.message_template_format_failed",
                    template_key=template_key,
                    error=exc,
                )
            )
        return "{} {}".format(data["prefix"], template_key)


def get_message_template(
    template_key: str, cfg: Dict[str, Any], language: str = DEFAULT_LANGUAGE
) -> str:
    messages = get_config_section(cfg, "messages")
    template = messages.get(template_key) if isinstance(messages, dict) else None
    if isinstance(template, str):
        return template
    # messages 缺键时按渲染语言取默认模板，en_us 用户不得收到中文兜底（P3-6）
    return get_default_message_template(template_key, language)


def build_message_values(
    template_key: str,
    values: Optional[Dict[str, Any]],
    onebot_cfg: Dict[str, Any],
    prefix: Optional[str],
) -> SafeFormatDict:
    data = SafeFormatDict()
    data.update(
        {
            # message_prefix 显式置空（YAML null）时 get 返回 None，需回退默认前缀（P3-5）
            "prefix": str(
                prefix
                if prefix is not None
                else onebot_cfg.get("message_prefix") or "[MCDR2Restic]"
            ),
            "plugin": "MCDR2Restic",
            "template_key": template_key,
        }
    )
    if values:
        for key, value in values.items():
            data[str(key)] = "" if value is None else str(value)
    return data
