# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from contextlib import closing, nullcontext
from datetime import datetime
from pathlib import Path
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def install_mcdr_stub():
    module_names = [
        "mcdreforged",
        "mcdreforged.api",
        "mcdreforged.api.all",
    ]
    for name in module_names:
        sys.modules.setdefault(name, types.ModuleType(name))
    api_all = sys.modules["mcdreforged.api.all"]
    for name in [
        "PluginServerInterface",
        "CommandSource",
        "Info",
        "Literal",
        "Integer",
        "Text",
        "GreedyText",
    ]:
        setattr(api_all, name, type(name, (), {}))
    api_all.__all__ = [
        "PluginServerInterface",
        "CommandSource",
        "Info",
        "Literal",
        "Integer",
        "Text",
        "GreedyText",
    ]


install_mcdr_stub()

import mcdr2restic.commands.command_handlers as command_handlers
import mcdr2restic.core.bootstrap as bootstrap
import mcdr2restic.core.i18n as i18n
import mcdr2restic.defaults.config_template_resources as config_template_resources
import mcdr2restic.restic.restic_lock_recovery as restic_lock_recovery
import mcdr2restic.restic.restic_service as restic_service
import mcdr2restic.restore.restore_workflow as restore_workflow
import tools.pack_plugin as pack_plugin
from mcdr2restic.backup.backup_scheduler import BackupScheduler
from mcdr2restic.backup.cron import CronExpression
from mcdr2restic.backup.scheduling import (
    compute_maintenance_wait_seconds,
    parse_daily_time,
)
from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.commands.restic_commands import ResticCommands
from mcdr2restic.config.config_loader import (
    load_config_file_mapping,
    replace_or_append_enabled_line,
)
from mcdr2restic.config.config_migration import (
    apply_config_file_migrations,
    migrate_config_file,
    migrate_legacy_config,
)
from mcdr2restic.config.state_store import (
    load_yaml_mapping_with_text_repair,
    repair_inconsistent_block_scalar_indentation,
)
from mcdr2restic.core.i18n import (
    make_source_translate,
    normalize_language,
    tr,
    tr_error,
)
from mcdr2restic.core.models import (
    BackupProblem,
    ConfigError,
    ResticCommandResult,
    ResticProgressState,
    RestoreSession,
)
from mcdr2restic.core.presentation import render_status_output, schedule_status_text
from mcdr2restic.core.runtime import create_runtime
from mcdr2restic.core.utils import non_negative_int, safe_int, tail_text
from mcdr2restic.defaults.default_config import DEFAULT_CONFIG, build_default_config
from mcdr2restic.defaults.default_config_templates import get_default_config_template
from mcdr2restic.defaults.message_defaults import get_default_message_template
from mcdr2restic.minecraft.minecraft_service import server_is_running, try_call_bool
from mcdr2restic.minecraft.player_activity import (
    parse_online_list_output,
    runtime_player_set,
)
from mcdr2restic.minecraft.player_activity_service import (
    has_recent_player_activity,
    resolve_known_online_players,
)
from mcdr2restic.notifications import render_message
from mcdr2restic.notifications.discord_webhook import (
    DiscordWebhookClient,
    build_discord_mentions,
    build_discord_request,
    truncate_discord_content,
)
from mcdr2restic.restic.restic_download import (
    is_default_restic_executable_path,
    resolve_restic_executable_path,
)
from mcdr2restic.restic.restic_guidance import classify_restic_failure_output
from mcdr2restic.restic.restic_progress_text import (
    format_restic_status,
    format_restic_summary,
)
from mcdr2restic.restic.restic_result import assert_restic_success, detect_error_lines
from mcdr2restic.restic.restic_runner import (
    resolve_popen_executable,
    start_restic_process,
)
from mcdr2restic.restic.restic_termination import (
    TerminateResult,
    termination_failure_suffix,
)
from mcdr2restic.restore.restore_task_repository import (
    add_restore_task,
    clear_restore_tasks,
    list_restore_tasks,
    restore_tasks_output,
)
from mcdr2restic.restore.restore_workflow import normalize_restore_include_path
from mcdr2restic.snapshots.snapshot_cache import build_snapshot_cache_key
from mcdr2restic.snapshots.snapshot_db import (
    insert_snapshot_row,
    open_snapshot_db,
    read_snapshot_page,
)
from mcdr2restic.snapshots.snapshot_importer import (
    ProcessTimeoutState,
    assert_snapshot_import_finished,
    iter_json_array_stream,
)
from mcdr2restic.update.update_check import (
    get_current_plugin_version,
    is_newer_version,
    normalize_release_version,
    read_bundled_plugin_version,
    version_number_tuple,
)


class FakeServer:
    def __init__(self, data_folder):
        self.data_folder = data_folder

    def get_data_folder(self):
        return self.data_folder


class FakeLogger:
    def __init__(self):
        self.debug_messages = []
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []

    def debug(self, text):
        self.debug_messages.append(str(text))

    def info(self, text):
        self.info_messages.append(str(text))

    def warning(self, text):
        self.warning_messages.append(str(text))

    def error(self, text):
        self.error_messages.append(str(text))


class ProbeServer:
    def __init__(self):
        self.logger = FakeLogger()
        self.startup_calls = 0

    def is_server_startup(self):
        self.startup_calls += 1
        raise RuntimeError("startup probe failed")


class FakePluginServer:
    def __init__(self, language="zh_cn"):
        self.logger = FakeLogger()
        self.language = language

    def get_mcdr_language(self):
        return self.language


class FakeCommandSource:
    def __init__(self, language=""):
        self.language = language
        self.replies = []

    def get_preference(self):
        return types.SimpleNamespace(language=self.language)

    def preferred_language_context(self):
        return nullcontext()

    def reply(self, text):
        self.replies.append(str(text))


class PermissiveCommandSource(FakeCommandSource):
    def __init__(self, server, language=""):
        super().__init__(language)
        self.server = server

    def has_permission(self, _level):
        return True

    def get_server(self):
        return self.server


class CommandServer(FakePluginServer):
    def __init__(self):
        super().__init__()
        self.commands = []

    def execute(self, command):
        self.commands.append(command)


__all__ = [
    "BackupProblem",
    "ConfigError",
    "BackupScheduler",
    "CommandContext",
    "CommandServer",
    "CronExpression",
    "DEFAULT_CONFIG",
    "FakeCommandSource",
    "FakeLogger",
    "FakePluginServer",
    "FakeServer",
    "Path",
    "PermissiveCommandSource",
    "ProcessTimeoutState",
    "REPO_ROOT",
    "ProbeServer",
    "ResticCommandResult",
    "ResticCommands",
    "ResticProgressState",
    "RestoreSession",
    "TerminateResult",
    "add_restore_task",
    "apply_config_file_migrations",
    "assert_restic_success",
    "assert_snapshot_import_finished",
    "bootstrap",
    "build_default_config",
    "build_discord_mentions",
    "build_discord_request",
    "build_snapshot_cache_key",
    "classify_restic_failure_output",
    "clear_restore_tasks",
    "closing",
    "compute_maintenance_wait_seconds",
    "config_template_resources",
    "create_runtime",
    "command_handlers",
    "datetime",
    "detect_error_lines",
    "DiscordWebhookClient",
    "format_restic_status",
    "format_restic_summary",
    "get_current_plugin_version",
    "get_default_config_template",
    "get_default_message_template",
    "has_recent_player_activity",
    "insert_snapshot_row",
    "io",
    "i18n",
    "is_default_restic_executable_path",
    "is_newer_version",
    "json",
    "list_restore_tasks",
    "load_yaml_mapping_with_text_repair",
    "load_config_file_mapping",
    "make_source_translate",
    "migrate_legacy_config",
    "migrate_config_file",
    "mock",
    "non_negative_int",
    "normalize_language",
    "normalize_release_version",
    "normalize_restore_include_path",
    "nullcontext",
    "open_snapshot_db",
    "os",
    "pack_plugin",
    "parse_daily_time",
    "parse_online_list_output",
    "read_bundled_plugin_version",
    "read_snapshot_page",
    "repair_inconsistent_block_scalar_indentation",
    "replace_or_append_enabled_line",
    "render_message",
    "render_status_output",
    "resolve_known_online_players",
    "resolve_popen_executable",
    "start_restic_process",
    "resolve_restic_executable_path",
    "restore_tasks_output",
    "restore_workflow",
    "restic_lock_recovery",
    "restic_service",
    "runtime_player_set",
    "safe_int",
    "schedule_status_text",
    "server_is_running",
    "sys",
    "tail_text",
    "tempfile",
    "termination_failure_suffix",
    "threading",
    "tr",
    "tr_error",
    "try_call_bool",
    "truncate_discord_content",
    "unittest",
    "version_number_tuple",
    "yaml",
    "zipfile",
    "iter_json_array_stream",
]
