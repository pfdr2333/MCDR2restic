# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import threading
import urllib.request
from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.defaults.default_constants import PLUGIN_ID
from mcdr2restic.notifications.message_templates import SafeFormatDict


WEBHOOK_HTTP_SUCCESS_MIN = 200
WEBHOOK_HTTP_SUCCESS_MAX = 300
DEFAULT_WEBHOOK_PAYLOAD: Dict[str, Any] = {"content": "{message}"}


class CustomWebhookClient:
    """POST notification text as JSON to a user-defined webhook endpoint."""

    def __init__(self, server: PluginServerInterface, cfg: Dict[str, Any]):
        self.server = server
        self.cfg = copy.deepcopy(cfg)
        self.enabled = bool(self.cfg.get("enabled", False))

    def send_message(
        self, text: str, values: Optional[Dict[str, Any]] = None
    ):
        if not self.enabled:
            return
        threading.Thread(
            target=self._send_message,
            args=(text, values),
            name="MCDR2Restic-Webhook-Send",
            daemon=True,
        ).start()

    def _send_message(self, text: str, values: Optional[Dict[str, Any]]):
        webhook_url = str(self.cfg.get("url", "") or "").strip()
        if not webhook_url:
            self.server.logger.warning(
                server_tr(self.server, "warn.webhook.url_empty")
            )
            return
        payload = self._build_payload(text, values)
        request = build_webhook_request(webhook_url, payload, self.cfg)
        timeout = max(1, int(self.cfg.get("send_timeout_seconds", 10)))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if (
                    status < WEBHOOK_HTTP_SUCCESS_MIN
                    or status >= WEBHOOK_HTTP_SUCCESS_MAX
                ):
                    self.server.logger.warning(
                        server_tr(self.server, "warn.webhook.status_error", status=status)
                    )
        except Exception as exc:
            self.server.logger.warning(
                server_tr(self.server, "warn.webhook.send_failed", error=exc)
            )

    def _build_payload(
        self, text: str, values: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        data = SafeFormatDict()
        data["message"] = str(text or "")
        data["plugin"] = "MCDR2Restic"
        for key, value in (values or {}).items():
            data[str(key)] = "" if value is None else str(value)
        return render_payload_leaves(self._payload_template(), data)

    def _payload_template(self) -> Dict[str, Any]:
        payload = self.cfg.get("payload")
        if not isinstance(payload, dict):
            return copy.deepcopy(DEFAULT_WEBHOOK_PAYLOAD)
        return payload


def render_payload_leaves(payload: Any, data: Dict[str, Any]) -> Any:
    if not isinstance(data, SafeFormatDict):
        safe_data = SafeFormatDict()
        safe_data.update(data)
        data = safe_data
    if isinstance(payload, dict):
        return {
            key: render_payload_leaves(value, data)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [render_payload_leaves(value, data) for value in payload]
    if isinstance(payload, str):
        try:
            return payload.format_map(data)
        except Exception:
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
