# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List


class TextEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class BackupRunStatus(TextEnum):
    NEVER = 'never'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    CANCELED = 'canceled'


class BackupTrigger(TextEnum):
    MANUAL = 'manual'
    SCHEDULED = 'scheduled'
    FORCED = 'forced'
    RESTORE_PRE_BACKUP = 'restore-pre-backup'


class RestorePhase(TextEnum):
    PRE_BACKUP = 'pre_backup'
    STOPPING = 'stopping'
    RESTORING = 'restoring'
    STARTING = 'starting'
    ROLLBACK = 'rollback'


BACKUP_STATUS_NEVER = BackupRunStatus.NEVER.value
BACKUP_STATUS_RUNNING = BackupRunStatus.RUNNING.value
BACKUP_STATUS_SUCCESS = BackupRunStatus.SUCCESS.value
BACKUP_STATUS_FAILED = BackupRunStatus.FAILED.value
BACKUP_STATUS_CANCELED = BackupRunStatus.CANCELED.value


def normalize_backup_run_status(status: Any) -> BackupRunStatus:
    if isinstance(status, BackupRunStatus):
        return status
    try:
        return BackupRunStatus(str(status or BackupRunStatus.NEVER.value))
    except ValueError:
        return BackupRunStatus.FAILED


def normalize_restore_phase(phase: Any) -> RestorePhase:
    if isinstance(phase, RestorePhase):
        return phase
    return RestorePhase(str(phase or RestorePhase.PRE_BACKUP.value))


def backup_trigger_label(trigger: Any) -> str:
    if isinstance(trigger, BackupTrigger):
        return trigger.value
    return str(trigger or BackupTrigger.MANUAL.value)


class BackupProblem(Exception):
    """Raised when a backup or restic workflow cannot continue."""

    def __init__(self, message: str = '', *, i18n_key: str = '', **params: Any):
        super().__init__(message or i18n_key)
        self.i18n_key = str(i18n_key or '')
        self.i18n_params = dict(params)

    def __str__(self) -> str:
        if self.i18n_key:
            from mcdr2restic.core.i18n import DEFAULT_LANGUAGE, tr
            return tr(DEFAULT_LANGUAGE, self.i18n_key, **self.i18n_params)
        return super().__str__()


class BackupCanceled(BackupProblem):
    """Raised when a running backup is canceled by user or shutdown flow."""


class LocalizedValueError(ValueError):
    """Raised when a pure validation path needs a translatable error."""

    def __init__(self, i18n_key: str, **params: Any):
        super().__init__(i18n_key)
        self.i18n_key = i18n_key
        self.i18n_params = dict(params)

    def __str__(self) -> str:
        from mcdr2restic.core.i18n import DEFAULT_LANGUAGE, tr
        return tr(DEFAULT_LANGUAGE, self.i18n_key, **self.i18n_params)


class CronError(LocalizedValueError):
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
    phase: RestorePhase
    started_at: str
    error: str = ''
    safety_snapshot_id: str = ''
    rollback_error: str = ''

    def __post_init__(self):
        self.phase = normalize_restore_phase(self.phase)


@dataclass
class RestoreStageResult:
    restore_error: str = ''
    rollback_error: str = ''


@dataclass
class BackupRunOutcome:
    status: BackupRunStatus
    message: str
    detail: str
    duration_seconds: int

    def __post_init__(self):
        self.status = normalize_backup_run_status(self.status)

    @property
    def failed(self) -> bool:
        return self.status != BackupRunStatus.SUCCESS

    @property
    def status_value(self) -> str:
        return self.status.value
