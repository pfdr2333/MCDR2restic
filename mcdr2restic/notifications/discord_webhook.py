# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import threading
import urllib.request
from typing import Any, Dict, List

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.defaults.default_constants import PLUGIN_ID


class DiscordWebhookClient:
    def __init__(self, server: PluginServerInterface, cfg: Dict[str, Any]):
        self.server = server
        self.cfg = copy.deepcopy(cfg)
        self.enabled = bool(self.cfg.get('enabled', False))

    def send_message(self, text: str):
        if not self.enabled:
            return
        threading.Thread(
            target=self._send_message,
            args=(text,),
            name='MCDR2Restic-Discord-Send',
            daemon=True
        ).start()

    def _send_message(self, text: str):
        webhook_url = str(self.cfg.get('webhook_url', '') or '').strip()
        if not webhook_url:
            self.server.logger.warning(server_tr(self.server, 'warn.discord.webhook_url_empty'))
            return
        request = build_discord_request(webhook_url, self._build_payload(text))
        timeout = max(1, int(self.cfg.get('send_timeout_seconds', 10)))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, 'status', 204)
                if status < 200 or status >= 300:
                    self.server.logger.warning(server_tr(self.server, 'warn.discord.status_error', status=status))
        except Exception as exc:
            self.server.logger.warning(server_tr(self.server, 'warn.discord.send_failed', error=exc))

    def _build_payload(self, text: str) -> Dict[str, Any]:
        content = self._with_mentions(text)
        payload: Dict[str, Any] = {
            'content': truncate_discord_content(content),
            'allowed_mentions': self._allowed_mentions()
        }
        username = str(self.cfg.get('username', '') or '').strip()
        avatar_url = str(self.cfg.get('avatar_url', '') or '').strip()
        if username:
            payload['username'] = username
        if avatar_url:
            payload['avatar_url'] = avatar_url
        return payload

    def _with_mentions(self, text: str) -> str:
        mentions = build_discord_mentions(self.cfg)
        if not mentions:
            return text
        return '{}\n{}'.format(' '.join(mentions), text)

    def _allowed_mentions(self) -> Dict[str, Any]:
        parse: List[str] = []
        if bool(self.cfg.get('mention_everyone', False)):
            parse.append('everyone')
        users = clean_id_list(self.cfg.get('mention_user_ids', []))
        roles = clean_id_list(self.cfg.get('mention_role_ids', []))
        return {
            'parse': parse,
            'users': users[:100],
            'roles': roles[:100],
            'replied_user': False
        }


def build_discord_request(webhook_url: str, payload: Dict[str, Any]) -> urllib.request.Request:
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    return urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            'User-Agent': 'MCDR2Restic/{}'.format(PLUGIN_ID),
            'Content-Type': 'application/json'
        },
        method='POST'
    )


def build_discord_mentions(cfg: Dict[str, Any]) -> List[str]:
    mentions: List[str] = []
    if bool(cfg.get('mention_everyone', False)):
        mentions.append('@everyone')
    mentions.extend('<@&{}>'.format(value) for value in clean_id_list(cfg.get('mention_role_ids', [])))
    mentions.extend('<@{}>'.format(value) for value in clean_id_list(cfg.get('mention_user_ids', [])))
    return mentions


def clean_id_list(values: Any) -> List[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def truncate_discord_content(text: str) -> str:
    text = str(text or '')
    if len(text) <= 2000:
        return text
    return '{}\n...'.format(text[:1996])
