# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


BACKUP_STATUS_RUNNING = 'running'
BACKUP_STATUS_SUCCESS = 'success'
BACKUP_STATUS_FAILED = 'failed'
BACKUP_STATUS_CANCELED = 'canceled'


class BackupProblem(Exception):
    """Raised when a backup or restic workflow cannot continue."""


class BackupCanceled(BackupProblem):
    """Raised when a running backup is canceled by user or shutdown flow."""


class CronError(ValueError):
    """Raised when a cron expression is invalid or cannot produce a next run."""


@dataclass
class ResticCommandResult:
    phase: str
    args: List[str]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    summary: Dict[str, Any] = None
    json_errors: List[str] = None
    snapshot_id: str = ''


@dataclass
class ResticProgressState:
    phase: str
    language: str
    status: Dict[str, Any] = None
    summary: Dict[str, Any] = None
    json_errors: List[str] = None
    last_text: str = ''
    seen_json: bool = False
    started_at: float = 0.0
    last_emit_at: float = 0.0
    last_emit_text: str = ''


@dataclass
class RestoreSession:
    tasks: List[Dict[str, Any]]
    cfg: Dict[str, Any]
    snapshot_cfg: Dict[str, Any]
    cache_key: str
    language: str
    phase: str
    started_at: str
    error: str = ''
    safety_snapshot_id: str = ''
    rollback_error: str = ''


@dataclass
class RestoreStageResult:
    restore_error: str = ''
    rollback_error: str = ''


@dataclass
class BackupRunOutcome:
    status: str
    message: str
    detail: str
    duration_seconds: int

    @property
    def failed(self) -> bool:
        return self.status != BACKUP_STATUS_SUCCESS
