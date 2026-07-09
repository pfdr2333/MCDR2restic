# -*- coding: utf-8 -*-
"""Command handlers for restore task management and restore workflow control."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from mcdreforged.api.all import CommandSource, PluginServerInterface

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.config.config_loader import get_command_root
from mcdr2restic.core.i18n import (
    TranslateFunc,
    make_source_translate,
    reply_tr,
    source_error_text,
)
from mcdr2restic.core.language import get_source_language
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.minecraft.minecraft_service import is_backup_running, is_mc_ready
from mcdr2restic.restore.restore_task_repository import (
    add_restore_task as add_restore_task_to_repository,
    clear_restore_tasks,
    delete_restore_task,
    list_restore_tasks,
    restore_tasks_output as render_restore_tasks_output,
)
from mcdr2restic.restore.restore_workflow import (
    create_restore_session,
    get_restore_apply_rejection,
    normalize_restore_include_path,
    normalize_restore_snapshot,
    set_restore_session,
    start_restore_pre_stop_thread,
)
from mcdr2restic.snapshots.snapshot_cache import (
    build_snapshot_cache_key,
    get_snapshot_cache_config,
)
from mcdr2restic.config.state_store import get_config_snapshot


class RestoreCommands:
    """Handle restore queue commands and kick off restore workflows."""

    def __init__(self, context: CommandContext):
        """Store the shared command context used by restore subcommands."""

        self.context = context

    def command_restore_add_file(
        self, source: CommandSource, context: Dict[str, Any]
    ) -> None:
        """Queue a file restore task."""

        self.command_restore_add(source, context, "file")

    def command_restore_add_folder(
        self, source: CommandSource, context: Dict[str, Any]
    ) -> None:
        """Queue a folder restore task."""

        self.command_restore_add(source, context, "folder")

    def command_restore_add_full(
        self, source: CommandSource, context: Dict[str, Any]
    ) -> None:
        """Queue a full-snapshot restore task."""

        self.command_restore_add(source, context, "full")

    def command_restore_add(
        self, source: CommandSource, context: Dict[str, Any], item_type: str
    ) -> None:
        """Validate input and enqueue a restore task of the requested type."""

        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        translate = make_source_translate(source, server)
        try:
            task_id = self.add_restore_task(server, cfg, context, item_type)
        except (BackupProblem, OSError, sqlite3.Error, TypeError, ValueError) as exc:
            reply_tr(
                source,
                server,
                "error.restore.add_failed",
                error=source_error_text(source, server, exc),
            )
            return
        source.reply(self.restore_add_reply(server, cfg, translate, task_id))

    def add_restore_task(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        context: Dict[str, Any],
        item_type: str,
    ) -> int:
        """Persist a restore task in the repository-scoped task queue."""

        snapshot = normalize_restore_snapshot(context.get("snapshot"))
        include_path = self.restore_include_path(cfg, context, item_type)
        return add_restore_task_to_repository(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg),
            snapshot,
            item_type,
            include_path,
        )

    def restore_include_path(
        self, cfg: Dict[str, Any], context: Dict[str, Any], item_type: str
    ) -> str:
        """Normalize the include path used by the restore task."""

        if item_type == "full":
            return "/"
        return normalize_restore_include_path(
            context.get("path"),
            cfg.get("restic", {}),
            get_command_root(self.context.app_runtime),
        )

    def restore_add_reply(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        translate: TranslateFunc,
        task_id: int,
    ) -> str:
        """Build the add-task success reply with the updated task list."""

        output = self.restore_tasks_output(server, cfg, translate)
        return "{}\n{}".format(
            translate("info.restore.task_added", task_id=task_id), output
        )

    def command_restore_list(self, source: CommandSource) -> None:
        """Show queued restore tasks for the active repository."""

        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        source.reply(
            self.restore_tasks_output(
                server, cfg, make_source_translate(source, server)
            )
        )

    def command_unrestore_task(
        self, source: CommandSource, context: Dict[str, Any]
    ) -> None:
        """Delete a single restore task from the queue."""

        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        translate = make_source_translate(source, server)
        task_id = int(context.get("task_id"))
        deleted = delete_restore_task(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg),
            task_id,
        )
        source.reply(
            "{}\n{}".format(
                self.delete_task_prefix(translate, task_id, deleted),
                self.restore_tasks_output(server, cfg, translate),
            )
        )

    def delete_task_prefix(
        self, translate: TranslateFunc, task_id: int, deleted: bool
    ) -> str:
        """Return the localized prefix for a delete-task reply."""

        key = "info.restore.task_deleted" if deleted else "info.restore.task_not_found"
        return translate(key, task_id=task_id)

    def command_unrestore_all(self, source: CommandSource) -> None:
        """Clear all queued restore tasks for the active repository."""

        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        translate = make_source_translate(source, server)
        count = clear_restore_tasks(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg),
        )
        prefix = translate("info.restore.tasks_cleared", count=count)
        source.reply(
            "{}\n{}".format(prefix, self.restore_tasks_output(server, cfg, translate))
        )

    def command_restore_apply(self, source: CommandSource) -> None:
        """Start the restore workflow for the queued tasks."""

        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        snapshot_cfg = self.current_snapshot_cache_config(cfg)
        cache_key = self.current_restore_cache_key(cfg)
        language = get_source_language(source, server)
        translate = make_source_translate(source, server)
        tasks = [
            dict(task) for task in list_restore_tasks(server, snapshot_cfg, cache_key)
        ]
        if self.reject_restore_apply(source, server, translate, tasks):
            return
        self.start_restore_apply(server, cfg, snapshot_cfg, cache_key, language, tasks)
        reply_tr(source, server, "info.restore.apply_started")

    def start_restore_apply(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        snapshot_cfg: Dict[str, Any],
        cache_key: str,
        language: str,
        tasks: List[Dict[str, Any]],
    ) -> None:
        """Persist restore session state and spawn the pre-stop worker thread."""

        session = create_restore_session(tasks, cfg, snapshot_cfg, cache_key, language)
        set_restore_session(self.context.app_runtime, session)
        start_restore_pre_stop_thread(
            self.context.app_runtime,
            server,
            self.context.snapshot_invalidator,
            get_config_snapshot,
        )

    def reject_restore_apply(
        self,
        source: CommandSource,
        server: PluginServerInterface,
        translate: TranslateFunc,
        tasks: List[Dict[str, Any]],
    ) -> bool:
        """Return True when the restore apply request must stop immediately."""

        rejection = get_restore_apply_rejection(
            self.context.app_runtime,
            server,
            translate,
            tasks,
            is_backup_running,
            is_mc_ready,
        )
        if rejection:
            source.reply(rejection)
            return True
        if self.context.app_runtime.restore.lock.acquire(blocking=False):
            return False
        reply_tr(source, server, "error.restore.already_running")
        return True

    def current_snapshot_cache_config(
        self, cfg: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Resolve the snapshot cache configuration for the current runtime."""

        return get_snapshot_cache_config(
            cfg,
            config_snapshot_provider=lambda: get_config_snapshot(
                self.context.app_runtime
            ),
        )

    def current_restore_cache_key(self, cfg: Optional[Dict[str, Any]] = None) -> str:
        """Build the repository-scoped cache key for restore task storage."""

        cfg = cfg or get_config_snapshot(self.context.app_runtime)
        restic_cfg = (
            cfg.get("restic", {}) if isinstance(cfg.get("restic"), dict) else {}
        )
        return build_snapshot_cache_key(restic_cfg)

    def restore_tasks_output(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        translate_or_language: Any,
    ) -> str:
        """Render the queued restore tasks using the provided translator."""

        return render_restore_tasks_output(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg),
            translate_or_language,
            self.context.get_command_root(),
        )
