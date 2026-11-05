# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List

from mcdr2restic.defaults.default_constants import PLUGIN_ID
from mcdr2restic.notifications.webhook_base import WebhookClientBase


DISCORD_CONTENT_MAX_CHARS = 2000


class DiscordWebhookClient(WebhookClientBase):
    thread_name = "MCDR2Restic-Discord-Send"
    url_config_key = "webhook_url"
    url_empty_message_key = "warn.discord.webhook_url_empty"
    status_error_message_key = "warn.discord.status_error"
    send_failed_message_key = "warn.discord.send_failed"

    def send_message(self, text: str) -> None:
        super().send_message(text)

    def build_request(
        self, webhook_url: str, text: str
    ) -> urllib.request.Request:
        return build_discord_request(webhook_url, self._build_payload(text))

    def _build_payload(self, text: str) -> Dict[str, Any]:
        content = self._with_mentions(text)
        payload: Dict[str, Any] = {
            "content": truncate_discord_content(content),
            "allowed_mentions": self._allowed_mentions(),
        }
        username = str(self.cfg.get("username", "") or "").strip()
        avatar_url = str(self.cfg.get("avatar_url", "") or "").strip()
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url
        return payload

    def _with_mentions(self, text: str) -> str:
        mentions = build_discord_mentions(self.cfg)
        if not mentions:
            return text
        return "{}\n{}".format(" ".join(mentions), text)

    def _allowed_mentions(self) -> Dict[str, Any]:
        parse: List[str] = []
        if bool(self.cfg.get("mention_everyone", False)):
            parse.append("everyone")
        users = clean_id_list(self.cfg.get("mention_user_ids", []))
        roles = clean_id_list(self.cfg.get("mention_role_ids", []))
        return {
            "parse": parse,
            "users": users[:100],
            "roles": roles[:100],
            "replied_user": False,
        }


def build_discord_request(
    webhook_url: str, payload: Dict[str, Any]
) -> urllib.request.Request:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "User-Agent": "MCDR2Restic/{}".format(PLUGIN_ID),
            "Content-Type": "application/json",
        },
        method="POST",
    )


def build_discord_mentions(cfg: Dict[str, Any]) -> List[str]:
    mentions: List[str] = []
    if bool(cfg.get("mention_everyone", False)):
        mentions.append("@everyone")
    mentions.extend(
        "<@&{}>".format(value)
        for value in clean_id_list(cfg.get("mention_role_ids", []))
    )
    mentions.extend(
        "<@{}>".format(value)
        for value in clean_id_list(cfg.get("mention_user_ids", []))
    )
    return mentions


def clean_id_list(values: Any) -> List[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def truncate_discord_content(text: str) -> str:
    text = str(text or "")
    if len(text) <= DISCORD_CONTENT_MAX_CHARS:
        return text
    return "{}\n...".format(text[:1996])
