# -*- coding: utf-8 -*-
"""[beta] 远端副本落后告警，与本地 RPO 陈旧告警分开计算。

进度只认成功复制：复制失败期间远端最新快照保持不变，落后时长随本地
最新快照与当前时间增长。告警按阈值小时数冷却，成功后复位。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import config_language
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import non_negative_int, now_text
from mcdr2restic.notifications.message_templates import (
    MessageRenderOptions,
    render_message,
)
from mcdr2restic.staging.staging_config import StagingSettings
from mcdr2restic.staging.staging_repository import parse_restic_time
from mcdr2restic.staging.staging_state import (
    staging_runtime_state,
    update_staging_runtime,
)


AdminNotifier = Callable[..., None]
SIMPLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def maybe_warn_remote_stale(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    settings: StagingSettings,
    admin_notifier: Optional[AdminNotifier] = None,
) -> bool:
    hours = settings.remote_stale_warning_hours
    if hours <= 0:
        return False
    runtime_state = staging_runtime_state(app_runtime)
    backlog = non_negative_int(runtime_state.get("staging_copy_backlog", 0))
    local_latest = str(runtime_state.get("staging_local_latest_time") or "").strip()
    if not local_latest:
        return False
    remote_latest = str(runtime_state.get("staging_remote_latest_time") or "").strip()
    if backlog <= 0 and remote_latest:
        return False
    lag_hours = remote_lag_hours(runtime_state, local_latest, remote_latest)
    if lag_hours < hours:
        return False
    if not stale_warning_due(runtime_state, hours):
        return False

    language = config_language(cfg, get_mcdr_language(server))
    values = {
        "hours": int(lag_hours),
        "local_time": local_latest,
        "remote_time": remote_latest or "-",
    }
    text = render_message(
        "staging_remote_stale",
        values,
        cfg,
        MessageRenderOptions(logger=getattr(server, "logger", None), language=language),
    )
    server.logger.warning(text)
    update_staging_runtime(
        app_runtime, server, staging_remote_stale_warned_at=now_text()
    )
    if admin_notifier is not None:
        admin_notifier("staging_remote_stale", values, cfg, False, log=False)
    return True


def remote_lag_hours(
    runtime_state: Dict[str, Any], local_latest: str, remote_latest: str
) -> float:
    local_moment = parse_runtime_time(local_latest)
    remote_moment = parse_restic_time(remote_latest) if remote_latest else None
    if local_moment is not None and remote_moment is not None:
        return max(0.0, (local_moment - remote_moment).total_seconds() / 3600.0)
    reference_text = (
        str(runtime_state.get("staging_last_copy_success_time") or "").strip()
        or local_latest
    )
    reference = parse_runtime_time(reference_text)
    if reference is None:
        return 0.0
    return max(
        0.0, (datetime.now().astimezone() - reference).total_seconds() / 3600.0
    )


def stale_warning_due(runtime_state: Dict[str, Any], hours: int) -> bool:
    warned_at = str(runtime_state.get("staging_remote_stale_warned_at") or "").strip()
    if not warned_at:
        return True
    warned_moment = parse_runtime_time(warned_at)
    if warned_moment is None:
        return True
    return (
        datetime.now().astimezone() - warned_moment
    ).total_seconds() >= hours * 3600


def parse_runtime_time(text: str) -> Optional[datetime]:
    value = str(text or "").strip()
    if not value:
        return None
    moment = parse_restic_time(value)
    if moment is not None:
        return moment
    try:
        return datetime.strptime(value, SIMPLE_TIME_FORMAT).replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
    except ValueError:
        return None


__all__ = [
    "maybe_warn_remote_stale",
    "parse_runtime_time",
    "remote_lag_hours",
    "stale_warning_due",
]
