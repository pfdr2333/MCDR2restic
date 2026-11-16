# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, cast

from mcdr2restic.backup.cron import CronExpression
from mcdr2restic.core.i18n import FALLBACK_LANGUAGE
from mcdr2restic.core.models import LocalizedValueError
from mcdr2restic.core.utils import get_config_section, non_negative_int
from mcdr2restic.defaults.default_constants import (
    DEFAULT_MAINTENANCE_CRON,
    DEFAULT_MIN_GAP_SECONDS,
)


DEFAULT_NORMAL_CRON = "0 0 0,3,6,9,12,15,18,21 * * *"
DISABLED_CRON = "0"
SCHEDULE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class ScheduleSpec:
    """一次排程的纯计算表示：固定间隔或 cron，二者只生效一个。

    ``next_after`` 以传入时刻为基准推进，调度线程据此维护计划点游标，
    避免每次都从 ``datetime.now()`` 重新起算造成漂移。
    """

    interval_seconds: int = 0
    cron: Optional[CronExpression] = None

    def next_after(self, after: datetime) -> datetime:
        if self.interval_seconds > 0:
            return after + timedelta(seconds=self.interval_seconds)
        if self.cron is None:
            raise LocalizedValueError("error.schedule.interval_negative")
        return self.cron.next_after(after)


@dataclass(frozen=True)
class ScheduledOccurrence:
    """一个到点的定时备份计划，交给推迟 worker 执行。

    deadline_monotonic 以 ``time.monotonic()`` 表达，跨线程共享不受系统
    时间调整影响；worker 越过期限即放弃本次，等下一个计划点。
    """

    scheduled_at: str
    scheduled_datetime: datetime
    next_due: datetime
    deadline_monotonic: Optional[float] = None


def compute_update_check_wait_seconds(cfg: Dict[str, Any]) -> Tuple[float, str]:
    update_cfg = (
        get_config_section(cfg, "update_check")
    )
    daily_time = parse_daily_time(str(update_cfg.get("daily_time", "00:00") or "00:00"))
    now = datetime.now()
    due = now.replace(hour=daily_time[0], minute=daily_time[1], second=0, microsecond=0)
    if due <= now:
        due = due + timedelta(days=1)
    return (due - now).total_seconds(), format_schedule_time(due)


def parse_daily_time(text: str) -> Tuple[int, int]:
    parts = str(text or "").strip().split(":")
    if len(parts) != 2:
        raise LocalizedValueError("error.schedule.daily_time_format")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise LocalizedValueError(
            "error.schedule.daily_time_not_integer", value=text
        ) from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise LocalizedValueError("error.schedule.daily_time_range", value=text)
    return hour, minute


def compute_wait_seconds(
    cfg: Dict[str, Any], language: str = FALLBACK_LANGUAGE
) -> Tuple[float, str]:
    wait = compute_schedule_wait_seconds(
        get_config_section(cfg, "schedule"),
        DEFAULT_NORMAL_CRON,
        disabled_when_zero_cron=False,
        language=language,
    )
    # 该参数组合下 callee 唯一的 None 分支（禁用 cron）不可达
    return cast(Tuple[float, str], wait)


def compute_force_wait_seconds(
    cfg: Dict[str, Any], language: str = FALLBACK_LANGUAGE
) -> Optional[Tuple[float, str]]:
    return compute_schedule_wait_seconds(
        get_config_section(cfg, "force_schedule"),
        DISABLED_CRON,
        disabled_when_zero_cron=True,
        language=language,
    )


def compute_maintenance_wait_seconds(
    cfg: Dict[str, Any], language: str = FALLBACK_LANGUAGE
) -> Optional[Tuple[float, str]]:
    return compute_schedule_wait_seconds(
        get_config_section(cfg, "maintenance_schedule"),
        DEFAULT_MAINTENANCE_CRON,
        disabled_when_zero_cron=True,
        language=language,
        empty_uses_default=True,
    )


def compute_schedule_wait_seconds(
    schedule: Dict[str, Any],
    default_cron: str,
    disabled_when_zero_cron: bool,
    language: str = FALLBACK_LANGUAGE,
    empty_uses_default: bool = False,
) -> Optional[Tuple[float, str]]:
    spec = parse_schedule_spec(
        schedule,
        default_cron,
        disabled_when_zero_cron,
        empty_uses_default=empty_uses_default,
    )
    if spec is None:
        return None
    now = datetime.now()
    next_time = spec.next_after(now)
    return max(0.0, (next_time - now).total_seconds()), format_schedule_time(next_time)


def parse_schedule_spec(
    schedule: Dict[str, Any],
    default_cron: str,
    disabled_when_zero_cron: bool,
    empty_uses_default: bool = False,
) -> Optional[ScheduleSpec]:
    if not isinstance(schedule, dict):
        schedule = {}
    raw_interval = schedule.get("interval_seconds", 0)
    try:
        interval_seconds = int(raw_interval)
    except (TypeError, ValueError) as exc:
        raise LocalizedValueError(
            "error.schedule.interval_not_integer", value=raw_interval
        ) from exc
    if interval_seconds > 0:
        return ScheduleSpec(interval_seconds=interval_seconds)
    if interval_seconds < 0:
        raise LocalizedValueError("error.schedule.interval_negative")
    cron_text = str(schedule.get("cron_expression", default_cron) or "").strip()
    if empty_uses_default and not cron_text:
        cron_text = default_cron
    if disabled_when_zero_cron and cron_text in ("", DISABLED_CRON):
        return None
    return ScheduleSpec(cron=CronExpression(cron_text))


def schedule_min_gap_seconds(cfg: Dict[str, Any]) -> int:
    schedule = get_config_section(cfg, "schedule")
    return non_negative_int(
        schedule.get("min_gap_seconds", DEFAULT_MIN_GAP_SECONDS),
        DEFAULT_MIN_GAP_SECONDS,
    )


def schedule_max_defer_seconds(cfg: Dict[str, Any]) -> int:
    schedule = get_config_section(cfg, "schedule")
    return non_negative_int(schedule.get("max_defer_seconds", 0), 0)


def compute_defer_deadline(
    scheduled_at: datetime,
    next_due: datetime,
    last_duration_seconds: int,
    min_gap_seconds: int,
    max_defer_seconds: int,
) -> datetime:
    """计算本次推迟的期限。

    默认（max_defer_seconds=0）取下一个计划点减去"上次耗时与最小间隔的较大者"：
    给下一次到点留出足够余量，避免推迟出来的备份刚好撞上下一份。显式配置
    max_defer_seconds 时以计划点之后该时长为准。
    """

    if max_defer_seconds > 0:
        return scheduled_at + timedelta(seconds=max_defer_seconds)
    reserve_seconds = max(
        non_negative_int(last_duration_seconds, 0),
        non_negative_int(min_gap_seconds, 0),
    )
    return next_due - timedelta(seconds=reserve_seconds)


def format_schedule_time(moment: datetime) -> str:
    return moment.strftime(SCHEDULE_TIME_FORMAT)


def parse_schedule_time(text: Any) -> Optional[datetime]:
    value = str(text or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, SCHEDULE_TIME_FORMAT)
    except ValueError:
        return None
