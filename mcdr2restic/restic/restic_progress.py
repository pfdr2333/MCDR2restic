# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from typing import Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.models import ResticProgressState
from mcdr2restic.restic.restic_progress_text import format_restic_json_error, format_restic_progress
from mcdr2restic.core.utils import tail_text


def build_restic_progress_state(phase: str, language: str, started: float) -> ResticProgressState:
    return ResticProgressState(
        phase=phase,
        language=language,
        json_errors=[],
        started_at=started,
        last_emit_at=started
    )


def handle_restic_stream_line(
    server: Optional[PluginServerInterface],
    progress: ResticProgressState,
    stream_name: str,
    line: str,
):
    text = str(line or '').strip()
    if not text:
        return
    progress.last_text = text
    if not text.startswith('{'):
        return
    handle_restic_json_line(server, progress, text)


def handle_restic_json_line(
    server: Optional[PluginServerInterface],
    progress: ResticProgressState,
    text: str,
):
    try:
        payload = json.loads(text)
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    progress.seen_json = True
    apply_restic_json_payload(server, progress, text, payload)


def apply_restic_json_payload(
    server: Optional[PluginServerInterface],
    progress: ResticProgressState,
    text: str,
    payload: dict,
):
    message_type = str(payload.get('message_type') or '').strip()
    if message_type == 'status':
        progress.status = payload
    elif message_type == 'summary':
        progress.summary = payload
    elif message_type in ('error', 'exit_error'):
        if progress.json_errors is None:
            progress.json_errors = []
        progress.json_errors.append(format_restic_json_error(payload))
    elif server is not None:
        server.logger.debug('restic {} JSON: {}'.format(progress.phase, tail_text(text, 500)))


def maybe_emit_restic_progress(
    server: Optional[PluginServerInterface],
    progress: ResticProgressState,
    interval: float,
    force: bool = False,
):
    if server is None:
        return
    now = time.monotonic()
    if not force and now - progress.last_emit_at < interval:
        return
    text = format_restic_progress(progress, force)
    if not text:
        return
    if force and text == progress.last_emit_text:
        return
    server.logger.info(text)
    progress.last_emit_at = now
    progress.last_emit_text = text
