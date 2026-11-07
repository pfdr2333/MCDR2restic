# -*- coding: utf-8 -*-
"""通知渠道相关的配置段声明。"""

from __future__ import annotations

from mcdr2restic.config.schema.model import Field, Section
from mcdr2restic.defaults.default_constants import DEFAULT_MESSAGE_PREFIX

ONEBOT = Section(
    key="onebot",
    fields=(
        Field("enabled", False, comment_key="template.comment.onebot.enabled"),
        Field("ws_url", "ws://127.0.0.1:8777", comment_key="template.comment.onebot.ws_url"),
        Field("access_token", "", comment_key="template.comment.onebot.access_token"),
        Field(
            "use_header_auth",
            False,
            comment_key="template.comment.onebot.use_header_auth",
        ),
        Field(
            "admin_qqs",
            [123456789],
            comment_key="template.comment.onebot.admin_qqs",
        ),
        Field(
            "message_prefix",
            DEFAULT_MESSAGE_PREFIX,
            comment_key="template.comment.onebot.message_prefix",
        ),
        Field(
            "connect_timeout_seconds",
            10,
            comment_key="template.comment.onebot.connect_timeout_seconds",
        ),
        Field("send_timeout_seconds", 10),
        Field("reconnect_interval_seconds", 5),
    ),
)

DISCORD = Section(
    key="discord",
    fields=(
        Field("enabled", False, comment_key="template.comment.discord.enabled"),
        Field(
            "webhook_url",
            "",
            comment_key="template.comment.discord.webhook_url",
        ),
        Field(
            "username",
            "MCDR2Restic",
            comment_key="template.comment.discord.username",
        ),
        Field("avatar_url", ""),
        Field(
            "message_prefix",
            DEFAULT_MESSAGE_PREFIX,
            comment_key="template.comment.discord.message_prefix",
        ),
        Field(
            "mention_user_ids",
            [],
            comment_key="template.comment.discord.mention_user_ids",
        ),
        Field("mention_role_ids", []),
        Field("mention_everyone", False),
        Field("send_timeout_seconds", 10),
    ),
)

CUSTOM_WEBHOOK = Section(
    key="custom_webhook",
    fields=(
        Field(
            "enabled",
            False,
            comment_key="template.comment.custom_webhook.enabled",
        ),
        Field("url", "", comment_key="template.comment.custom_webhook.url"),
        Field(
            "message_prefix",
            DEFAULT_MESSAGE_PREFIX,
            comment_key="template.comment.custom_webhook.message_prefix",
        ),
        Field(
            "send_timeout_seconds",
            10,
            comment_key="template.comment.custom_webhook.send_timeout_seconds",
        ),
        Field("headers", {}, comment_key="template.comment.custom_webhook.headers"),
        Field(
            "payload",
            {"content": "{message}"},
            comment_key="template.comment.custom_webhook.payload",
        ),
    ),
)

NOTIFICATION = Section(
    key="notification",
    intro_comment_key="template.comment.notification",
    fields=(
        Field(
            "notify_on_start",
            True,
            comment_key="template.comment.notification.notify_on_start",
            comment_inline=True,
        ),
        Field(
            "notify_on_success",
            True,
            comment_key="template.comment.notification.notify_on_success",
            comment_inline=True,
        ),
        Field(
            "notify_on_failure",
            True,
            comment_key="template.comment.notification.notify_on_failure",
            comment_inline=True,
        ),
        Field(
            "notify_on_skip",
            False,
            comment_key="template.comment.notification.notify_on_skip",
            comment_inline=True,
        ),
    ),
)
