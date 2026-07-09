# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import os
from typing import Any, Dict, Optional

import yaml
from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.config_paths import get_data_file_path
from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.default_constants import STATE_NAME
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.core.runtime import PluginRuntime


def save_config_unlocked(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface] = None,
):
    target = server or app_runtime.service.server
    if target is None:
        return

    state = {'runtime': copy.deepcopy(app_runtime.config_state.config.get('runtime', build_default_runtime()))}
    app_runtime.config_state.state.clear()
    app_runtime.config_state.state.update(copy.deepcopy(state))
    save_yaml_file(get_data_file_path(target, STATE_NAME), state)


def get_config_snapshot(app_runtime: PluginRuntime) -> Dict[str, Any]:
    with app_runtime.config_state.lock:
        snapshot = copy.deepcopy(app_runtime.config_state.config) if app_runtime.config_state.config else build_default_config()
        ensure_runtime(snapshot, app_runtime.config_state.state)
        return snapshot


def ensure_runtime(
    cfg: Dict[str, Any],
    persisted_state: Optional[Dict[str, Any]] = None,
):
    runtime_state = cfg.setdefault('runtime', {})
    for key, value in build_default_runtime().items():
        runtime_state.setdefault(key, copy.deepcopy(value))

    state_runtime = persisted_runtime_state(persisted_state)
    if state_runtime is not None:
        for key, value in state_runtime.items():
            runtime_state[key] = copy.deepcopy(value)
    runtime_state.pop('max_online_players_in_wait_period', None)


def persisted_runtime_state(persisted_state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    state_runtime = persisted_state.get('runtime') if isinstance(persisted_state, dict) else None
    if isinstance(state_runtime, dict):
        return state_runtime
    return None


def merge_defaults(target: Dict[str, Any], defaults: Dict[str, Any]):
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
            continue
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            merge_defaults(target[key], value)


def load_yaml_mapping(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf8') as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        return {}
    return data


def save_yaml_file(path: str, data: Dict[str, Any]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False, default_flow_style=False)


def load_state_file(server: PluginServerInterface) -> Dict[str, Any]:
    state = load_yaml_mapping(get_data_file_path(server, STATE_NAME))
    runtime_state = state.get('runtime')
    if not isinstance(runtime_state, dict):
        state['runtime'] = build_default_runtime()
        return state

    merge_defaults(runtime_state, build_default_runtime())
    return state
