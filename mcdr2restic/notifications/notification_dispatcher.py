# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.notifications.message_templates import render_message


ConfigProvider = Callable[[], Dict[str, Any]]


class NotificationDispatcher:
    def __init__(self, app_runtime: PluginRuntime, config_provider: ConfigProvider):
        self.app_runtime = app_runtime
        self.config_provider = config_provider

    def notify_admins(
        self,
        template_key: str,
        values: Optional[Dict[str, Any]] = None,
        cfg: Optional[Dict[str, Any]] = None,
        failure: bool = False
    ):
        cfg = cfg or self.config_provider()
        log_text = render_message(template_key, values, cfg, logger=self._logger())
        self._log(log_text, failure)
        self._notify_onebot(template_key, values, cfg)
        self._notify_discord(template_key, values, cfg)

    def _logger(self):
        server = self.app_runtime.service.server
        return server.logger if server is not None else None

    def _log(self, text: str, failure: bool):
        logger = self._logger()
        if logger is None:
            return
        if failure:
            logger.warning(text)
            return
        logger.info(text)

    def _notify_onebot(self, template_key: str, values: Optional[Dict[str, Any]], cfg: Dict[str, Any]):
        onebot_cfg = cfg.get('onebot', {})
        if not onebot_cfg.get('enabled', False):
            return
        if self.app_runtime.service.onebot is None:
            self._warn('OneBot 未启动，无法发送通知: {}'.format(template_key))
            return
        text = render_message(template_key, values, cfg, str(onebot_cfg.get('message_prefix', '[MCDR2Restic]')), self._logger())
        for qid in onebot_cfg.get('admin_qqs', []):
            self._send_onebot_message(qid, text)

    def _send_onebot_message(self, qid: Any, text: str):
        try:
            self.app_runtime.service.onebot.send_private_msg(int(qid), text)
        except Exception as exc:
            self._warn('发送 OneBot 通知到 QQ {} 失败: {}'.format(qid, exc))

    def _notify_discord(self, template_key: str, values: Optional[Dict[str, Any]], cfg: Dict[str, Any]):
        discord_cfg = cfg.get('discord', {})
        if not discord_cfg.get('enabled', False):
            return
        if self.app_runtime.service.discord is None:
            self._warn('Discord 未初始化，无法发送通知: {}'.format(template_key))
            return
        text = render_message(template_key, values, cfg, str(discord_cfg.get('message_prefix', '[MCDR2Restic]')), self._logger())
        try:
            self.app_runtime.service.discord.send_message(text)
        except Exception as exc:
            self._warn('发送 Discord 通知失败: {}'.format(exc))

    def _warn(self, text: str):
        logger = self._logger()
        if logger is not None:
            logger.warning(text)
