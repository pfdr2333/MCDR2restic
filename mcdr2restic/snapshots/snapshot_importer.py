# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_config import (
    build_restic_environment,
    get_effective_restic_repository,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_EXECUTABLE,
    RESTIC_CFG_WORKING_DIRECTORY,
    RESTIC_COMMAND_SNAPSHOTS,
    RESTIC_ENV_REPOSITORY,
    RESTIC_OPTION_JSON,
)
from mcdr2restic.restic.restic_runner import (
    build_restic_popen_kwargs,
    resolve_popen_executable,
)
from mcdr2restic.restic.restic_termination import (
    TerminateResult,
    terminate_process,
)
from mcdr2restic.snapshots.snapshot_db import insert_snapshot_row
from mcdr2restic.core.utils import tail_text


SNAPSHOT_IMPORT_COMMIT_INTERVAL = 100
SNAPSHOT_STDERR_TAIL_CHARS = 4000
SNAPSHOT_ERROR_TAIL_CHARS = 1000
JSON_STREAM_READ_SIZE = 65536
RESTIC_READER_JOIN_TIMEOUT_SECONDS = 2


@dataclass
class ProcessTimeoutState:
    timed_out: threading.Event
    termination_result: Optional[TerminateResult] = None


def import_restic_snapshots_to_sql(
    restic_cfg: Dict[str, Any],
    conn: sqlite3.Connection,
    cache_key: str,
    timeout_seconds: int,
) -> int:
    process = start_restic_snapshot_process(restic_cfg)
    stderr_tail = TextTailBuffer(SNAPSHOT_STDERR_TAIL_CHARS)
    stderr_thread = start_snapshot_stderr_reader(process, stderr_tail)
    timeout_state, timer = start_process_timeout_timer(process, timeout_seconds)
    try:
        count = import_snapshot_stdout(process, conn, cache_key)
        return_code = process.wait()
    finally:
        timer.cancel()
        stderr_thread.join(timeout=RESTIC_READER_JOIN_TIMEOUT_SECONDS)

    assert_snapshot_import_finished(timeout_seconds, timeout_state, return_code, stderr_tail.text)
    conn.commit()
    return count


class TextTailBuffer:
    def __init__(self, max_chars: int):
        self.max_chars = max(1, int(max_chars))
        self.text = ''

    def append(self, chunk: str):
        self.text = (self.text + str(chunk))[-self.max_chars:]


def start_snapshot_stderr_reader(
    process: subprocess.Popen,
    stderr_tail: TextTailBuffer,
) -> threading.Thread:
    thread = threading.Thread(
        target=read_snapshot_stderr,
        args=(process, stderr_tail),
        name='MCDR2Restic-SnapshotStderr',
        daemon=True
    )
    thread.start()
    return thread


def read_snapshot_stderr(process: subprocess.Popen, stderr_tail: TextTailBuffer):
    if process.stderr is None:
        return
    while True:
        chunk = process.stderr.read(4096)
        if not chunk:
            return
        stderr_tail.append(chunk)


def start_process_timeout_timer(
    process: subprocess.Popen,
    timeout_seconds: int,
) -> Tuple[ProcessTimeoutState, threading.Timer]:
    timeout_state = ProcessTimeoutState(threading.Event())
    timer = threading.Timer(timeout_seconds, terminate_process_after_timeout, args=(process, timeout_state))
    timer.daemon = True
    timer.start()
    return timeout_state, timer


def terminate_process_after_timeout(process: subprocess.Popen, timeout_state: ProcessTimeoutState):
    timeout_state.timed_out.set()
    timeout_state.termination_result = terminate_process(process)


def import_snapshot_stdout(
    process: subprocess.Popen,
    conn: sqlite3.Connection,
    cache_key: str,
) -> int:
    if process.stdout is None:
        raise BackupProblem(i18n_key='error.snapshot.stdout_missing')
    count = 0
    for snapshot in iter_json_array_stream(process.stdout):
        if not isinstance(snapshot, dict):
            continue
        insert_snapshot_row(conn, cache_key, snapshot)
        count += 1
        if count % SNAPSHOT_IMPORT_COMMIT_INTERVAL == 0:
            conn.commit()
    return count


def assert_snapshot_import_finished(
    timeout_seconds: int,
    timeout_state: ProcessTimeoutState,
    return_code: int,
    stderr_tail: str,
):
    if timeout_state.timed_out.is_set():
        if timeout_state.termination_result is not None and not timeout_state.termination_result.terminated:
            raise BackupProblem(
                i18n_key='error.snapshot.timeout_termination_failed',
                timeout_seconds=timeout_seconds,
                error=timeout_state.termination_result.error or ''
            )
        raise BackupProblem(i18n_key='error.snapshot.timeout', timeout_seconds=timeout_seconds)
    if return_code == 0:
        return
    raise BackupProblem(
        i18n_key='error.snapshot.return_code',
        return_code=return_code,
        output=tail_text(stderr_tail, SNAPSHOT_ERROR_TAIL_CHARS)
    )


def start_restic_snapshot_process(restic_cfg: Dict[str, Any]) -> subprocess.Popen:
    executable = str(restic_cfg.get(RESTIC_CFG_EXECUTABLE, 'restic') or 'restic')
    env = build_snapshot_restic_environment(restic_cfg)
    cwd = restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or None
    command = [resolve_popen_executable(executable, cwd), RESTIC_COMMAND_SNAPSHOTS, RESTIC_OPTION_JSON]
    try:
        return subprocess.Popen(command, **build_restic_popen_kwargs(cwd, env))
    except FileNotFoundError:
        raise BackupProblem(i18n_key='error.restic.executable_not_found', executable=executable)
    except Exception as exc:
        raise BackupProblem(i18n_key='error.snapshot.start_failed', error=exc)


def build_snapshot_restic_environment(restic_cfg: Dict[str, Any]) -> Dict[str, str]:
    env = build_restic_environment(restic_cfg)
    if str(env.get(RESTIC_ENV_REPOSITORY, '') or '').strip():
        return env

    repository = get_effective_restic_repository(restic_cfg)
    if repository:
        env[RESTIC_ENV_REPOSITORY] = repository
    return env


def iter_json_array_stream(stream):
    decoder = json.JSONDecoder()
    buffer = ''
    in_array = False
    eof = False
    while True:
        buffer, eof = read_json_stream_chunk(stream, buffer, eof)
        buffer, in_array, items, finished = decode_json_array_buffer(decoder, buffer, in_array, eof)
        for item in items:
            yield item
        if finished:
            return
        if eof:
            break
    raise BackupProblem(i18n_key='error.snapshot.output_ended_early')


def read_json_stream_chunk(stream, buffer: str, eof: bool) -> Tuple[str, bool]:
    if eof:
        return buffer, eof
    chunk = stream.read(JSON_STREAM_READ_SIZE)
    if not chunk:
        return buffer, True
    return buffer + chunk, False


def decode_json_array_buffer(
    decoder: json.JSONDecoder,
    buffer: str,
    in_array: bool,
    eof: bool,
) -> Tuple[str, bool, List[Any], bool]:
    items: List[Any] = []
    while True:
        buffer = buffer.lstrip()
        if not in_array:
            buffer, in_array = enter_json_array(buffer)
            if not in_array:
                return buffer, in_array, items, False
        if not buffer:
            return buffer, in_array, items, False
        if buffer[0] == ']':
            return buffer, in_array, items, True
        if buffer[0] == ',':
            buffer = buffer[1:]
            continue
        try:
            item, index = decoder.raw_decode(buffer)
        except json.JSONDecodeError:
            if eof:
                raise BackupProblem(i18n_key='error.snapshot.parse_failed')
            return buffer, in_array, items, False
        buffer = buffer[index:]
        items.append(item)


def enter_json_array(buffer: str) -> Tuple[str, bool]:
    if not buffer:
        return buffer, False
    if buffer[0] != '[':
        raise BackupProblem(i18n_key='error.snapshot.not_json_array')
    return buffer[1:], True
