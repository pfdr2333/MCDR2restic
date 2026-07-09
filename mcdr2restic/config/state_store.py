# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import yaml
from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.config_paths import get_data_file_path
from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.default_constants import STATE_NAME
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.core.runtime import PluginRuntime


BLOCK_SCALAR_HEADER_PATTERN = re.compile(
    r"^(?P<indent>[ ]*)(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*\|-\s*$"
)


@dataclass(frozen=True)
class YamlMappingLoadResult:
    mapping: Dict[str, Any]
    repaired_text: Optional[str] = None


def save_config_unlocked(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface] = None,
):
    target = server or app_runtime.service.server
    if target is None:
        return

    state = {
        "runtime": copy.deepcopy(
            app_runtime.config_state.config.get("runtime", build_default_runtime())
        )
    }
    app_runtime.config_state.state.clear()
    app_runtime.config_state.state.update(copy.deepcopy(state))
    save_yaml_file(get_data_file_path(target, STATE_NAME), state)


def get_config_snapshot(app_runtime: PluginRuntime) -> Dict[str, Any]:
    with app_runtime.config_state.lock:
        snapshot = (
            copy.deepcopy(app_runtime.config_state.config)
            if app_runtime.config_state.config
            else build_default_config()
        )
        ensure_runtime(snapshot, app_runtime.config_state.state)
        return snapshot


def ensure_runtime(
    cfg: Dict[str, Any],
    persisted_state: Optional[Dict[str, Any]] = None,
):
    runtime_state = cfg.setdefault("runtime", {})
    for key, value in build_default_runtime().items():
        runtime_state.setdefault(key, copy.deepcopy(value))

    state_runtime = persisted_runtime_state(persisted_state)
    if state_runtime is not None:
        for key, value in state_runtime.items():
            runtime_state[key] = copy.deepcopy(value)
    runtime_state.pop("max_online_players_in_wait_period", None)


def persisted_runtime_state(
    persisted_state: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    state_runtime = (
        persisted_state.get("runtime") if isinstance(persisted_state, dict) else None
    )
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
    return load_yaml_mapping_with_text_repair(path).mapping


def load_yaml_mapping_with_text_repair(
    path: str,
    repair_text: Optional[Callable[[str], Optional[str]]] = None,
) -> YamlMappingLoadResult:
    if not os.path.exists(path):
        return YamlMappingLoadResult({})

    text = read_text_file(path)
    try:
        return YamlMappingLoadResult(parse_yaml_mapping_text(text))
    except yaml.YAMLError:
        if repair_text is None:
            raise
        repaired_text = repair_text(text)
        if not repaired_text or repaired_text == text:
            raise
        return YamlMappingLoadResult(
            parse_yaml_mapping_text(repaired_text), repaired_text
        )


def parse_yaml_mapping_text(text: str) -> Dict[str, Any]:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        return {}
    return data


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf8") as file:
        return file.read()


def save_yaml_file(path: str, data: Dict[str, Any]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as file:
        yaml.safe_dump(
            data, file, allow_unicode=True, sort_keys=False, default_flow_style=False
        )


def load_state_file(server: PluginServerInterface) -> Dict[str, Any]:
    state = load_yaml_mapping(get_data_file_path(server, STATE_NAME))
    runtime_state = state.get("runtime")
    if not isinstance(runtime_state, dict):
        state["runtime"] = build_default_runtime()
        return state

    merge_defaults(runtime_state, build_default_runtime())
    return state


def repair_inconsistent_block_scalar_indentation(text: str) -> Optional[str]:
    lines = text.splitlines(keepends=True)
    repaired = False
    index = 0
    while index < len(lines):
        match = match_block_scalar_header(lines[index])
        if match is None:
            index += 1
            continue

        block_end = find_block_scalar_end(lines, index + 1, len(match.group("indent")))
        repaired = (
            repair_first_block_scalar_line(
                lines, index + 1, block_end, len(match.group("indent"))
            )
            or repaired
        )
        index = block_end

    if not repaired:
        return None
    return "".join(lines)


def match_block_scalar_header(line: str):
    return BLOCK_SCALAR_HEADER_PATTERN.match(line.rstrip("\r\n"))


def find_block_scalar_end(lines: list, start_index: int, header_indent: int) -> int:
    for index in range(start_index, len(lines)):
        if not lines[index].strip():
            continue
        if leading_space_count(lines[index]) <= header_indent:
            return index
    return len(lines)


def repair_first_block_scalar_line(
    lines: list, start_index: int, end_index: int, header_indent: int
) -> bool:
    content_indexes = [
        index for index in range(start_index, end_index) if lines[index].strip()
    ]
    if len(content_indexes) < 2:
        return False

    first_index = content_indexes[0]
    first_indent = leading_space_count(lines[first_index])
    following_indents = [
        leading_space_count(lines[index]) for index in content_indexes[1:]
    ]
    target_indent = min(following_indents)
    if first_indent <= target_indent or target_indent <= header_indent:
        return False

    lines[first_index] = rewrite_line_indent(
        lines[first_index], first_indent, target_indent
    )
    return True


def leading_space_count(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def rewrite_line_indent(line: str, old_indent: int, new_indent: int) -> str:
    stripped_line = line.rstrip("\r\n")
    newline = line[len(stripped_line) :]
    content = stripped_line[old_indent:]
    return "{}{}{}".format(" " * new_indent, content, newline)
