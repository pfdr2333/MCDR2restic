# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict

from mcdr2restic.defaults.config_template_resources import config_template_text


DEFAULT_MESSAGE_KEYS = (
    "backup_start",
    "backup_success",
    "backup_failure",
    "backup_skip_no_player",
    "backup_skipped",
    "backup_not_ready",
    "backup_stale_warning",
    "maintenance_failed",
    "maintenance_skipped",
    "schedule_config_error",
    "staging_hint",
    "staging_remote_stale",
)


def build_default_messages(language: str = "zh_cn") -> Dict[str, str]:
    return {
        key: config_template_text(language, "template.message.{}".format(key))
        for key in DEFAULT_MESSAGE_KEYS
    }


def get_default_message_template(template_key: str, language: str = "zh_cn") -> str:
    return build_default_messages(language).get(template_key, template_key)
