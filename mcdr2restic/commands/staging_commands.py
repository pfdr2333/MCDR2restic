# -*- coding: utf-8 -*-
"""[beta] `staging status|copy|purge` 命令。

状态命令只读；copy 触发后台 worker；purge 仅支持文件系统型远端，
与复制/维护/手管 restic 通过 staging.io_lock 串行。
"""

from __future__ import annotations

import contextlib
import shutil
from typing import Any, Dict, List, Optional

from mcdreforged.api.all import CommandSource, PluginServerInterface

from mcdr2restic.commands.command_context import CommandContext
from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.core.i18n import config_language, reply_tr, server_tr, tr, tr_error
from mcdr2restic.core.language import get_mcdr_language, get_source_language
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.core.runtime import ResticExecutionSlot
from mcdr2restic.core.utils import format_bytes, get_config_section, safe_int
from mcdr2restic.restic.restic_config import nearest_existing_directory
from mcdr2restic.restore.restore_workflow import is_restore_running
from mcdr2restic.staging.staging_config import (
    StagingPaths,
    StagingSettings,
    build_local_restic_config,
    build_remote_restic_config,
    get_staging_settings,
    staging_enabled,
    validate_staging_settings,
)
from mcdr2restic.staging.staging_purge import purge_staging_remote
from mcdr2restic.staging.staging_repository import (
    chunker_polynomial,
    latest_snapshot_time,
    make_remote_probe_deadline,
    probe_repository,
    read_snapshots,
)
from mcdr2restic.staging.staging_worker import request_copy


class StagingCommands:
    def __init__(self, context: CommandContext):
        self.context = context

    def command_status(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        language = get_source_language(source, server)
        if not staging_enabled(cfg):
            source.reply(tr(language, "status.staging.disabled"))
            return
        restic_cfg = get_config_section(cfg, "restic")
        settings = get_staging_settings(cfg)
        try:
            paths = validate_staging_settings(settings, restic_cfg)
        except BackupProblem as exc:
            source.reply(
                tr(
                    language,
                    "status.staging.config_error",
                    error=tr_error(language, exc),
                )
            )
            return
        if paths is None:
            source.reply(tr(language, "status.staging.disabled"))
            return

        lines: List[str] = [
            tr(language, "status.staging.title"),
            tr(
                language,
                "status.staging.mode",
                value=tr(language, "status.staging.mode.staging"),
            ),
            tr(language, "status.staging.repository", path=paths.repository_path),
        ]
        query_lock = self.context.app_runtime.service.snapshot_query_lock
        if not query_lock.acquire(blocking=False):
            lines.append(tr(language, "status.staging.query_busy"))
            source.reply("\n".join(lines))
            return
        try:
            lines.extend(
                self.collect_live_status(
                    server, cfg, restic_cfg, settings, paths
                )
            )
        finally:
            query_lock.release()
        source.reply("\n".join(lines))

    def collect_live_status(
        self,
        server: PluginServerInterface,
        cfg: Dict[str, Any],
        restic_cfg: Dict[str, Any],
        settings: StagingSettings,
        paths: StagingPaths,
    ) -> List[str]:
        language = config_language(cfg, get_mcdr_language(server))
        local_cfg = build_local_restic_config(restic_cfg, settings, paths)
        remote_cfg = build_remote_restic_config(restic_cfg)
        slot = ResticExecutionSlot()
        runtime_state = get_config_section(cfg, "runtime")
        # status 在 MCDR TaskExecutor 线程同步执行：远端读取必须带短超时，
        # 否则不可达远端的 restic 内部重试会阻塞整个执行器数分钟（E2E NEW-P3）。
        # 所有远端命令共用同一个 deadline，总耗时不超过一个探测周期（P2-6）。
        remote_deadline = make_remote_probe_deadline(
            settings.remote_probe_timeout_seconds
        )
        lines: List[str] = []
        local_latest = self.read_latest(server, local_cfg, slot)
        remote_latest = self.read_latest(
            server, remote_cfg, slot, deadline=remote_deadline
        )
        lines.append(
            tr(
                language,
                "status.staging.local_latest",
                value=local_latest or tr(language, "status.never"),
            )
        )
        if remote_latest is None:
            # 读取失败（含超时）时回退上次已知值，并如实标注不是实时数据
            cached = str(
                runtime_state.get("staging_remote_latest_time") or ""
            ).strip()
            if cached:
                lines.append(
                    tr(
                        language,
                        "status.staging.remote_latest.cached",
                        value=cached,
                    )
                )
            else:
                lines.append(
                    tr(
                        language,
                        "status.staging.remote_latest",
                        value=tr(language, "status.never"),
                    )
                )
        else:
            lines.append(
                tr(
                    language,
                    "status.staging.remote_latest",
                    value=remote_latest,
                )
            )
        lines.append(self.format_disk_free(language, paths.repository_path))
        lines.append(self.format_backlog(language, runtime_state))
        lines.extend(self.format_cursor_anomaly(language, runtime_state))
        lines.append(self.format_last_copy(language, runtime_state))
        lines.append(
            self.format_chunker_status(language, local_cfg, remote_cfg, slot, remote_deadline)
        )
        return lines

    def read_latest(
        self,
        server: PluginServerInterface,
        restic_cfg: Dict[str, Any],
        slot: Any,
        deadline: Optional[float] = None,
    ):
        try:
            return latest_snapshot_time(
                read_snapshots(
                    self.context.app_runtime,
                    server,
                    restic_cfg,
                    slot=slot,
                    latest_only=True,
                    deadline=deadline,
                )
            )
        except Exception:
            return None

    def format_disk_free(self, language: str, repository_path: str) -> str:
        try:
            usage = shutil.disk_usage(nearest_existing_directory(repository_path))
        except Exception:
            return tr(language, "status.staging.disk_free.unknown")
        if not usage.total:
            return tr(language, "status.staging.disk_free.unknown")
        free_percent = usage.free * 100.0 / usage.total
        return tr(
            language,
            "status.staging.disk_free",
            free=format_bytes(usage.free),
            total=format_bytes(usage.total),
            percent="{:.1f}".format(free_percent),
        )

    def format_backlog(
        self, language: str, runtime_state: Dict[str, Any]
    ) -> str:
        backlog = safe_int(runtime_state.get("staging_copy_backlog", 0))
        if backlog < 0:
            # 游标不可信时不能让状态页显示 0，否则等于声称"远端已完整"
            return tr(language, "status.staging.backlog_unknown")
        return tr(language, "status.staging.backlog", value=backlog)

    def format_cursor_anomaly(
        self, language: str, runtime_state: Dict[str, Any]
    ) -> List[str]:
        anomaly = str(runtime_state.get("staging_cursor_anomaly") or "").strip()
        if not anomaly:
            return []
        reason = tr(language, "status.staging.cursor_reason.{}".format(anomaly))
        return [tr(language, "status.staging.cursor_anomaly", reason=reason)]

    def format_last_copy(
        self, language: str, runtime_state: Dict[str, Any]
    ) -> str:
        status = str(runtime_state.get("staging_last_copy_status") or "never")
        time_text = str(runtime_state.get("staging_last_copy_time") or "").strip()
        if not time_text:
            return tr(language, "status.staging.last_copy.never")
        message = str(runtime_state.get("staging_last_copy_message") or "").strip()
        return tr(
            language,
            "status.staging.last_copy",
            status=localized_copy_status(status, language),
            time=time_text,
            message=message,
        )

    def format_chunker_status(
        self,
        language: str,
        local_cfg: Dict[str, Any],
        remote_cfg: Dict[str, Any],
        slot: Any,
        remote_deadline: Optional[float],
    ) -> str:
        local_probe = probe_repository(self.context.app_runtime, local_cfg, slot=slot)
        # 复用外层 deadline：远端黑洞时第二个 cat-config 不再重新计满一分钟
        remote_probe = probe_repository(
            self.context.app_runtime,
            remote_cfg,
            slot=slot,
            deadline=remote_deadline,
        )
        if not local_probe.ok:
            return tr(language, "status.staging.chunker.unknown")
        if remote_probe.missing:
            return tr(language, "status.staging.chunker.remote_missing")
        if not remote_probe.ok:
            return tr(language, "status.staging.chunker.unknown")
        local_chunker = chunker_polynomial(local_probe.config)
        remote_chunker = chunker_polynomial(remote_probe.config)
        if local_chunker == remote_chunker:
            return tr(language, "status.staging.chunker.matched")
        return tr(
            language,
            "status.staging.chunker.mismatch",
            local=local_chunker or "-",
            remote=remote_chunker or "-",
        )

    def command_copy(self, source: CommandSource):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        if not staging_enabled(cfg):
            reply_tr(source, server, "error.staging.disabled")
            return
        if is_restore_running(self.context.app_runtime):
            reply_tr(source, server, "error.staging.restore_running")
            return
        if not request_copy(self.context.app_runtime, manual=True):
            reply_tr(source, server, "error.staging.worker_not_running")
            return
        reply_tr(source, server, "info.staging.copy_manual_started")

    def command_purge(self, source: CommandSource):
        self.purge(source, force=False)

    def command_purge_force(self, source: CommandSource):
        self.purge(source, force=True)

    def purge(self, source: CommandSource, force: bool):
        if not self.context.check_command_permission(source):
            return
        server = self.context.server_from_source(source)
        cfg = get_config_snapshot(self.context.app_runtime)
        if not staging_enabled(cfg):
            reply_tr(source, server, "error.staging.disabled")
            return
        if is_restore_running(self.context.app_runtime):
            reply_tr(source, server, "error.staging.restore_running")
            return
        io_lock = self.context.app_runtime.staging.io_lock
        if not io_lock.acquire(blocking=False):
            reply_tr(source, server, "error.staging.busy")
            return
        self.context.app_runtime.staging.purge_active = True
        try:
            # 拿锁后复检：保持锁与 has-restore 的检查顺序，关闭"检查通过后恢复
            # 才启动"的窗口（P2-3）。恢复入口同时检查 purge_active。
            if is_restore_running(self.context.app_runtime):
                reply_tr(source, server, "error.staging.restore_running")
                return
            try:
                message = purge_staging_remote(
                    self.context.app_runtime, server, cfg, force=force
                )
            except BackupProblem as exc:
                source.reply(tr_error(get_source_language(source, server), exc))
                return
        finally:
            self.context.app_runtime.staging.purge_active = False
            if io_lock.locked():
                io_lock.release()
        with contextlib.suppress(Exception):
            self.context.snapshot_invalidator(
                server,
                get_config_section(cfg, "restic"),
                server_tr(server, "snapshot.cache.reason.staging_purge"),
            )
        source.reply(message)


def localized_copy_status(status: str, language: str) -> str:
    return tr(language, "status.staging.copy_status.{}".format(status))


__all__ = ["StagingCommands"]
