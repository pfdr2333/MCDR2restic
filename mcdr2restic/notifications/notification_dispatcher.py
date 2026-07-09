# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.core.language import get_mcdr_language
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
        failure: bool = False,
    ):
        cfg = cfg or self.config_provider()
        language = self._language()
        log_text = render_message(
            template_key, values, cfg, logger=self._logger(), language=language
        )
        self._log(log_text, failure)
        self._notify_onebot(template_key, values, cfg, language)
        self._notify_discord(template_key, values, cfg, language)

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

    def _notify_onebot(
        self,
        template_key: str,
        values: Optional[Dict[str, Any]],
        cfg: Dict[str, Any],
        language: str,
    ):
        onebot_cfg = cfg.get("onebot", {})
        if not onebot_cfg.get("enabled", False):
            return
        if self.app_runtime.service.onebot is None:
            self._warn_server(
                "warn.notify.onebot_not_started", template_key=template_key
            )
            return
        text = render_message(
            template_key,
            values,
            cfg,
            str(onebot_cfg.get("message_prefix", "[MCDR2Restic]")),
            self._logger(),
            language,
        )
        for qid in onebot_cfg.get("admin_qqs", []):
            self._send_onebot_message(qid, text)

    def _send_onebot_message(self, qid: Any, text: str):
        try:
            self.app_runtime.service.onebot.send_private_msg(int(qid), text)
        except Exception as exc:
            self._warn_server("warn.notify.onebot_send_failed", qid=qid, error=exc)

    def _notify_discord(
        self,
        template_key: str,
        values: Optional[Dict[str, Any]],
        cfg: Dict[str, Any],
        language: str,
    ):
        discord_cfg = cfg.get("discord", {})
        if not discord_cfg.get("enabled", False):
            return
        if self.app_runtime.service.discord is None:
            self._warn_server(
                "warn.notify.discord_not_initialized", template_key=template_key
            )
            return
        text = render_message(
            template_key,
            values,
            cfg,
            str(discord_cfg.get("message_prefix", "[MCDR2Restic]")),
            self._logger(),
            language,
        )
        try:
            self.app_runtime.service.discord.send_message(text)
        except Exception as exc:
            self._warn_server("warn.notify.discord_send_failed", error=exc)

    def _warn(self, text: str):
        logger = self._logger()
        if logger is not None:
            logger.warning(text)

    def _warn_server(self, key: str, **params: Any):
        server = self.app_runtime.service.server
        if server is None:
            return
        self._warn(server_tr(server, key, **params))

    def _language(self) -> str:
        return get_mcdr_language(self.app_runtime.service.server)
