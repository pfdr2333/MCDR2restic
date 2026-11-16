# -*- coding: utf-8 -*-
"""门控长期拦下备份时的陈旧备份告警。

告警只负责把代价讲清楚，不催促管理员立刻手动补备份：门控放行后
备份会自动开始。计时以最后一次成功备份为锚点，告警后 24 小时冷却，
成功备份即复位；服务器未运行时门控不会进入等待，也不会告警。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.scheduling import parse_schedule_time
from mcdr2restic.config.state_store import (
    ensure_runtime,
    save_config_unlocked,
)
from mcdr2restic.core.i18n import config_language, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.utils import get_config_section, non_negative_int, now_text
from mcdr2restic.defaults.default_constants import DOCUMENTATION_CONFIGURATION_URL
from mcdr2restic.minecraft.minecraft_service import server_is_running


STALE_WARNING_COOLDOWN_HOURS = 24

AdminNotifier = Callable[[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool], None]


def maybe_warn_stale_backup(
    app_runtime,
    server: Optional[PluginServerInterface],
    cfg: Dict[str, Any],
    admin_notifier: AdminNotifier,
    gate_sample: tuple[Optional[int], int],
) -> bool:
    """告警失败不得影响门控等待本身，异常只降级为一条日志。"""

    try:
        return _maybe_warn_stale_backup(
            app_runtime, server, cfg, admin_notifier, gate_sample
        )
    except Exception as exc:
        if server is not None:
            server.logger.warning(
                tr(
                    config_language(cfg, get_mcdr_language(server)),
                    "warn.backup.stale_warning_failed",
                    error=exc,
                )
            )
        return False


def _maybe_warn_stale_backup(
    app_runtime,
    server: Optional[PluginServerInterface],
    cfg: Dict[str, Any],
    admin_notifier: AdminNotifier,
    gate_sample: tuple[Optional[int], int],
) -> bool:
    current, max_online = gate_sample
    if server is None or not server_is_running(app_runtime, server):
        return False
    threshold_hours = non_negative_int(
        get_config_section(cfg, "notification").get("stale_backup_warning_hours", 24),
        24,
    )
    if threshold_hours <= 0:
        return False
    last_success_text, last_success = resolve_last_success_backup(cfg)
    if last_success is None:
        return False
    now = datetime.now()
    stale_hours = max(0, int((now - last_success).total_seconds() // 3600))
    if stale_hours < threshold_hours:
        return False
    runtime_state = get_config_section(cfg, "runtime")
    warned_at = parse_schedule_time(runtime_state.get("stale_backup_warned_at"))
    if warned_at is not None and now - warned_at < timedelta(
        hours=STALE_WARNING_COOLDOWN_HOURS
    ):
        return False

    language = config_language(cfg, get_mcdr_language(server))
    values = {
        "hours": stale_hours,
        "last_success": last_success_text,
        "skips": max(
            1,
            non_negative_int(
                runtime_state.get("gate_deferral_count_since_success", 0)
            ),
        ),
        "current": current if current is not None else "-",
        "max_online": max_online,
        "root": str(get_config_section(cfg, "command").get("root", "!!restic")),
        "doc_url": DOCUMENTATION_CONFIGURATION_URL,
    }
    server.logger.warning(
        tr(
            language,
            "warn.backup.stale_backup_warning",
            hours=values["hours"],
            last_success=values["last_success"],
            skips=values["skips"],
            current=values["current"],
            max_online=values["max_online"],
        )
    )
    admin_notifier("backup_stale_warning", values, cfg, True)
    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        app_runtime.config_state.config["runtime"]["stale_backup_warned_at"] = now_text()
        save_config_unlocked(app_runtime, server)
    return True


def resolve_last_success_backup(
    cfg: Dict[str, Any],
) -> tuple[str, Optional[datetime]]:
    """读取最后成功备份时间；老状态文件没有该字段时回退到上次成功结束时间。"""

    runtime_state = get_config_section(cfg, "runtime")
    text = str(runtime_state.get("last_success_backup_end_time") or "").strip()
    moment = parse_schedule_time(text)
    if moment is not None:
        return text, moment
    if str(runtime_state.get("last_backup_status") or "") != "success":
        return "", None
    fallback_text = str(runtime_state.get("last_backup_end_time") or "").strip()
    return fallback_text, parse_schedule_time(fallback_text)
