# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import os
import re
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

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
    normalize_filesystem_path,
    path_contains_or_equals,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_BACKUP_COMMAND,
    RESTIC_CFG_MAINTENANCE_COMMANDS,
    RESTIC_CFG_WORKING_DIRECTORY,
    RESTIC_COMMAND_RESTORE,
    RESTIC_COMMAND_ROLLBACK,
    RESTIC_OPTION_INCLUDE,
    RESTIC_OPTION_TAG,
    RESTIC_OPTION_TARGET,
)
from mcdr2restic.restic.restic_download import (
    ensure_default_restic_executable_available,
)
from mcdr2restic.minecraft.minecraft_service import try_force_save_on
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_service import make_restic_deadline, run_backup_body
from mcdr2restic.core.utils import now_text


RESTORE_SNAPSHOT_WHITESPACE_PATTERN = re.compile(r"\s")
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


def normalize_restore_snapshot(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise BackupProblem(i18n_key="error.restore.snapshot_empty")
    if RESTORE_SNAPSHOT_WHITESPACE_PATTERN.search(text):
        raise BackupProblem(i18n_key="error.restore.snapshot_whitespace")
    return text


def normalize_restore_include_path(
    value: Any, restic_cfg: Dict[str, Any], command_root: str
) -> str:
    raw = str(value or "").strip().strip("\"'")
    if not raw:
        raise BackupProblem(i18n_key="error.restore.path_empty")

    relative = resolve_restore_relative_path(raw, restic_cfg)
    parts = normalize_restore_path_parts(relative)
    if not parts:
        raise BackupProblem(
            i18n_key="error.restore.use_full_snapshot_command",
            command_root=command_root,
        )
    return "/" + "/".join(parts)


def resolve_restore_relative_path(raw: str, restic_cfg: Dict[str, Any]) -> str:
    workdir = get_restic_working_directory(restic_cfg)
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if os.path.isabs(expanded) and path_contains_or_equals(workdir, expanded):
        absolute = normalize_filesystem_path(expanded)
        return os.path.relpath(absolute, workdir)
    if is_strict_filesystem_absolute_path(expanded):
        raise BackupProblem(i18n_key="error.restore.path_outside_workdir", path=raw)
    return raw


def normalize_restore_path_parts(relative_path: str) -> List[str]:
    relative = str(relative_path or "")
    relative = relative.replace("\\", "/").strip()
    while relative.startswith("/"):
        relative = relative[1:]
    if relative.startswith("./"):
        relative = relative[2:]

    parts: List[str] = []
    for part in relative.split("/"):
        part = part.strip()
        if not part or part == ".":
            continue
        if part == "..":
            raise BackupProblem(i18n_key="error.restore.path_parent_reference")
        parts.append(part)
    return parts


def get_restic_working_directory(restic_cfg: Dict[str, Any]) -> str:
    return normalize_filesystem_path(
        str(restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or os.getcwd())
    )


def is_strict_filesystem_absolute_path(path: str) -> bool:
    text = str(path or "")
    if WINDOWS_ABSOLUTE_PATH_PATTERN.match(text):
        return True
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    return False


def get_restore_apply_rejection(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    translate_or_language: Any,
    tasks: List[Dict[str, Any]],
    backup_running_provider: Callable[[PluginRuntime], bool],
    mc_ready_provider: Callable[[PluginRuntime, PluginServerInterface], bool],
) -> str:
    translate = normalize_translate(translate_or_language)
    if not tasks:
        return translate("error.restore.no_tasks")
    if backup_running_provider(app_runtime):
        return translate("error.restore.backup_running")
    if not mc_ready_provider(app_runtime, server):
        return translate("error.restore.minecraft_not_ready")
    return ""


def create_restore_session(
    tasks: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
    language: str,
) -> RestoreSession:
    return RestoreSession(
        tasks=tasks,
        cfg=cfg,
        snapshot_cfg=snapshot_cfg,
        cache_key=cache_key,
        language=language,
        phase=RestorePhase.PRE_BACKUP,
        started_at=now_text(),
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


def mark_restore_rollback_started(app_runtime: PluginRuntime):
    update_restore_phase(app_runtime, RestorePhase.ROLLBACK)


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
        release_restore_pre_backup_slot(app_runtime)


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
        app_runtime, server, cfg, "pre-restore", invalidate_snapshot_cache_func
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
    restic_cfg = (
        backup_cfg.get("restic", {})
        if isinstance(backup_cfg.get("restic"), dict)
        else {}
    )
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
        app_runtime, server, "restore stop failed", config_snapshot_provider
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
            app_runtime, server, "restore pre-backup failed", config_snapshot_provider
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


def release_restore_pre_backup_slot(app_runtime: PluginRuntime):
    app_runtime.backup.label = None
    if app_runtime.backup.lock.locked():
        app_runtime.backup.lock.release()


def build_pre_restore_backup_command(cfg: Dict[str, Any]) -> List[str]:
    restic_cfg = cfg.get("restic", {}) if isinstance(cfg.get("restic"), dict) else {}
    args = normalize_command_args(restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []))
    tag = str(
        cfg.get("restore", {}).get("pre_restore_backup_tag", "mcdr2restic-pre-restore")
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
            server_tr(server, "warn.restore.stop_return_code", code=server_return_code)
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
    return cfg.get("restic", {}) if isinstance(cfg.get("restic"), dict) else {}


def execute_restore_tasks(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession,
):
    deadline = make_restic_deadline(restic_cfg)
    ensure_default_restic_executable_available(server, restic_cfg)
    for task in session.tasks:
        result = run_restic_command(
            app_runtime,
            restic_cfg,
            build_restore_command(restic_cfg, task),
            RESTIC_COMMAND_RESTORE,
            deadline,
        )
        assert_restic_success(restic_cfg, result)
        server.logger.info(
            server_tr(
                server,
                "info.restore.task_completed",
                task_id=task.get("id"),
                snapshot=task.get("snapshot"),
                include_path=task.get("include_path"),
            )
        )
    server.logger.info(
        server_tr(server, "info.restore.tasks_completed_starting_server")
    )


def try_rollback_after_restore_failure(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession,
    restore_error: str,
) -> str:
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
        language, "error.restore.start_stopped", server_return_code=server_return_code
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
    mark_restore_rollback_started(app_runtime)
    try:
        deadline = make_restic_deadline(restic_cfg)
        result = run_restic_command(
            app_runtime,
            restic_cfg,
            build_full_restore_command(restic_cfg, snapshot_id),
            RESTIC_COMMAND_ROLLBACK,
            deadline,
        )
        assert_restic_success(restic_cfg, result)
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
    args = build_full_restore_command(restic_cfg, snapshot)
    item_type = str(task.get("item_type") or "")
    include_path = str(task.get("include_path") or "").strip()
    if item_type in ("file", "folder"):
        args.extend([RESTIC_OPTION_INCLUDE, include_path])
    elif item_type != "full":
        raise BackupProblem(
            i18n_key="error.restore.unknown_task_type", item_type=item_type
        )
    return args


def build_full_restore_command(restic_cfg: Dict[str, Any], snapshot: str) -> List[str]:
    target = get_restic_working_directory(restic_cfg)
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
        if rollback_error:
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
