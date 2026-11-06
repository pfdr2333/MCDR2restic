# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.scheduling import (
    compute_force_wait_seconds,
    compute_wait_seconds,
)
from mcdr2restic.core.i18n import DEFAULT_LANGUAGE, normalize_translate, tr_error
from mcdr2restic.core.models import BackupRunStatus
from mcdr2restic.restic.restic_guidance import command_root_from_config
from mcdr2restic.snapshots.snapshot_cache import (
    build_snapshot_cache_key,
    ensure_snapshot_cache_fresh,
    get_snapshot_cache_config,
    get_snapshot_page_size,
)
from mcdr2restic.snapshots.snapshot_db import (
    read_snapshot_page,
)
from mcdr2restic.snapshots.snapshot_query import SnapshotQueryContext
from mcdr2restic.core.utils import get_config_section, non_negative_int


RESTIC_TIME_DISPLAY_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})")
SNAPSHOT_PATHS_PREVIEW_MAX_LENGTH = 80
SNAPSHOT_PATHS_PREVIEW_TRUNCATE_AT = 77


@dataclass(frozen=True)
class StatusViewContext:
    """状态页与快照页共享的渲染上下文。"""

    cfg: Dict[str, Any]
    language: str
    translate: Callable[..., str]
    server: Optional[PluginServerInterface] = None

    @classmethod
    def create(
        cls,
        cfg: Dict[str, Any],
        language: str,
        server: Optional[PluginServerInterface],
        translate: Optional[Callable[..., str]] = None,
    ) -> "StatusViewContext":
        return cls(
            cfg=cfg,
            language=language,
            translate=normalize_translate(translate or language),
            server=server,
        )


class RuntimeStatusProviders(NamedTuple):
    """状态页所需的运行时状态探针。"""

    backup_running: Callable[[], bool]
    restore_running: Callable[[], bool]
    mc_ready: Callable[[Optional[PluginServerInterface]], bool]


def render_status_output(
    ctx: StatusViewContext,
    providers: RuntimeStatusProviders,
    snapshot_query_lock: threading.Lock,
    snapshot_page: int,
) -> str:
    status_view = build_status_view(ctx, providers)
    snapshot_lines = render_snapshot_status_lines(
        ctx, snapshot_query_lock, snapshot_page
    )
    return "\n".join(format_status_view(status_view, ctx.translate) + snapshot_lines)


def build_status_view(
    ctx: StatusViewContext, providers: RuntimeStatusProviders
) -> Dict[str, Any]:
    cfg = ctx.cfg
    translate = ctx.translate
    running = providers.backup_running()
    activity = build_player_activity_view(get_config_section(cfg, "runtime"), translate)
    schedules = build_schedule_status_view(cfg, translate, ctx.language)
    last_backup_status_raw = activity.pop("last_backup_status_raw")
    return {
        "enabled": localized_bool(bool(cfg.get("enabled", True)), translate),
        "backup_running": localized_bool(running, translate),
        "restore_running": localized_bool(providers.restore_running(), translate),
        "mc_ready": localized_bool(providers.mc_ready(ctx.server), translate),
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
    ctx: StatusViewContext,
    snapshot_query_lock: threading.Lock,
    page: int,
) -> List[str]:
    translate = ctx.translate
    server = ctx.server
    page = max(1, int(page))
    title = translate("snapshot.title")
    if server is None:
        return render_snapshot_status_message(title, translate, "snapshot.unavailable")

    restic_cfg = get_config_section(ctx.cfg, "restic")
    snapshot_cfg = get_snapshot_cache_config(ctx.cfg)
    if not bool(snapshot_cfg.get("enabled", True)):
        return render_snapshot_status_message(
            title, translate, "snapshot.cache_disabled"
        )

    query = SnapshotQueryContext(
        query_lock=snapshot_query_lock,
        server=server,
        restic_cfg=restic_cfg,
        snapshot_cfg=snapshot_cfg,
        language=ctx.language,
        command_root=command_root_from_config(ctx.cfg),
    )
    try:
        page_context = load_snapshot_status_page(query, page)
    except Exception as exc:
        return render_snapshot_status_message(
            title, translate, "snapshot.query_failed", error=tr_error(ctx.language, exc)
        )

    return format_snapshot_status_page(ctx, title, page_context)


def render_snapshot_status_message(
    title: str, translate: Callable[..., str], message_key: str, **params: Any
) -> List[str]:
    return ["", "{}:".format(title), "  {}".format(translate(message_key, **params))]


def load_snapshot_status_page(
    query: SnapshotQueryContext,
    requested_page: int,
) -> Dict[str, Any]:
    page_size = get_snapshot_page_size(query.snapshot_cfg)
    cache_key = build_snapshot_cache_key(query.restic_cfg)
    refresh_note = ensure_snapshot_cache_fresh(query)
    page_data = read_snapshot_page(
        query.server, cache_key, requested_page, page_size, query.snapshot_cfg
    )
    total = int(page_data.get("total", 0))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(requested_page, total_pages)
    if page != requested_page:
        page_data = read_snapshot_page(
            query.server, cache_key, page, page_size, query.snapshot_cfg
        )
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "refresh_note": refresh_note,
        "page_data": page_data,
    }


def format_snapshot_status_page(
    ctx: StatusViewContext,
    title: str,
    page_context: Dict[str, Any],
) -> List[str]:
    page = int(page_context["page"])
    page_data = page_context["page_data"]
    lines = ["", "{}:".format(title)]
    append_snapshot_cache_summary(lines, ctx.translate, page_context)
    append_snapshot_cache_notes(
        lines, ctx.translate, page_data, str(page_context.get("refresh_note") or "")
    )
    append_snapshot_rows(lines, ctx, page, page_context)
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
    ctx: StatusViewContext,
    page: int,
    page_context: Dict[str, Any],
):
    translate = ctx.translate
    page_data = page_context["page_data"]
    rows = page_data.get("rows", [])
    if not rows:
        lines.append("  {}".format(translate("snapshot.no_snapshots")))
        return

    page_size = int(page_context["page_size"])
    for index, row in enumerate(rows, start=(page - 1) * page_size + 1):
        lines.append(format_snapshot_line(index, row))
    append_snapshot_next_page_hint(
        lines, ctx, page, int(page_context["total_pages"])
    )


def append_snapshot_next_page_hint(
    lines: List[str],
    ctx: StatusViewContext,
    page: int,
    total_pages: int,
):
    if page >= total_pages:
        return
    root = str(get_config_section(ctx.cfg, "command").get("root", "!!restic"))
    lines.append(ctx.translate("snapshot.next_page", root=root, page=page + 1))


def format_snapshot_line(index: int, row: sqlite3.Row) -> str:
    short_id = str(row["short_id"] or row["id"] or "")[:8]
    time_text = format_restic_time_for_display(str(row["time_text"] or ""))
    host = str(row["hostname"] or "")
    tags = str(row["tags_text"] or "")
    paths = str(row["paths_text"] or "")
    if len(paths) > SNAPSHOT_PATHS_PREVIEW_MAX_LENGTH:
        paths = paths[:SNAPSHOT_PATHS_PREVIEW_TRUNCATE_AT] + "..."
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
        interval_text = localized_fixed_interval_text(schedule, translate)
        if interval_text is not None:
            # 固定间隔模式下"X 秒后"与间隔重复，直接返回间隔文案（P3-18）
            return interval_text
        return translate(
            "status.schedule.wait", seconds=int(wait_seconds), detail=due_text
        )
    except Exception as exc:
        return translate(
            "status.schedule.cannot_calculate", error=tr_error(language, exc)
        )


def localized_fixed_interval_text(
    schedule: Dict[str, Any], translate_or_language: Any
) -> Optional[str]:
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
    return None


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
        BackupRunStatus.INTERRUPTED.value: "backup.status.interrupted",
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
