# -*- coding: utf-8 -*-
"""[beta] 门控等待超时跳过备份后的中转库方案提示。

唯一提示入口：只有人数门控/备份槽位等待超时（GateWaitStatus.DEADLINE）
才提示一次；7 天冷却，staging 已启用时不提示，不自动修改配置。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import config_language
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section, now_text
from mcdr2restic.defaults.default_constants import (
    DOCUMENTATION_STAGING_URL,
    STAGING_HINT_COOLDOWN_SECONDS,
)
from mcdr2restic.notifications.message_templates import (
    MessageRenderOptions,
    render_message,
)
from mcdr2restic.staging.staging_config import staging_enabled
from mcdr2restic.staging.staging_state import (
    staging_runtime_state,
    update_staging_runtime,
)
from mcdr2restic.staging.staging_warning import parse_runtime_time


AdminNotifier = Callable[..., None]


def maybe_show_staging_hint(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    cfg: Dict[str, Any],
    admin_notifier: Optional[AdminNotifier] = None,
) -> bool:
    if staging_enabled(cfg):
        return False
    if not hint_cooldown_passed(staging_runtime_state(app_runtime)):
        return False

    language = config_language(cfg, get_mcdr_language(server))
    values = {"documentation_url": DOCUMENTATION_STAGING_URL}
    text = render_message(
        "staging_hint",
        values,
        cfg,
        MessageRenderOptions(logger=getattr(server, "logger", None), language=language),
    )
    server.logger.warning(text)
    update_staging_runtime(
        app_runtime, server, staging_hint_last_at=now_text()
    )
    if admin_notifier is not None and bool(
        get_config_section(cfg, "notification").get("notify_on_skip", False)
    ):
        admin_notifier("staging_hint", values, cfg, False, log=False)
    return True


def hint_cooldown_passed(runtime_state: Dict[str, Any]) -> bool:
    last_text = str(runtime_state.get("staging_hint_last_at") or "").strip()
    last_moment = parse_runtime_time(last_text)
    if last_moment is None:
        return True
    return (
        datetime.now().astimezone() - last_moment
    ).total_seconds() >= STAGING_HINT_COOLDOWN_SECONDS


__all__ = ["hint_cooldown_passed", "maybe_show_staging_hint"]
