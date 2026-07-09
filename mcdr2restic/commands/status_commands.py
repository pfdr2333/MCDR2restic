# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict

from mcdreforged.api.all import CommandSource

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.core.i18n import make_source_translate
from mcdr2restic.core.language import get_source_language
from mcdr2restic.minecraft.minecraft_service import is_backup_running, is_mc_ready
from mcdr2restic.core.presentation import render_status_output
from mcdr2restic.restore.restore_workflow import is_restore_running
from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.core.utils import non_negative_int


class StatusCommands:
    def __init__(self, context: CommandContext):
        self.context = context

    def command_status(self, source: CommandSource):
        self.command_status_with_page(source, 1)

    def command_status_page(self, source: CommandSource, context: Dict[str, Any]):
        self.command_status_with_page(source, max(1, non_negative_int(context.get('page', 1), 1)))

    def command_status_with_page(self, source: CommandSource, page: int):
        if not self.context.check_command_permission(source):
            return

        app_runtime = self.context.app_runtime
        cfg = get_config_snapshot(app_runtime)
        server = self.context.server_from_source(source)
        language = get_source_language(source, server)
        source.reply(render_status_output(
            app_runtime.service.snapshot_query_lock,
            cfg,
            language,
            server,
            page,
            backup_running_provider=lambda: is_backup_running(app_runtime),
            restore_running_provider=lambda: is_restore_running(app_runtime),
            mc_ready_provider=lambda target: is_mc_ready(app_runtime, target),
            translate=make_source_translate(source, server),
        ))
