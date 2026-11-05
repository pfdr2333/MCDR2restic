# -*- coding: utf-8 -*-
"""Recover from stale local restic repository locks left by dead processes."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import os
import platform
import re
import socket
from dataclasses import dataclass
from typing import Any, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr, tr_error
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import BackupProblem, ResticCommandResult
from mcdr2restic.restic.restic_constants import RESTIC_COMMAND_UNLOCK
from mcdr2restic.restic.restic_context import ResticOperationContext
from mcdr2restic.restic.restic_result import assert_restic_success
from mcdr2restic.restic.restic_runner import run_restic_command


LOCKED_REPOSITORY_PATTERN = re.compile(
    r"repository is already locked(?: exclusively)? by PID (?P<pid>\d+) on (?P<host>\S+)",
    re.IGNORECASE,
)
LOCKED_REPOSITORY_HINT = "repository is already locked"
WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WINDOWS_SYNCHRONIZE = 0x00100000
WINDOWS_ERROR_ACCESS_DENIED = 5
WINDOWS_ERROR_INVALID_PARAMETER = 87


@dataclass(frozen=True)
class ResticLockInfo:
    """Metadata parsed from a restic repository-lock error message."""

    pid: int
    host: str


def run_restic_command_with_lock_recovery(
    ctx: ResticOperationContext,
    configured_args: Any,
    phase: str,
) -> ResticCommandResult:
    """Run a restic command and retry once after unlocking a stale local lock."""

    result = run_restic_command(
        ctx.app_runtime, ctx.restic_cfg, configured_args, phase, ctx.deadline
    )
    lock_info = recoverable_stale_lock_info(result)
    if lock_info is None:
        return result
    if ctx.app_runtime.backup.cancel.is_set():
        return result

    log_server_message(
        ctx.server,
        "warning",
        "warn.restic.lock_recovery_started",
        phase=phase,
        pid=lock_info.pid,
        host=lock_info.host,
    )
    unlock_result = run_restic_command(
        ctx.app_runtime,
        ctx.restic_cfg,
        [RESTIC_COMMAND_UNLOCK],
        RESTIC_COMMAND_UNLOCK,
        ctx.deadline,
    )
    try:
        assert_restic_success(ctx.restic_cfg, unlock_result)
    except BackupProblem as exc:
        log_server_message(
            ctx.server,
            "warning",
            "warn.restic.lock_recovery_unlock_failed",
            phase=phase,
            error=tr_error(get_mcdr_language(ctx.server), exc),
        )
        return result

    log_server_message(
        ctx.server,
        "info",
        "info.restic.lock_recovery_completed",
        phase=phase,
    )
    return run_restic_command(
        ctx.app_runtime, ctx.restic_cfg, configured_args, phase, ctx.deadline
    )


def recoverable_stale_lock_info(
    result: ResticCommandResult,
) -> Optional[ResticLockInfo]:
    """Return lock metadata when the error clearly points to a dead local process."""

    if result.return_code == 0:
        # 命令已成功却再触发解锁重跑会让同一命令执行两次
        return None
    combined_output = combine_restic_output(result)
    if LOCKED_REPOSITORY_HINT not in combined_output.lower():
        return None

    lock_info = extract_restic_lock_info(combined_output)
    if lock_info is None:
        return None
    if not lock_belongs_to_current_host(lock_info.host):
        return None
    if process_exists(lock_info.pid):
        return None
    return lock_info


def combine_restic_output(result: ResticCommandResult) -> str:
    """Combine stdout and stderr for lock detection without losing either stream."""

    return "{}\n{}".format(result.stdout, result.stderr)


def extract_restic_lock_info(text: str) -> Optional[ResticLockInfo]:
    """Parse PID and host from restic's repository-locked error text."""

    match = LOCKED_REPOSITORY_PATTERN.search(str(text or ""))
    if match is None:
        return None
    return ResticLockInfo(pid=int(match.group("pid")), host=match.group("host"))


def lock_belongs_to_current_host(host: str) -> bool:
    """Return True when the lock host name clearly refers to the current machine."""

    normalized_host = normalize_host_name(host)
    if not normalized_host:
        return False
    return normalized_host in current_host_aliases()


def current_host_aliases() -> set[str]:
    """Collect normalized host name aliases for conservative local-host matching."""

    raw_names = {
        platform.node(),
        socket.gethostname(),
        socket.getfqdn(),
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("HOSTNAME", ""),
    }
    if hasattr(os, "uname"):
        with contextlib.suppress(OSError):
            raw_names.add(os.uname().nodename)

    aliases: set[str] = set()
    for name in raw_names:
        normalized = normalize_host_name(name)
        if not normalized:
            continue
        aliases.add(normalized)
        aliases.add(normalized.split(".", 1)[0])
    return aliases


def normalize_host_name(host: str) -> str:
    """Normalize host names for case-insensitive comparisons."""

    return str(host or "").strip().rstrip(".").lower()


def process_exists(pid: int) -> bool:
    """Return True when the given PID still exists on the current machine."""

    if pid <= 0:
        return False
    if os.name == "nt":
        return process_exists_windows(pid)
    return process_exists_posix(pid)


def process_exists_posix(pid: int) -> bool:
    """Check process existence on POSIX platforms using signal 0."""

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH


def process_exists_windows(pid: int) -> bool:
    """Check process existence on Windows via OpenProcess."""

    access = WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION | WINDOWS_SYNCHRONIZE
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(access, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    error_code = ctypes.get_last_error()
    if error_code == WINDOWS_ERROR_INVALID_PARAMETER:
        return False
    return error_code == WINDOWS_ERROR_ACCESS_DENIED or error_code != 0


def log_server_message(
    server: PluginServerInterface, level: str, key: str, **params: Any
):
    """Log a translated message when the target logger exposes the requested level."""

    logger = getattr(server, "logger", None)
    log_func = getattr(logger, level, None) if logger is not None else None
    if callable(log_func):
        log_func(server_tr(server, key, **params))
