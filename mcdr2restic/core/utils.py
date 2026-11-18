# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict


# 运行时长文本的唯一格式：state.yml 写入、状态页展示与各模块解析都用它
RUNTIME_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_text() -> str:
    return datetime.now().strftime(RUNTIME_TIME_FORMAT)


def format_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return "{:.1f} {}".format(size, unit)
        size /= 1024.0
    return "{:.1f} TiB".format(size)


def tail_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text.strip()
    return "...\n{}".format(text[-max_chars:].strip())


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


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


def get_config_section(cfg: Any, key: str) -> Dict[str, Any]:
    """读取映射类型的配置段；缺失或类型写错时返回空 dict，防止坏配置杀死后台线程。"""

    section = cfg.get(key) if isinstance(cfg, dict) else None
    return section if isinstance(section, dict) else {}
