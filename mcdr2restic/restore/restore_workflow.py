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

from mcdr2restic.core.models import BackupProblem, RestoreSession, RestoreStageResult
from mcdr2restic.core.presentation import localized_text
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
from mcdr2restic.restic.restic_download import ensure_default_restic_executable_available
from mcdr2restic.minecraft.minecraft_service import try_force_save_on
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_service import make_restic_deadline, run_backup_body
from mcdr2restic.core.utils import now_text


def normalize_restore_snapshot(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        raise BackupProblem('snapshot 不能为空')
    if re.search(r'\s', text):
        raise BackupProblem('snapshot 不能包含空白字符')
    return text

def normalize_restore_include_path(value: Any, restic_cfg: Dict[str, Any], command_root: str) -> str:
    raw = str(value or '').strip().strip('"\'')
    if not raw:
        raise BackupProblem('恢复路径不能为空')

    relative = resolve_restore_relative_path(raw, restic_cfg)
    parts = normalize_restore_path_parts(relative)
    if not parts:
        raise BackupProblem('请使用 {} restore <snapshot> 恢复整份快照'.format(command_root))
    return '/' + '/'.join(parts)

def resolve_restore_relative_path(raw: str, restic_cfg: Dict[str, Any]) -> str:
    workdir = get_restic_working_directory(restic_cfg)
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if os.path.isabs(expanded) and path_contains_or_equals(workdir, expanded):
        absolute = normalize_filesystem_path(expanded)
        return os.path.relpath(absolute, workdir)
    if is_strict_filesystem_absolute_path(expanded):
        raise BackupProblem('恢复路径必须位于 restic 工作目录内: {}'.format(raw))
    return raw

def normalize_restore_path_parts(relative_path: str) -> List[str]:
    relative = str(relative_path or '')
    relative = relative.replace('\\', '/').strip()
    while relative.startswith('/'):
        relative = relative[1:]
    if relative.startswith('./'):
        relative = relative[2:]

    parts: List[str] = []
    for part in relative.split('/'):
        part = part.strip()
        if not part or part == '.':
            continue
        if part == '..':
            raise BackupProblem('恢复路径不能包含 ..')
        parts.append(part)
    return parts

def get_restic_working_directory(restic_cfg: Dict[str, Any]) -> str:
    return normalize_filesystem_path(str(restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or os.getcwd()))

def is_strict_filesystem_absolute_path(path: str) -> bool:
    text = str(path or '')
    if re.match(r'^[A-Za-z]:[\\/]', text):
        return True
    if text.startswith('\\\\') or text.startswith('//'):
        return True
    return False

def get_restore_apply_rejection(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    language: str,
    tasks: List[Dict[str, Any]],
    backup_running_provider: Callable[[PluginRuntime], bool],
    mc_ready_provider: Callable[[PluginRuntime, PluginServerInterface], bool],
) -> str:
    if not tasks:
        return localized_text(language, '暂无恢复任务', 'No restore tasks')
    if backup_running_provider(app_runtime):
        return localized_text(language, '当前已有备份在执行，暂不能开始恢复', 'A backup is running; restore cannot start now')
    if not mc_ready_provider(app_runtime, server):
        return localized_text(language, 'Minecraft 服务端尚未确认正常运行，拒绝恢复', 'Minecraft is not ready; restore refused')
    return ''

def create_restore_session(
    tasks: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
    language: str
) -> RestoreSession:
    return RestoreSession(
        tasks=tasks,
        cfg=cfg,
        snapshot_cfg=snapshot_cfg,
        cache_key=cache_key,
        language=language,
        phase='pre_backup',
        started_at=now_text()
    )

def start_restore_pre_stop_thread(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    invalidate_snapshot_cache_func: Callable[[PluginServerInterface, Dict[str, Any], str], None],
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]]
):
    thread = threading.Thread(
        target=run_restore_pre_stop_stage,
        args=(app_runtime, server, invalidate_snapshot_cache_func, config_snapshot_provider),
        name='MCDR2Restic-Restore-PreStop',
        daemon=True
    )
    app_runtime.restore.thread = thread
    thread.start()

def set_restore_session(app_runtime: PluginRuntime, session: Optional[RestoreSession]):
    with app_runtime.restore.state_lock:
        app_runtime.restore.session = session

def get_restore_session(app_runtime: PluginRuntime) -> Optional[RestoreSession]:
    with app_runtime.restore.state_lock:
        return app_runtime.restore.session

def update_restore_phase(app_runtime: PluginRuntime, phase: str):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.phase = phase

def mark_restore_safety_snapshot(app_runtime: PluginRuntime, safety_snapshot_id: str):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.safety_snapshot_id = safety_snapshot_id

def mark_restore_stopping(app_runtime: PluginRuntime, safety_snapshot_id: str):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.phase = 'stopping'
            app_runtime.restore.session.safety_snapshot_id = safety_snapshot_id

def mark_restore_starting(app_runtime: PluginRuntime, result: RestoreStageResult):
    with app_runtime.restore.state_lock:
        if app_runtime.restore.session is not None:
            app_runtime.restore.session.phase = 'starting'
            app_runtime.restore.session.error = result.restore_error
            app_runtime.restore.session.rollback_error = result.rollback_error

def mark_restore_rollback_started(app_runtime: PluginRuntime):
    update_restore_phase(app_runtime, 'rollback')

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
    invalidate_snapshot_cache_func: Callable[[PluginServerInterface, Dict[str, Any], str], None],
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]]
):
    session = get_restore_session(app_runtime)
    if session is None:
        finish_restore_workflow(app_runtime, server, '恢复流程状态丢失', failure=True)
        return
    if not acquire_restore_pre_backup_slot(app_runtime):
        finish_restore_workflow(
            app_runtime,
            server,
            '恢复流程无法创建保护快照：当前已有备份在执行',
            failure=True,
            expected_session=session,
        )
        return

    try:
        safety_snapshot_id = create_restore_safety_snapshot(app_runtime, server, session, invalidate_snapshot_cache_func)
        request_minecraft_stop_for_restore(app_runtime, server, session, safety_snapshot_id, config_snapshot_provider)
    except Exception as exc:
        handle_restore_pre_stop_failure(app_runtime, server, session, exc, config_snapshot_provider)
    finally:
        release_restore_pre_backup_slot(app_runtime)

def acquire_restore_pre_backup_slot(app_runtime: PluginRuntime) -> bool:
    if not app_runtime.backup.lock.acquire(blocking=False):
        return False
    app_runtime.backup.label = 'restore-pre-backup'
    app_runtime.backup.cancel.clear()
    return True

def create_restore_safety_snapshot(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    invalidate_snapshot_cache_func: Callable[[PluginServerInterface, Dict[str, Any], str], None]
) -> str:
    cfg = build_pre_restore_backup_config(session.cfg)
    server.logger.info('恢复流程开始：创建恢复前保护快照，共 {} 个恢复任务'.format(len(session.tasks)))
    safety_snapshot_id = run_backup_body(app_runtime, server, cfg, 'pre-restore', invalidate_snapshot_cache_func)
    if safety_snapshot_id:
        mark_restore_safety_snapshot(app_runtime, safety_snapshot_id)
        server.logger.info('恢复前保护快照已创建: {}'.format(safety_snapshot_id[:8]))
        return safety_snapshot_id
    raise BackupProblem('保护快照已完成，但 restic --json 未返回 snapshot_id，已拒绝继续恢复')

def build_pre_restore_backup_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    backup_cfg = copy.deepcopy(cfg)
    restic_cfg = backup_cfg.get('restic', {}) if isinstance(backup_cfg.get('restic'), dict) else {}
    restic_cfg[RESTIC_CFG_BACKUP_COMMAND] = build_pre_restore_backup_command(backup_cfg)
    restic_cfg[RESTIC_CFG_MAINTENANCE_COMMANDS] = []
    backup_cfg['restic'] = restic_cfg
    return backup_cfg

def request_minecraft_stop_for_restore(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    safety_snapshot_id: str,
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]]
):
    mark_restore_stopping(app_runtime, safety_snapshot_id)
    set_mcdr_exit_after_stop(server, False)
    server.logger.info('保护快照完成，正在通过 MCDR 停止 Minecraft 服务端')
    if server.stop():
        return
    try_force_save_on(app_runtime, server, 'restore stop failed', config_snapshot_provider)
    finish_restore_workflow(
        app_runtime,
        server,
        '恢复流程无法停止 Minecraft 服务端',
        failure=True,
        expected_session=session,
    )

def handle_restore_pre_stop_failure(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    exc: Exception,
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]]
):
    try:
        try_force_save_on(app_runtime, server, 'restore pre-backup failed', config_snapshot_provider)
    except Exception as save_exc:
        server.logger.warning('恢复流程失败后恢复 save-on 也失败: {}'.format(save_exc))
    finish_restore_workflow(
        app_runtime,
        server,
        '恢复流程在保护快照阶段失败: {}'.format(exc),
        failure=True,
        expected_session=session,
    )

def release_restore_pre_backup_slot(app_runtime: PluginRuntime):
    app_runtime.backup.label = None
    if app_runtime.backup.lock.locked():
        app_runtime.backup.lock.release()

def build_pre_restore_backup_command(cfg: Dict[str, Any]) -> List[str]:
    restic_cfg = cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}
    args = normalize_command_args(restic_cfg.get(RESTIC_CFG_BACKUP_COMMAND, []))
    tag = str(cfg.get('restore', {}).get('pre_restore_backup_tag', 'mcdr2restic-pre-restore') or '').strip()
    if tag:
        args.extend([RESTIC_OPTION_TAG, tag])
    return args

def set_mcdr_exit_after_stop(server: PluginServerInterface, value: bool):
    setter = getattr(server, 'set_exit_after_stop_flag', None)
    if callable(setter):
        try:
            setter(bool(value))
        except Exception as exc:
            server.logger.debug('设置 MCDR exit-after-stop 标志失败: {}'.format(exc))

def handle_restore_server_stop(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    server_return_code: int,
    clear_restore_tasks_func: Callable[[PluginServerInterface, Dict[str, Any], str], int]
) -> bool:
    session = get_restore_session(app_runtime)
    if session is None:
        return False
    if session.phase == 'starting':
        finish_restore_workflow(
            app_runtime,
            server,
            build_restore_start_stopped_message(server_return_code),
            failure=True,
            expected_session=session,
        )
        return True
    if session.phase != 'stopping':
        return False
    if server_return_code != 0:
        server.logger.warning('恢复流程检测到 Minecraft 停止返回码非 0: {}'.format(server_return_code))
    update_restore_phase(app_runtime, 'restoring')
    thread = threading.Thread(
        target=run_restore_after_stop_stage,
        args=(app_runtime, server, clear_restore_tasks_func),
        name='MCDR2Restic-Restore-AfterStop',
        daemon=True
    )
    app_runtime.restore.thread = thread
    thread.start()
    return True

def run_restore_after_stop_stage(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    clear_restore_tasks_func: Callable[[PluginServerInterface, Dict[str, Any], str], int]
):
    session = get_restore_session(app_runtime)
    if session is None:
        finish_restore_workflow(app_runtime, server, '恢复流程状态丢失，无法执行 restore', failure=True)
        return

    result = execute_restore_after_stop(app_runtime, server, session)
    if not result.restore_error:
        clear_completed_restore_tasks(server, session, clear_restore_tasks_func)

    mark_restore_starting(app_runtime, result)
    start_server_after_restore(app_runtime, server, session, result)

def execute_restore_after_stop(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession
) -> RestoreStageResult:
    restic_cfg = get_restore_restic_config(session.cfg)
    try:
        execute_restore_tasks(app_runtime, server, restic_cfg, session)
        return RestoreStageResult()
    except Exception as exc:
        restore_error = str(exc)
        rollback_error = try_rollback_after_restore_failure(app_runtime, server, restic_cfg, session, restore_error)
        return RestoreStageResult(restore_error=restore_error, rollback_error=rollback_error)

def get_restore_restic_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}

def execute_restore_tasks(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession
):
    deadline = make_restic_deadline(restic_cfg)
    ensure_default_restic_executable_available(server, restic_cfg)
    for task in session.tasks:
        result = run_restic_command(
            app_runtime,
            restic_cfg,
            build_restore_command(restic_cfg, task),
            RESTIC_COMMAND_RESTORE,
            deadline
        )
        assert_restic_success(restic_cfg, result)
        server.logger.info('恢复任务 #{} 完成: {} -> {}'.format(task.get('id'), task.get('snapshot'), task.get('include_path')))
    server.logger.info('恢复任务全部完成，正在启动 Minecraft 服务端')

def try_rollback_after_restore_failure(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession,
    restore_error: str
) -> str:
    server.logger.warning('恢复流程执行 restore 阶段失败，将立即恢复到保护快照: {}'.format(restore_error))
    return rollback_to_safety_snapshot(app_runtime, server, restic_cfg, session)

def clear_completed_restore_tasks(
    server: PluginServerInterface,
    session: RestoreSession,
    clear_restore_tasks_func: Callable[[PluginServerInterface, Dict[str, Any], str], int]
):
    try:
        clear_restore_tasks_func(server, session.snapshot_cfg, session.cache_key)
    except Exception as exc:
        server.logger.warning('恢复任务已完成，但清空任务队列失败，请手动检查 restore list: {}'.format(exc))

def start_server_after_restore(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    result: RestoreStageResult
):
    if not server.start():
        finish_restore_workflow(
            app_runtime,
            server,
            build_restore_start_failure_message(result),
            failure=True,
            expected_session=session,
        )
        return
    start_restore_startup_watchdog(app_runtime, server, session, result)


def start_restore_startup_watchdog(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    session: RestoreSession,
    result: RestoreStageResult
):
    timeout_seconds = get_restore_start_timeout_seconds(session)
    thread = threading.Thread(
        target=watch_restore_startup_timeout,
        args=(app_runtime, server, session, result, timeout_seconds),
        name='MCDR2Restic-Restore-StartupWatchdog',
        daemon=True
    )
    app_runtime.restore.thread = thread
    thread.start()


def watch_restore_startup_timeout(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    expected_session: RestoreSession,
    result: RestoreStageResult,
    timeout_seconds: int
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
    if session is None or session.phase != 'starting':
        return False
    if expected_session is not None and session is not expected_session:
        return False
    message = build_restore_start_timeout_message(result, timeout_seconds)
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
    restore_cfg = session.cfg.get('restore', {}) if isinstance(session.cfg.get('restore'), dict) else {}
    try:
        return max(1, int(restore_cfg.get('start_timeout_seconds', 120)))
    except Exception:
        return 120

def build_restore_start_failure_message(result: RestoreStageResult) -> str:
    message = '恢复流程已结束，但启动 Minecraft 服务端失败'
    if result.restore_error:
        message = '{}；restore 阶段错误: {}'.format(message, result.restore_error)
    if result.rollback_error:
        message = '{}；保护快照回滚错误: {}'.format(message, result.rollback_error)
    return message


def build_restore_start_timeout_message(result: RestoreStageResult, timeout_seconds: int) -> str:
    message = '恢复流程已结束，但 Minecraft 启动在 {} 秒内未完成'.format(timeout_seconds)
    if result.restore_error:
        message = '{}；restore 阶段错误: {}'.format(message, result.restore_error)
    if result.rollback_error:
        message = '{}；保护快照回滚错误: {}'.format(message, result.rollback_error)
    return message


def build_restore_start_stopped_message(server_return_code: int) -> str:
    return '恢复流程等待 Minecraft 启动时服务端再次停止，返回码: {}'.format(server_return_code)

def rollback_to_safety_snapshot(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    session: RestoreSession
) -> str:
    snapshot_id = str(session.safety_snapshot_id or '').strip()
    if not snapshot_id:
        message = '恢复失败后无法回滚：保护快照 ID 丢失'
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
            deadline
        )
        assert_restic_success(restic_cfg, result)
        server.logger.info('已恢复到恢复前保护快照: {}'.format(snapshot_id[:8]))
        return ''
    except Exception as exc:
        message = str(exc)
        server.logger.error('恢复到保护快照失败: {}\n{}'.format(message, traceback.format_exc()))
        return message

def build_restore_command(restic_cfg: Dict[str, Any], task: Dict[str, Any]) -> List[str]:
    snapshot = normalize_restore_snapshot(task.get('snapshot'))
    args = build_full_restore_command(restic_cfg, snapshot)
    item_type = str(task.get('item_type') or '')
    include_path = str(task.get('include_path') or '').strip()
    if item_type in ('file', 'folder'):
        args.extend([RESTIC_OPTION_INCLUDE, include_path])
    elif item_type != 'full':
        raise BackupProblem('未知恢复任务类型: {}'.format(item_type))
    return args

def build_full_restore_command(restic_cfg: Dict[str, Any], snapshot: str) -> List[str]:
    target = get_restic_working_directory(restic_cfg)
    return [RESTIC_COMMAND_RESTORE, normalize_restore_snapshot(snapshot), RESTIC_OPTION_TARGET, target]

def handle_restore_server_startup(app_runtime: PluginRuntime, server: PluginServerInterface):
    session = get_restore_session(app_runtime)
    if session is None or session.phase != 'starting':
        return
    error = str(session.error or '')
    rollback_error = str(session.rollback_error or '')
    if error:
        if rollback_error:
            message = '恢复流程结束，Minecraft 已重新启动；restore 阶段曾失败: {}；保护快照回滚也失败: {}'.format(error, rollback_error)
        else:
            message = '恢复流程结束，Minecraft 已重新启动；restore 阶段曾失败，已回滚到恢复前保护快照: {}'.format(error)
        finish_restore_workflow(app_runtime, server, message, failure=True, expected_session=session)
    else:
        finish_restore_workflow(app_runtime, server, '恢复流程完成，Minecraft 已重新启动', expected_session=session)
