# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mcdr2restic.defaults.default_constants import RESTIC_PROGRESS_INTERVAL_SECONDS
from mcdr2restic.core.i18n import tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import (
    BackupCanceled,
    BackupProblem,
    ResticCommandResult,
    ResticProgressState,
)
from mcdr2restic.restic.restic_config import (
    build_restic_environment,
    normalize_command_args,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_EXECUTABLE,
    RESTIC_CFG_MAX_OUTPUT_CHARS,
    RESTIC_CFG_PROGRESS_INTERVAL,
    RESTIC_CFG_WORKING_DIRECTORY,
    RESTIC_COMMAND_RESTORE,
    RESTIC_COMMAND_ROLLBACK,
    RESTIC_JSON_OUTPUT_PHASES,
    RESTIC_OPTION_JSON,
)
from mcdr2restic.restic.restic_progress import (
    build_restic_progress_state,
    handle_restic_stream_line,
    maybe_emit_restic_progress,
)
from mcdr2restic.restic.restic_termination import (
    terminate_process,
    termination_failure_suffix,
    warn_if_termination_failed,
)
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import tail_text


RESTIC_PROCESS_WAIT_TIMEOUT_SECONDS = 5
RESTIC_READER_JOIN_TIMEOUT_SECONDS = 2


def resolve_popen_executable(executable: str, cwd: Optional[str]) -> str:
    text = str(executable or "").strip()
    if not text or os.path.isabs(text):
        return text
    if not cwd:
        return text
    if os.sep in text or (os.altsep and os.altsep in text):
        return os.path.abspath(os.path.join(str(cwd), text))
    return text


def run_restic_command(
    app_runtime: PluginRuntime,
    restic_cfg: Dict[str, Any],
    configured_args: Any,
    phase: str,
    deadline: Optional[float],
) -> ResticCommandResult:
    args = normalize_restic_phase_args(configured_args, phase)
    executable, cwd, env, command = build_restic_process_command(restic_cfg, args)
    ensure_restic_deadline_available(deadline, phase)
    started = time.monotonic()
    process = start_restic_process(command, cwd, env, executable, phase)
    progress = build_restic_progress_state(
        phase, get_mcdr_language(app_runtime.service.server), started
    )
    progress_interval = get_restic_progress_interval(restic_cfg)
    app_runtime.backup.current_process = process
    try:
        stdout, stderr, return_code, timed_out = read_restic_process_output(
            app_runtime, process, progress, progress_interval, deadline
        )
    finally:
        app_runtime.backup.current_process = None

    assert_restic_process_completed(
        app_runtime, restic_cfg, phase, started, stdout, stderr, timed_out
    )
    maybe_emit_restic_progress(
        app_runtime.service.server, progress, progress_interval, force=True
    )
    return build_restic_command_result(
        phase, args, return_code, stdout, stderr, started, progress
    )


def assert_restic_process_completed(
    app_runtime: PluginRuntime,
    restic_cfg: Dict[str, Any],
    phase: str,
    started: float,
    stdout: str,
    stderr: str,
    timed_out: bool,
):
    if app_runtime.backup.cancel.is_set():
        raise BackupCanceled(i18n_key="error.backup.cancel_requested")
    if timed_out:
        raise_restic_timeout(restic_cfg, phase, started, stdout, stderr)


def normalize_restic_phase_args(configured_args: Any, phase: str) -> List[str]:
    args = prepare_restic_args_for_phase(normalize_command_args(configured_args), phase)
    if args:
        return args
    raise BackupProblem(i18n_key="error.restic.command_empty", phase=phase)


def build_restic_process_command(
    restic_cfg: Dict[str, Any],
    args: List[str],
) -> Tuple[str, Optional[str], Dict[str, str], List[str]]:
    executable = str(restic_cfg.get(RESTIC_CFG_EXECUTABLE, "restic"))
    cwd = restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or None
    env = build_restic_environment(restic_cfg)
    command = [resolve_popen_executable(executable, cwd)] + args
    return executable, cwd, env, command


def ensure_restic_deadline_available(deadline: Optional[float], phase: str):
    remaining = get_deadline_remaining(deadline)
    if remaining is None or remaining > 0:
        return
    raise BackupProblem(i18n_key="error.restic.deadline_exhausted", phase=phase)


def start_restic_process(
    command: List[str],
    cwd: Optional[str],
    env: Dict[str, str],
    executable: str,
    phase: str,
) -> subprocess.Popen:
    try:
        return subprocess.Popen(command, **build_restic_popen_kwargs(cwd, env))
    except FileNotFoundError:
        raise BackupProblem(
            i18n_key="error.restic.executable_not_found", executable=executable
        )
    except Exception as exc:
        raise BackupProblem(
            i18n_key="error.restic.start_failed", phase=phase, error=exc
        )


def build_restic_popen_kwargs(
    cwd: Optional[str], env: Dict[str, str]
) -> Dict[str, Any]:
    popen_kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "cwd": cwd,
        "env": env,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        popen_kwargs["start_new_session"] = True
    return popen_kwargs


def read_restic_process_output(
    app_runtime: PluginRuntime,
    process: subprocess.Popen,
    progress: ResticProgressState,
    progress_interval: int,
    deadline: Optional[float],
) -> Tuple[str, str, int, bool]:
    output_queue: "queue.Queue[Tuple[str, Optional[str]]]" = queue.Queue()
    reader_threads = start_restic_reader_threads(process, output_queue)
    try:
        stdout_lines, stderr_lines, timed_out = consume_restic_output(
            app_runtime,
            process,
            progress,
            progress_interval,
            deadline,
            output_queue,
            len(reader_threads),
        )
        return_code = wait_for_restic_exit(process, get_runtime_language(app_runtime))
        return "".join(stdout_lines), "".join(stderr_lines), return_code, timed_out
    finally:
        join_restic_reader_threads(reader_threads)


def consume_restic_output(
    app_runtime: PluginRuntime,
    process: subprocess.Popen,
    progress: ResticProgressState,
    progress_interval: int,
    deadline: Optional[float],
    output_queue: "queue.Queue[Tuple[str, Optional[str]]]",
    active_streams: int,
) -> Tuple[List[str], List[str], bool]:
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    timed_out = False
    while active_streams > 0:
        timed_out = ensure_restic_process_can_continue(app_runtime, process, deadline)
        if timed_out:
            break
        active_streams = consume_restic_output_item(
            app_runtime,
            progress,
            progress_interval,
            output_queue,
            active_streams,
            stdout_lines,
            stderr_lines,
            deadline,
        )
    return stdout_lines, stderr_lines, timed_out


def ensure_restic_process_can_continue(
    app_runtime: PluginRuntime,
    process: subprocess.Popen,
    deadline: Optional[float],
) -> bool:
    language = get_runtime_language(app_runtime)
    if app_runtime.backup.cancel.is_set():
        result = terminate_process(process)
        warn_if_termination_failed(
            get_runtime_logger(app_runtime),
            tr(language, "action.restic.cancel_process"),
            result,
            language,
        )
        raise_backup_canceled_with_termination_result(result, language)
    remaining = get_deadline_remaining(deadline)
    if remaining is None or remaining > 0:
        return False
    result = terminate_process(process)
    warn_if_termination_failed(
        get_runtime_logger(app_runtime),
        tr(language, "action.restic.timeout_terminate_process"),
        result,
        language,
    )
    return True


def consume_restic_output_item(
    app_runtime: PluginRuntime,
    progress: ResticProgressState,
    progress_interval: int,
    output_queue: "queue.Queue[Tuple[str, Optional[str]]]",
    active_streams: int,
    stdout_lines: List[str],
    stderr_lines: List[str],
    deadline: Optional[float],
) -> int:
    wait = compute_restic_queue_wait(
        progress, progress_interval, get_deadline_remaining(deadline)
    )
    try:
        stream_name, line = output_queue.get(timeout=wait)
    except queue.Empty:
        maybe_emit_restic_progress(
            app_runtime.service.server, progress, progress_interval
        )
        return active_streams
    if line is None:
        return active_streams - 1
    append_restic_output_line(stream_name, line, stdout_lines, stderr_lines)
    handle_restic_stream_line(app_runtime.service.server, progress, stream_name, line)
    maybe_emit_restic_progress(app_runtime.service.server, progress, progress_interval)
    return active_streams


def get_runtime_logger(app_runtime: PluginRuntime) -> Any:
    server = app_runtime.service.server
    return getattr(server, "logger", None)


def append_restic_output_line(
    stream_name: str,
    line: str,
    stdout_lines: List[str],
    stderr_lines: List[str],
):
    if stream_name == "stdout":
        stdout_lines.append(line)
        return
    stderr_lines.append(line)


def wait_for_restic_exit(process: subprocess.Popen, language: str) -> int:
    try:
        return int(process.wait(timeout=RESTIC_PROCESS_WAIT_TIMEOUT_SECONDS))
    except subprocess.TimeoutExpired:
        result = terminate_process(process)
        raise BackupProblem(
            i18n_key="error.restic.process_still_running_after_output",
            detail=termination_failure_suffix(result, language),
        )


def join_restic_reader_threads(reader_threads: Sequence[threading.Thread]):
    for thread in reader_threads:
        thread.join(timeout=RESTIC_READER_JOIN_TIMEOUT_SECONDS)


def raise_restic_timeout(
    restic_cfg: Dict[str, Any],
    phase: str,
    started: float,
    stdout: str,
    stderr: str,
):
    raise BackupProblem(
        i18n_key="error.restic.timeout",
        phase=phase,
        seconds=int(time.monotonic() - started),
        output=tail_text(
            stdout + "\n" + stderr,
            int(restic_cfg.get(RESTIC_CFG_MAX_OUTPUT_CHARS, 1800)),
        ),
    )


def build_restic_command_result(
    phase: str,
    args: List[str],
    return_code: int,
    stdout: str,
    stderr: str,
    started: float,
    progress: ResticProgressState,
) -> ResticCommandResult:
    summary = progress.summary or {}
    return ResticCommandResult(
        phase=phase,
        args=args,
        return_code=int(return_code),
        stdout=stdout,
        stderr=stderr,
        duration_seconds=time.monotonic() - started,
        summary=summary,
        json_errors=progress.json_errors or [],
        snapshot_id=str(summary.get("snapshot_id") or ""),
    )


def prepare_restic_args_for_phase(args: List[str], phase: str) -> List[str]:
    if phase not in RESTIC_JSON_OUTPUT_PHASES:
        return args
    command_name = RESTIC_COMMAND_RESTORE if phase == RESTIC_COMMAND_ROLLBACK else phase
    if command_name not in args:
        return args
    if any(
        item == RESTIC_OPTION_JSON or item.startswith(RESTIC_OPTION_JSON + "=")
        for item in args
    ):
        return args
    insert_at = args.index("--") if "--" in args else len(args)
    return args[:insert_at] + [RESTIC_OPTION_JSON] + args[insert_at:]


def get_deadline_remaining(deadline: Optional[float]) -> Optional[float]:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def start_restic_reader_threads(
    process: subprocess.Popen,
    output_queue: "queue.Queue[Tuple[str, Optional[str]]]",
) -> List[threading.Thread]:
    threads: List[threading.Thread] = []
    for stream_name, stream in [("stdout", process.stdout), ("stderr", process.stderr)]:
        if stream is None:
            continue
        thread = threading.Thread(
            target=read_restic_stream,
            args=(stream_name, stream, output_queue),
            name="MCDR2Restic-{}-Reader".format(stream_name),
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return threads


def read_restic_stream(
    stream_name: str,
    stream: Any,
    output_queue: "queue.Queue[Tuple[str, Optional[str]]]",
):
    try:
        for line in iter(stream.readline, ""):
            output_queue.put((stream_name, line))
    finally:
        output_queue.put((stream_name, None))


def get_restic_progress_interval(restic_cfg: Dict[str, Any]) -> float:
    try:
        value = float(
            restic_cfg.get(
                RESTIC_CFG_PROGRESS_INTERVAL, RESTIC_PROGRESS_INTERVAL_SECONDS
            )
        )
    except Exception:
        value = RESTIC_PROGRESS_INTERVAL_SECONDS
    return max(1.0, value)


def compute_restic_queue_wait(
    progress: ResticProgressState, interval: float, remaining: Optional[float]
) -> float:
    until_progress = max(0.1, interval - (time.monotonic() - progress.last_emit_at))
    if remaining is not None:
        until_progress = min(until_progress, max(0.1, remaining))
    return min(1.0, until_progress)


def get_runtime_language(app_runtime: PluginRuntime) -> str:
    return get_mcdr_language(app_runtime.service.server)


def raise_backup_canceled_with_termination_result(result, language: str):
    suffix = termination_failure_suffix(result, language)
    if suffix:
        raise BackupCanceled(
            i18n_key="error.backup.cancel_requested_with_failure", detail=suffix
        )
    raise BackupCanceled(i18n_key="error.backup.cancel_requested")
