# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from typing import Any, Dict, Sequence

from mcdr2restic.core.i18n import tr
from mcdr2restic.core.models import ResticProgressState
from mcdr2restic.restic.restic_constants import (
    RESTIC_COMMAND_RESTORE,
    RESTIC_COMMAND_ROLLBACK,
)


RESTORE_ITEM_COUNT_PHASES = frozenset({RESTIC_COMMAND_RESTORE, RESTIC_COMMAND_ROLLBACK})


def format_restic_progress(progress: ResticProgressState, force: bool = False) -> str:
    if force and progress.summary:
        return format_restic_summary(progress)
    if progress.status:
        return format_restic_status(progress)
    elapsed = int(time.monotonic() - progress.started_at)
    return tr(
        progress.language, "restic.running", phase=progress.phase, elapsed=elapsed
    )


def format_restic_status(progress: ResticProgressState) -> str:
    status = progress.status or {}
    values = build_restic_status_values(status)
    current_text = current_files_text(status.get("current_files", []))
    return format_restic_status_text(
        progress.language, progress.phase, values, current_text
    )


def build_restic_status_values(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "percent": get_restic_percent(status),
        "files_done": first_int(status, ["files_done", "files_restored"]),
        "total_files": first_int(status, ["total_files"]),
        "bytes_done": first_int(status, ["bytes_done", "bytes_restored"]),
        "total_bytes": first_int(status, ["total_bytes"]),
    }


def current_files_text(current_files: Any) -> str:
    if not isinstance(current_files, list):
        return ""
    return ", ".join(str(item) for item in current_files[-2:])


def format_restic_status_text(
    language: str, phase: str, values: Dict[str, Any], current_text: str
) -> str:
    parts = [
        tr(language, "restic.progress.title", phase=phase, percent=values["percent"])
    ]
    append_status_details(parts, language, values, current_text)
    return progress_joiner(language).join(parts)


def append_status_details(
    parts: list, language: str, values: Dict[str, Any], current_text: str
):
    if values["total_files"] > 0:
        parts.append(
            tr(
                language,
                "restic.progress.files",
                done=values["files_done"],
                total=values["total_files"],
            )
        )
    if values["total_bytes"] > 0:
        parts.append(
            tr(
                language,
                "restic.progress.data",
                done=format_bytes(values["bytes_done"]),
                total=format_bytes(values["total_bytes"]),
            )
        )
    if current_text:
        parts.append(tr(language, "restic.progress.current", current=current_text))


def format_restic_summary(progress: ResticProgressState) -> str:
    values = build_restic_summary_values(progress)
    return format_restic_summary_text(progress.language, progress.phase, values)


def build_restic_summary_values(progress: ResticProgressState) -> Dict[str, Any]:
    summary = progress.summary or {}
    return {
        "snapshot_id": str(summary.get("snapshot_id") or "").strip(),
        "total_files": first_int(summary, ["total_files_processed", "total_files"]),
        "done_files": first_int(
            summary, ["files_restored", "total_files_processed", "total_files"]
        ),
        "total_bytes": first_int(summary, ["total_bytes_processed", "total_bytes"]),
        "done_bytes": first_int(
            summary, ["bytes_restored", "total_bytes_processed", "total_bytes"]
        ),
        "duration": first_float(summary, ["total_duration"]),
    }


def format_restic_summary_text(
    language: str, phase: str, values: Dict[str, Any]
) -> str:
    parts = [tr(language, "restic.summary.title", phase=phase)]
    append_summary_file_part(parts, language, phase, values)
    append_common_summary_parts(parts, language, values)
    return progress_joiner(language).join(parts)


def append_summary_file_part(
    parts: list, language: str, phase: str, values: Dict[str, Any]
):
    if values["total_files"] <= 0:
        return
    if (
        values["done_files"] > values["total_files"]
        and phase in RESTORE_ITEM_COUNT_PHASES
    ):
        parts.append(
            tr(
                language,
                "restic.summary.restored_items",
                done=values["done_files"],
                total=values["total_files"],
            )
        )
        return
    parts.append(
        tr(
            language,
            "restic.summary.files",
            done=values["done_files"],
            total=values["total_files"],
        )
    )


def append_common_summary_parts(parts: list, language: str, values: Dict[str, Any]):
    if values["total_bytes"] > 0:
        parts.append(
            tr(
                language,
                "restic.summary.data",
                done=format_bytes(values["done_bytes"]),
                total=format_bytes(values["total_bytes"]),
            )
        )
    if values["duration"] > 0:
        parts.append(
            tr(
                language,
                "restic.summary.duration",
                seconds="{:.1f}".format(values["duration"]),
            )
        )
    if values["snapshot_id"]:
        parts.append(
            tr(
                language,
                "restic.summary.snapshot",
                snapshot_id=values["snapshot_id"][:8],
            )
        )


def progress_joiner(language: str) -> str:
    return (
        "，" if str(language or "").lower().replace("-", "_").startswith("zh") else ", "
    )


def format_restic_json_error(payload: Dict[str, Any]) -> str:
    parts = build_restic_json_error_parts(payload)
    return ": ".join(parts)


def build_restic_json_error_parts(payload: Dict[str, Any]) -> list:
    message_type = str(payload.get("message_type") or "error")
    error_value = payload.get("error")
    message = restic_error_message(payload, error_value)
    during = str(payload.get("during") or "").strip()
    item = str(payload.get("item") or "").strip()
    parts = [message_type]
    if during:
        parts.append(during)
    if item:
        parts.append(item)
    if message:
        parts.append(message)
    return parts


def restic_error_message(payload: Dict[str, Any], error_value: Any) -> str:
    if isinstance(error_value, dict):
        return str(error_value.get("message") or error_value)
    return str(payload.get("message") or error_value or "")


def get_restic_percent(payload: Dict[str, Any]) -> str:
    try:
        value = float(payload.get("percent_done"))
    except Exception:
        return "?"
    if value <= 1.0:
        value *= 100.0
    return "{:.1f}%".format(max(0.0, min(100.0, value)))


def first_int(payload: Dict[str, Any], names: Sequence[str]) -> int:
    for name in names:
        try:
            value = payload.get(name)
            if value is not None:
                return max(0, int(value))
        except Exception:
            continue
    return 0


def first_float(payload: Dict[str, Any], names: Sequence[str]) -> float:
    for name in names:
        try:
            value = payload.get(name)
            if value is not None:
                return max(0.0, float(value))
        except Exception:
            continue
    return 0.0


def format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return "{} {}".format(int(size), unit)
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0
