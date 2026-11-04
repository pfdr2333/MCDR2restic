# -*- coding: utf-8 -*-
"""Fail when both copies of a language file were edited and diverged.

Root ``lang/`` files are generated copies of ``mcdr2restic/lang/`` with keys
prefixed by the plugin id. Hand-editing both copies instead of running
``tools/sync_language_resources.py`` can leave them inconsistent; this check
flags that situation for a pushed revision (base SHA -> HEAD).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

PLUGIN_PREFIX = "mcdr2restic."
LANGUAGE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("lang/zh_cn.json", "mcdr2restic/lang/zh_cn.json"),
    ("lang/en_us.json", "mcdr2restic/lang/en_us.json"),
    ("lang/zh_tw.json", "mcdr2restic/lang/zh_tw.json"),
)
ZERO_SHA = "0000000000000000000000000000000000000000"


def load_semantic_messages(path: str) -> Dict[str, str]:
    """Load a lang file and strip the plugin prefix from root-copy keys."""

    with open(path, "r", encoding="utf8") as file:
        data = json.load(file)
    return {
        key[len(PLUGIN_PREFIX):] if key.startswith(PLUGIN_PREFIX) else key: str(value)
        for key, value in data.items()
    }


def normalize_base_sha(base: str) -> str:
    base = (base or "").strip()
    if not base or set(base) == {"0"}:
        return "HEAD~1"
    return base


def file_changed_between(base: str, head: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "diff", "--name-only", base, head, "--", path],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def find_diverged_pairs(base: str, head: str = "HEAD") -> List[Tuple[str, str]]:
    diverged: List[Tuple[str, str]] = []
    for root_file, package_file in LANGUAGE_PAIRS:
        if not (
            file_changed_between(base, head, root_file)
            and file_changed_between(base, head, package_file)
        ):
            continue
        if load_semantic_messages(root_file) != load_semantic_messages(package_file):
            diverged.append((root_file, package_file))
    return diverged


def main() -> int:
    os.chdir(Path(__file__).resolve().parents[1])
    base = normalize_base_sha(sys.argv[1] if len(sys.argv) > 1 else "")
    diverged = find_diverged_pairs(base)
    for root_file, package_file in diverged:
        print(
            "::error::{} and {} were both modified but their content diverged."
            " Edit mcdr2restic/lang only, then run"
            " 'python tools/sync_language_resources.py' and commit the result.".format(
                root_file, package_file
            )
        )
    return 1 if diverged else 0


if __name__ == "__main__":
    raise SystemExit(main())
