# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List


@dataclass
class BootstrapResult:
    logs: List[str]
    websocket_client: Any = None


def bootstrap_log(logs: List[str], message: str):
    logs.append(message)
    print('[MCDR2Restic bootstrap] {}'.format(message))


def load_websocket_client(logs: List[str]):
    try:
        import websocket as client
        if hasattr(client, 'WebSocketApp'):
            return client
        bootstrap_log(logs, '检测到 websocket 模块但缺少 WebSocketApp')
        bootstrap_log(logs, '请删除错误的 websocket 包，并让 MCDR 按 requirements.txt 安装 websocket-client')
        return None
    except Exception:  # pragma: no cover - optional runtime dependency
        return None


def ensure_runtime_dependencies() -> BootstrapResult:
    logs: List[str] = []
    websocket_client = load_websocket_client(logs)
    return BootstrapResult(logs=logs, websocket_client=websocket_client)
