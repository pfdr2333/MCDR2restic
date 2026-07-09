# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.models import BackupCanceled, BackupProblem
from mcdr2restic.restic.restic_termination import TerminateResult, terminate_process, warn_if_termination_failed
from mcdr2restic.core.runtime import PluginRuntime


def execute_mc_command(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    command: str,
    label: str
):
    if not command:
        return
    if not server_is_running(app_runtime, server):
        raise BackupProblem('执行 {} 前检测到 Minecraft 服务端不在运行状态'.format(label))
    server.logger.info('执行 Minecraft 命令: {}'.format(label))
    try:
        server.execute(command)
    except Exception as exc:
        raise BackupProblem('执行 Minecraft 命令 {} 失败: {}'.format(label, exc))


def try_force_save_on(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface],
    reason: str,
    config_snapshot_provider: Callable[[PluginRuntime], Dict[str, Any]]
):
    if server is None:
        return
    cfg = config_snapshot_provider(app_runtime)
    command = cfg.get('minecraft', {}).get('save_on_command', 'save-on')
    if not command or not server_is_running(app_runtime, server):
        return
    try:
        server.logger.info('尝试恢复自动保存 save-on ({})'.format(reason))
        server.execute(command)
        wait = float(cfg.get('minecraft', {}).get('wait_after_save_on_seconds', 1))
        if wait > 0:
            time.sleep(min(wait, 5.0))
    except Exception as exc:
        raise BackupProblem('执行 save-on 失败: {}'.format(exc))


def request_cancel_current_backup(app_runtime: PluginRuntime, reason: str) -> Optional[TerminateResult]:
    app_runtime.backup.cancel.set()
    process = app_runtime.backup.current_process
    result = None
    if process is not None and process.poll() is None:
        result = terminate_process(process)
    if app_runtime.service.server is not None:
        warn_if_termination_failed(
            app_runtime.service.server.logger,
            '停止当前备份的 restic 进程',
            result
        )
        app_runtime.service.server.logger.warning('已请求停止当前备份: {}'.format(reason))
    return result


def is_backup_running(app_runtime: PluginRuntime) -> bool:
    return app_runtime.backup.lock.locked()


def check_canceled(app_runtime: PluginRuntime):
    if app_runtime.backup.cancel.is_set():
        raise BackupCanceled('收到停止请求')


def sleep_or_cancel(app_runtime: PluginRuntime, seconds: float):
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        check_canceled(app_runtime)
        time.sleep(min(0.2, end - time.monotonic()))


def server_is_running(app_runtime: PluginRuntime, server: Optional[PluginServerInterface]) -> bool:
    if server is None:
        return False
    startup_method = getattr(server, 'is_server_startup', None)
    if callable(startup_method):
        startup_result = try_call_bool(server, startup_method, 'is_server_startup')
        if startup_result is True:
            return True
    if app_runtime.service.server_ready:
        return True
    running_method = getattr(server, 'is_server_running', None)
    if callable(running_method):
        running_result = try_call_bool(server, running_method, 'is_server_running')
        if running_result is not None:
            return running_result
    return bool(app_runtime.service.server_ready)


def is_mc_ready(app_runtime: PluginRuntime, server: Optional[PluginServerInterface]) -> bool:
    return server_is_running(app_runtime, server)


def try_call_bool(
    server: PluginServerInterface,
    func: Callable[[], Any],
    label: str,
) -> Optional[bool]:
    try:
        return bool(func())
    except Exception as exc:
        debug_server_probe_failure(server, label, exc)
        return None


def debug_server_probe_failure(server: PluginServerInterface, label: str, exc: Exception):
    logger = getattr(server, 'logger', None)
    debug = getattr(logger, 'debug', None)
    if callable(debug):
        debug('MCDR 状态探测 {} 失败，已使用兼容降级: {}'.format(label, exc))
