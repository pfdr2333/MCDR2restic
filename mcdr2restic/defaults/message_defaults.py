# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict

from mcdr2restic.core.i18n import tr


DEFAULT_MESSAGE_KEYS = (
    'backup_start',
    'backup_success',
    'backup_failure',
    'backup_skip_no_player',
    'backup_not_ready',
    'schedule_config_error',
)


def build_default_messages(language: str = 'zh_cn') -> Dict[str, str]:
    return {
        key: tr(language, 'template.message.{}'.format(key))
        for key in DEFAULT_MESSAGE_KEYS
    }


def get_default_message_template(template_key: str, language: str = 'zh_cn') -> str:
    return build_default_messages(language).get(template_key, template_key)
