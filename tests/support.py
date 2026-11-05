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


SimpleNamespace = types.SimpleNamespace
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
        "QuotableText",
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
        "QuotableText",
        "GreedyText",
    ]


install_mcdr_stub()

from mcdr2restic.commands import command_handlers
from mcdr2restic.core import bootstrap
from mcdr2restic.core import i18n
from mcdr2restic.defaults import config_template_resources
from mcdr2restic.restic import restic_context
from mcdr2restic.restic import restic_lock_recovery
from mcdr2restic.restic import restic_service
from mcdr2restic.restore import restore_paths
from mcdr2restic.restore import restore_workflow
from tools import pack_plugin
from tools import verify_plugin
from mcdr2restic.backup.backup_scheduler import BackupScheduler, SchedulerDependencies
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
    WHOLE_BLOCK_SECTIONS,
    apply_config_file_migrations,
    migrate_config_file,
    migrate_legacy_config,
)
from mcdr2restic.config.state_store import (
    load_state_file,
    load_yaml_mapping,
    load_yaml_mapping_with_text_repair,
    repair_inconsistent_block_scalar_indentation,
    save_text_file_atomically,
    save_yaml_file,
)
from mcdr2restic.minecraft.player_activity_service import persist_player_state
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
from mcdr2restic.core.presentation import (
    RuntimeStatusProviders,
    StatusViewContext,
    render_status_output,
    schedule_status_text,
)
from mcdr2restic.core.runtime import create_runtime
from mcdr2restic.core.utils import non_negative_int, safe_int, tail_text
from mcdr2restic.defaults.default_config import build_default_config
from mcdr2restic.defaults.default_config_templates import get_default_config_template
from mcdr2restic.defaults.message_defaults import get_default_message_template
from mcdr2restic.defaults.restic_release_defaults import get_default_restic_version
from mcdr2restic.minecraft.minecraft_service import server_is_running, try_call_bool
from mcdr2restic.minecraft.player_activity import (
    parse_online_list_output,
    runtime_player_set,
)
from mcdr2restic.minecraft.player_activity_service import (
    has_recent_player_activity,
    resolve_known_online_players,
)
from mcdr2restic.minecraft.save_flush_watcher import SaveFlushWatcher
from mcdr2restic.notifications import render_message
from mcdr2restic.notifications.custom_webhook import (
    CustomWebhookClient,
    build_webhook_request,
    render_payload_leaves,
)
from mcdr2restic.notifications.discord_webhook import (
    DiscordWebhookClient,
    build_discord_mentions,
    build_discord_request,
    truncate_discord_content,
)
from mcdr2restic.notifications.notification_dispatcher import NotificationDispatcher
from mcdr2restic.notifications.onebot_client import OneBotClient
from mcdr2restic.restic.restic_download import (
    ResticDownloadTarget,
    expected_restic_sha256,
    extract_sha256_from_sums,
    fetch_restic_release_metadata,
    install_restic_from_urls,
    is_default_restic_executable_path,
    resolve_restic_executable_path,
    verify_restic_download,
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
    RestoreTaskRecord,
    add_restore_task,
    clear_restore_tasks,
    list_restore_tasks,
    restore_tasks_output,
)
from mcdr2restic.restore.restore_paths import normalize_restore_include_path
from mcdr2restic.snapshots.snapshot_cache import build_snapshot_cache_key
from mcdr2restic.snapshots.snapshot_db import (
    insert_snapshot_row,
    open_snapshot_db,
    read_snapshot_page,
)
from mcdr2restic.snapshots.snapshot_importer import (
    ProcessTimeoutState,
    assert_snapshot_import_finished,
    import_restic_snapshots_to_sql,
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

    def get_data_folder(self):
        return ""


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
    "REPO_ROOT",
    "WHOLE_BLOCK_SECTIONS",
    "BackupProblem",
    "BackupScheduler",
    "CommandContext",
    "CommandServer",
    "ConfigError",
    "CronExpression",
    "CustomWebhookClient",
    "DiscordWebhookClient",
    "FakeCommandSource",
    "FakeLogger",
    "FakePluginServer",
    "FakeServer",
    "NotificationDispatcher",
    "OneBotClient",
    "Path",
    "PermissiveCommandSource",
    "ProbeServer",
    "ProcessTimeoutState",
    "ResticCommandResult",
    "ResticCommands",
    "ResticDownloadTarget",
    "ResticProgressState",
    "RestoreSession",
    "RestoreTaskRecord",
    "RuntimeStatusProviders",
    "SaveFlushWatcher",
    "SchedulerDependencies",
    "SimpleNamespace",
    "StatusViewContext",
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
    "build_webhook_request",
    "classify_restic_failure_output",
    "clear_restore_tasks",
    "closing",
    "command_handlers",
    "compute_maintenance_wait_seconds",
    "config_template_resources",
    "create_runtime",
    "datetime",
    "detect_error_lines",
    "expected_restic_sha256",
    "extract_sha256_from_sums",
    "fetch_restic_release_metadata",
    "format_restic_status",
    "format_restic_summary",
    "get_current_plugin_version",
    "get_default_config_template",
    "get_default_message_template",
    "get_default_restic_version",
    "has_recent_player_activity",
    "i18n",
    "import_restic_snapshots_to_sql",
    "insert_snapshot_row",
    "install_restic_from_urls",
    "io",
    "is_default_restic_executable_path",
    "is_newer_version",
    "iter_json_array_stream",
    "json",
    "list_restore_tasks",
    "load_config_file_mapping",
    "load_state_file",
    "load_yaml_mapping",
    "load_yaml_mapping_with_text_repair",
    "make_source_translate",
    "migrate_config_file",
    "migrate_legacy_config",
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
    "persist_player_state",
    "read_bundled_plugin_version",
    "read_snapshot_page",
    "render_message",
    "render_payload_leaves",
    "render_status_output",
    "repair_inconsistent_block_scalar_indentation",
    "replace_or_append_enabled_line",
    "resolve_known_online_players",
    "resolve_popen_executable",
    "resolve_restic_executable_path",
    "restic_context",
    "restic_lock_recovery",
    "restic_service",
    "restore_paths",
    "restore_tasks_output",
    "restore_workflow",
    "runtime_player_set",
    "safe_int",
    "save_text_file_atomically",
    "save_yaml_file",
    "schedule_status_text",
    "server_is_running",
    "start_restic_process",
    "sys",
    "tail_text",
    "tempfile",
    "termination_failure_suffix",
    "threading",
    "tr",
    "tr_error",
    "truncate_discord_content",
    "try_call_bool",
    "unittest",
    "verify_plugin",
    "verify_restic_download",
    "version_number_tuple",
    "yaml",
    "zipfile",
]
