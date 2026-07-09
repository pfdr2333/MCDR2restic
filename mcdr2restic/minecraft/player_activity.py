# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from mcdr2restic.core.utils import non_negative_int, now_text


MC_COLOR_CODE_PATTERN = re.compile(r"§.")
MC_LIST_PLAYER_COUNT_EN = re.compile(
    r"\bThere are\s+(\d+)\s+of\s+a\s+max\s+of\s+\d+\s+players?\s+online\b",
    re.IGNORECASE,
)
MC_LIST_PLAYER_RATIO_EN = re.compile(
    r"\b(\d+)\s*/\s*\d+\s+players?\s+online\b", re.IGNORECASE
)
MC_LIST_PLAYER_RATIO_BRACKET = re.compile(r"\((\d+)\s*/\s*\d+\)")
MC_LIST_PLAYER_COUNT_SHORT_EN = re.compile(
    r"\b(\d+)\s+players?\s+online\b", re.IGNORECASE
)
MC_LIST_PLAYER_COUNT_CN = re.compile(r"(?:当前)?(?:有)?\s*(\d+)\s*(?:个)?玩家在线")
MC_LIST_PLAYER_COUNT_PATTERNS = (
    MC_LIST_PLAYER_COUNT_EN,
    MC_LIST_PLAYER_RATIO_EN,
    MC_LIST_PLAYER_RATIO_BRACKET,
    MC_LIST_PLAYER_COUNT_SHORT_EN,
    MC_LIST_PLAYER_COUNT_CN,
)


def player_activity_required(cfg: Dict[str, Any]) -> bool:
    schedule = cfg.get("schedule", {})
    if "require_player_activity_in_wait_period" in schedule:
        return bool(schedule.get("require_player_activity_in_wait_period", True))
    return bool(schedule.get("require_player_joined_in_wait_period", True))


def runtime_player_set(runtime_state: Dict[str, Any]) -> Set[str]:
    players = runtime_state.get("known_online_players", [])
    if isinstance(players, str):
        players = [players]
    if not isinstance(players, (list, tuple, set)):
        return set()
    return {str(player) for player in players if str(player).strip()}


def mark_player_activity_unlocked(
    runtime_state: Dict[str, Any],
    current_online: int,
    source: str,
    result: Optional[str] = None,
):
    current_online = non_negative_int(current_online)
    runtime_state["current_online_players"] = current_online
    if current_online > 0:
        runtime_state["player_activity_since_last_backup"] = True
    runtime_state["last_online_check"] = now_text()
    runtime_state["last_online_check_source"] = source
    runtime_state["last_online_check_result"] = result or "{} online".format(
        current_online
    )


def reset_player_activity_period_unlocked(runtime_state: Dict[str, Any]):
    current_online = non_negative_int(runtime_state.get("current_online_players", 0))
    runtime_state["player_activity_since_last_backup"] = current_online > 0
    runtime_state["player_joined_since_last_backup"] = False
    runtime_state["player_joined_since_last_check"] = False
    runtime_state["player_left_since_last_check"] = False


def parse_online_list_output(output: str) -> Tuple[Optional[int], List[str]]:
    text = MC_COLOR_CODE_PATTERN.sub("", str(output or "")).strip()
    count = parse_online_count(text)
    names = parse_online_names(text)
    if count is None and names:
        count = len(names)
    return count, names


def parse_online_count(text: str) -> Optional[int]:
    for pattern in MC_LIST_PLAYER_COUNT_PATTERNS:
        match = pattern.search(text)
        if match:
            return non_negative_int(match.group(1))
    return None


def parse_online_names(text: str) -> List[str]:
    if ":" not in text:
        return []
    tail = text.rsplit(":", 1)[1].strip()
    if not tail:
        return []
    return [name.strip() for name in tail.split(",") if name.strip()]
