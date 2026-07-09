# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


def now_text() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def tail_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text.strip()
    return '...\n{}'.format(text[-max_chars:].strip())


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def non_negative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default
