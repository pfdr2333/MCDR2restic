# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import copy
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import yaml
from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.config_paths import get_data_file_path
from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.default_constants import STATE_NAME
from mcdr2restic.defaults.runtime_defaults import build_default_runtime
from mcdr2restic.core.i18n import server_tr, tr
from mcdr2restic.core.models import BackupRunStatus
from mcdr2restic.core.runtime import PluginRuntime


BLOCK_SCALAR_HEADER_PATTERN = re.compile(
    # safe_dump 对以换行结尾的多行值产出 |（clip）头，修复须同样识别（P5-2）
    r"^(?P<indent>[ ]*)(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:\s*\|[-+]?\s*$"
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
    sync_state_unlocked(app_runtime, state)
    save_yaml_file(get_data_file_path(target, STATE_NAME), state)


def sync_state_unlocked(
    app_runtime: PluginRuntime,
    state: Optional[Dict[str, Any]] = None,
):
    """把最新 runtime 状态同步进内存镜像；镜像滞后会让快照读回旧状态。"""

    if state is None:
        state = {
            "runtime": copy.deepcopy(
                app_runtime.config_state.config.get("runtime", build_default_runtime())
            )
        }
    app_runtime.config_state.state.clear()
    app_runtime.config_state.state.update(copy.deepcopy(state))


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


def reset_stale_backup_running_status(
    app_runtime: PluginRuntime, language: str
):
    """进程被杀后 state.yml 残留的 running 状态只在加载时复位一次。

    不能放进 ensure_runtime：它是配置快照的热路径，复位会打断进行中
    备份的合法 running 状态。
    """

    thread = app_runtime.backup.thread
    if thread is not None and thread.is_alive():
        return
    runtime_state = app_runtime.config_state.config.get("runtime")
    if runtime_state is None or runtime_state.get(
        "last_backup_status"
    ) != BackupRunStatus.RUNNING.value:
        return
    message = tr(language, "warn.backup.running_status_reset")
    runtime_state["last_backup_status"] = BackupRunStatus.INTERRUPTED.value
    runtime_state["last_backup_message"] = message
    server = app_runtime.service.server
    if server is not None:
        server.logger.warning(message)


def merge_defaults(target: Dict[str, Any], defaults: Dict[str, Any]):
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
            continue
        if isinstance(target.get(key), dict) and isinstance(value, dict):
            merge_defaults(target[key], value)


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
    save_text_file_atomically(
        path,
        yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        ),
    )


def save_text_file_atomically(path: str, text: str, attempts: int = 5):
    """先写唯一命名的临时文件再原子替换：进程中断不会损坏主文件，
    并发写方与修复回写也不会因共享 .tmp 名互相截断（P1-4）。"""

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    last_error: OSError = PermissionError(path)
    for attempt in range(attempts):
        temp_path = "{}.{}.tmp".format(path, uuid.uuid4().hex)
        try:
            with open(temp_path, "w", encoding="utf8") as file:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            # Windows 并发替换同一目标会瞬时共享冲突，退避重试而非丢弃本次写入
            last_error = exc
            with contextlib.suppress(OSError):
                os.remove(temp_path)
            time.sleep(0.05 * (attempt + 1))
    raise last_error


def load_state_file(server: PluginServerInterface) -> Dict[str, Any]:
    path = get_data_file_path(server, STATE_NAME)
    result = load_yaml_mapping_with_text_repair(
        path, repair_inconsistent_block_scalar_indentation
    )
    state = result.mapping
    if result.repaired_text is not None:
        # 回写修复后的文本，避免每次启动重复修复
        with contextlib.suppress(OSError):
            save_text_file_atomically(path, result.repaired_text)
        server.logger.warning(
            server_tr(
                server,
                "warn.config.auto_repaired_block_scalar_indentation",
                path=path,
            )
        )
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
