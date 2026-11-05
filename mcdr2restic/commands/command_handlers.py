# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Set

from mcdreforged.api.all import (
    GreedyText,
    Integer,
    Literal,
    PluginServerInterface,
    QuotableText,
    Text,
)

from mcdr2restic.commands.backup_commands import BackupCommands
from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.commands.restore_commands import RestoreCommands
from mcdr2restic.commands.restic_commands import ResticCommands
from mcdr2restic.core.i18n import server_rtr
from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.commands.status_commands import StatusCommands


class CommandHandlers:
    def __init__(self, context: CommandContext):
        self.context = context
        self.status = StatusCommands(self.context)
        self.backup = BackupCommands(self.context)
        self.restore = RestoreCommands(self.context)
        self.restic = ResticCommands(self.context)

    def register_commands(self, server: PluginServerInterface):
        roots = [self.context.get_command_root()]
        roots.extend(
            get_config_snapshot(self.context.app_runtime)
            .get("command", {})
            .get("aliases", [])
        )
        seen: Set[str] = set()
        for root_name in roots:
            root_name = str(root_name).strip()
            if not root_name or root_name in seen:
                continue
            seen.add(root_name)
            server.register_command(self.build_command_tree(root_name))

    def register_help_messages(self, server: PluginServerInterface):
        server.register_help_message(
            self.context.get_command_root(),
            server_rtr(server, "help.command_description"),
            permission=self.context.get_command_permission_level(),
        )

    def build_command_tree(self, root_name: str):
        return (
            Literal(root_name)
            .runs(self.status.command_status)
            .then(
                Literal("status")
                .runs(self.status.command_status)
                .then(
                    Literal("p").then(
                        Integer("page").runs(self.status.command_status_page)
                    )
                )
            )
            .then(Literal("start").runs(self.backup.command_start))
            .then(Literal("stop").runs(self.backup.command_stop))
            .then(Literal("backup").runs(self.backup.command_backup))
            .then(Literal("init").runs(self.restic.command_init))
            .then(Literal("maintenance").runs(self.restic.command_maintenance))
            .then(Literal("unlock").runs(self.restic.command_unlock))
            .then(self.build_restore_command_tree())
            .then(
                Literal("unrestore")
                .then(Literal("all").runs(self.restore.command_unrestore_all))
                .then(Integer("task_id").runs(self.restore.command_unrestore_task))
            )
            .then(Literal("reload").runs(self.backup.command_reload))
        )

    def build_restore_command_tree(self):
        return (
            Literal("restore")
            .then(Literal("list").runs(self.restore.command_restore_list))
            .then(
                Literal("apply")
                .runs(self.restore.command_restore_apply)
                .then(
                    Literal("--skip-safety").runs(
                        self.restore.command_restore_apply_skip_safety
                    )
                )
            )
            .then(
                Text("snapshot")
                .runs(self.restore.command_restore_add_full)
                .then(
                    Literal("to").then(
                        QuotableText("target")
                        .runs(self.restore.command_restore_add_full_to)
                        .then(
                            Literal("file").then(
                                GreedyText("path").runs(
                                    self.restore.command_restore_add_file_to
                                )
                            )
                        )
                        .then(
                            Literal("folder").then(
                                GreedyText("path").runs(
                                    self.restore.command_restore_add_folder_to
                                )
                            )
                        )
                    )
                )
                .then(
                    Literal("file").then(
                        GreedyText("path").runs(self.restore.command_restore_add_file)
                    )
                )
                .then(
                    Literal("folder").then(
                        GreedyText("path").runs(self.restore.command_restore_add_folder)
                    )
                )
            )
        )
