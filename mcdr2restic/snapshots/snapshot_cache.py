# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import threading
import time
from contextlib import closing
from typing import Any, Callable, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.defaults.default_config import build_default_config, build_default_snapshot_cache_config
from mcdr2restic.defaults.default_constants import (
    SNAPSHOT_PAGE_SIZE,
    SNAPSHOT_QUERY_TIMEOUT_SECONDS,
)
from mcdr2restic.core.language import is_zh_language
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_config import (
    get_effective_restic_repository,
    normalize_filesystem_path,
)
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_ENVIRONMENT,
    RESTIC_CFG_EXECUTABLE,
    RESTIC_CFG_PASSWORD,
    RESTIC_CFG_PASSWORD_FILE,
    RESTIC_CFG_REPOSITORY,
    RESTIC_CFG_WORKING_DIRECTORY,
)
from mcdr2restic.snapshots.snapshot_db import (
    commit_refreshed_snapshot_cache,
    delete_snapshot_temp_rows,
    open_snapshot_db,
    read_snapshot_meta,
    record_snapshot_refresh_failure,
)
from mcdr2restic.snapshots.snapshot_importer import import_restic_snapshots_to_sql
from mcdr2restic.config.state_store import merge_defaults
from mcdr2restic.core.utils import now_text, sha256_text


def build_snapshot_cache_key(restic_cfg: Dict[str, Any]) -> str:
    configured_env = restic_cfg.get(RESTIC_CFG_ENVIRONMENT, {})
    if not isinstance(configured_env, dict):
        configured_env = {}
    key_material = {
        RESTIC_CFG_EXECUTABLE: str(restic_cfg.get(RESTIC_CFG_EXECUTABLE, 'restic') or 'restic'),
        RESTIC_CFG_WORKING_DIRECTORY: normalize_optional_path(str(restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY, '') or '')),
        RESTIC_CFG_REPOSITORY: str(get_effective_restic_repository(restic_cfg) or ''),
        RESTIC_CFG_PASSWORD: sha256_text(str(restic_cfg.get(RESTIC_CFG_PASSWORD, '') or '')),
        RESTIC_CFG_PASSWORD_FILE: normalize_optional_path(str(restic_cfg.get(RESTIC_CFG_PASSWORD_FILE, '') or '')),
        RESTIC_CFG_ENVIRONMENT: hashed_environment_pairs(configured_env),
    }
    text = json.dumps(key_material, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256_text(text)


def hashed_environment_pairs(configured_env: Dict[Any, Any]):
    return [
        [str(key), sha256_text('' if value is None else str(value))]
        for key, value in sorted(configured_env.items(), key=lambda item: str(item[0]))
    ]


def normalize_optional_path(path: str) -> str:
    text = str(path or '').strip()
    if not text:
        return ''
    try:
        return normalize_filesystem_path(text)
    except Exception:
        return text


def get_snapshot_cache_config(
    cfg: Optional[Dict[str, Any]] = None,
    config_snapshot_provider: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if cfg is None:
        cfg = config_snapshot_provider() if config_snapshot_provider is not None else build_default_config()
    snapshot_cfg = cfg.get('snapshot_cache', {}) if isinstance(cfg.get('snapshot_cache'), dict) else {}
    merged = copy.deepcopy(snapshot_cfg)
    merge_defaults(merged, build_default_snapshot_cache_config())
    return merged


def get_snapshot_page_size(snapshot_cfg: Dict[str, Any]) -> int:
    return max(1, min(100, int(snapshot_cfg.get('page_size', SNAPSHOT_PAGE_SIZE) or SNAPSHOT_PAGE_SIZE)))


def get_snapshot_query_timeout(snapshot_cfg: Dict[str, Any]) -> int:
    return max(1, int(snapshot_cfg.get('query_timeout_seconds', SNAPSHOT_QUERY_TIMEOUT_SECONDS) or SNAPSHOT_QUERY_TIMEOUT_SECONDS))


def ensure_snapshot_cache_fresh(
    snapshot_query_lock: threading.Lock,
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    cache_key: str,
    snapshot_cfg: Dict[str, Any],
    language: str,
) -> str:
    if is_snapshot_cache_valid(server, snapshot_cfg, cache_key):
        return ''
    if not snapshot_query_lock.acquire(blocking=False):
        return localized_refresh_running(language)
    try:
        refresh_snapshot_cache(server, restic_cfg, cache_key, snapshot_cfg)
    except BackupProblem:
        return ''
    finally:
        snapshot_query_lock.release()
    return ''


def is_snapshot_cache_valid(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
    cache_key: str,
) -> bool:
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        meta = read_snapshot_meta(conn, cache_key)
    return meta is not None and int(meta['invalidated'] or 0) == 0


def localized_refresh_running(language: str) -> str:
    if is_zh_language(language):
        return '快照缓存正在刷新'
    return 'Snapshot cache refresh is running'


def refresh_snapshot_cache(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    cache_key: str,
    snapshot_cfg: Dict[str, Any],
):
    temp_key = make_snapshot_refresh_temp_key(cache_key)
    started = time.monotonic()
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        try:
            count = import_restic_snapshots_to_sql(restic_cfg, conn, temp_key, get_snapshot_query_timeout(snapshot_cfg))
            commit_refreshed_snapshot_cache(conn, cache_key, temp_key, count, time.monotonic() - started)
        except Exception as exc:
            error = record_snapshot_refresh_failure(conn, cache_key, temp_key, exc, time.monotonic() - started)
            raise BackupProblem(error)
        finally:
            delete_snapshot_temp_rows(conn, temp_key)


def make_snapshot_refresh_temp_key(cache_key: str) -> str:
    return '{}:refresh:{}:{}'.format(cache_key, int(time.time() * 1000), threading.get_ident())


def invalidate_snapshot_cache(
    server: Optional[PluginServerInterface] = None,
    restic_cfg: Optional[Dict[str, Any]] = None,
    reason: str = 'repository changed',
    default_server: Optional[PluginServerInterface] = None,
    config_snapshot_provider: Optional[Callable[[], Dict[str, Any]]] = None,
):
    target, resolved_restic_cfg, snapshot_cfg = resolve_snapshot_invalidation_context(
        server,
        restic_cfg,
        default_server,
        config_snapshot_provider
    )
    if target is None:
        return
    if not bool(snapshot_cfg.get('enabled', True)):
        return
    try:
        mark_snapshot_cache_invalid(target, snapshot_cfg, resolved_restic_cfg, reason)
    except Exception as exc:
        target.logger.debug('标记 restic 快照缓存失效失败: {}'.format(exc))


def resolve_snapshot_invalidation_context(
    server: Optional[PluginServerInterface],
    restic_cfg: Optional[Dict[str, Any]],
    default_server: Optional[PluginServerInterface],
    config_snapshot_provider: Optional[Callable[[], Dict[str, Any]]],
) -> Tuple[Optional[PluginServerInterface], Dict[str, Any], Dict[str, Any]]:
    target = server or default_server
    if restic_cfg is not None:
        return target, restic_cfg, get_snapshot_cache_config(config_snapshot_provider=config_snapshot_provider)

    cfg = config_snapshot_provider() if config_snapshot_provider is not None else build_default_config()
    resolved_restic_cfg = cfg.get('restic', {}) if isinstance(cfg.get('restic'), dict) else {}
    return target, resolved_restic_cfg, get_snapshot_cache_config(cfg, config_snapshot_provider=config_snapshot_provider)


def mark_snapshot_cache_invalid(
    server: PluginServerInterface,
    snapshot_cfg: Dict[str, Any],
    restic_cfg: Dict[str, Any],
    reason: str,
):
    cache_key = build_snapshot_cache_key(restic_cfg)
    with closing(open_snapshot_db(server, snapshot_cfg)) as conn:
        existing = read_snapshot_meta(conn, cache_key)
        with conn:
            conn.execute(
                '''
                INSERT OR REPLACE INTO snapshot_meta
                (
                    cache_key, updated_at_epoch, updated_at_text, snapshot_count,
                    error, invalidated, invalidation_reason, last_refresh_duration
                )
                VALUES (?, ?, ?, ?, '', 1, ?, ?)
                ''',
                build_snapshot_invalidation_row(cache_key, existing, reason)
            )


def build_snapshot_invalidation_row(cache_key: str, existing, reason: str) -> Tuple[Any, ...]:
    return (
        cache_key,
        float(existing['updated_at_epoch'] or 0) if existing is not None else 0,
        existing['updated_at_text'] if existing is not None else None,
        int(existing['snapshot_count'] or 0) if existing is not None else 0,
        '{} @ {}'.format(reason, now_text()),
        float(existing['last_refresh_duration'] or 0) if existing is not None else 0
    )
