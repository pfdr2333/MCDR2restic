# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Callable, Dict, NamedTuple, Optional

from mcdr2restic.core.i18n import config_language, server_tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section
from mcdr2restic.notifications.message_templates import (
    MessageRenderOptions,
    render_message,
)


ConfigProvider = Callable[[], Dict[str, Any]]


class ChannelSpec(NamedTuple):
    """通知通道的接入参数：配置段、客户端属性、告警键与投递函数。"""

    section: str
    client_attr: str
    not_started_key: str
    send_failed_key: str
    deliver: Callable[[Any, Dict[str, Any], str, Optional[Dict[str, Any]]], None]


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
            template_key,
            values,
            cfg,
            MessageRenderOptions(logger=self._logger(), language=language),
        )
        self._log(log_text, failure)
        for spec in self._channel_specs():
            self._notify_channel(spec, template_key, values, cfg, language)

    def _channel_specs(self):
        return (
            ChannelSpec(
                section="onebot",
                client_attr="onebot",
                not_started_key="warn.notify.onebot_not_started",
                # 外层兜底拿不到具体 QQ 号，不能用含 {user_id} 的键（P3-4）
                send_failed_key="warn.notify.onebot_deliver_failed",
                deliver=self._deliver_onebot,
            ),
            ChannelSpec(
                section="discord",
                client_attr="discord",
                not_started_key="warn.notify.discord_not_initialized",
                send_failed_key="warn.notify.discord_send_failed",
                deliver=self._deliver_client,
            ),
            ChannelSpec(
                section="custom_webhook",
                client_attr="custom_webhook",
                not_started_key="warn.notify.webhook_not_initialized",
                send_failed_key="warn.notify.webhook_send_failed",
                deliver=self._deliver_client,
            ),
        )

    def _notify_channel(
        self,
        spec: ChannelSpec,
        template_key: str,
        values: Optional[Dict[str, Any]],
        cfg: Dict[str, Any],
        language: str,
    ):
        channel_cfg = get_config_section(cfg, spec.section)
        if not channel_cfg.get("enabled", False):
            return
        client = getattr(self.app_runtime.service, spec.client_attr)
        if client is None:
            self._warn_server(spec.not_started_key, template_key=template_key)
            return
        text = render_message(
            template_key,
            values,
            cfg,
            MessageRenderOptions(
                prefix=str(channel_cfg.get("message_prefix", "[MCDR2Restic]")),
                logger=self._logger(),
                language=language,
            ),
        )
        try:
            spec.deliver(client, channel_cfg, text, values)
        except Exception as exc:
            self._warn_server(spec.send_failed_key, error=exc)

    def _deliver_onebot(
        self,
        client: Any,
        channel_cfg: Dict[str, Any],
        text: str,
        values: Optional[Dict[str, Any]],
    ):
        admin_qqs = channel_cfg.get("admin_qqs", [])
        if not isinstance(admin_qqs, (list, tuple)):
            # 字符串会被逐字符拆分并向错误 QQ 号真实发送，标量则 TypeError
            self._warn_server("warn.notify.onebot_admin_qqs_invalid")
            return
        # 每个QQ的发送失败已单独告警，不中断其余收件人
        for qid in admin_qqs:
            self._send_onebot_message(qid, text)

    def _deliver_client(
        self,
        client: Any,
        channel_cfg: Dict[str, Any],
        text: str,
        values: Optional[Dict[str, Any]],
    ):
        client.send_message(text, values)

    def _send_onebot_message(self, qid: Any, text: str):
        try:
            self.app_runtime.service.onebot.send_private_msg(int(qid), text)
        except Exception as exc:
            self._warn_server(
                "warn.notify.onebot_send_failed", user_id=qid, error=exc
            )

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
        return config_language(
            self.config_provider(), get_mcdr_language(self.app_runtime.service.server)
        )
