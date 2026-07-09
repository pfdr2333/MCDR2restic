# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.defaults.default_constants import DEFAULT_PROXY_PREFIXES, DEFAULT_UPDATE_API_URL, PLUGIN_REPOSITORY_URL
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_download import build_download_urls, download_bytes, mask_download_url
from mcdr2restic.backup.scheduling import compute_update_check_wait_seconds


ConfigProvider = Callable[[], Dict[str, Any]]
PLUGIN_METADATA_FILE = 'mcdreforged.plugin.json'


class UpdateChecker:
    def __init__(
        self,
        server: PluginServerInterface,
        check_on_startup: bool,
        config_provider: ConfigProvider
    ):
        self.server = server
        self.check_on_startup = check_on_startup
        self.config_provider = config_provider
        self.stop_event = threading.Event()
        self.wakeup_event = threading.Event()
        self.thread = threading.Thread(target=self._main, name='MCDR2Restic-UpdateCheck', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wakeup_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def _main(self):
        self.server.logger.info(server_tr(self.server, 'log.update.thread_started'))
        if self.check_on_startup:
            self.check_now('startup')
        while not self.stop_event.is_set():
            try:
                wait_seconds, due_text = compute_update_check_wait_seconds(self.config_provider())
            except Exception as exc:
                self.server.logger.warning(server_tr(self.server, 'warn.update.compute_next_failed', error=exc))
                self._wait(60)
                continue
            self.server.logger.debug(server_tr(
                self.server,
                'debug.update.next_wait',
                seconds=int(wait_seconds),
                due_text=due_text
            ))
            if self._wait(wait_seconds) or self.stop_event.is_set():
                continue
            self.check_now('daily')
        self.server.logger.info(server_tr(self.server, 'log.update.thread_stopped'))

    def check_now(self, reason: str):
        cfg = self.config_provider()
        update_cfg = cfg.get('update_check', {}) if isinstance(cfg.get('update_check'), dict) else {}
        if not bool(update_cfg.get('enabled', True)):
            return
        try:
            current_version = get_current_plugin_version(self.server)
            latest = fetch_latest_plugin_release(update_cfg)
            latest_version = release_version_from_payload(latest)
            latest_url = str(latest.get('html_url') or update_cfg.get('release_page_url') or PLUGIN_REPOSITORY_URL)
            self._log_check_result(reason, current_version, latest_version, latest_url)
        except Exception as exc:
            self.server.logger.warning(server_tr(self.server, 'warn.update.check_failed', reason=reason, error=exc))

    def _log_check_result(self, reason: str, current_version: str, latest_version: str, latest_url: str):
        if is_newer_version(latest_version, current_version):
            self.server.logger.warning(server_tr(
                self.server,
                'warn.update.new_version',
                latest_version=latest_version,
                current_version=current_version,
                latest_url=latest_url
            ))
            return
        self.server.logger.info(server_tr(
            self.server,
            'info.update.up_to_date',
            current_version=current_version,
            latest_version=latest_version,
            reason=reason
        ))

    def _wait(self, seconds: float) -> bool:
        end = time.monotonic() + max(0.0, seconds)
        self.wakeup_event.clear()
        while not self.stop_event.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            if self.wakeup_event.wait(timeout=min(60.0, remaining)):
                self.wakeup_event.clear()
                return True
        return True


def get_current_plugin_version(server: Optional[PluginServerInterface]) -> str:
    metadata_version = read_server_plugin_version(server)
    if metadata_version:
        return metadata_version
    return normalize_release_version(read_bundled_plugin_version()) or '0.0.0'


def read_server_plugin_version(server: Optional[PluginServerInterface]) -> str:
    if server is None:
        return ''
    try:
        metadata = server.get_self_metadata()
        version = getattr(metadata, 'version', '')
    except Exception:
        return ''
    return normalize_release_version(str(version)) if version else ''


def read_bundled_plugin_version(metadata_path: Optional[str] = None) -> str:
    path = metadata_path or bundled_plugin_metadata_path()
    try:
        with open(path, 'r', encoding='utf8') as file:
            data = json.load(file)
    except Exception:
        return ''
    return str(data.get('version', '') or '') if isinstance(data, dict) else ''


def bundled_plugin_metadata_path() -> str:
    # update_check.py lives two package levels below the plugin metadata file.
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, PLUGIN_METADATA_FILE)
    )


def fetch_latest_plugin_release(update_cfg: Dict[str, Any]) -> Dict[str, Any]:
    api_url = str(update_cfg.get('api_url', DEFAULT_UPDATE_API_URL) or DEFAULT_UPDATE_API_URL).strip()
    timeout = max(3, int(update_cfg.get('timeout_seconds', 10)))
    urls = build_download_urls(api_url, update_cfg.get('proxy_prefixes', DEFAULT_PROXY_PREFIXES))
    last_error = ''
    for url in urls:
        try:
            payload = json.loads(download_bytes(url, timeout).decode('utf-8'))
            if isinstance(payload, dict) and (payload.get('tag_name') or payload.get('name')):
                return payload
            raise BackupProblem(i18n_key='error.update.release_payload_invalid')
        except Exception as exc:
            last_error = '{}: {}'.format(mask_download_url(url), exc)
    raise BackupProblem(i18n_key='error.update.latest_release_fetch_failed', error=last_error or '')


def release_version_from_payload(payload: Dict[str, Any]) -> str:
    version = normalize_release_version(str(payload.get('tag_name') or payload.get('name') or ''))
    if not version:
        raise BackupProblem(i18n_key='error.update.latest_release_missing_version')
    return version


def normalize_release_version(version: str) -> str:
    text = str(version or '').strip()
    if text.lower().startswith('version '):
        text = text.split(None, 1)[1].strip()
    return text.lstrip('vV').strip()


def is_newer_version(latest: str, current: str) -> bool:
    latest_tuple = version_number_tuple(latest)
    current_tuple = version_number_tuple(current)
    if latest_tuple or current_tuple:
        width = max(len(latest_tuple), len(current_tuple), 1)
        return pad_version_tuple(latest_tuple, width) > pad_version_tuple(current_tuple, width)
    return normalize_release_version(latest) > normalize_release_version(current)


def pad_version_tuple(version: Tuple[int, ...], width: int) -> Tuple[int, ...]:
    return version + (0,) * max(0, width - len(version))


def version_number_tuple(version: str) -> Tuple[int, ...]:
    text = normalize_release_version(version)
    main = re.split(r'[-+_\s]', text, maxsplit=1)[0]
    return tuple(int(part) for part in re.findall(r'\d+', main))
