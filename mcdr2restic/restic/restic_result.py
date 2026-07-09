# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from mcdr2restic.core.models import BackupProblem, ResticCommandResult
from mcdr2restic.core.utils import tail_text
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_ERROR_REGEXES,
    RESTIC_CFG_IGNORE_ERROR_REGEXES,
    RESTIC_CFG_MAX_OUTPUT_CHARS,
    RESTIC_CFG_SUCCESS_EXIT_CODES,
)


def assert_restic_success(restic_cfg: Dict[str, Any], result: ResticCommandResult):
    success_codes = set(
        int(code) for code in restic_cfg.get(RESTIC_CFG_SUCCESS_EXIT_CODES, [0])
    )
    combined = "{}\n{}".format(result.stdout, result.stderr)
    max_output_chars = int(restic_cfg.get(RESTIC_CFG_MAX_OUTPUT_CHARS, 1800))

    assert_no_json_errors(result, max_output_chars)
    assert_return_code_success(result, success_codes, combined, max_output_chars)
    assert_no_suspicious_output(restic_cfg, result, combined, max_output_chars)


def assert_no_json_errors(result: ResticCommandResult, max_output_chars: int):
    if result.json_errors:
        raise BackupProblem(
            i18n_key="error.restic.json_output_error",
            phase=result.phase,
            output=tail_text("\n".join(result.json_errors), max_output_chars),
        )


def assert_return_code_success(
    result: ResticCommandResult,
    success_codes: set,
    combined_output: str,
    max_output_chars: int,
):
    if result.return_code in success_codes:
        return
    raise BackupProblem(
        i18n_key="error.restic.return_code",
        phase=result.phase,
        return_code=result.return_code,
        duration_seconds=int(result.duration_seconds),
        output=tail_text(combined_output, max_output_chars),
    )


def assert_no_suspicious_output(
    restic_cfg: Dict[str, Any],
    result: ResticCommandResult,
    combined_output: str,
    max_output_chars: int,
):
    suspicious_lines = detect_error_lines(
        combined_output,
        restic_cfg.get(RESTIC_CFG_ERROR_REGEXES, []),
        restic_cfg.get(RESTIC_CFG_IGNORE_ERROR_REGEXES, []),
    )
    if not suspicious_lines:
        return
    raise BackupProblem(
        i18n_key="error.restic.suspicious_output",
        phase=result.phase,
        output=tail_text("\n".join(suspicious_lines), max_output_chars),
    )


def detect_error_lines(
    text: str, patterns: Iterable[str], ignore_patterns: Iterable[str]
) -> List[str]:
    compiled = [re.compile(pattern) for pattern in patterns if pattern]
    ignored = [re.compile(pattern) for pattern in ignore_patterns if pattern]
    lines: List[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if any(regex.search(line) for regex in ignored):
            continue
        if any(regex.search(line) for regex in compiled):
            lines.append(line)
    return lines
