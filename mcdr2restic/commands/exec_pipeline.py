# -*- coding: utf-8 -*-
"""!!restic exec 的内存过滤管线：用行级 grep/head/tail 组合模拟终端管道。

不做真管道（跨平台 shell/grep 差异与引号转义复杂），restic 输出本来就被
读流线程逐行送回，在这里按行过滤即可获得一致的体验。
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import List, Protocol, Sequence

from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_config import split_command_string

PIPELINE_TOKEN = "|"
STAGE_GREP = "grep"
STAGE_HEAD = "head"
STAGE_TAIL = "tail"
STAGE_NAMES = (STAGE_GREP, STAGE_HEAD, STAGE_TAIL)
STAGE_NAMES_TEXT = "/".join(STAGE_NAMES)
GREP_IGNORE_CASE_FLAG = "-i"

DEFAULT_WINDOW_LINES = 50
EXEC_DISPLAY_MAX_LINES = 200
EXEC_DISPLAY_MAX_LINE_CHARS = 300
TRUNCATION_SUFFIX = "…"


class ExecStage(Protocol):
    """行过滤阶段协议：feed 处理实时行，flush 输出缓冲行（如 tail）。"""

    def feed(self, line: str) -> List[str]:
        ...

    def flush(self) -> List[str]:
        ...


@dataclass
class ExecPipelineRequest:
    restic_args: List[str]
    stages: List[ExecStage]


class GrepStage:
    def __init__(self, pattern: str, ignore_case: bool = False):
        flags = re.IGNORECASE if ignore_case else 0
        self.pattern = re.compile(pattern, flags)

    def feed(self, line: str) -> List[str]:
        return [line] if self.pattern.search(line) else []

    def flush(self) -> List[str]:
        return []


class HeadStage:
    def __init__(self, limit: int):
        self.remaining = limit

    def feed(self, line: str) -> List[str]:
        if self.remaining <= 0:
            return []
        self.remaining -= 1
        return [line]

    def flush(self) -> List[str]:
        return []


class TailStage:
    def __init__(self, limit: int):
        self.buffer: deque = deque(maxlen=limit)

    def feed(self, line: str) -> List[str]:
        self.buffer.append(line)
        return []

    def flush(self) -> List[str]:
        return list(self.buffer)


class ExecOutputPipeline:
    """驱动阶段链：行过滤 + 显示上限与统计，供命令层流式消费。"""

    def __init__(self, stages: Sequence[ExecStage]):
        self._stages = list(stages)
        self.total_lines = 0
        self.shown_lines = 0
        self.suppressed_lines = 0

    def feed(self, line: str) -> List[str]:
        self.total_lines += 1
        return self._cap(self._run_through(line, self._stages))

    def flush(self) -> List[str]:
        lines: List[str] = []
        for index, stage in enumerate(self._stages):
            for flushed in stage.flush():
                lines.extend(self._run_through(flushed, self._stages[index + 1:]))
        return self._cap(lines)

    def _run_through(self, line: str, stages: Sequence[ExecStage]) -> List[str]:
        current = [line]
        for stage in stages:
            following: List[str] = []
            for item in current:
                following.extend(stage.feed(item))
            current = following
            if not current:
                return []
        return current

    def _cap(self, lines: List[str]) -> List[str]:
        shown: List[str] = []
        for item in lines:
            if self.shown_lines >= EXEC_DISPLAY_MAX_LINES:
                self.suppressed_lines += 1
                continue
            shown.append(truncate_display_line(item))
            self.shown_lines += 1
        return shown


def truncate_display_line(line: str) -> str:
    text = str(line).rstrip("\r\n")
    if len(text) > EXEC_DISPLAY_MAX_LINE_CHARS:
        return text[:EXEC_DISPLAY_MAX_LINE_CHARS] + TRUNCATION_SUFFIX
    return text


def parse_exec_pipeline(raw: str) -> ExecPipelineRequest:
    """把 exec 原始参数拆成 restic 参数与过滤阶段，非法输入抛本地化异常。"""
    tokens = split_command_string(str(raw or ""))
    segments = split_tokens_on_pipeline_token(tokens)
    if not segments[0]:
        raise BackupProblem(i18n_key="error.exec.usage")
    stages = [_build_stage(segment) for segment in segments[1:]]
    return ExecPipelineRequest(restic_args=segments[0], stages=stages)


def split_tokens_on_pipeline_token(tokens: List[str]) -> List[List[str]]:
    segments: List[List[str]] = [[]]
    for token in tokens:
        if token == PIPELINE_TOKEN:
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _build_stage(tokens: List[str]) -> ExecStage:
    if not tokens:
        raise BackupProblem(i18n_key="error.exec.usage")
    name, params = tokens[0], tokens[1:]
    if name == STAGE_GREP:
        return _build_grep_stage(params)
    if name in (STAGE_HEAD, STAGE_TAIL):
        return _build_window_stage(name, params)
    raise BackupProblem(
        i18n_key="error.exec.unknown_stage", stage=name, stages=STAGE_NAMES_TEXT
    )


def _build_grep_stage(params: List[str]) -> GrepStage:
    ignore_case = False
    if params and params[0] == GREP_IGNORE_CASE_FLAG:
        ignore_case = True
        params = params[1:]
    if not params:
        raise BackupProblem(i18n_key="error.exec.usage")
    pattern = " ".join(params)
    try:
        return GrepStage(pattern, ignore_case)
    except re.error as exc:
        raise BackupProblem(
            i18n_key="error.exec.invalid_pattern", pattern=pattern, error=exc
        ) from exc


def _build_window_stage(name: str, params: List[str]):
    if len(params) > 1:
        raise BackupProblem(i18n_key="error.exec.usage")
    limit = DEFAULT_WINDOW_LINES if not params else _parse_window_lines(params[0])
    if name == STAGE_HEAD:
        return HeadStage(limit)
    return TailStage(limit)


def _parse_window_lines(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        limit = 0
    if limit <= 0:
        raise BackupProblem(i18n_key="error.exec.invalid_count", value=value)
    return limit
