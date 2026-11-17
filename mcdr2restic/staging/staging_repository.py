# -*- coding: utf-8 -*-
"""[beta] 中转库与远端库的探测、初始化、快照读取与游标计算。

所有函数都只通过 restic 命令或纯函数工作；调用方负责持有备份槽位或
``staging.io_lock``。仓库探测不依赖 restic 的报错文本决定是否复制：
chunker 参数必须由插件自己比对。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import tr_error
from mcdr2restic.core.language import get_mcdr_language
from mcdr2restic.core.models import BackupProblem, ResticCommandResult, TextEnum
from mcdr2restic.core.runtime import PluginRuntime
from mcdr2restic.restic.restic_config import get_effective_restic_repository
from mcdr2restic.restic.restic_result import (
    assert_restic_success,
    combine_restic_output,
)
from mcdr2restic.restic.restic_runner import run_restic_command
from mcdr2restic.staging.staging_config import (
    StagingPaths,
    StagingSettings,
    build_cat_config_args,
    build_copy_source_config,
    build_init_args,
    build_local_restic_config,
    build_remote_restic_config,
    build_snapshots_args,
    local_source_credentials,
    remote_source_credentials,
)


CAT_CONFIG_PHASE = "staging-cat-config"
SNAPSHOTS_PHASE = "staging-snapshots"
INIT_LOCAL_PHASE = "staging-init-local"
INIT_REMOTE_PHASE = "staging-init-remote"
COPY_PHASE = "staging-copy"
PRUNE_PHASE = "staging-prune"
CACHE_CLEANUP_PHASE = "staging-cache-cleanup"

AUTH_ERROR_MARKERS = (
    "wrong password",
    "password is incorrect",
    "no key found",
    "invalid password",
)
MISSING_REPOSITORY_MARKERS = (
    "repository does not exist",
    "no such file or directory",
    "unable to open config file",
    "is there a repository at the following location",
    "does not exist",
)

RESTIC_TIME_PATTERN = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<offset>Z|[+-]\d{2}:?\d{2})?$"
)


class RepoProbeStatus(TextEnum):
    OK = "ok"
    MISSING = "missing"
    AUTH = "auth"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class RepoProbe:
    status: RepoProbeStatus
    config: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == RepoProbeStatus.OK

    @property
    def missing(self) -> bool:
        return self.status == RepoProbeStatus.MISSING


def probe_repository(
    app_runtime: PluginRuntime,
    restic_cfg: Dict[str, Any],
    slot: Any = None,
    deadline: Optional[float] = None,
) -> RepoProbe:
    result = run_restic_command(
        app_runtime,
        restic_cfg,
        build_cat_config_args(),
        CAT_CONFIG_PHASE,
        deadline,
        slot=slot,
    )
    if result.return_code == 0:
        config = parse_json_object(result.stdout)
        if config is None:
            return RepoProbe(
                RepoProbeStatus.UNREACHABLE,
                error=result.stdout.strip() or result.stderr.strip(),
            )
        return RepoProbe(RepoProbeStatus.OK, config)
    return RepoProbe(classify_repo_failure(result), error=combine_restic_output(result))


def classify_repo_failure(result: ResticCommandResult) -> RepoProbeStatus:
    text = combine_restic_output(result).lower()
    if any(marker in text for marker in AUTH_ERROR_MARKERS):
        return RepoProbeStatus.AUTH
    if any(marker in text for marker in MISSING_REPOSITORY_MARKERS):
        return RepoProbeStatus.MISSING
    return RepoProbeStatus.UNREACHABLE


def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    payload = parse_json_payload(text)
    return payload if isinstance(payload, dict) else None


def parse_json_payload(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return None
    return None


def ensure_local_repository(  # noqa: PLR0913 仓库准备需要完整上下文
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    settings: StagingSettings,
    paths: StagingPaths,
    slot: Any = None,
) -> Dict[str, Any]:
    """[beta] 确保本地中转库存在；按远端是否已存在决定初始化方向。"""

    local_cfg = build_local_restic_config(restic_cfg, settings, paths)
    local_probe = probe_repository(app_runtime, local_cfg, slot=slot)
    if local_probe.ok:
        return local_cfg
    if local_probe.status == RepoProbeStatus.AUTH:
        raise BackupProblem(
            i18n_key="error.staging.local_password", error=local_probe.error
        )
    if local_probe.status == RepoProbeStatus.UNREACHABLE:
        raise BackupProblem(
            i18n_key="error.staging.local_unavailable", error=local_probe.error
        )

    remote_cfg = build_remote_restic_config(restic_cfg)
    remote_probe = probe_repository(app_runtime, remote_cfg, slot=slot)
    if remote_probe.ok:
        source_password, source_password_file = remote_source_credentials(remote_cfg)
        init_cfg = build_copy_source_config(
            local_cfg, source_password, source_password_file
        )
        init_args = build_init_args(
            get_effective_restic_repository(remote_cfg), copy_chunker_params=True
        )
    elif remote_probe.status == RepoProbeStatus.MISSING:
        init_cfg = local_cfg
        init_args = build_init_args()
    else:
        raise BackupProblem(
            i18n_key="error.staging.remote_unavailable_for_init",
            error=remote_probe.error,
        )

    run_initialization(
        app_runtime,
        server,
        init_cfg,
        init_args,
        INIT_LOCAL_PHASE,
        "error.staging.local_init_failed",
        slot,
    )
    verify = probe_repository(app_runtime, local_cfg, slot=slot)
    if not verify.ok:
        raise BackupProblem(
            i18n_key="error.staging.local_init_failed", error=verify.error
        )
    return local_cfg


def ensure_remote_repository_for_copy(  # noqa: PLR0913 仓库准备需要完整上下文
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    settings: StagingSettings,
    paths: StagingPaths,
    slot: Any = None,
) -> Tuple[Dict[str, Any], bool]:
    """[beta] 确保远端库存在；返回 (远端配置, 本次是否新建)。"""

    remote_cfg = build_remote_restic_config(restic_cfg)
    probe = probe_repository(app_runtime, remote_cfg, slot=slot)
    if probe.ok:
        return remote_cfg, False
    if probe.status == RepoProbeStatus.AUTH:
        raise BackupProblem(i18n_key="error.staging.remote_auth", error=probe.error)
    if probe.status == RepoProbeStatus.UNREACHABLE:
        raise BackupProblem(
            i18n_key="error.staging.remote_unreachable", error=probe.error
        )

    source_password, source_password_file = local_source_credentials(settings, paths)
    init_cfg = build_copy_source_config(
        remote_cfg, source_password, source_password_file
    )
    init_args = build_init_args(
        paths.repository_path, copy_chunker_params=True
    )
    run_initialization(
        app_runtime,
        server,
        init_cfg,
        init_args,
        INIT_REMOTE_PHASE,
        "error.staging.remote_init_failed",
        slot,
    )
    verify = probe_repository(app_runtime, remote_cfg, slot=slot)
    if not verify.ok:
        raise BackupProblem(
            i18n_key="error.staging.remote_init_failed", error=verify.error
        )
    return remote_cfg, True


def run_initialization(  # noqa: PLR0913 初始化参数集中在一个内部入口
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    args: List[str],
    phase: str,
    failure_key: str,
    slot: Any,
):
    try:
        result = run_restic_command(
            app_runtime, restic_cfg, args, phase, None, slot=slot
        )
        assert_restic_success(restic_cfg, result, logger=getattr(server, "logger", None))
    except BackupProblem as exc:
        raise BackupProblem(
            i18n_key=failure_key,
            error=tr_error(get_mcdr_language(server), exc),
        ) from exc


def assert_matching_chunkers(
    app_runtime: PluginRuntime,
    local_cfg: Dict[str, Any],
    remote_cfg: Dict[str, Any],
    slot: Any = None,
) -> str:
    local_probe = probe_repository(app_runtime, local_cfg, slot=slot)
    remote_probe = probe_repository(app_runtime, remote_cfg, slot=slot)
    if not local_probe.ok:
        raise BackupProblem(
            i18n_key="error.staging.local_unavailable", error=local_probe.error
        )
    if not remote_probe.ok:
        raise BackupProblem(
            i18n_key="error.staging.remote_unreachable", error=remote_probe.error
        )
    local_chunker = chunker_polynomial(local_probe.config)
    remote_chunker = chunker_polynomial(remote_probe.config)
    if local_chunker != remote_chunker:
        raise BackupProblem(
            i18n_key="error.staging.chunker_mismatch",
            local_chunker=local_chunker or "-",
            remote_chunker=remote_chunker or "-",
        )
    return local_chunker


def chunker_polynomial(config: Dict[str, Any]) -> str:
    return str(config.get("chunker_polynomial") or "").strip()


def read_snapshots(
    app_runtime: PluginRuntime,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    slot: Any = None,
    latest_only: bool = False,
) -> List[Dict[str, str]]:
    result = run_restic_command(
        app_runtime,
        restic_cfg,
        build_snapshots_args(latest_only=latest_only),
        SNAPSHOTS_PHASE,
        None,
        slot=slot,
    )
    assert_restic_success(restic_cfg, result, logger=getattr(server, "logger", None))
    return parse_snapshots(result.stdout)


def parse_snapshots(text: str) -> List[Dict[str, str]]:
    payload = parse_json_payload(text)
    if isinstance(payload, list):
        items: Iterable[Any] = payload
    elif isinstance(payload, dict):
        items = [payload]
    else:
        items = parse_snapshot_lines(text)
    return [
        normalized
        for normalized in (normalize_snapshot(item) for item in items)
        if normalized is not None
    ]


def parse_snapshot_lines(text: str) -> List[Any]:
    items: List[Any] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return items


def normalize_snapshot(item: Any) -> Optional[Dict[str, str]]:
    if not isinstance(item, dict):
        return None
    snapshot_id = str(item.get("id") or item.get("short_id") or "").strip()
    snapshot_time = str(item.get("time") or "").strip()
    if not snapshot_id or not snapshot_time:
        return None
    return {
        "id": snapshot_id,
        "short_id": str(item.get("short_id") or snapshot_id)[:8],
        "time": snapshot_time,
    }


def latest_snapshot_time(snapshots: Sequence[Dict[str, str]]) -> Optional[str]:
    latest_time: Optional[str] = None
    for snapshot in snapshots:
        snapshot_time = str(snapshot.get("time") or "")
        if not snapshot_time:
            continue
        if latest_time is None or compare_restic_times(snapshot_time, latest_time) > 0:
            latest_time = snapshot_time
    return latest_time


def select_pending_snapshots(
    snapshots: Sequence[Dict[str, str]],
    cursor_time: Optional[str],
    cursor_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, str]]:
    """[beta] 只选择游标之后的新快照；同一时间的快照按 ID 去重。"""

    ordered = sorted(
        (item for item in snapshots if item.get("id") and item.get("time")),
        key=lambda item: item["time"],
    )
    if not cursor_time:
        return ordered
    cursor_id_set = {str(item) for item in (cursor_ids or ())}
    pending: List[Dict[str, str]] = []
    for snapshot in ordered:
        comparison = compare_restic_times(str(snapshot["time"]), cursor_time)
        if comparison > 0 or (
            comparison == 0 and str(snapshot["id"]) not in cursor_id_set
        ):
            pending.append(snapshot)
    return pending


def cursor_from_snapshots(
    snapshots: Sequence[Dict[str, str]],
) -> Tuple[Optional[str], List[str]]:
    latest = latest_snapshot_time(snapshots)
    if latest is None:
        return None, []
    ids = [
        str(snapshot["id"])
        for snapshot in snapshots
        if compare_restic_times(str(snapshot.get("time") or ""), latest) == 0
    ]
    return latest, ids


def compare_restic_times(first: str, second: str) -> int:
    first_moment = parse_restic_time(first)
    second_moment = parse_restic_time(second)
    if first_moment is not None and second_moment is not None:
        if first_moment == second_moment:
            return 0
        return 1 if first_moment > second_moment else -1
    first_text = str(first or "")
    second_text = str(second or "")
    if first_text == second_text:
        return 0
    return 1 if first_text > second_text else -1


def parse_restic_time(text: str) -> Optional[datetime]:
    match = RESTIC_TIME_PATTERN.match(str(text or "").strip())
    if match is None:
        return None
    fraction = (match.group("fraction") or "")[:6].ljust(6, "0")
    offset = match.group("offset") or ""
    if offset == "Z":
        offset = "+00:00"
    elif len(offset) == 5 and ":" not in offset:
        offset = offset[:3] + ":" + offset[3:]
    normalized = match.group("base")
    if fraction:
        normalized += "." + fraction
    normalized += offset
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
