# -*- coding: utf-8 -*-
"""陈旧备份看门狗：独立时钟驱动的门控停滞告警。

备份调度可能一天才触发一次，把检查挂在计划点上会让 24 小时阈值最多
晚一个调度周期才生效。因此这里用一个独立线程按固定节奏重算：

- 停滞起点由首次门控推迟写入（``gate_stall``），已等待时长由运行时钟
  现算，重启/崩溃/停服都不会丢失进度；
- 成功备份清空起点，告警随之自动复位；
- 达到阈值后发一次告警，之后每 24 小时再提醒，直到出现成功备份；
- 线程还承担运行时钟的周期性心跳结算，使崩溃后能丢弃停机时长。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.backup.gate_stall import gate_stall_elapsed_seconds
from mcdr2restic.backup.running_time import (
    RUNNING_CLOCK_HEARTBEAT_SECONDS,
    settle_running_clock,
)
from mcdr2restic.backup.scheduling import parse_schedule_time
from mcdr2restic.config.state_store import (
    ensure_runtime,
    get_config_snapshot,
    save_config_unlocked,
)
from mcdr2restic.core.i18n import config_language, server_tr, tr
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.core.utils import get_config_section, non_negative_int, now_text
from mcdr2restic.defaults.default_constants import DOCUMENTATION_CONFIGURATION_URL
from mcdr2restic.minecraft.minecraft_service import server_is_running


# 检查节奏：阈值到点后最多晚这么多秒告警，与备份调度周期无关
STALE_WATCHDOG_POLL_SECONDS = 60.0
STALE_WARNING_COOLDOWN_HOURS = 24
DEFAULT_STALE_WARNING_HOURS = 24
# 冷却时间戳晚于当前时间超过该容差时视为状态损坏/时钟回拨（P3-4）
FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 300

# 第四个位置参数是 failure 级别，log 为关键字参数（与 staging 提示一致）
AdminNotifier = Callable[..., None]


class StaleBackupWatchdog:
    """常驻线程：重算停滞时长、发告警、结算运行时钟心跳。"""

    def __init__(
        self,
        app_runtime: PluginRuntime,
        admin_notifier: AdminNotifier,
        poll_seconds: float = STALE_WATCHDOG_POLL_SECONDS,
    ):
        self.app_runtime = app_runtime
        self.admin_notifier = admin_notifier
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.wakeup_event = threading.Event()
        self.thread = threading.Thread(
            target=self._main,
            name="MCDR2Restic-StaleWatchdog",
            daemon=True,
        )
        self._last_heartbeat_monotonic: Optional[float] = None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wakeup_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def wakeup(self):
        """配置变化/状态复位后立即重算，不必等下一个检查周期。"""

        self.wakeup_event.set()

    def run_once(self) -> bool:
        """检查一次；返回是否发出了告警。便于测试与手动触发。"""

        return self._tick()

    def _main(self):
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                server = self.app_runtime.service.server
                if server is not None:
                    server.logger.warning(
                        server_tr(
                            server,
                            "warn.backup.stale_watchdog_failed",
                            error=exc,
                        )
                    )
            self.wakeup_event.wait(self.poll_seconds)
            self.wakeup_event.clear()

    def _tick(self) -> bool:
        self._settle_clock_if_due()
        server = self.app_runtime.service.server
        if server is None:
            return False
        cfg = get_config_snapshot(self.app_runtime)
        return maybe_warn_stale_backup(
            self.app_runtime, server, cfg, self.admin_notifier
        )

    def _settle_clock_if_due(self):
        """每 30 分钟把运行片段结算落盘，崩溃后最多丢一个间隔的精度。"""

        now = time.monotonic()
        # None 表示本进程还没结算过：首轮直接结算，避免依赖宿主 uptime
        if (
            self._last_heartbeat_monotonic is not None
            and now - self._last_heartbeat_monotonic < RUNNING_CLOCK_HEARTBEAT_SECONDS
        ):
            return
        server = self.app_runtime.service.server
        # 服务器未运行时运行时钟本就不走，无需结算（心跳时间保持不变）
        if server is None or not server_is_running(self.app_runtime, server):
            self._last_heartbeat_monotonic = now
            return
        settle_running_clock(self.app_runtime, server)
        self._last_heartbeat_monotonic = now


def maybe_warn_stale_backup(
    app_runtime: PluginRuntime,
    server: Optional[PluginServerInterface],
    cfg: Dict[str, Any],
    admin_notifier: AdminNotifier,
) -> bool:
    """达到停滞阈值且不在冷却期内时告警一次。

    与旧实现不同，这里不再要求“此刻正被门控推迟”：只要首次推迟以来
    24 小时没有成功备份就告警，避免恰好没在等待时永远不报。
    """

    if (
        server is None
        or not server_is_running(app_runtime, server)
        # 备份被停用或插件正在卸载时不再报警：此时没有“该备份却没备份”的前提
        or not bool(cfg.get("enabled", True))
        or app_runtime.service.stopping.is_set()
    ):
        return False
    threshold_hours, _ = resolve_stale_warning_hours(cfg)
    if threshold_hours <= 0:
        return False
    runtime_state = get_config_section(cfg, "runtime")
    elapsed = gate_stall_elapsed_seconds(runtime_state)
    if elapsed is None or elapsed < threshold_hours * 3600:
        return False
    warned_at_text = str(runtime_state.get("stale_backup_warned_at") or "").strip()
    warned_at = parse_schedule_time(warned_at_text)
    if warned_at is not None:
        now = datetime.now()
        if warned_at - now > timedelta(seconds=FUTURE_TIMESTAMP_TOLERANCE_SECONDS):
            # 时钟回拨或手改状态会让冷却无限后延；按无效处理并借本次告警自愈（P3-4）
            server.logger.warning(
                server_tr(
                    server,
                    "warn.backup.stale_warning_timestamp_ahead",
                    timestamp=warned_at_text,
                )
            )
        elif now - warned_at < timedelta(hours=STALE_WARNING_COOLDOWN_HOURS):
            return False

    last_success_text, _ = resolve_last_success_backup(cfg)
    max_online = non_negative_int(
        get_config_section(cfg, "schedule").get("backup_player_gate_max_online", 0)
    )
    values = {
        "hours": elapsed // 3600,
        "last_success": last_success_text or "-",
        "first_deferral": str(runtime_state.get("gate_stall_started_at") or "-"),
        # 正常流程有基线时计数至少为 1；这里不兜底成 1，手工改出的 0 如实显示（P3-6）
        "skips": non_negative_int(
            runtime_state.get("gate_deferral_count_since_success", 0)
        ),
        "current": non_negative_int(
            runtime_state.get("current_online_players", 0)
        ),
        "max_online": max_online,
        "root": str(get_config_section(cfg, "command").get("root", "!!restic")),
        "doc_url": DOCUMENTATION_CONFIGURATION_URL,
    }
    language = config_language(cfg, get_mcdr_language(server))
    server.logger.warning(
        tr(
            language,
            "warn.backup.stale_backup_warning",
            hours=values["hours"],
            last_success=values["last_success"],
            first_deferral=values["first_deferral"],
            skips=values["skips"],
            current=values["current"],
            max_online=values["max_online"],
        )
    )
    # 控制台已输出完整的新版文案，通知渠道不再重复打一条旧正文（P3-1）
    admin_notifier("backup_stale_warning", values, cfg, True, log=False)
    with app_runtime.config_state.lock:
        ensure_runtime(
            app_runtime.config_state.config,
            app_runtime.config_state.state,
        )
        app_runtime.config_state.config["runtime"]["stale_backup_warned_at"] = (
            now_text()
        )
        save_config_unlocked(app_runtime, server)
    return True


def raw_stale_warning_hours(cfg: Dict[str, Any]) -> Any:
    return get_config_section(cfg, "notification").get(
        "stale_backup_warning_hours", DEFAULT_STALE_WARNING_HOURS
    )


def resolve_stale_warning_hours(cfg: Dict[str, Any]) -> Tuple[int, bool]:
    """读取告警阈值；返回 (小时数, 配置是否非法)。"""

    return parse_stale_warning_hours(raw_stale_warning_hours(cfg))


def parse_stale_warning_hours(raw: Any) -> Tuple[int, bool]:
    """把阈值配置解析成小时数，并标记是否需要提示用户。

    非数字回落默认 24 小时，负数按 0（关闭）处理：两种都算非法，交由
    配置加载时提示一次，避免用户误以为设置已生效（P3-5）。空值按未配置
    处理，静默取默认值。
    """

    if raw is None:
        return DEFAULT_STALE_WARNING_HOURS, False
    # bool 是 int 的子类，true 会被解释成"1 小时"，按非法输入处理更安全
    if isinstance(raw, bool):
        return DEFAULT_STALE_WARNING_HOURS, True
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_STALE_WARNING_HOURS, True
    if hours < 0:
        return 0, True
    return hours, False


def warn_invalid_stale_warning_config(
    server: PluginServerInterface, cfg: Dict[str, Any]
) -> None:
    """配置加载/重载时提示非法的阈值配置；每个加载周期只提示一次。"""

    raw = raw_stale_warning_hours(cfg)
    hours, invalid = parse_stale_warning_hours(raw)
    if not invalid:
        return
    server.logger.warning(
        server_tr(
            server,
            "warn.config.stale_backup_warning_hours_invalid",
            value=raw,
            hours=hours,
        )
    )


def resolve_last_success_backup(
    cfg: Dict[str, Any],
) -> tuple[str, Optional[datetime]]:
    """读取最后成功备份时间；老状态文件没有该字段时回退到上次成功结束时间。"""

    runtime_state = get_config_section(cfg, "runtime")
    text = str(runtime_state.get("last_success_backup_end_time") or "").strip()
    moment = parse_schedule_time(text)
    if moment is not None:
        return text, moment
    if str(runtime_state.get("last_backup_status") or "") != "success":
        return "", None
    fallback_text = str(runtime_state.get("last_backup_end_time") or "").strip()
    return fallback_text, parse_schedule_time(fallback_text)
