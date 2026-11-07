# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Type

from mcdr2restic.core.i18n import tr


class TextEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class BackupRunStatus(TextEnum):
    NEVER = "never"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    # 进程被杀时 state.yml 会残留 running，启动检测到后改写为该状态
    INTERRUPTED = "interrupted"


class BackupTrigger(TextEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    FORCED = "forced"
    RESTORE_PRE_BACKUP = "restore-pre-backup"


class RestorePhase(TextEnum):
    PRE_BACKUP = "pre_backup"
    STOPPING = "stopping"
    RESTORING = "restoring"
    STARTING = "starting"


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


# 触发标签的展示映射：中文消息中不直出英文枚举值；未映射值（如内部状态标记）保持原样
TRIGGER_DISPLAY_LABEL_KEYS = {
    BackupTrigger.MANUAL.value: "label.trigger.manual",
    BackupTrigger.SCHEDULED.value: "label.trigger.scheduled",
    BackupTrigger.FORCED.value: "label.trigger.forced",
    BackupTrigger.RESTORE_PRE_BACKUP.value: "label.trigger.restore_pre_backup",
}


def backup_trigger_display_label(trigger: Any, language: str) -> str:
    text = backup_trigger_label(trigger)
    key = TRIGGER_DISPLAY_LABEL_KEYS.get(text)
    if key is None:
        return text
    return tr(language, key)


class BackupProblem(Exception):
    """Raised when a backup or restic workflow cannot continue."""

    def __init__(self, message: str = "", *, i18n_key: str = "", **params: Any):
        super().__init__(message or i18n_key)
        self.i18n_key = str(i18n_key or "")
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


class ConfigError(LocalizedValueError):
    """Raised when the plugin configuration cannot be loaded or migrated."""


# 预期业务错误：消息已本地化、对用户可解释，日志与回复只取干净消息，
# 不附 traceback；其余异常按未预期故障处理，保留完整堆栈便于排查
EXPECTED_BUSINESS_ERRORS: Tuple[Type[Exception], ...] = (
    BackupProblem,
    LocalizedValueError,
)


@dataclass
class ResticCommandResult:
    phase: str
    args: List[str]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    summary: Optional[Dict[str, Any]] = None
    json_errors: Optional[List[str]] = None
    snapshot_id: str = ""


@dataclass
class ResticProgressState:
    phase: str
    language: str
    status: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
    json_errors: Optional[List[str]] = None
    last_text: str = ""
    seen_json: bool = False
    started_at: float = 0.0
    last_emit_at: float = 0.0
    last_emit_text: str = ""


@dataclass
class RestoreSession:
    tasks: List[Dict[str, Any]]
    cfg: Dict[str, Any]
    snapshot_cfg: Dict[str, Any]
    cache_key: str
    language: str
    phase: RestorePhase
    error: str = ""
    safety_snapshot_id: str = ""
    rollback_error: str = ""
    skip_safety_snapshot: bool = False

    def __post_init__(self):
        self.phase = normalize_restore_phase(self.phase)


@dataclass
class RestoreStageResult:
    restore_error: str = ""
    rollback_error: str = ""


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
