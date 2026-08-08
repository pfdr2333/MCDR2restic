# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict

from mcdr2restic.defaults.config_template_resources import config_template_text


DEFAULT_MESSAGE_KEYS = (
    "backup_start",
    "backup_success",
    "backup_failure",
    "backup_skip_no_player",
    "backup_not_ready",
    "schedule_config_error",
)


def build_default_messages(language: str = "zh_cn") -> Dict[str, str]:
    return {
        key: config_template_text(language, "template.message.{}".format(key))
        for key in DEFAULT_MESSAGE_KEYS
    }


DEFAULT_MESSAGES_ZH = build_default_messages("zh_cn")
DEFAULT_MESSAGES_EN = build_default_messages("en_us")


def get_default_message_template(template_key: str, language: str = "zh_cn") -> str:
    return build_default_messages(language).get(template_key, template_key)
