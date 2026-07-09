# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Optional

from mcdreforged.api.all import CommandSource, PluginServerInterface

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.config.config_loader import get_command_root
from mcdr2restic.core.language import get_mcdr_language, is_zh_language
from mcdr2restic.minecraft.minecraft_service import is_backup_running, is_mc_ready
from mcdr2restic.core.presentation import localized_text
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
from mcdr2restic.snapshots.snapshot_cache import build_snapshot_cache_key, get_snapshot_cache_config
from mcdr2restic.config.state_store import get_config_snapshot


class RestoreCommands:
    def __init__(self, context: CommandContext):
        self.context = context

    def command_restore_add_file(self, source: CommandSource, context: Dict[str, Any]):
        self.command_restore_add(source, context, 'file')

    def command_restore_add_folder(self, source: CommandSource, context: Dict[str, Any]):
        self.command_restore_add(source, context, 'folder')

    def command_restore_add_full(self, source: CommandSource, context: Dict[str, Any]):
        self.command_restore_add(source, context, 'full')

    def command_restore_add(self, source: CommandSource, context: Dict[str, Any], item_type: str):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        language = get_mcdr_language(server)
        try:
            task_id = self.add_restore_task(server, cfg, context, item_type)
        except Exception as exc:
            source.reply(localized_text(language, '添加恢复任务失败: {}'.format(exc), 'Failed to add restore task: {}'.format(exc)))
            return
        source.reply(self.restore_add_reply(server, cfg, language, task_id))

    def add_restore_task(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        context: Dict[str, Any],
        item_type: str,
    ) -> int:
        snapshot = normalize_restore_snapshot(context.get('snapshot'))
        include_path = self.restore_include_path(cfg, context, item_type)
        return add_restore_task_to_repository(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg),
            snapshot,
            item_type,
            include_path
        )

    def restore_include_path(self, cfg: Dict[str, Any], context: Dict[str, Any], item_type: str) -> str:
        if item_type == 'full':
            return '/'
        return normalize_restore_include_path(
            context.get('path'),
            cfg.get('restic', {}),
            get_command_root(self.context.app_runtime)
        )

    def restore_add_reply(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        language: str,
        task_id: int,
    ) -> str:
        output = self.restore_tasks_output(server, cfg, language)
        if is_zh_language(language):
            return '已添加恢复任务 #{}\n{}'.format(task_id, output)
        return 'Added restore task #{}\n{}'.format(task_id, output)

    def command_restore_list(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        source.reply(self.restore_tasks_output(server, cfg, get_mcdr_language(server)))

    def command_unrestore_task(self, source: CommandSource, context: Dict[str, Any]):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        language = get_mcdr_language(server)
        task_id = int(context.get('task_id'))
        deleted = delete_restore_task(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg),
            task_id
        )
        source.reply('{}\n{}'.format(
            self.delete_task_prefix(language, task_id, deleted),
            self.restore_tasks_output(server, cfg, language)
        ))

    def delete_task_prefix(self, language: str, task_id: int, deleted: bool) -> str:
        if is_zh_language(language):
            return '已删除恢复任务 #{}'.format(task_id) if deleted else '未找到恢复任务 #{}'.format(task_id)
        return 'Deleted restore task #{}'.format(task_id) if deleted else 'Restore task #{} was not found'.format(task_id)

    def command_unrestore_all(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        language = get_mcdr_language(server)
        count = clear_restore_tasks(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg)
        )
        prefix = '已删除 {} 个恢复任务'.format(count) if is_zh_language(language) else 'Deleted {} restore task(s)'.format(count)
        source.reply('{}\n{}'.format(prefix, self.restore_tasks_output(server, cfg, language)))

    def command_restore_apply(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        snapshot_cfg = self.current_snapshot_cache_config(cfg)
        cache_key = self.current_restore_cache_key(cfg)
        language = get_mcdr_language(server)
        tasks = [dict(task) for task in list_restore_tasks(server, snapshot_cfg, cache_key)]
        if self.reject_restore_apply(source, server, language, tasks):
            return
        self.start_restore_apply(server, cfg, snapshot_cfg, cache_key, language, tasks)
        source.reply(localized_text(
            language,
            '恢复流程已开始：先创建保护快照，然后通过 MCDR 停止 MC，停机 hook 将继续执行恢复',
            'Restore workflow started: creating a safety snapshot, then stopping Minecraft via MCDR; the stop hook will continue the restore'
        ))

    def start_restore_apply(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        snapshot_cfg: Dict[str, Any],
        cache_key: str,
        language: str,
        tasks: list,
    ):
        session = create_restore_session(tasks, cfg, snapshot_cfg, cache_key, language)
        set_restore_session(self.context.app_runtime, session)
        start_restore_pre_stop_thread(
            self.context.app_runtime,
            server,
            self.context.snapshot_invalidator,
            get_config_snapshot
        )

    def reject_restore_apply(
        self,
        source: CommandSource,
        server: PluginServerInterface,
        language: str,
        tasks: list,
    ) -> bool:
        rejection = get_restore_apply_rejection(
            self.context.app_runtime,
            server,
            language,
            tasks,
            is_backup_running,
            is_mc_ready
        )
        if rejection:
            source.reply(rejection)
            return True
        if self.context.app_runtime.restore.lock.acquire(blocking=False):
            return False
        source.reply(localized_text(language, '当前已有恢复流程在执行', 'A restore workflow is already running'))
        return True

    def current_snapshot_cache_config(self, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return get_snapshot_cache_config(
            cfg,
            config_snapshot_provider=lambda: get_config_snapshot(self.context.app_runtime)
        )

    def current_restore_cache_key(self, cfg: Optional[Dict[str, Any]] = None) -> str:
        cfg = cfg or get_config_snapshot(self.context.app_runtime)
        restic_cfg = cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}
        return build_snapshot_cache_key(restic_cfg)

    def restore_tasks_output(self, server: PluginServerInterface, cfg: Dict[str, Any], language: str) -> str:
        return render_restore_tasks_output(
            server,
            self.current_snapshot_cache_config(cfg),
            self.current_restore_cache_key(cfg),
            language,
            self.context.get_command_root()
        )
