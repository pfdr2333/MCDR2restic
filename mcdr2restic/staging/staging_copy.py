# -*- coding: utf-8 -*-
"""[beta] 解冻后的本地→远端复制周期与游标推进。

复制只发送游标之后的新快照，显式传快照 ID，避免远端 retention 已删除的
旧快照被 restic copy-all 复活。失败分三类：可重试、鉴权/配额不可重试、
chunker 不匹配永久失败；后两类不阻塞本地备份。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import config_language, tr, tr_error
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import BackupCanceled, BackupProblem
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section, now_text
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.staging.staging_config import (
    StagingPaths,
    StagingSettings,
    build_copy_args,
    build_copy_source_config,
    build_local_restic_config,
    local_source_credentials,
)
from mcdr2restic.staging.staging_failure import FailureKind, classify_failure
from mcdr2restic.staging.staging_repository import (
    COPY_PHASE,
    CursorCoverage,
    RepoProbeStatus,
    assert_matching_chunkers,
    check_cursor_coverage,
    cursor_from_snapshots,
    ensure_remote_repository_for_copy,
    latest_snapshot_time,
    make_remote_probe_deadline,
    probe_repository,
    read_snapshots,
    select_pending_snapshots,
)
from mcdr2restic.staging.staging_state import (
    STAGING_COPY_STATUS_CANCELED,
    STAGING_COPY_STATUS_FAILED,
    STAGING_COPY_STATUS_PERMANENT,
    STAGING_COPY_STATUS_RUNNING,
    STAGING_COPY_STATUS_SKIPPED,
    STAGING_COPY_STATUS_SUCCESS,
    staging_runtime_state,
    update_staging_runtime,
)


# 游标不可信时无法断言还有多少本地快照未复制；状态页与告警都按"未知"处理
BACKLOG_UNKNOWN = -1


@dataclass(frozen=True)
class RemoteLatest:
    """[beta] 远端最新快照的探测结果。

    ``ok=False`` 表示探测本身失败（超时/不可达），此时 ``time`` 无意义：
    调用方必须保留上次已知值，不能用本地快照时间顶替，否则"远端落后"
    会被误判成已追平而不再告警。
    """

    time: Optional[str] = None
    ok: bool = False


@dataclass(frozen=True)
class CopyCycleResult:
    status: str
    message: str = ""
    pending_count: int = 0
    copied_count: int = 0
    retryable: bool = False
    canceled: bool = False
    permanent: bool = False
    remote_initialized: bool = False
    chunker_mismatch: bool = False
    auth_error: bool = False
    quota_error: bool = False
    # 游标与本地快照不一致：本轮不信任游标并全量重放，backlog 无法断言
    cursor_untrusted: bool = False
    backlog_unknown: bool = False

    @property
    def success(self) -> bool:
        return self.status == STAGING_COPY_STATUS_SUCCESS


def run_copy_cycle(  # noqa: PLR0913,PLR0911 复制周期需要完整上下文与分支
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    settings: StagingSettings,
    paths: StagingPaths,
    slot: Any = None,
) -> CopyCycleResult:
    runtime_state = staging_runtime_state(app_runtime)
    cursor_time = runtime_state.get("staging_cursor_time")
    cursor_ids = runtime_state.get("staging_cursor_ids")
    restic_cfg = get_config_section(cfg, "restic")
    local_cfg = build_local_restic_config(restic_cfg, settings, paths)
    pending = []
    remote_initialized = False
    coverage = CursorCoverage(True)
    language = config_language(cfg, get_mcdr_language(server))
    try:
        local_probe = probe_repository(app_runtime, local_cfg, slot=slot)
        if not local_probe.ok:
            if local_probe.status == RepoProbeStatus.MISSING and not cursor_time:
                # 刚启用、首备未跑：本地库还不存在是预期状态，静默等待而非
                # 连环告警，避免用户误以为启用失败
                return _record_wait_first_backup(app_runtime, server)
            return _record_failure(
                app_runtime,
                server,
                _local_missing_result(server, local_probe.error),
            )
        remote_cfg, remote_initialized = ensure_remote_repository_for_copy(
            app_runtime, server, restic_cfg, settings, paths, slot=slot
        )
        chunker_result = _check_chunkers(
            app_runtime, server, local_cfg, remote_cfg, slot, settings
        )
        if chunker_result is not None:
            return chunker_result

        snapshots = read_snapshots(app_runtime, server, local_cfg, slot=slot)
        cursor_text = str(cursor_time) if cursor_time else None
        coverage = check_cursor_coverage(
            snapshots, cursor_text, _coerce_ids(cursor_ids)
        )
        if coverage.untrusted:
            # 游标不可信时不能据此跳过任何本地快照：全量重放（restic copy 幂等），
            # 复制成功后再用真实快照重建游标。backlog 此时无法断言，记为未知。
            _record_cursor_anomaly(app_runtime, server, language, coverage, cursor_text)
            pending = select_pending_snapshots(snapshots, None, ())
            update_staging_runtime(
                app_runtime, server, staging_copy_backlog=BACKLOG_UNKNOWN
            )
        else:
            pending = select_pending_snapshots(
                snapshots,
                cursor_text,
                _coerce_ids(cursor_ids),
            )
            update_staging_runtime(
                app_runtime, server, staging_copy_backlog=len(pending)
            )
        if not pending:
            remote = probe_remote_latest(
                app_runtime,
                server,
                remote_cfg,
                slot,
                settings.remote_probe_timeout_seconds,
            )
            message = tr(language, "info.staging.copy_nothing", count=len(snapshots))
            _record_success(
                app_runtime,
                server,
                message,
                remote,
                remote_initialized,
            )
            return CopyCycleResult(
                STAGING_COPY_STATUS_SUCCESS,
                message=message,
                remote_initialized=remote_initialized,
            )

        update_staging_runtime(
            app_runtime,
            server,
            staging_last_copy_time=now_text(),
            staging_last_copy_status=STAGING_COPY_STATUS_RUNNING,
            staging_last_copy_message=tr(
                language, "info.staging.copy_running", count=len(pending)
            ),
        )
        result = copy_pending_snapshots(
            app_runtime, server, remote_cfg, settings, paths, pending, slot
        )
        assert_restic_success(
            remote_cfg, result, logger=getattr(server, "logger", None)
        )
    except BackupCanceled as exc:
        message = tr(language, "warn.staging.copy_canceled", error=exc)
        _record_canceled(
            app_runtime,
            server,
            _backlog_value(pending, coverage),
            message,
        )
        return CopyCycleResult(
            STAGING_COPY_STATUS_CANCELED,
            message=message,
            pending_count=len(pending),
            retryable=True,
            canceled=True,
            cursor_untrusted=coverage.untrusted,
            backlog_unknown=coverage.untrusted,
        )
    except BackupProblem as exc:
        return _record_failure(
            app_runtime,
            server,
            _classify_copy_problem(
                server, exc, pending, backlog_unknown=coverage.untrusted
            ),
        )
    except Exception as exc:
        return _record_failure(
            app_runtime,
            server,
            CopyCycleResult(
                STAGING_COPY_STATUS_FAILED,
                message=tr_error(language, exc),
                pending_count=len(pending),
                retryable=True,
                cursor_untrusted=coverage.untrusted,
                backlog_unknown=coverage.untrusted,
            ),
        )

    latest_time, latest_ids = cursor_from_snapshots(pending)
    remote = probe_remote_latest(
        app_runtime, server, remote_cfg, slot, settings.remote_probe_timeout_seconds
    )
    message = tr(
        language,
        "info.staging.copy_recovered"
        if coverage.untrusted
        else "info.staging.copy_completed",
        count=len(pending),
    )
    _record_success(
        app_runtime,
        server,
        message,
        remote,
        remote_initialized,
    )
    update_staging_runtime(
        app_runtime,
        server,
        staging_cursor_time=latest_time,
        staging_cursor_ids=latest_ids,
    )
    return CopyCycleResult(
        STAGING_COPY_STATUS_SUCCESS,
        message=message,
        pending_count=0,
        copied_count=len(pending),
        remote_initialized=remote_initialized,
        cursor_untrusted=coverage.untrusted,
    )


def copy_pending_snapshots(  # noqa: PLR0913 复制参数集中在一个内部入口
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    remote_cfg: Dict[str, Any],
    settings: StagingSettings,
    paths: StagingPaths,
    pending: Sequence[Dict[str, str]],
    slot: Any,
):
    source_password, source_password_file = local_source_credentials(settings, paths)
    source_cfg = build_copy_source_config(
        remote_cfg, source_password, source_password_file
    )
    args = build_copy_args(
        paths.repository_path,
        [str(item["id"]) for item in pending],
        paths.cache_dir,
        settings.copy_limit_upload_kib,
    )
    return run_restic_command(
        app_runtime, source_cfg, args, COPY_PHASE, None, slot=slot
    )


def _check_chunkers(  # noqa: PLR0913 chunker 校验需要完整上下文
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    local_cfg: Dict[str, Any],
    remote_cfg: Dict[str, Any],
    slot: Any,
    settings: StagingSettings,
) -> Optional[CopyCycleResult]:
    try:
        assert_matching_chunkers(
            app_runtime,
            local_cfg,
            remote_cfg,
            slot=slot,
            remote_probe_timeout_seconds=settings.remote_probe_timeout_seconds,
        )
    except BackupProblem as exc:
        if exc.i18n_key != "error.staging.chunker_mismatch":
            raise
        message = tr_error(get_mcdr_language(server), exc)
        update_staging_runtime(
            app_runtime,
            server,
            staging_chunker_mismatch=True,
            staging_last_copy_time=now_text(),
            staging_last_copy_status=STAGING_COPY_STATUS_PERMANENT,
            staging_last_copy_message=message,
        )
        server.logger.warning(message)
        return CopyCycleResult(
            STAGING_COPY_STATUS_PERMANENT,
            message=message,
            permanent=True,
            chunker_mismatch=True,
        )
    return None


def _classify_copy_problem(
    server: PluginServerInterface,
    exc: BackupProblem,
    pending: Sequence[Dict[str, str]],
    backlog_unknown: bool = False,
) -> CopyCycleResult:
    # 只按 i18n_key 与 restic 原始输出分类：对译文做英文子串匹配会让
    # 中文环境下"超时"与"配额"互相误判（P1-2）
    language = get_mcdr_language(server)
    message = tr_error(language, exc)
    kind = classify_failure(exc)
    if kind == FailureKind.AUTH:
        return CopyCycleResult(
            STAGING_COPY_STATUS_FAILED,
            message=message,
            pending_count=len(pending),
            retryable=False,
            auth_error=True,
            backlog_unknown=backlog_unknown,
        )
    if kind == FailureKind.QUOTA:
        return CopyCycleResult(
            STAGING_COPY_STATUS_FAILED,
            message=message,
            pending_count=len(pending),
            retryable=False,
            quota_error=True,
            backlog_unknown=backlog_unknown,
        )
    return CopyCycleResult(
        STAGING_COPY_STATUS_FAILED,
        message=message,
        pending_count=len(pending),
        retryable=True,
        backlog_unknown=backlog_unknown,
    )


def _local_missing_result(
    server: PluginServerInterface, error: str
) -> CopyCycleResult:
    message = tr(
        get_mcdr_language(server), "error.staging.local_repo_missing", error=error
    )
    return CopyCycleResult(
        STAGING_COPY_STATUS_FAILED,
        message=message,
        retryable=True,
    )


def _record_wait_first_backup(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
) -> CopyCycleResult:
    message = tr(
        get_mcdr_language(server), "info.staging.copy_waiting_first_backup"
    )
    runtime_state = staging_runtime_state(app_runtime)
    already_waiting = (
        str(runtime_state.get("staging_last_copy_status") or "")
        == STAGING_COPY_STATUS_SKIPPED
        and str(runtime_state.get("staging_last_copy_message") or "") == message
    )
    if already_waiting:
        # 首备被门控推迟数天时，每 30 秒重写 state.yml 并打 info 只会刷屏（P2-9）
        server.logger.debug(message)
        return CopyCycleResult(
            STAGING_COPY_STATUS_SKIPPED,
            message=message,
            retryable=True,
        )
    update_staging_runtime(
        app_runtime,
        server,
        staging_last_copy_status=STAGING_COPY_STATUS_SKIPPED,
        staging_last_copy_message=message,
    )
    server.logger.info(message)
    return CopyCycleResult(
        STAGING_COPY_STATUS_SKIPPED,
        message=message,
        retryable=True,
    )


def _coerce_ids(value: Any) -> Sequence[str]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _backlog_value(
    pending: Sequence[Dict[str, str]], coverage: CursorCoverage
) -> int:
    """游标不可信时 backlog 无法断言，用哨兵值表示"未知"。"""

    return BACKLOG_UNKNOWN if coverage.untrusted else len(pending)


def _record_cursor_anomaly(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    language: str,
    coverage: CursorCoverage,
    cursor_time: Optional[str],
):
    if coverage.reason == "missing_ids":
        message = tr(
            language,
            "warn.staging.cursor_ids_missing",
            missing_ids=", ".join(coverage.missing_ids) or "-",
            local_latest=coverage.local_latest or "-",
        )
    else:
        message = tr(
            language,
            "warn.staging.cursor_ahead",
            cursor_time=str(cursor_time or "-"),
            local_latest=coverage.local_latest or "-",
        )
    update_staging_runtime(
        app_runtime,
        server,
        staging_cursor_anomaly=coverage.reason,
        staging_copy_backlog=BACKLOG_UNKNOWN,
        staging_last_copy_message=message,
    )
    server.logger.warning(message)


def _record_success(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    message: str,
    remote: RemoteLatest,
    _remote_initialized: bool,
):
    finished_at = now_text()
    values: Dict[str, Any] = {
        "staging_last_copy_time": finished_at,
        "staging_last_copy_success_time": finished_at,
        "staging_last_copy_status": STAGING_COPY_STATUS_SUCCESS,
        "staging_last_copy_message": message,
        "staging_copy_backlog": 0,
        "staging_chunker_mismatch": False,
        "staging_cursor_anomaly": "",
    }
    if remote.ok:
        values["staging_remote_latest_time"] = remote.time
        values["staging_remote_probe_failed"] = False
        values["staging_remote_stale_warned_at"] = None
    else:
        # 探测失败时保留上次已知值：用本地快照时间顶替会让远端落后被误判为已追平
        values["staging_remote_probe_failed"] = True
    update_staging_runtime(app_runtime, server, **values)


def _record_failure(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    result: CopyCycleResult,
):
    # 游标异常原因由 _record_cursor_anomaly 写入，这里只更新复制结果字段
    values: Dict[str, Any] = {
        "staging_last_copy_time": now_text(),
        "staging_last_copy_status": result.status,
        "staging_last_copy_message": result.message,
    }
    if result.backlog_unknown:
        values["staging_copy_backlog"] = BACKLOG_UNKNOWN
    elif result.pending_count > 0:
        values["staging_copy_backlog"] = result.pending_count
    update_staging_runtime(app_runtime, server, **values)
    log_copy_failure(server, result)
    return result


def _record_canceled(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    backlog: int,
    message: str,
):
    update_staging_runtime(
        app_runtime,
        server,
        staging_last_copy_time=now_text(),
        staging_last_copy_status=STAGING_COPY_STATUS_CANCELED,
        staging_last_copy_message=message,
        staging_copy_backlog=backlog,
    )
    server.logger.info(message)


def log_copy_failure(server: PluginServerInterface, result: CopyCycleResult):
    language = get_mcdr_language(server)
    if result.auth_error:
        server.logger.warning(
            tr(language, "warn.staging.copy_auth_failed", error=result.message)
        )
        return
    if result.quota_error:
        server.logger.warning(
            tr(language, "warn.staging.copy_quota_failed", error=result.message)
        )
        return
    server.logger.warning(
        tr(language, "warn.staging.copy_failed", error=result.message)
    )


def probe_remote_latest(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    remote_cfg: Dict[str, Any],
    slot: Any,
    remote_probe_timeout_seconds: int = 0,
) -> RemoteLatest:
    try:
        snapshots = read_snapshots(
            app_runtime,
            server,
            remote_cfg,
            slot=slot,
            latest_only=True,
            # 远端 snapshots 与探测一样受 restic 内部分钟级重试拖累；
            # 复制周期不能被它挂住（E2E NEW-P3）
            deadline=make_remote_probe_deadline(remote_probe_timeout_seconds),
        )
    except Exception:
        return RemoteLatest(ok=False)
    return RemoteLatest(time=latest_snapshot_time(snapshots), ok=True)


__all__ = [
    "BACKLOG_UNKNOWN",
    "CopyCycleResult",
    "RemoteLatest",
    "copy_pending_snapshots",
    "log_copy_failure",
    "probe_remote_latest",
    "run_copy_cycle",
]
