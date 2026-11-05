# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import urllib.request
from typing import Any, Callable, Dict, Optional

from mcdr2restic.defaults.default_constants import PLUGIN_ID
from mcdr2restic.core.i18n import server_tr
from mcdr2restic.notifications.message_templates import SafeFormatDict
from mcdr2restic.notifications.webhook_base import WebhookClientBase


DEFAULT_WEBHOOK_PAYLOAD: Dict[str, Any] = {"content": "{message}"}


class CustomWebhookClient(WebhookClientBase):
    """POST notification text as JSON to a user-defined webhook endpoint."""

    def send_message(
        self, text: str, values: Optional[Dict[str, Any]] = None
    ) -> None:
        super().send_message(text, values)

    def build_request(
        self, webhook_url: str, text: str, values: Optional[Dict[str, Any]] = None
    ) -> urllib.request.Request:
        payload = self._build_payload(text, values)
        return build_webhook_request(webhook_url, payload, self.cfg)

    def _build_payload(
        self, text: str, values: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        data = SafeFormatDict()
        data["message"] = str(text or "")
        data["plugin"] = "MCDR2Restic"
        for key, value in (values or {}).items():
            data[str(key)] = "" if value is None else str(value)
        return render_payload_leaves(
            self._payload_template(), data, self._log_template_render_error
        )

    def _log_template_render_error(self, template: str, exc: Exception):
        self.server.logger.warning(
            server_tr(
                self.server, "warn.webhook.template_render_failed", error=exc
            )
        )

    def _payload_template(self) -> Dict[str, Any]:
        payload = self.cfg.get("payload")
        if not isinstance(payload, dict):
            return copy.deepcopy(DEFAULT_WEBHOOK_PAYLOAD)
        return payload


def render_payload_leaves(
    payload: Any, data: Dict[str, Any], on_render_error: Optional[Callable[[str, Exception], None]] = None
) -> Any:
    if not isinstance(data, SafeFormatDict):
        safe_data = SafeFormatDict()
        safe_data.update(data)
        data = safe_data
    if isinstance(payload, dict):
        return {
            key: render_payload_leaves(value, data, on_render_error)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [render_payload_leaves(value, data, on_render_error) for value in payload]
    if isinstance(payload, str):
        try:
            return payload.format_map(data)
        except Exception as exc:
            # 回退为原样发送，但必须留下告警避免静默错误
            if on_render_error is not None:
                on_render_error(payload, exc)
            return payload
    return payload


def build_webhook_request(
    webhook_url: str, payload: Any, cfg: Dict[str, Any]
) -> urllib.request.Request:
    data = json.dumps(payload, ensure_ascii=False).encode("utf8")
    headers = {"User-Agent": "MCDR2Restic/{}".format(PLUGIN_ID)}
    for key, value in (cfg.get("headers") or {}).items():
        if str(key).strip() and str(value).strip():
            headers[str(key).strip()] = str(value).strip()
    headers.setdefault("Content-Type", "application/json")
    return urllib.request.Request(webhook_url, data=data, headers=headers, method="POST")
