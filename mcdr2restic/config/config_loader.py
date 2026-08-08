# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, Optional

import yaml
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
from mcdr2restic.core.i18n import config_language, set_active_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import ConfigError
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.config.state_store import (
    ensure_runtime,
    get_config_snapshot,
    load_state_file,
    merge_defaults,
    save_config_unlocked,
)


def load_config(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    source: Optional[CommandSource] = None,
):
    mcdr_language = get_mcdr_language(server)
    loaded = load_config_mapping(server, mcdr_language)
    language = config_language(loaded, mcdr_language)
    merge_defaults(loaded, default_config_for_language(language))
    language = config_language(loaded, mcdr_language)
    migrate_config_file(server, language, loaded)

    state = load_state_file(server)
    with app_runtime.config_state.lock:
        app_runtime.config_state.config = loaded
        app_runtime.config_state.state = state
        app_runtime.config_state.language = language
        ensure_runtime(app_runtime.config_state.config, app_runtime.config_state.state)
        save_config_unlocked(app_runtime, server)
    set_active_language(language)
    if source is not None:
        source.reply(tr(language, "info.config.reloaded", name=CONFIG_NAME))


def load_config_mapping(server: PluginServerInterface, language: str) -> Dict[str, Any]:
    ensure_config_file_exists(server, language)
    loaded = load_config_file_mapping(server)
    loaded = strip_comment_keys(loaded)
    loaded.pop("runtime", None)
    migrate_legacy_config(loaded)
    return loaded


def load_config_file_mapping(server: PluginServerInterface) -> Dict[str, Any]:
    path = get_data_file_path(server, CONFIG_NAME)
    try:
        with open(path, "r", encoding="utf8") as file:
            text = file.read()
    except OSError as exc:
        raise ConfigError("error.config.read_failed", path=path, error=exc) from exc
    try:
        loaded = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError("error.config.yaml_invalid", path=path) from exc
    if not isinstance(loaded, dict):
        raise ConfigError("error.config.root_not_mapping", path=path)
    return loaded


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
