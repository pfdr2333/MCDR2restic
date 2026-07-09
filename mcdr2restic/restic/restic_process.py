# -*- coding: utf-8 -*-
from __future__ import annotations

# Compatibility facade for the old restic_process import path. The implementation
# is split into runner, progress parsing, progress text, and termination modules.
from mcdr2restic.restic.restic_progress import (
    build_restic_progress_state,
    handle_restic_stream_line,
    maybe_emit_restic_progress,
)
from mcdr2restic.restic.restic_progress_text import (
    first_float,
    first_int,
    format_bytes,
    format_restic_json_error,
    format_restic_progress,
    format_restic_status,
    format_restic_summary,
    get_restic_percent,
)
from mcdr2restic.restic.restic_runner import (
    append_restic_output_line,
    build_restic_command_result,
    build_restic_popen_kwargs,
    build_restic_process_command,
    compute_restic_queue_wait,
    consume_restic_output,
    consume_restic_output_item,
    ensure_restic_deadline_available,
    ensure_restic_process_can_continue,
    get_deadline_remaining,
    get_restic_progress_interval,
    join_restic_reader_threads,
    normalize_restic_phase_args,
    prepare_restic_args_for_phase,
    raise_restic_timeout,
    read_restic_process_output,
    read_restic_stream,
    resolve_popen_executable,
    run_restic_command,
    start_restic_process,
    start_restic_reader_threads,
    wait_for_restic_exit,
)
from mcdr2restic.restic.restic_termination import TerminateResult, terminate_process


__all__ = [
    "TerminateResult",
    "append_restic_output_line",
    "build_restic_command_result",
    "build_restic_popen_kwargs",
    "build_restic_process_command",
    "build_restic_progress_state",
    "compute_restic_queue_wait",
    "consume_restic_output",
    "consume_restic_output_item",
    "ensure_restic_deadline_available",
    "ensure_restic_process_can_continue",
    "first_float",
    "first_int",
    "format_bytes",
    "format_restic_json_error",
    "format_restic_progress",
    "format_restic_status",
    "format_restic_summary",
    "get_deadline_remaining",
    "get_restic_percent",
    "get_restic_progress_interval",
    "handle_restic_stream_line",
    "join_restic_reader_threads",
    "maybe_emit_restic_progress",
    "normalize_restic_phase_args",
    "prepare_restic_args_for_phase",
    "raise_restic_timeout",
    "read_restic_process_output",
    "read_restic_stream",
    "resolve_popen_executable",
    "run_restic_command",
    "start_restic_process",
    "start_restic_reader_threads",
    "terminate_process",
    "wait_for_restic_exit",
]
