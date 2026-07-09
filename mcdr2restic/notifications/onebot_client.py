# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface


class OneBotClient:
    def __init__(self, server: PluginServerInterface, cfg: Dict[str, Any], websocket_module: Any = None):
        self.server = server
        self.cfg = copy.deepcopy(cfg)
        self.websocket_client = websocket_module
        self.enabled = bool(self.cfg.get('enabled', False))
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.send_lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.ws = None

    def start(self):
        if not self.enabled:
            return
        if self.websocket_client is None:
            self.server.logger.warning(
                'OneBot 通知已启用，但 websocket-client 不可用；'
                '请从 MCDR Python 环境移除错误的 websocket 包，并通过 requirements.txt 安装 websocket-client'
            )
            return
        self.thread = threading.Thread(target=self._thread_main, name='MCDR2Restic-OneBot', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self._close_websocket()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=3)

    def send_private_msg(self, user_id: int, text: str):
        if not self.enabled or self.websocket_client is None:
            return
        threading.Thread(
            target=self._send_private_msg,
            args=(user_id, text),
            name='MCDR2Restic-OneBot-Send',
            daemon=True
        ).start()

    def _close_websocket(self):
        if self.ws is None:
            return
        try:
            self.ws.close()
        except Exception as exc:
            self.server.logger.debug('OneBot WS 关闭异常: {}'.format(exc))

    def _thread_main(self):
        while not self.stop_event.is_set():
            self._connect_once()
            if not self.stop_event.is_set():
                self._sleep(float(self.cfg.get('reconnect_interval_seconds', 5)))

    def _connect_once(self):
        url, headers = self._build_connect_auth()
        app = None
        try:
            self.websocket_client.setdefaulttimeout(float(self.cfg.get('connect_timeout_seconds', 10)))
            app = self.websocket_client.WebSocketApp(
                url,
                header=headers or None,
                on_open=self._build_open_callback(url),
                on_error=self._handle_connection_error,
                on_close=self._handle_connection_closed
            )
            self.ws = app
            app.run_forever(ping_interval=0)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.server.logger.warning('OneBot WS 连接异常: {}'.format(exc))
        finally:
            self.connected_event.clear()
            if self.ws is app:
                self.ws = None

    def _build_open_callback(self, url: str):
        def on_open(ws):
            self.ws = ws
            self.connected_event.set()
            self.server.logger.info('OneBot WS 已连接: {}'.format(self._safe_url(url)))

        return on_open

    def _handle_connection_error(self, _ws, error):
        if not self.stop_event.is_set():
            self.server.logger.warning('OneBot WS 连接异常: {}'.format(error))

    def _handle_connection_closed(self, _ws, _close_status_code, _close_msg):
        self.connected_event.clear()
        self.ws = None

    def _send_private_msg(self, user_id: int, text: str):
        timeout = float(self.cfg.get('send_timeout_seconds', 10))
        if not self.connected_event.wait(timeout=timeout):
            self.server.logger.warning('OneBot 发送 QQ {} 失败: OneBot WS 未连接'.format(user_id))
            return
        ws = self.ws
        if ws is None:
            self.server.logger.warning('OneBot 发送 QQ {} 失败: OneBot WS 未连接'.format(user_id))
            return
        try:
            with self.send_lock:
                ws.send(json.dumps(build_private_message_action(user_id, text), ensure_ascii=False))
        except Exception as exc:
            self.server.logger.warning('OneBot 发送 QQ {} 失败: {}'.format(user_id, exc))

    def _sleep(self, seconds: float):
        end = time.monotonic() + max(0.0, seconds)
        while not self.stop_event.is_set() and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))

    def _build_connect_auth(self) -> Tuple[str, Optional[Dict[str, str]]]:
        url = str(self.cfg.get('ws_url', 'ws://127.0.0.1:8777'))
        token = str(self.cfg.get('access_token', '') or '')
        if not token:
            return url, None
        if bool(self.cfg.get('use_header_auth', False)):
            return url, {'Authorization': 'Bearer {}'.format(token)}
        separator = '&' if '?' in url else '?'
        return '{}{}access_token={}'.format(url, separator, token), None

    @staticmethod
    def _safe_url(url: str) -> str:
        return re.sub(r'([?&]access_token=)[^&]+', r'\1***', url)


def build_private_message_action(user_id: int, text: str) -> Dict[str, Any]:
    return {
        'action': 'send_private_msg',
        'params': {
            'user_id': int(user_id),
            'message': text
        },
        'echo': 'mcdr2restic-{}'.format(int(time.time() * 1000))
    }
