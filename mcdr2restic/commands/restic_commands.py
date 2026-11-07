# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from mcdreforged.api.all import CommandSource, PluginServerInterface

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.commands.exec_pipeline import ExecOutputPipeline, parse_exec_pipeline
from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.core.i18n import reply_tr, server_tr, source_error_text
from mcdr2restic.core.models import BackupProblem, ResticCommandResult
from mcdr2restic.core.utils import get_config_section
from mcdr2restic.restore.restore_workflow import is_restore_running
from mcdr2restic.restic.restic_constants import (
    RESTIC_COMMAND_INIT,
    RESTIC_COMMAND_UNLOCK,
    RESTIC_OPTION_REMOVE_ALL,
)
from mcdr2restic.restic.restic_download import ensure_default_restic_executable_available
from mcdr2restic.restic.restic_guidance import restic_management_command
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.restic.restic_service import make_restic_deadline

# 透传命令的内部 phase 标签：不能与 backup/restore/rollback 重名，
# 否则会触发 prepare_restic_args_for_phase 的 --json 自动注入
EXEC_PHASE = "exec"


class ResticCommands:
    def __init__(self, context: CommandContext):
        self.context = context

    def command_init(self, source: CommandSource):
        self.command_manage_repository(source, RESTIC_COMMAND_INIT)

    def command_maintenance(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        try:
            started = self.context.maintenance_runner_factory().start_thread(server)
        except Exception as exc:
            reply_tr(
                source, server, "error.maintenance.thread_start_failed", error=exc
            )
            return
        if started:
            reply_tr(source, server, "info.maintenance.manual_started")
            return
        if is_restore_running(self.context.app_runtime):
            reply_tr(source, server, "error.restic.manual.restore_running")
            return
        reply_tr(source, server, "error.restic.manual.backup_running")

    def command_unlock(self, source: CommandSource):
        self.command_manage_repository(source, RESTIC_COMMAND_UNLOCK)

    def command_unlock_remove_all(self, source: CommandSource):
        # --remove-all 连"锁进程 PID 已被复用而看似仍存活"的残留锁一并移除，
        # 是普通 unlock 清不掉锁时的唯一手段
        self.command_manage_repository(
            source,
            RESTIC_COMMAND_UNLOCK,
            extra_args=[RESTIC_OPTION_REMOVE_ALL],
            force_unlock=True,
        )

    def command_manage_repository(
        self,
        source: CommandSource,
        command: str,
        extra_args: Optional[List[str]] = None,
        force_unlock: bool = False,
    ):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        restic_cfg = get_config_section(cfg, "restic")
        if self.reject_manual_restic_command(source):
            return

        self.context.app_runtime.backup.label = "manual-{}".format(command)
        self.context.app_runtime.backup.cancel.clear()
        # 登记命令线程供卸载统一取消/join：restic 挂起时重载不留僵尸子进程（P2-2）
        self.context.app_runtime.backup.thread = threading.current_thread()
        try:
            ensure_default_restic_executable_available(server, restic_cfg)
            result = run_restic_command(
                self.context.app_runtime,
                restic_cfg,
                self.restic_command_args(command) + list(extra_args or []),
                command,
                make_restic_deadline(restic_cfg),
            )
            assert_restic_success(
                restic_cfg, result, self.context.get_command_root(), server.logger
            )
        except BackupProblem as exc:
            source.reply(source_error_text(source, server, exc))
            if command == RESTIC_COMMAND_UNLOCK and not force_unlock:
                # 普通 unlock 对存活 PID 占用的锁无效，失败即引导强制清锁
                source.reply(
                    server_tr(
                        server,
                        "warn.restic.unlock.force_hint",
                        force_unlock_command=self.force_unlock_command_text(),
                    )
                )
            return
        finally:
            self.context.app_runtime.release_backup_slot()

        self.handle_manual_restic_success(
            source, server, restic_cfg, command, force_unlock
        )

    def reject_manual_restic_command(self, source: CommandSource) -> bool:
        server = self.context.server_from_source(source)
        if is_restore_running(self.context.app_runtime):
            reply_tr(source, server, "error.restic.manual.restore_running")
            return True
        if self.context.app_runtime.backup.lock.acquire(blocking=False):
            return False
        reply_tr(source, server, "error.restic.manual.backup_running")
        return True

    def command_exec_usage(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        reply_tr(source, server, "error.exec.usage")

    def command_exec(self, source: CommandSource, context: Dict[str, Any]):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        try:
            request = parse_exec_pipeline(str(context.get("args", "")))
        except BackupProblem as exc:
            source.reply(source_error_text(source, server, exc))
            return
        cfg = get_config_snapshot(self.context.app_runtime)
        restic_cfg = get_config_section(cfg, "restic")
        if self.reject_manual_restic_command(source):
            return

        self.context.app_runtime.backup.label = "exec-{}".format(request.restic_args[0])
        self.context.app_runtime.backup.cancel.clear()
        # 登记命令线程供卸载统一取消/join，与手动管理命令同一套生命周期（P2-2）
        self.context.app_runtime.backup.thread = threading.current_thread()
        pipeline = ExecOutputPipeline(request.stages)
        sink = self.build_exec_line_sink(source, server, pipeline)
        try:
            ensure_default_restic_executable_available(server, restic_cfg)
            reply_tr(
                source, server, "info.exec.started", command=" ".join(request.restic_args)
            )
            result = run_restic_command(
                self.context.app_runtime,
                restic_cfg,
                request.restic_args,
                EXEC_PHASE,
                None,
                line_sink=sink,
            )
            assert_restic_success(
                restic_cfg, result, self.context.get_command_root(), server.logger
            )
        except BackupProblem as exc:
            source.reply(source_error_text(source, server, exc))
            return
        finally:
            self.context.app_runtime.release_backup_slot()

        for line in pipeline.flush():
            source.reply(line)
        self.reply_exec_summary(source, server, pipeline, result)

    def build_exec_line_sink(
        self,
        source: CommandSource,
        server: PluginServerInterface,
        pipeline: ExecOutputPipeline,
    ) -> Callable[[str, str], None]:
        logger = getattr(server, "logger", None)

        def sink(stream_name: str, line: str) -> None:
            if logger is not None:
                logger.info("restic exec [{}] {}".format(stream_name, str(line).rstrip()))
            for display_line in pipeline.feed(line):
                source.reply(display_line)

        return sink

    def reply_exec_summary(
        self,
        source: CommandSource,
        server: PluginServerInterface,
        pipeline: ExecOutputPipeline,
        result: ResticCommandResult,
    ):
        params: Dict[str, Any] = {
            "return_code": result.return_code,
            "duration": round(result.duration_seconds, 1),
            "total": pipeline.total_lines,
            "shown": pipeline.shown_lines,
        }
        if pipeline.suppressed_lines:
            params["suppressed"] = pipeline.suppressed_lines
            reply_tr(source, server, "info.exec.completed_truncated", **params)
            return
        reply_tr(source, server, "info.exec.completed", **params)


    def restic_command_args(self, command: str) -> List[str]:
        if command == RESTIC_COMMAND_INIT:
            return [RESTIC_COMMAND_INIT]
        if command == RESTIC_COMMAND_UNLOCK:
            return [RESTIC_COMMAND_UNLOCK]
        raise BackupProblem(i18n_key="error.restic.command_empty", phase=command)

    def handle_manual_restic_success(
        self,
        source: CommandSource,
        server: Any,
        restic_cfg: Dict[str, Any],
        command: str,
        force_unlock: bool = False,
    ):
        if command == RESTIC_COMMAND_INIT:
            self.context.snapshot_invalidator(
                server,
                restic_cfg,
                server_tr(server, "snapshot.cache.reason.repository_initialized"),
            )
            reply_tr(source, server, "info.restic.init.completed")
            return
        if force_unlock:
            reply_tr(source, server, "info.restic.unlock.force_completed")
            return
        # 普通 unlock 可能因锁进程 PID 被复用而清不掉，成功回复同样给出强制清锁路径
        reply_tr(
            source,
            server,
            "info.restic.unlock.completed",
            force_unlock_command=self.force_unlock_command_text(),
        )

    def force_unlock_command_text(self) -> str:
        return restic_management_command(
            self.context.get_command_root(),
            "{} {}".format(RESTIC_COMMAND_UNLOCK, RESTIC_OPTION_REMOVE_ALL),
        )
