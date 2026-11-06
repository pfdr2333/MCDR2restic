# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import normalize_translate, server_tr, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import (
    BackupProblem,
    BackupTrigger,
    RestorePhase,
    RestoreSession,
    RestoreStageResult,
    normalize_restore_phase,
)
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.restic.restic_config import (
    normalize_command_args,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_BACKUP_COMMAND,
    RESTIC_CFG_MAINTENANCE_COMMANDS,
    RESTIC_COMMAND_RESTORE,
    RESTIC_COMMAND_ROLLBACK,
    RESTIC_OPTION_INCLUDE,
    RESTIC_OPTION_TAG,
    RESTIC_OPTION_TARGET,
)
from mcdr2restic.restic.restic_download import (
    ensure_default_restic_executable_available,
)
from mcdr2restic.restic.restic_guidance import command_root_from_config
from mcdr2restic.minecraft.minecraft_service import try_force_save_on
from mcdr2restic.restic.restic_context import ResticOperationContext
from mcdr2restic.restic.restic_lock_recovery import (
    run_restic_command_with_lock_recovery,
)
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_service import make_restic_deadline, run_backup_body
from mcdr2restic.restore.restore_paths import (
    assert_restore_target_allowed,
    get_restic_working_directory,
    normalize_restore_snapshot,
)
from mcdr2restic.core.utils import get_config_section


def is_direct_restore_session(tasks: List[Dict[str, Any]]) -> bool:
    """Direct restore only when every task targets an external directory."""

    return bool(tasks) and all(
        str(task.get("target_directory") or "").strip() for task in tasks
    )


class RestoreReadinessProviders(NamedTuple):
    """恢复前就绪状态探针。"""

    backup_running: Callable[[PluginRuntime], bool]
    mc_ready: Callable[[PluginRuntime, PluginServerInterface], bool]


class RestoreQueueContext(NamedTuple):
    """恢复任务队列所属的仓库上下文。"""

    cfg: Dict[str, Any]
    snapshot_cfg: Dict[str, Any]
    cache_key: str


def get_restore_apply_rejection(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    translate_or_language: Any,
    tasks: List[Dict[str, Any]],
    providers: RestoreReadinessProviders,
) -> str:
    translate = normalize_translate(translate_or_language)
    if not tasks:
        return translate("error.restore.no_tasks")
    if providers.backup_running(app_runtime):
        return translate("error.restore.backup_running")
    if not providers.mc_ready(app_runtime, server):
        return translate("error.restore.minecraft_not_ready")
    return ""


def create_restore_session(
    tasks: List[Dict[str, Any]],
    queue: RestoreQueueContext,
    language: str,
    skip_safety_snapshot: bool = False,
) -> RestoreSession:
    return RestoreSession(
        tasks=tasks,
        cfg=queue.cfg,
        snapshot_cfg=queue.snapshot_cfg,
        cache_key=queue.cache_key,
        language=language,
        phase=RestorePhase.PRE_BACKUP,
        skip_safety_snapshot=skip_safety_snapshot,
    )


def start_restore_pre_stop_thread(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    invalidate_snapshot_cache_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], None
    ],
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]],
):
    thread = threading.Thread(
        target=run_restore_pre_stop_stage,
        args=(
            app_runtime,
            server,
            invalidate_snapshot_cache_func,
            config_snapshot_provider,
        ),
        name="MCDR2Restic-Restore-PreStop",
        daemon=True,
    )
    app_runtime.restore.thread = thread
    thread.start()


def set_restore_session(app_runtime: PluginRuntime, session: Optional[RestoreSession]):
    with app_runtime.restore.state_lock:
        app_runtime.restore.session = session


def get_restore_session(app_runtime: PluginRuntime) -> Optional[RestoreSession]:
    with app_runtime.restore.state_lock:
        return app_runtime.restore.session


def update_restore_phase(app_runtime: PluginRuntime, phase: RestorePhase):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.phase = normalize_restore_phase(phase)


def mark_restore_safety_snapshot(app_runtime: PluginRuntime, safety_snapshot_id: str):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.safety_snapshot_id = safety_snapshot_id


def mark_restore_stopping(app_runtime: PluginRuntime, safety_snapshot_id: str):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.phase = RestorePhase.STOPPING
            app_runtime.restore.session.safety_snapshot_id = safety_snapshot_id


def mark_restore_starting(app_runtime: PluginRuntime, result: RestoreStageResult):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.phase = RestorePhase.STARTING
            app_runtime.restore.session.error = result.restore_error
            app_runtime.restore.session.rollback_error = result.rollback_error


def is_restore_running(app_runtime: PluginRuntime) -> bool:
    return app_runtime.restore.lock.locked()


def finish_restore_workflow(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface],
    message: str,
    failure: bool = False,
    expected_session: Optional[RestoreSession] = None,
) -> bool:
    if not clear_restore_workflow_state(app_runtime, expected_session):
        return False
    if server is not None:
        if failure:
            server.logger.warning(message)
        else:
            server.logger.info(message)
    return True


def clear_restore_workflow_state(
    app_runtime: PluginRuntime,
    expected_session: Optional[RestoreSession],
) -> bool:
    with app_runtime.restore.state_lock:
        current_session = app_runtime.restore.session
        if expected_session is not None and current_session is not expected_session:
            return False
        app_runtime.restore.session = None
        if app_runtime.restore.lock.locked():
            app_runtime.restore.lock.release()
        return True


def run_restore_pre_stop_stage(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    invalidate_snapshot_cache_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], None
    ],
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]],
):
    session = get_restore_session(app_runtime)
    if session is None:
        finish_restore_workflow(
            app_runtime,
            server,
            server_tr(server, "error.restore.state_missing"),
            failure=True,
        )
        return
    if session.skip_safety_snapshot:
        server.logger.warning(
            server_tr(server, "warn.restore.safety_snapshot_skipped")
        )
        request_minecraft_stop_for_restore(
            app_runtime, server, session, "", config_snapshot_provider
        )
        return
    if not acquire_restore_pre_backup_slot(app_runtime):
        finish_restore_workflow(
            app_runtime,
            server,
            server_tr(server, "error.restore.pre_backup_slot_busy"),
            failure=True,
            expected_session=session,
        )
        return

    try:
        safety_snapshot_id = create_restore_safety_snapshot(
            app_runtime, server, session, invalidate_snapshot_cache_func
        )
        request_minecraft_stop_for_restore(
            app_runtime, server, session, safety_snapshot_id, config_snapshot_provider
        )
    except Exception as exc:
        handle_restore_pre_stop_failure(
            app_runtime, server, session, exc, config_snapshot_provider
        )
    finally:
        app_runtime.release_backup_slot()


def start_direct_restore_thread(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    clear_restore_tasks_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], int
    ],
):
    thread = threading.Thread(
        target=run_direct_restore_stage,
        args=(app_runtime, server, session, clear_restore_tasks_func),
        name="MCDR2Restic-Restore-Direct",
        daemon=True,
    )
    app_runtime.restore.thread = thread
    thread.start()


def run_direct_restore_stage(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    clear_restore_tasks_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], int
    ],
):
    try:
        restic_cfg = get_restore_restic_config(session.cfg)
        update_restore_phase(app_runtime, RestorePhase.RESTORING)
        execute_restore_tasks(app_runtime, server, restic_cfg, session)
    except Exception as exc:
        finish_restore_workflow(
            app_runtime,
            server,
            server_tr(server, "warn.restore.direct_failed", error=exc),
            failure=True,
            expected_session=session,
        )
        return
    clear_completed_restore_tasks(server, session, clear_restore_tasks_func)
    finish_restore_workflow(
        app_runtime,
        server,
        server_tr(server, "info.restore.direct_completed"),
        expected_session=session,
    )


def acquire_restore_pre_backup_slot(app_runtime: PluginRuntime) -> bool:
    if not app_runtime.backup.lock.acquire(blocking=False):
        return False
    app_runtime.backup.label = BackupTrigger.RESTORE_PRE_BACKUP.value
    app_runtime.backup.cancel.clear()
    return True


def create_restore_safety_snapshot(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    invalidate_snapshot_cache_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], None
    ],
) -> str:
    cfg = build_pre_restore_backup_config(session.cfg)
    server.logger.info(
        server_tr(server, "log.restore.pre_backup_started", count=len(session.tasks))
    )
    safety_snapshot_id = run_backup_body(
        app_runtime, server, cfg, invalidate_snapshot_cache_func
    )
    if safety_snapshot_id:
        mark_restore_safety_snapshot(app_runtime, safety_snapshot_id)
        server.logger.info(
            server_tr(
                server,
                "info.restore.safety_snapshot_created",
                snapshot_id=safety_snapshot_id[:8],
            )
        )
        return safety_snapshot_id
    raise BackupProblem(i18n_key="error.restore.safety_snapshot_missing_id")


def build_pre_restore_backup_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    backup_cfg = copy.deepcopy(cfg)
    restic_cfg = get_config_section(backup_cfg, "restic")
    restic_cfg[RESTIC_CFG_BACKUP_COMMAND] = build_pre_restore_backup_command(backup_cfg)
    restic_cfg[RESTIC_CFG_MAINTENANCE_COMMANDS] = []
    backup_cfg["restic"] = restic_cfg
    return backup_cfg


def request_minecraft_stop_for_restore(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    safety_snapshot_id: str,
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]],
):
    mark_restore_stopping(app_runtime, safety_snapshot_id)
    set_mcdr_exit_after_stop(server, False)
    server.logger.info(server_tr(server, "log.restore.stop_server"))
    if server.stop():
        return
    try_force_save_on(
        app_runtime,
        server,
        server_tr(server, "reason.restore_stop_failed"),
        config_snapshot_provider,
    )
    finish_restore_workflow(
        app_runtime,
        server,
        server_tr(server, "error.restore.stop_server_failed"),
        failure=True,
        expected_session=session,
    )


def handle_restore_pre_stop_failure(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    exc: Exception,
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]],
):
    try:
        try_force_save_on(
            app_runtime,
            server,
            server_tr(server, "reason.restore_pre_backup_failed"),
            config_snapshot_provider,
        )
    except Exception as save_exc:
        server.logger.warning(
            server_tr(server, "warn.restore.save_on_recovery_failed", error=save_exc)
        )
    finish_restore_workflow(
        app_runtime,
        server,
        server_tr(server, "error.restore.pre_backup_failed", error=exc),
        failure=True,
        expected_session=session,
    )




def build_pre_restore_backup_command(cfg: Dict[str, Any]) -> List[str]:
    restic_cfg = get_config_section(cfg, "restic")
    args = normalize_command_args(restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []))
    tag = str(
        get_config_section(cfg, "restore").get(
            "pre_restore_backup_tag", "mcdr2restic-pre-restore"
        )
        or ""
    ).strip()
    if tag:
        args.extend([RESTIC_OPTION_TAG, tag])
    return args


def set_mcdr_exit_after_stop(server: PluginServerInterface, value: bool):
    setter = getattr(server, "set_exit_after_stop_flag", None)
    if callable(setter):
        try:
            setter(bool(value))
        except Exception as exc:
            server.logger.debug(
                server_tr(
                    server, "debug.restore.exit_after_stop_flag_failed", error=exc
                )
            )


def handle_restore_server_stop(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    server_return_code: int,
    clear_restore_tasks_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], int
    ],
) -> bool:
    session = get_restore_session(app_runtime)
    if session is None:
        return False
    if session.phase == RestorePhase.STARTING:
        finish_restore_workflow(
            app_runtime,
            server,
            build_restore_start_stopped_message(
                get_mcdr_language(server), server_return_code
            ),
            failure=True,
            expected_session=session,
        )
        return True
    if session.phase != RestorePhase.STOPPING:
        return False
    if server_return_code != 0:
        server.logger.warning(
            server_tr(
                server, "warn.restore.stop_return_code", return_code=server_return_code
            )
        )
    update_restore_phase(app_runtime, RestorePhase.RESTORING)
    thread = threading.Thread(
        target=run_restore_after_stop_stage,
        args=(app_runtime, server, clear_restore_tasks_func),
        name="MCDR2Restic-Restore-AfterStop",
        daemon=True,
    )
    app_runtime.restore.thread = thread
    thread.start()
    return True


def run_restore_after_stop_stage(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    clear_restore_tasks_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], int
    ],
):
    session = get_restore_session(app_runtime)
    if session is None:
        finish_restore_workflow(
            app_runtime,
            server,
            server_tr(server, "error.restore.state_missing_after_stop"),
            failure=True,
        )
        return

    result = execute_restore_after_stop(app_runtime, server, session)
    if not result.restore_error:
        server.logger.info(
            server_tr(server, "info.restore.tasks_completed_starting_server")
        )
        clear_completed_restore_tasks(server, session, clear_restore_tasks_func)

    mark_restore_starting(app_runtime, result)
    start_server_after_restore(app_runtime, server, session, result)


def execute_restore_after_stop(
    app_runtime: PluginRuntime, server: PluginServerInterface, session: RestoreSession
) -> RestoreStageResult:
    restic_cfg = get_restore_restic_config(session.cfg)
    try:
        execute_restore_tasks(app_runtime, server, restic_cfg, session)
        return RestoreStageResult()
    except Exception as exc:
        restore_error = str(exc)
        rollback_error = try_rollback_after_restore_failure(
            app_runtime, server, restic_cfg, session, restore_error
        )
        return RestoreStageResult(
            restore_error=restore_error, rollback_error=rollback_error
        )


def get_restore_restic_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return get_config_section(cfg, "restic")


def execute_restore_tasks(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession,
):
    deadline = make_restic_deadline(restic_cfg)
    command_root = command_root_from_config(session.cfg)
    ensure_default_restic_executable_available(server, restic_cfg)
    for task in session.tasks:
        assert_restore_task_target_allowed(server, restic_cfg, task)
        result = run_restic_command_with_lock_recovery(
            ResticOperationContext(
                app_runtime, server, restic_cfg, deadline, command_root=command_root
            ),
            build_restore_command(restic_cfg, task),
            RESTIC_COMMAND_RESTORE,
        )
        assert_restic_success(
            restic_cfg, result, command_root, server.logger
        )
        log_restore_task_completed(server, task)


def assert_restore_task_target_allowed(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    task: Dict[str, Any],
):
    """Re-check persisted targets against the current config before executing."""

    target = str(task.get("target_directory") or "").strip()
    if not target:
        return
    assert_restore_target_allowed(
        target, restic_cfg, data_dir=server.get_data_folder()
    )


def log_restore_task_completed(
    server: PluginServerInterface, task: Dict[str, Any]
):
    target_directory = str(task.get("target_directory") or "").strip()
    include_path = str(task.get("include_path") or "")
    if target_directory:
        message = server_tr(
            server,
            "info.restore.task_completed_to",
            task_id=task.get("id"),
            snapshot=task.get("snapshot"),
            target=target_directory,
        )
    elif include_path and include_path != "/":
        message = server_tr(
            server,
            "info.restore.task_completed",
            task_id=task.get("id"),
            snapshot=task.get("snapshot"),
            include_path=include_path,
        )
    else:
        # 整份快照任务 include_path 恒为 "/"，单列一个不带路径的文案
        message = server_tr(
            server,
            "info.restore.task_completed_full",
            task_id=task.get("id"),
            snapshot=task.get("snapshot"),
        )
    server.logger.info(message)


def try_rollback_after_restore_failure(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession,
    restore_error: str,
) -> str:
    if session.skip_safety_snapshot:
        server.logger.warning(
            server_tr(
                server,
                "warn.restore.skip_safety_no_rollback",
                error=restore_error,
            )
        )
        return ""
    server.logger.warning(
        server_tr(
            server, "warn.restore.restore_stage_failed_rollback", error=restore_error
        )
    )
    return rollback_to_safety_snapshot(app_runtime, server, restic_cfg, session)


def clear_completed_restore_tasks(
    server: PluginServerInterface,
    session: RestoreSession,
    clear_restore_tasks_func: Callable[
        [PluginServerInterface, Dict[str, Any], str], int
    ],
):
    try:
        clear_restore_tasks_func(server, session.snapshot_cfg, session.cache_key)
    except Exception as exc:
        server.logger.warning(
            server_tr(server, "warn.restore.clear_tasks_failed", error=exc)
        )


def start_server_after_restore(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    result: RestoreStageResult,
):
    if not server.start():
        finish_restore_workflow(
            app_runtime,
            server,
            build_restore_start_failure_message(get_mcdr_language(server), result),
            failure=True,
            expected_session=session,
        )
        return
    start_restore_startup_watchdog(app_runtime, server, session, result)


def start_restore_startup_watchdog(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    result: RestoreStageResult,
):
    timeout_seconds = get_restore_start_timeout_seconds(session)
    thread = threading.Thread(
        target=watch_restore_startup_timeout,
        args=(app_runtime, server, session, result, timeout_seconds),
        name="MCDR2Restic-Restore-StartupWatchdog",
        daemon=True,
    )
    app_runtime.restore.thread = thread
    thread.start()


def watch_restore_startup_timeout(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    expected_session: RestoreSession,
    result: RestoreStageResult,
    timeout_seconds: int,
):
    time.sleep(timeout_seconds)
    finish_restore_start_timeout_if_still_starting(
        app_runtime,
        server,
        result,
        timeout_seconds,
        expected_session=expected_session,
    )


def finish_restore_start_timeout_if_still_starting(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    result: RestoreStageResult,
    timeout_seconds: int,
    expected_session: Optional[RestoreSession] = None,
) -> bool:
    session = get_restore_session(app_runtime)
    if session is None or session.phase != RestorePhase.STARTING:
        return False
    if expected_session is not None and session is not expected_session:
        return False
    message = build_restore_start_timeout_message(
        get_mcdr_language(server), result, timeout_seconds
    )
    return finish_restore_workflow(
        app_runtime,
        server,
        message,
        failure=True,
        expected_session=session,
    )


def get_restore_start_timeout_seconds(session: Optional[RestoreSession]) -> int:
    if session is None:
        return 120
    restore_cfg = (
        session.cfg.get("restore", {})
        if isinstance(session.cfg.get("restore"), dict)
        else {}
    )
    try:
        return max(1, int(restore_cfg.get("start_timeout_seconds", 120)))
    except Exception:
        return 120


def build_restore_start_failure_message(
    language: str, result: RestoreStageResult
) -> str:
    return append_restore_result_details(
        language, tr(language, "error.restore.start_failed"), result
    )


def build_restore_start_timeout_message(
    language: str, result: RestoreStageResult, timeout_seconds: int
) -> str:
    return append_restore_result_details(
        language,
        tr(language, "error.restore.start_timeout", timeout_seconds=timeout_seconds),
        result,
    )


def build_restore_start_stopped_message(language: str, server_return_code: int) -> str:
    return tr(
        language, "error.restore.start_stopped", return_code=server_return_code
    )


def rollback_to_safety_snapshot(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession,
) -> str:
    snapshot_id = str(session.safety_snapshot_id or "").strip()
    if not snapshot_id:
        message = server_tr(server, "error.restore.rollback_snapshot_missing")
        server.logger.error(message)
        return message
    try:
        deadline = make_restic_deadline(restic_cfg)
        command_root = command_root_from_config(session.cfg)
        result = run_restic_command_with_lock_recovery(
            ResticOperationContext(
                app_runtime, server, restic_cfg, deadline, command_root=command_root
            ),
            build_full_restore_command(restic_cfg, snapshot_id),
            RESTIC_COMMAND_ROLLBACK,
        )
        assert_restic_success(
            restic_cfg, result, command_root, server.logger
        )
        server.logger.info(
            server_tr(
                server, "info.restore.rollback_completed", snapshot_id=snapshot_id[:8]
            )
        )
        return ""
    except Exception as exc:
        message = str(exc)
        server.logger.error(
            server_tr(server, "error.restore.rollback_failed", error=message)
            + "\n"
            + traceback.format_exc()
        )
        return message


def build_restore_command(
    restic_cfg: Dict[str, Any], task: Dict[str, Any]
) -> List[str]:
    snapshot = normalize_restore_snapshot(task.get("snapshot"))
    target_directory = str(task.get("target_directory") or "").strip()
    args = build_full_restore_command(restic_cfg, snapshot, target_directory)
    item_type = str(task.get("item_type") or "")
    include_path = str(task.get("include_path") or "").strip()
    if item_type in ("file", "folder"):
        args.extend([RESTIC_OPTION_INCLUDE, include_path])
    elif item_type != "full":
        raise BackupProblem(
            i18n_key="error.restore.unknown_task_type", item_type=item_type
        )
    return args


def build_full_restore_command(
    restic_cfg: Dict[str, Any], snapshot: str, target_directory: str = ""
) -> List[str]:
    target = target_directory or get_restic_working_directory(restic_cfg)
    return [
        RESTIC_COMMAND_RESTORE,
        normalize_restore_snapshot(snapshot),
        RESTIC_OPTION_TARGET,
        target,
    ]


def handle_restore_server_startup(
    app_runtime: PluginRuntime, server: PluginServerInterface
):
    session = get_restore_session(app_runtime)
    if session is None or session.phase != RestorePhase.STARTING:
        return
    error = str(session.error or "")
    rollback_error = str(session.rollback_error or "")
    if error:
        if session.skip_safety_snapshot:
            message = server_tr(
                server,
                "warn.restore.completed_with_restart_skipped_safety",
                restore_error=error,
            )
        elif rollback_error:
            message = server_tr(
                server,
                "warn.restore.completed_with_restart_and_failed_rollback",
                restore_error=error,
                rollback_error=rollback_error,
            )
        else:
            message = server_tr(
                server,
                "warn.restore.completed_with_restart_after_rollback",
                restore_error=error,
            )
        finish_restore_workflow(
            app_runtime, server, message, failure=True, expected_session=session
        )
    else:
        finish_restore_workflow(
            app_runtime,
            server,
            server_tr(server, "info.restore.completed"),
            expected_session=session,
        )


def append_restore_result_details(
    language: str, message: str, result: RestoreStageResult
) -> str:
    text = str(message)
    if result.restore_error:
        text = "{}; {}".format(
            text,
            tr(language, "detail.restore.restore_error", error=result.restore_error),
        )
    if result.rollback_error:
        text = "{}; {}".format(
            text,
            tr(language, "detail.restore.rollback_error", error=result.rollback_error),
        )
    return text
