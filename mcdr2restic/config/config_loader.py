# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import re
from typing import Any, Dict, Optional

from mcdreforged.api.all import CommandSource, PluginServerInterface

from mcdr2restic.config.config_migration import (
    migrate_config_file,
    migrate_legacy_config,
)
from mcdr2restic.config.config_paths import (
    ensure_config_file_exists,
    get_data_file_path,
)
from mcdr2restic.defaults.default_config import default_config_for_language
from mcdr2restic.defaults.default_constants import CONFIG_NAME
from mcdr2restic.core.i18n import reply_tr, server_tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.config.state_store import (
    ensure_runtime,
    get_config_snapshot,
    load_state_file,
    load_yaml_mapping_with_text_repair,
    merge_defaults,
    repair_inconsistent_block_scalar_indentation,
    save_config_unlocked,
)


def load_config(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    source: Optional[CommandSource] = None,
):
    language = get_mcdr_language(server)
    loaded = load_config_mapping(server, language)
    state = load_state_file(server)
    with app_runtime.config_state.lock:
        app_runtime.config_state.config = loaded
        app_runtime.config_state.state = state
        merge_defaults(
            app_runtime.config_state.config, default_config_for_language(language)
        )
        ensure_runtime(app_runtime.config_state.config, app_runtime.config_state.state)
        save_config_unlocked(app_runtime, server)
    migrate_config_file(server, language, get_config_snapshot(app_runtime))
    if source is not None:
        reply_tr(source, server, "info.config.reloaded", name=CONFIG_NAME)


def load_config_mapping(server: PluginServerInterface, language: str) -> Dict[str, Any]:
    defaults = default_config_for_language(language)
    ensure_config_file_exists(server, language)
    loaded = load_config_file_mapping(server)
    if not isinstance(loaded, dict):
        loaded = copy.deepcopy(defaults)
    loaded = strip_comment_keys(loaded)
    loaded.pop("runtime", None)
    migrate_legacy_config(loaded)
    return loaded


def load_config_file_mapping(server: PluginServerInterface) -> Dict[str, Any]:
    path = get_data_file_path(server, CONFIG_NAME)
    load_result = load_yaml_mapping_with_text_repair(
        path, repair_inconsistent_block_scalar_indentation
    )
    if load_result.repaired_text is not None:
        write_text_file(path, load_result.repaired_text)
        server.logger.warning(
            server_tr(
                server,
                "warn.config.auto_repaired_block_scalar_indentation",
                path=path,
            )
        )
    return load_result.mapping


def strip_comment_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_comment_keys(item)
            for key, item in value.items()
            if not str(key).startswith("_comment") and not str(key).endswith("_comment")
        }
    if isinstance(value, list):
        return [strip_comment_keys(item) for item in value]
    return value


def save_enabled_unlocked(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    enabled: bool,
):
    app_runtime.config_state.config["enabled"] = bool(enabled)
    path = get_data_file_path(server, CONFIG_NAME)
    ensure_config_file_exists(server, get_mcdr_language(server))
    lines = read_config_lines(path)
    lines = replace_or_append_enabled_line(lines, enabled)
    with open(path, "w", encoding="utf8") as file:
        file.writelines(lines)


def read_config_lines(path: str):
    with open(path, "r", encoding="utf8") as file:
        return file.readlines()


def write_text_file(path: str, text: str):
    with open(path, "w", encoding="utf8") as file:
        file.write(text)


def replace_or_append_enabled_line(lines: list, enabled: bool) -> list:
    enabled_text = "enabled: {}\n".format("true" if enabled else "false")
    for index, line in enumerate(lines):
        if re.match(r"^enabled\s*:", line):
            lines[index] = enabled_text
            return lines

    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"
    lines.append(enabled_text)
    return lines


def get_command_root(app_runtime: PluginRuntime) -> str:
    cfg = get_config_snapshot(app_runtime)
    return str(cfg.get("command", {}).get("root", "!!restic"))
