# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.config.state_store import ensure_runtime, save_config_unlocked
from mcdr2restic.core.i18n import server_tr
from mcdr2restic.minecraft.player_activity import (
    mark_player_activity_unlocked,
    parse_online_list_output,
    player_activity_required,
    reset_player_activity_period_unlocked,
    runtime_player_set,
)
from mcdr2restic.minecraft.minecraft_service import server_is_running
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import non_negative_int, now_text, tail_text


def handle_player_joined(
    app_runtime: PluginRuntime, server: PluginServerInterface, player: str
):
    with app_runtime.config_state.lock:
        ensure_runtime(app_runtime.config_state.config)
        runtime_state = app_runtime.config_state.config["runtime"]
        known_players = runtime_player_set(runtime_state)
        already_known = player in known_players
        known_players.add(player)
        previous_current = non_negative_int(
            runtime_state.get("current_online_players", 0)
        )
        current_online = max(
            len(known_players), previous_current + (0 if already_known else 1)
        )
        runtime_state["known_online_players"] = sorted(known_players)
        mark_player_activity_unlocked(runtime_state, current_online, "join event")
        runtime_state["player_joined_since_last_backup"] = True
        runtime_state["player_joined_since_last_check"] = True
        runtime_state["player_activity_since_last_backup"] = True
        runtime_state["last_player_joined"] = "{} @ {}".format(player, now_text())
        save_config_unlocked(app_runtime, server)
    server.logger.debug(
        server_tr(server, "debug.player.joined_recorded", player=player)
    )


def handle_player_left(
    app_runtime: PluginRuntime, server: PluginServerInterface, player: str
):
    with app_runtime.config_state.lock:
        ensure_runtime(app_runtime.config_state.config)
        runtime_state = app_runtime.config_state.config["runtime"]
        known_players = runtime_player_set(runtime_state)
        was_known = player in known_players
        known_players.discard(player)
        previous_current = non_negative_int(
            runtime_state.get("current_online_players", 0)
        )
        current_online = max(
            len(known_players),
            previous_current - (1 if was_known or previous_current > 0 else 0),
        )
        runtime_state["known_online_players"] = sorted(known_players)
        record_player_left_unlocked(runtime_state, player, current_online)
        save_config_unlocked(app_runtime, server)
    server.logger.debug(
        server_tr(
            server,
            "debug.player.left_recorded",
            player=player,
            current_online=current_online,
        )
    )


def record_player_left_unlocked(
    runtime_state: Dict[str, Any], player: str, current_online: int
):
    runtime_state["current_online_players"] = current_online
    runtime_state["player_activity_since_last_backup"] = True
    runtime_state["player_left_since_last_check"] = True
    runtime_state["last_player_left"] = "{} @ {}".format(player, now_text())
    runtime_state["last_online_check"] = now_text()
    runtime_state["last_online_check_source"] = "left event"
    runtime_state["last_online_check_result"] = "{} online after {} left".format(
        current_online, player
    )


def sample_online_players(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface],
    cfg: Dict[str, Any],
    reason: str,
) -> Optional[int]:
    if server is None or not player_activity_required(cfg):
        return None
    if not server_is_running(app_runtime, server):
        return None
    command = get_online_check_command(cfg)
    if not command:
        return None
    rcon_query = getattr(server, "rcon_query", None)
    if not callable(rcon_query):
        server.logger.debug(server_tr(server, "debug.player.sample_unsupported"))
        return None
    return query_online_players(app_runtime, server, rcon_query, command, reason)


def get_online_check_command(cfg: Dict[str, Any]) -> str:
    schedule = cfg.get("schedule", {})
    return str(schedule.get("online_check_command", "list") or "").strip()


def query_online_players(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    rcon_query,
    command: str,
    reason: str,
) -> Optional[int]:
    try:
        result = rcon_query(command)
    except Exception as exc:
        server.logger.debug(
            server_tr(server, "debug.player.sample_failed", reason=reason, error=exc)
        )
        return None
    if result is None:
        server.logger.debug(
            server_tr(server, "debug.player.sample_missing_result", reason=reason)
        )
        return None
    count, names = parse_online_list_output(str(result))
    if count is None:
        server.logger.debug(
            server_tr(
                server,
                "debug.player.sample_unparseable",
                reason=reason,
                output=tail_text(str(result), 300),
            )
        )
        return None
    record_online_sample(app_runtime, server, command, result, count, names)
    return count


def record_online_sample(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    command: str,
    result: Any,
    count: int,
    names,
):
    with app_runtime.config_state.lock:
        ensure_runtime(app_runtime.config_state.config)
        runtime_state = app_runtime.config_state.config["runtime"]
        runtime_state["known_online_players"] = resolve_known_online_players(
            runtime_state, count, names
        )
        mark_player_activity_unlocked(
            runtime_state, count, "rcon {}".format(command), tail_text(str(result), 300)
        )
        save_config_unlocked(app_runtime, server)


def resolve_known_online_players(
    runtime_state: Dict[str, Any], count: int, names
) -> list:
    if names:
        return sorted(names)
    if count == 0:
        return []
    return sorted(runtime_player_set(runtime_state))


def should_skip_for_no_player_activity(
    app_runtime: PluginRuntime, cfg: Dict[str, Any]
) -> bool:
    if not player_activity_required(cfg):
        return False
    sample_online_players(
        app_runtime, app_runtime.service.server, cfg, "schedule trigger"
    )
    with app_runtime.config_state.lock:
        ensure_runtime(app_runtime.config_state.config)
        runtime_state = app_runtime.config_state.config["runtime"]
        has_activity = has_recent_player_activity(runtime_state)
        reset_player_activity_period_unlocked(runtime_state)
        save_config_unlocked(app_runtime, app_runtime.service.server)
    return not has_activity


def has_recent_player_activity(runtime_state: Dict[str, Any]) -> bool:
    current_online = non_negative_int(runtime_state.get("current_online_players", 0))
    joined = bool(
        runtime_state.get("player_joined_since_last_check", False)
        or runtime_state.get("player_joined_since_last_backup", False)
    )
    left = bool(runtime_state.get("player_left_since_last_check", False))
    return current_online > 0 or joined or left
