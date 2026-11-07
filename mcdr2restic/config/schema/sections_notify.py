# -*- coding: utf-8 -*-
"""通知渠道相关的配置段声明。"""

from __future__ import annotations

from mcdr2restic.config.schema.model import Field, Section
from mcdr2restic.defaults.default_constants import DEFAULT_MESSAGE_PREFIX

ONEBOT = Section(
    key="onebot",
    fields=(
        Field("enabled", False),
        Field("ws_url", "ws://127.0.0.1:8777"),
        Field("access_token", ""),
        Field("use_header_auth", False),
        Field("admin_qqs", [123456789]),
        Field("message_prefix", DEFAULT_MESSAGE_PREFIX),
        Field("connect_timeout_seconds", 10),
        Field("send_timeout_seconds", 10),
        Field("reconnect_interval_seconds", 5),
    ),
)

DISCORD = Section(
    key="discord",
    fields=(
        Field("enabled", False),
        Field("webhook_url", ""),
        Field("username", "MCDR2Restic"),
        Field("avatar_url", ""),
        Field("message_prefix", DEFAULT_MESSAGE_PREFIX),
        Field("mention_user_ids", []),
        Field("mention_role_ids", []),
        Field("mention_everyone", False),
        Field("send_timeout_seconds", 10),
    ),
)

CUSTOM_WEBHOOK = Section(
    key="custom_webhook",
    fields=(
        Field("enabled", False),
        Field("url", ""),
        Field("message_prefix", DEFAULT_MESSAGE_PREFIX),
        Field("send_timeout_seconds", 10),
        Field("headers", {}),
        Field("payload", {"content": "{message}"}),
    ),
)

NOTIFICATION = Section(
    key="notification",
    fields=(
        Field("notify_on_start", True),
        Field("notify_on_success", True),
        Field("notify_on_failure", True),
        Field("notify_on_skip", False),
    ),
)
