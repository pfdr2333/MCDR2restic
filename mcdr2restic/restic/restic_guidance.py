# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Dict


RESTIC_FAILURE_LOCKED = "locked"
RESTIC_FAILURE_REPOSITORY_NOT_INITIALIZED = "repository_not_initialized"

LOCKED_REPOSITORY_HINT = "repository is already locked"
UNINITIALIZED_REPOSITORY_PATTERNS = (
    re.compile(r"repository .* does not exist", re.IGNORECASE),
    re.compile(r"is there a repository at the following location", re.IGNORECASE),
    re.compile(r"unable to open config file", re.IGNORECASE),
    re.compile(r"config file does not exist", re.IGNORECASE),
)


def classify_restic_failure_output(text: str) -> str:
    """Classify restic output that benefits from an operator-facing command hint."""

    output = str(text or "")
    if LOCKED_REPOSITORY_HINT in output.lower():
        return RESTIC_FAILURE_LOCKED
    if any(pattern.search(output) for pattern in UNINITIALIZED_REPOSITORY_PATTERNS):
        return RESTIC_FAILURE_REPOSITORY_NOT_INITIALIZED
    return ""


def command_root_from_config(cfg: Dict[str, Any]) -> str:
    command_cfg = cfg.get("command", {}) if isinstance(cfg.get("command"), dict) else {}
    return str(command_cfg.get("root", "!!restic") or "!!restic").strip() or "!!restic"


def restic_management_command(command_root: str, subcommand: str) -> str:
    root = str(command_root or "").strip() or "!!restic"
    return "{} {}".format(root, str(subcommand or "").strip())
