# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import threading
import urllib.request
from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr


WEBHOOK_HTTP_SUCCESS_MIN = 200
WEBHOOK_HTTP_SUCCESS_MAX = 300


class WebhookClientBase:
    """Discord 与自定义 webhook 客户端的公共骨架。

    子类提供目标 URL 键、告警语言键与 build_request 实现；基类负责 enabled
    判定、后台线程派发、URL 缺失告警、HTTP 状态检查与发送失败告警。
    """

    thread_name = "MCDR2Restic-Webhook-Send"
    url_config_key = "url"
    url_empty_message_key = "warn.webhook.url_empty"
    status_error_message_key = "warn.webhook.status_error"
    send_failed_message_key = "warn.webhook.send_failed"

    def __init__(self, server: PluginServerInterface, cfg: Dict[str, Any]):
        self.server = server
        self.cfg = copy.deepcopy(cfg)
        self.enabled = bool(self.cfg.get("enabled", False))

    # 统一签名契约：dispatcher 对所有 webhook 通道都传 (text, values)，
    # 子类不得收窄参数，否则绑定实参时抛 TypeError、通知整体失效
    def send_message(
        self, text: str, values: Optional[Dict[str, Any]] = None
    ) -> None:
        if not self.enabled:
            return
        threading.Thread(
            target=self._send_message,
            args=(text, values),
            name=self.thread_name,
            daemon=True,
        ).start()

    def _send_message(
        self, text: str, values: Optional[Dict[str, Any]] = None
    ) -> None:
        webhook_url = str(self.cfg.get(self.url_config_key, "") or "").strip()
        if not webhook_url:
            self.server.logger.warning(
                server_tr(self.server, self.url_empty_message_key)
            )
            return
        try:
            # 构造请求与解析超时同样可能因垃圾配置抛错，必须与 urlopen 一起受保护，
            # 否则 daemon 线程静默死亡、通知无声丢失
            request = self.build_request(webhook_url, text, values)
            timeout = max(1, int(self.cfg.get("send_timeout_seconds", 10)))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", WEBHOOK_HTTP_SUCCESS_MIN)
                if (
                    status < WEBHOOK_HTTP_SUCCESS_MIN
                    or status >= WEBHOOK_HTTP_SUCCESS_MAX
                ):
                    self.server.logger.warning(
                        server_tr(
                            self.server, self.status_error_message_key, status=status
                        )
                    )
        except Exception as exc:
            self.server.logger.warning(
                server_tr(self.server, self.send_failed_message_key, error=exc)
            )

    def build_request(self, webhook_url: str, *args: Any) -> urllib.request.Request:
        raise NotImplementedError
