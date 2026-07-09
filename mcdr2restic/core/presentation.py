# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.scheduling import (
    compute_force_wait_seconds,
    compute_wait_seconds,
)
from mcdr2restic.core.i18n import DEFAULT_LANGUAGE, normalize_translate, tr_error
from mcdr2restic.core.models import BackupRunStatus
from mcdr2restic.snapshots.snapshot_cache import (
    build_snapshot_cache_key,
    ensure_snapshot_cache_fresh,
    get_snapshot_cache_config,
    get_snapshot_page_size,
)
from mcdr2restic.snapshots.snapshot_db import (
    read_snapshot_page,
)
from mcdr2restic.core.utils import non_negative_int


RESTIC_TIME_DISPLAY_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})")


def render_status_output(
    snapshot_query_lock: threading.Lock,
    cfg: Dict[str, Any],
    language: str,
    server: Optional[PluginServerInterface],
    snapshot_page: int,
    backup_running_provider: Callable[[], bool],
    restore_running_provider: Callable[[], bool],
    mc_ready_provider: Callable[[Optional[PluginServerInterface]], bool],
    translate: Optional[Callable[..., str]] = None,
) -> str:
    translate = normalize_translate(translate or language)
    status_view = build_status_view(
        cfg,
        translate,
        language,
        server,
        backup_running_provider,
        restore_running_provider,
        mc_ready_provider,
    )
    snapshot_lines = render_snapshot_status_lines(
        snapshot_query_lock, cfg, translate, language, server, snapshot_page
    )
    return "\n".join(format_status_view(status_view, translate) + snapshot_lines)


def build_status_view(
    cfg: Dict[str, Any],
    translate: Callable[..., str],
    language: str,
    server: Optional[PluginServerInterface],
    backup_running_provider: Callable[[], bool],
    restore_running_provider: Callable[[], bool],
    mc_ready_provider: Callable[[Optional[PluginServerInterface]], bool],
) -> Dict[str, Any]:
    running = backup_running_provider()
    activity = build_player_activity_view(cfg.get("runtime", {}), translate)
    schedules = build_schedule_status_view(cfg, translate, language)
    last_backup_status_raw = activity.pop("last_backup_status_raw")
    return {
        "enabled": localized_bool(bool(cfg.get("enabled", True)), translate),
        "backup_running": localized_bool(running, translate),
        "restore_running": localized_bool(restore_running_provider(), translate),
        "mc_ready": localized_bool(mc_ready_provider(server), translate),
        **activity,
        **schedules,
        "last_backup_status": localized_backup_status(
            last_backup_status_raw, translate
        ),
    }


def build_player_activity_view(
    runtime_state: Dict[str, Any], translate: Callable[..., str]
) -> Dict[str, Any]:
    joined = bool(runtime_state.get("player_joined_since_last_check", False))
    joined = joined or bool(runtime_state.get("player_joined_since_last_backup", False))
    return {
        "current_online": non_negative_int(
            runtime_state.get("current_online_players", 0)
        ),
        "joined": localized_bool(joined, translate),
        "left": localized_bool(
            bool(runtime_state.get("player_left_since_last_check", False)), translate
        ),
        "last_online_check": runtime_state.get("last_online_check")
        or localized_never(translate),
        "last_online_source": localized_online_source(
            runtime_state.get("last_online_check_source"), translate
        ),
        "last_backup_status_raw": runtime_state.get(
            "last_backup_status", BackupRunStatus.NEVER.value
        ),
    }


def build_schedule_status_view(
    cfg: Dict[str, Any], translate: Callable[..., str], language: str
) -> Dict[str, str]:
    return {
        "normal_next_text": schedule_status_text(cfg, False, translate, language),
        "force_next_text": schedule_status_text(cfg, True, translate, language),
    }


def format_status_view(
    status_view: Dict[str, Any], translate: Callable[..., str]
) -> List[str]:
    return [
        translate("status.title"),
        translate("status.enabled", value=status_view["enabled"]),
        translate("status.backup_running", value=status_view["backup_running"]),
        translate("status.restore_running", value=status_view["restore_running"]),
        translate("status.minecraft_ready", value=status_view["mc_ready"]),
        translate("status.player_activity"),
        translate("status.current_online", value=status_view["current_online"]),
        translate("status.joined", value=status_view["joined"]),
        translate("status.left", value=status_view["left"]),
        translate("status.last_online_check", value=status_view["last_online_check"]),
        translate("status.check_source", value=status_view["last_online_source"]),
        translate("status.schedules"),
        translate("status.normal_backup", value=status_view["normal_next_text"]),
        translate("status.forced_backup", value=status_view["force_next_text"]),
        translate("status.last_backup_status", value=status_view["last_backup_status"]),
    ]


def render_snapshot_status_lines(
    snapshot_query_lock: threading.Lock,
    cfg: Dict[str, Any],
    translate: Callable[..., str],
    language: str,
    server: Optional[PluginServerInterface],
    page: int,
) -> List[str]:
    page = max(1, int(page))
    title = translate("snapshot.title")
    if server is None:
        return render_snapshot_status_message(title, translate, "snapshot.unavailable")

    restic_cfg = cfg.get("restic", {}) if isinstance(cfg.get("restic"), dict) else {}
    snapshot_cfg = get_snapshot_cache_config(cfg)
    if not bool(snapshot_cfg.get("enabled", True)):
        return render_snapshot_status_message(
            title, translate, "snapshot.cache_disabled"
        )

    try:
        page_context = load_snapshot_status_page(
            snapshot_query_lock, server, restic_cfg, snapshot_cfg, language, page
        )
    except Exception as exc:
        return render_snapshot_status_message(
            title, translate, "snapshot.query_failed", error=tr_error(language, exc)
        )

    return format_snapshot_status_page(cfg, translate, language, title, page_context)


def render_snapshot_status_message(
    title: str, translate: Callable[..., str], message_key: str, **params: Any
) -> List[str]:
    return ["", "{}:".format(title), "  {}".format(translate(message_key, **params))]


def load_snapshot_status_page(
    snapshot_query_lock: threading.Lock,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    snapshot_cfg: Dict[str, Any],
    language: str,
    requested_page: int,
) -> Dict[str, Any]:
    page_size = get_snapshot_page_size(snapshot_cfg)
    cache_key = build_snapshot_cache_key(restic_cfg)
    refresh_note = ensure_snapshot_cache_fresh(
        snapshot_query_lock, server, restic_cfg, cache_key, snapshot_cfg, language
    )
    page_data = read_snapshot_page(
        server, cache_key, requested_page, page_size, snapshot_cfg
    )
    total = int(page_data.get("total", 0))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(requested_page, total_pages)
    if page != requested_page:
        page_data = read_snapshot_page(server, cache_key, page, page_size, snapshot_cfg)
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "refresh_note": refresh_note,
        "page_data": page_data,
    }


def format_snapshot_status_page(
    cfg: Dict[str, Any],
    translate: Callable[..., str],
    language: str,
    title: str,
    page_context: Dict[str, Any],
) -> List[str]:
    page = int(page_context["page"])
    page_data = page_context["page_data"]
    lines = ["", "{}:".format(title)]
    append_snapshot_cache_summary(lines, translate, page_context)
    append_snapshot_cache_notes(
        lines, translate, page_data, str(page_context.get("refresh_note") or "")
    )
    append_snapshot_rows(lines, cfg, translate, language, page, page_context)
    return lines


def append_snapshot_cache_summary(
    lines: List[str], translate: Callable[..., str], page_context: Dict[str, Any]
):
    updated_at = page_context["page_data"].get("updated_at_text") or localized_never(
        translate
    )
    total = int(page_context["total"])
    page = int(page_context["page"])
    total_pages = int(page_context["total_pages"])
    lines.append(
        translate(
            "snapshot.cache.summary",
            updated_at=updated_at,
            total=total,
            page=page,
            total_pages=total_pages,
        )
    )


def append_snapshot_cache_notes(
    lines: List[str],
    translate: Callable[..., str],
    page_data: Dict[str, Any],
    refresh_note: str,
):
    error = str(page_data.get("error") or "").strip()
    invalidated = bool(page_data.get("invalidated", False))
    invalidation_reason = str(page_data.get("invalidation_reason") or "").strip()
    if refresh_note:
        lines.append("  {}".format(refresh_note))
    if invalidated and invalidation_reason:
        lines.append(
            translate("snapshot.cache.invalidated", reason=invalidation_reason)
        )
    if error:
        lines.append(translate("snapshot.cache.stale_error", error=error))


def append_snapshot_rows(
    lines: List[str],
    cfg: Dict[str, Any],
    translate: Callable[..., str],
    language: str,
    page: int,
    page_context: Dict[str, Any],
):
    page_data = page_context["page_data"]
    rows = page_data.get("rows", [])
    if not rows:
        lines.append("  {}".format(translate("snapshot.no_snapshots")))
        return

    page_size = int(page_context["page_size"])
    for index, row in enumerate(rows, start=(page - 1) * page_size + 1):
        lines.append(format_snapshot_line(index, row, translate))
    append_snapshot_next_page_hint(
        lines, cfg, translate, page, int(page_context["total_pages"])
    )


def append_snapshot_next_page_hint(
    lines: List[str],
    cfg: Dict[str, Any],
    translate: Callable[..., str],
    page: int,
    total_pages: int,
):
    if page >= total_pages:
        return
    root = str(cfg.get("command", {}).get("root", "!!restic"))
    lines.append(translate("snapshot.next_page", root=root, page=page + 1))


def format_snapshot_line(
    index: int, row: sqlite3.Row, translate: Callable[..., str]
) -> str:
    short_id = str(row["short_id"] or row["id"] or "")[:8]
    time_text = format_restic_time_for_display(str(row["time_text"] or ""))
    host = str(row["hostname"] or "")
    tags = str(row["tags_text"] or "")
    paths = str(row["paths_text"] or "")
    if len(paths) > 80:
        paths = paths[:77] + "..."
    extras = []
    if host:
        extras.append(host)
    if tags:
        extras.append("#{}".format(tags))
    if paths:
        extras.append(paths)
    detail = " | ".join(extras)
    if detail:
        return "  {}. {} {} | {}".format(index, short_id, time_text, detail)
    return "  {}. {} {}".format(index, short_id, time_text)


def format_restic_time_for_display(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    match = RESTIC_TIME_DISPLAY_PATTERN.match(text)
    if match:
        return "{} {}".format(match.group(1), match.group(2))
    return text


def schedule_status_text(
    cfg: Dict[str, Any],
    forced: bool,
    translate_or_language: Any,
    language: Optional[str] = None,
) -> str:
    translate = normalize_translate(translate_or_language)
    if language is None:
        language = (
            translate_or_language
            if isinstance(translate_or_language, str)
            else DEFAULT_LANGUAGE
        )
    try:
        result = (
            compute_force_wait_seconds(cfg, language)
            if forced
            else compute_wait_seconds(cfg, language)
        )
        if result is None:
            return translate("status.schedule.disabled")
        wait_seconds, due_text = result
        schedule = cfg.get("force_schedule" if forced else "schedule", {})
        detail = localized_schedule_detail(schedule, due_text, translate)
        return translate(
            "status.schedule.wait", seconds=int(wait_seconds), detail=detail
        )
    except Exception as exc:
        return translate(
            "status.schedule.cannot_calculate", error=tr_error(language, exc)
        )


def localized_schedule_detail(
    schedule: Dict[str, Any], due_text: str, translate_or_language: Any
) -> str:
    translate = normalize_translate(translate_or_language)
    try:
        interval_seconds = (
            int(schedule.get("interval_seconds", 0))
            if isinstance(schedule, dict)
            else 0
        )
    except Exception:
        interval_seconds = 0
    if interval_seconds > 0:
        return translate("status.schedule.fixed_interval", seconds=interval_seconds)
    return due_text


def localized_bool(value: bool, translate_or_language: Any) -> str:
    translate = normalize_translate(translate_or_language)
    return translate("status.bool.yes" if value else "status.bool.no")


def localized_never(translate_or_language: Any) -> str:
    translate = normalize_translate(translate_or_language)
    return translate("status.never")


def localized_backup_status(status: Any, translate_or_language: Any) -> str:
    translate = normalize_translate(translate_or_language)
    text = str(status or BackupRunStatus.NEVER.value)
    mapping = {
        BackupRunStatus.NEVER.value: "backup.status.never",
        BackupRunStatus.RUNNING.value: "backup.status.running",
        BackupRunStatus.SUCCESS.value: "backup.status.success",
        BackupRunStatus.FAILED.value: "backup.status.failed",
        BackupRunStatus.CANCELED.value: "backup.status.canceled",
    }
    key = mapping.get(text)
    return translate(key) if key else text


def localized_online_source(source: Any, translate_or_language: Any) -> str:
    translate = normalize_translate(translate_or_language)
    if not source:
        return localized_never(translate)
    text = str(source)
    mapping = {
        "join event": "online.source.join_event",
        "left event": "online.source.left_event",
        "server stop": "online.source.server_stop",
    }
    if text.startswith("rcon "):
        return translate("online.source.rcon", command=text[5:])
    key = mapping.get(text)
    return translate(key) if key else text
