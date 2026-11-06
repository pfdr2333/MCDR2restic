# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from mcdr2restic.config.state_store import get_config_snapshot
from mcdr2restic.defaults.default_constants import (
    RESTIC_PROGRESS_INTERVAL_SECONDS,
)
from mcdr2restic.core.i18n import config_language, tr
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
from mcdr2restic.restic.restic_download import (
    find_system_restic,
    is_bare_restic_name,
    is_config_folder_default_restic_path,
    resolve_default_restic_install_path,
    system_restic_preferred,
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


class ResticProcessOutcome(NamedTuple):
    """单次 restic 进程执行产生的输出与退出信息。"""

    stdout: str
    stderr: str
    return_code: int
    timed_out: bool


@dataclass
class ResticStreamBuffers:
    """输出读取过程中累积的流缓冲。"""

    stdout_lines: List[str] = field(default_factory=list)
    stderr_lines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResticStreamSession:
    """一次 restic 进程输出读取会话的固定上下文。"""

    app_runtime: PluginRuntime
    process: subprocess.Popen
    progress: ResticProgressState
    progress_interval: int
    deadline: Optional[float]
    output_queue: "queue.Queue[Tuple[str, Optional[str]]]"


def resolve_popen_executable(
    executable: str, cwd: Optional[str], prefer_system: bool = False
) -> str:
    text = str(executable or "").strip()
    if not text or os.path.isabs(text):
        return text
    if is_config_folder_default_restic_path(text) or is_bare_restic_name(text):
        if prefer_system:
            found = find_system_restic()
            if found:
                return found
        return resolve_default_restic_install_path(text)
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
        outcome = read_restic_process_output(
            app_runtime, process, progress, progress_interval, deadline
        )
    finally:
        app_runtime.backup.current_process = None

    assert_restic_process_completed(app_runtime, restic_cfg, phase, started, outcome)
    # 非 JSON 阶段（init/unlock/maintenance）没有 summary/status，
    # 强推进度只会把已结束的命令说成"仍在执行"（P3-3）
    if progress.summary or progress.status:
        maybe_emit_restic_progress(
            app_runtime.service.server, progress, progress_interval, force=True
        )
    return build_restic_command_result(phase, args, outcome, started, progress)


def assert_restic_process_completed(
    app_runtime: PluginRuntime,
    restic_cfg: Dict[str, Any],
    phase: str,
    started: float,
    outcome: ResticProcessOutcome,
):
    canceled = app_runtime.backup.cancel.is_set()
    # restic 已成功收尾（退出码 0）时取消请求才刚到达，快照实际已落库，
    # 仍标 canceled 会与仓库真实状态矛盾，只有被打断的进程才按取消处理（P5-1）
    if canceled and (outcome.timed_out or outcome.return_code != 0):
        raise BackupCanceled(i18n_key="error.backup.cancel_requested")
    if outcome.timed_out:
        raise_restic_timeout(restic_cfg, phase, started, outcome.stdout, outcome.stderr)


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
    command = [
        resolve_popen_executable(
            executable, cwd, prefer_system=system_restic_preferred(restic_cfg)
        ),
        *args,
    ]
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
    phase: str,
) -> subprocess.Popen:
    return start_restic_subprocess(
        command,
        cwd,
        env,
        lambda exc: BackupProblem(
            i18n_key="error.restic.start_failed", phase=phase, error=exc
        ),
    )


def start_restic_subprocess(
    command: List[str],
    cwd: Optional[str],
    env: Dict[str, str],
    build_start_failed_error: Callable[[Exception], BackupProblem],
) -> subprocess.Popen:
    """共享的 restic 子进程启动包装：可执行文件缺失与启动失败转本地化异常。"""

    try:
        return subprocess.Popen(command, **build_restic_popen_kwargs(cwd, env))
    except FileNotFoundError as exc:
        # 报 Popen 实际尝试的路径：裸名会被解析到平台默认路径，
        # 配置原文对不上会让用户找错位置（P3-5）
        raise BackupProblem(
            i18n_key="error.restic.executable_not_found", executable=command[0]
        ) from exc
    except Exception as exc:
        raise build_start_failed_error(exc) from exc


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
) -> ResticProcessOutcome:
    output_queue: "queue.Queue[Tuple[str, Optional[str]]]" = queue.Queue()
    reader_threads = start_restic_reader_threads(process, output_queue)
    session = ResticStreamSession(
        app_runtime=app_runtime,
        process=process,
        progress=progress,
        progress_interval=progress_interval,
        deadline=deadline,
        output_queue=output_queue,
    )
    try:
        buffers, timed_out = consume_restic_output(session, len(reader_threads))
        return_code = wait_for_restic_exit(process, get_runtime_language(app_runtime))
        return ResticProcessOutcome(
            "".join(buffers.stdout_lines),
            "".join(buffers.stderr_lines),
            return_code,
            timed_out,
        )
    finally:
        join_restic_reader_threads(reader_threads)


def consume_restic_output(
    session: ResticStreamSession,
    active_streams: int,
) -> Tuple[ResticStreamBuffers, bool]:
    buffers = ResticStreamBuffers()
    timed_out = False
    while active_streams > 0:
        timed_out = ensure_restic_process_can_continue(
            session.app_runtime, session.process, session.deadline
        )
        if timed_out:
            break
        active_streams = consume_restic_output_item(session, active_streams, buffers)
    return buffers, timed_out


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
    session: ResticStreamSession,
    active_streams: int,
    buffers: ResticStreamBuffers,
) -> int:
    wait = compute_restic_queue_wait(
        session.progress,
        session.progress_interval,
        get_deadline_remaining(session.deadline),
    )
    try:
        stream_name, line = session.output_queue.get(timeout=wait)
    except queue.Empty:
        maybe_emit_restic_progress(
            session.app_runtime.service.server,
            session.progress,
            session.progress_interval,
        )
        return active_streams
    if line is None:
        return active_streams - 1
    append_restic_output_line(
        stream_name, line, buffers.stdout_lines, buffers.stderr_lines
    )
    handle_restic_stream_line(session.app_runtime.service.server, session.progress, line)
    maybe_emit_restic_progress(
        session.app_runtime.service.server,
        session.progress,
        session.progress_interval,
    )
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
    except subprocess.TimeoutExpired as exc:
        result = terminate_process(process)
        raise BackupProblem(
            i18n_key="error.restic.process_still_running_after_output",
            detail=termination_failure_suffix(result, language),
        ) from exc


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
    outcome: ResticProcessOutcome,
    started: float,
    progress: ResticProgressState,
) -> ResticCommandResult:
    summary = progress.summary or {}
    return ResticCommandResult(
        phase=phase,
        args=args,
        return_code=int(outcome.return_code),
        stdout=outcome.stdout,
        stderr=outcome.stderr,
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
    return [*args[:insert_at], RESTIC_OPTION_JSON, *args[insert_at:]]


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
    return config_language(
        get_config_snapshot(app_runtime), get_mcdr_language(app_runtime.service.server)
    )


def raise_backup_canceled_with_termination_result(result, language: str):
    suffix = termination_failure_suffix(result, language)
    if suffix:
        raise BackupCanceled(
            i18n_key="error.backup.cancel_requested_with_failure", detail=suffix
        )
    raise BackupCanceled(i18n_key="error.backup.cancel_requested")
