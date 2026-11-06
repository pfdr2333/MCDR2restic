# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.core.models import BackupProblem, ResticCommandResult
from mcdr2restic.core.utils import tail_text
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_ERROR_REGEXES,
    RESTIC_CFG_IGNORE_ERROR_REGEXES,
    RESTIC_CFG_MAX_OUTPUT_CHARS,
    RESTIC_CFG_SUCCESS_EXIT_CODES,
)
from mcdr2restic.restic.restic_guidance import (
    RESTIC_FAILURE_LOCKED,
    RESTIC_FAILURE_REPOSITORY_NOT_INITIALIZED,
    classify_restic_failure_output,
    restic_management_command,
)


DEFAULT_SUCCESS_EXIT_CODE = 0


def combine_restic_output(result: ResticCommandResult) -> str:
    """Combine stdout and stderr without losing either stream."""

    return "{}\n{}".format(result.stdout, result.stderr)


def warn_invalid_restic_config(logger: Any, key: str, **params: Any):
    if logger is None:
        return
    logger.warning(server_tr(None, key, **params))


def get_success_exit_codes(
    restic_cfg: Dict[str, Any], logger: Any = None
) -> Set[int]:
    raw = restic_cfg.get(RESTIC_CFG_SUCCESS_EXIT_CODES, [DEFAULT_SUCCESS_EXIT_CODE])
    codes: Set[int] = set()
    if isinstance(raw, (list, tuple)):
        for code in raw:
            parsed = parse_exit_code(code, logger)
            if parsed is not None:
                codes.add(parsed)
    else:
        warn_invalid_restic_config(
            logger, "warn.restic.success_exit_codes_invalid", value=raw
        )
    return codes or {DEFAULT_SUCCESS_EXIT_CODE}


def parse_exit_code(code: Any, logger: Any) -> Optional[int]:
    if isinstance(code, bool):
        warn_invalid_restic_config(
            logger, "warn.restic.success_exit_codes_invalid", value=code
        )
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        warn_invalid_restic_config(
            logger, "warn.restic.success_exit_codes_invalid", value=code
        )
        return None


def compile_restic_patterns(
    patterns: Iterable[Any], config_key: str, logger: Any = None
) -> List[re.Pattern]:
    compiled: List[re.Pattern] = []
    for pattern in patterns:
        if not pattern:
            continue
        try:
            compiled.append(re.compile(pattern))
        except (re.error, TypeError) as exc:
            warn_invalid_restic_config(
                logger,
                "warn.restic.error_regex_invalid",
                config_key=config_key,
                pattern=pattern,
                error=exc,
            )
    return compiled


def assert_restic_success(
    restic_cfg: Dict[str, Any],
    result: ResticCommandResult,
    command_root: str = "!!restic",
    logger: Any = None,
):
    success_codes = get_success_exit_codes(restic_cfg, logger)
    combined = combine_restic_output(result)
    max_output_chars = int(restic_cfg.get(RESTIC_CFG_MAX_OUTPUT_CHARS, 1800))

    assert_no_json_errors(result, max_output_chars)
    assert_return_code_success(
        result, success_codes, combined, max_output_chars, command_root
    )
    assert_no_suspicious_output(restic_cfg, result, combined, max_output_chars, logger)


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
    command_root: str = "!!restic",
):
    if result.return_code in success_codes:
        return
    failure_kind = classify_restic_failure_output(combined_output)
    if failure_kind == RESTIC_FAILURE_REPOSITORY_NOT_INITIALIZED:
        raise_restic_return_code_with_command_hint(
            result,
            combined_output,
            max_output_chars,
            "error.restic.return_code.repository_not_initialized",
            init_command=restic_management_command(command_root, "init"),
        )
    if failure_kind == RESTIC_FAILURE_LOCKED:
        raise_restic_return_code_with_command_hint(
            result,
            combined_output,
            max_output_chars,
            "error.restic.return_code.locked",
            unlock_command=restic_management_command(command_root, "unlock"),
        )
    raise BackupProblem(
        i18n_key="error.restic.return_code",
        phase=result.phase,
        return_code=result.return_code,
        duration_seconds=int(result.duration_seconds),
        output=tail_text(combined_output, max_output_chars),
    )


def raise_restic_return_code_with_command_hint(
    result: ResticCommandResult,
    combined_output: str,
    max_output_chars: int,
    i18n_key: str,
    **params: Any,
):
    raise BackupProblem(
        i18n_key=i18n_key,
        phase=result.phase,
        return_code=result.return_code,
        duration_seconds=int(result.duration_seconds),
        output=tail_text(combined_output, max_output_chars),
        **params,
    )


def assert_no_suspicious_output(
    restic_cfg: Dict[str, Any],
    result: ResticCommandResult,
    combined_output: str,
    max_output_chars: int,
    logger: Any = None,
):
    suspicious_lines = detect_error_lines(
        combined_output,
        restic_cfg.get(RESTIC_CFG_ERROR_REGEXES, []),
        restic_cfg.get(RESTIC_CFG_IGNORE_ERROR_REGEXES, []),
        logger,
    )
    if not suspicious_lines:
        return
    raise BackupProblem(
        i18n_key="error.restic.suspicious_output",
        phase=result.phase,
        output=tail_text("\n".join(suspicious_lines), max_output_chars),
    )


def detect_error_lines(
    text: str,
    patterns: Iterable[str],
    ignore_patterns: Iterable[str],
    logger: Any = None,
) -> List[str]:
    compiled = compile_restic_patterns(patterns, "restic.error_regexes", logger)
    ignored = compile_restic_patterns(
        ignore_patterns, "restic.ignore_error_regexes", logger
    )
    lines: List[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if any(regex.search(line) for regex in ignored):
            continue
        if any(regex.search(line) for regex in compiled):
            lines.append(line)
    return lines
