# -*- coding: utf-8 -*-
"""Detect ``save-all flush`` completion from server console output."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, List, Optional


NOTIFY_POLL_INTERVAL_SECONDS = 0.2


class SaveFlushWatcher:
    """布防后匹配服务器输出中的 flush 完成提示，供备份线程等待。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._patterns: List[str] = []
        self._event = threading.Event()

    def arm(self, patterns: List[str]) -> threading.Event:
        """开始监听并返回完成事件；调用方在结束后必须 disarm。"""

        with self._lock:
            self._patterns = [
                pattern for pattern in (str(item) for item in patterns) if pattern
            ]
            self._event.clear()
            return self._event

    def disarm(self):
        with self._lock:
            self._patterns = []
        self._event.clear()

    def is_armed(self) -> bool:
        with self._lock:
            return bool(self._patterns)

    def notify_output(self, info: Any):
        """由 on_info 回调调用；非布防状态直接返回以保证开销可忽略。"""

        if not self.is_armed() or self._event.is_set():
            return
        if bool(getattr(info, "is_user", False)):
            return
        content = str(getattr(info, "content", "") or "")
        if not content:
            return
        with self._lock:
            patterns = list(self._patterns)
        if any(pattern in content for pattern in patterns):
            self._event.set()

    def wait(
        self,
        timeout_seconds: float,
        check_canceled: Optional[Callable[[], None]] = None,
    ) -> bool:
        """等待完成提示，返回是否检测到完成；超时或取消时结束等待。"""

        end = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            remaining = end - time.monotonic()
            if self._event.wait(min(NOTIFY_POLL_INTERVAL_SECONDS, max(0.0, remaining))):
                return True
            if check_canceled is not None:
                check_canceled()
            if end - time.monotonic() <= 0:
                return self._event.is_set()
