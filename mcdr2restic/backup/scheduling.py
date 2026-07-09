# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from mcdr2restic.backup.cron import CronExpression


DEFAULT_NORMAL_CRON = '0 0 0,3,6,9,12,15,18,21 * * *'
DISABLED_CRON = '0'


def compute_update_check_wait_seconds(cfg: Dict[str, Any]) -> Tuple[float, str]:
    update_cfg = cfg.get('update_check', {}) if isinstance(cfg.get('update_check'), dict) else {}
    daily_time = parse_daily_time(str(update_cfg.get('daily_time', '00:00') or '00:00'))
    now = datetime.now()
    due = now.replace(hour=daily_time[0], minute=daily_time[1], second=0, microsecond=0)
    if due <= now:
        due = due + timedelta(days=1)
    return (due - now).total_seconds(), due.strftime('%Y-%m-%d %H:%M:%S')


def parse_daily_time(text: str) -> Tuple[int, int]:
    parts = str(text or '').strip().split(':')
    if len(parts) != 2:
        raise ValueError('daily_time 必须是 HH:MM')
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError('daily_time 超出范围: {}'.format(text))
    return hour, minute


def compute_wait_seconds(cfg: Dict[str, Any]) -> Tuple[float, str]:
    return compute_schedule_wait_seconds(
        cfg.get('schedule', {}),
        DEFAULT_NORMAL_CRON,
        disabled_when_zero_cron=False
    )


def compute_force_wait_seconds(cfg: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    return compute_schedule_wait_seconds(cfg.get('force_schedule', {}), DISABLED_CRON, disabled_when_zero_cron=True)


def compute_schedule_wait_seconds(
    schedule: Dict[str, Any],
    default_cron: str,
    disabled_when_zero_cron: bool
) -> Optional[Tuple[float, str]]:
    if not isinstance(schedule, dict):
        schedule = {}
    interval_seconds = int(schedule.get('interval_seconds', 0))
    if interval_seconds > 0:
        return float(interval_seconds), '固定间隔 {} 秒'.format(interval_seconds)
    if interval_seconds < 0:
        raise ValueError('interval_seconds 不能小于 0')
    cron_text = str(schedule.get('cron_expression', default_cron) or '').strip()
    if disabled_when_zero_cron and cron_text in ('', DISABLED_CRON):
        return None
    cron = CronExpression(cron_text)
    now = datetime.now()
    next_time = cron.next_after(now)
    return max(0.0, (next_time - datetime.now()).total_seconds()), next_time.strftime('%Y-%m-%d %H:%M:%S')
