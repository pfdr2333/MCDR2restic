# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from mcdr2restic.core.i18n import DEFAULT_LANGUAGE, tr


@dataclass(frozen=True)
class BootstrapLogEntry:
    i18n_key: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BootstrapResult:
    logs: List[BootstrapLogEntry]
    websocket_client: Any = None


def bootstrap_log(logs: List[BootstrapLogEntry], i18n_key: str, **params: Any):
    entry = BootstrapLogEntry(i18n_key, dict(params))
    logs.append(entry)
    print('[MCDR2Restic bootstrap] {}'.format(tr(DEFAULT_LANGUAGE, i18n_key, **params)))


def load_websocket_client(logs: List[BootstrapLogEntry]):
    try:
        import websocket as client
        if hasattr(client, 'WebSocketApp'):
            return client
        bootstrap_log(logs, 'warn.bootstrap.websocket_missing_websocket_app')
        bootstrap_log(logs, 'warn.bootstrap.websocket_wrong_package')
        return None
    except Exception:  # pragma: no cover - optional runtime dependency
        return None


def ensure_runtime_dependencies() -> BootstrapResult:
    logs: List[BootstrapLogEntry] = []
    websocket_client = load_websocket_client(logs)
    return BootstrapResult(logs=logs, websocket_client=websocket_client)
