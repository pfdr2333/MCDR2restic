# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Any, Optional


UNKNOWN_TERMINATION_ERROR = '未知错误'


@dataclass
class TerminateResult:
    graceful: bool = False
    killed: bool = False
    error: str = ''

    @property
    def terminated(self) -> bool:
        return self.graceful or self.killed


def terminate_process(process: subprocess.Popen) -> TerminateResult:
    graceful_error = try_graceful_terminate(process)
    if not graceful_error:
        return TerminateResult(graceful=True)

    kill_error = try_force_kill(process)
    if not kill_error:
        return TerminateResult(killed=True, error=graceful_error)
    return TerminateResult(error='{}; {}'.format(graceful_error, kill_error))


def try_graceful_terminate(process: subprocess.Popen) -> str:
    try:
        if os.name == 'nt':
            process.terminate()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=10)
        return ''
    except Exception as exc:
        return str(exc)


def try_force_kill(process: subprocess.Popen) -> str:
    try:
        if os.name == 'nt':
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        return ''
    except Exception as exc:
        return str(exc)


def termination_failure_suffix(result: Optional[TerminateResult]) -> str:
    if result is None or result.terminated:
        return ''
    return '；终止失败：{}'.format(result.error or UNKNOWN_TERMINATION_ERROR)


def termination_failure_message(action: str, result: Optional[TerminateResult]) -> str:
    suffix = termination_failure_suffix(result)
    if not suffix:
        return ''
    return '{}{}'.format(action, suffix)


def warn_if_termination_failed(logger: Any, action: str, result: Optional[TerminateResult]):
    message = termination_failure_message(action, result)
    warning = getattr(logger, 'warning', None)
    if message and callable(warning):
        warning(message)
