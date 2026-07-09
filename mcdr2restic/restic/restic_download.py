# -*- coding: utf-8 -*-
from __future__ import annotations

import bz2
import copy
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional, Set, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.defaults.default_constants import PLUGIN_ID
from mcdr2restic.defaults.restic_release_defaults import build_restic_fallback_release
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_AUTO_DOWNLOAD,
    RESTIC_CFG_DOWNLOAD_PROXY_PREFIXES,
    RESTIC_CFG_DOWNLOAD_TIMEOUT_SECONDS,
    RESTIC_CFG_DOWNLOAD_VERSION,
    RESTIC_CFG_EXECUTABLE,
    RESTIC_CFG_WORKING_DIRECTORY,
)


def ensure_default_restic_executable_available(server: PluginServerInterface, restic_cfg: Dict[str, Any]):
    if not bool(restic_cfg.get(RESTIC_CFG_AUTO_DOWNLOAD, True)):
        return
    executable = str(restic_cfg.get(RESTIC_CFG_EXECUTABLE, './restic') or '').strip()
    if not is_default_restic_executable_path(executable):
        return
    target_path = resolve_restic_executable_path(restic_cfg, executable)
    if os.path.exists(target_path):
        return
    install_default_restic_for_platform(server, restic_cfg, target_path)


def install_default_restic_for_platform(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    target_path: str
):
    platform_info = get_restic_download_platform()
    if platform_info is None:
        server.logger.warning(server_tr(server, 'warn.restic.auto_download.unsupported', target_path=target_path))
        return
    system_name, asset_keyword, output_name = platform_info
    server.logger.info(server_tr(
        server,
        'info.restic.auto_download.start',
        system_name=system_name,
        target_path=target_path
    ))
    download_and_install_restic(server, restic_cfg, asset_keyword, output_name, target_path)


def is_default_restic_executable_path(executable: str) -> bool:
    normalized = executable.replace('\\', '/').lower()
    return normalized in ('./restic', 'restic', './restic.exe', 'restic.exe')


def resolve_restic_executable_path(restic_cfg: Dict[str, Any], executable: str) -> str:
    expanded = os.path.expanduser(os.path.expandvars(executable))
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    cwd = restic_cfg.get(RESTIC_CFG_WORKING_DIRECTORY) or os.getcwd()
    return os.path.abspath(os.path.join(str(cwd), expanded))


def get_restic_download_platform() -> Optional[Tuple[str, str, str]]:
    machine = platform.machine().lower()
    if machine not in ('x86_64', 'amd64'):
        return None
    if sys.platform.startswith('linux'):
        return 'linux', 'linux_amd64.bz2', 'restic'
    if os.name == 'nt':
        return 'windows', 'windows_amd64.zip', 'restic.exe'
    return None


def download_and_install_restic(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    asset_keyword: str,
    output_name: str,
    target_path: str
):
    timeout = max(10, int(restic_cfg.get(RESTIC_CFG_DOWNLOAD_TIMEOUT_SECONDS, 120)))
    version = str(restic_cfg.get(RESTIC_CFG_DOWNLOAD_VERSION, 'latest') or 'latest').strip()
    release = fetch_restic_release_metadata(version, timeout)
    asset = select_restic_release_asset(release, asset_keyword)
    asset_url = str(asset.get('browser_download_url', '') or '')
    asset_name = str(asset.get('name', '') or '')
    if not asset_url:
        raise BackupProblem(
            i18n_key='error.restic.release_missing_download_url',
            asset_name=asset_name or asset_keyword
        )
    install_restic_from_urls(server, restic_cfg, asset_url, asset_name, asset_keyword, output_name, target_path, timeout)


def install_restic_from_urls(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    asset_url: str,
    asset_name: str,
    asset_keyword: str,
    output_name: str,
    target_path: str,
    timeout: int
):
    urls = build_download_urls(asset_url, restic_cfg.get(RESTIC_CFG_DOWNLOAD_PROXY_PREFIXES, []))
    last_error = ''
    os.makedirs(os.path.dirname(target_path) or '.', exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mcdr2restic-') as temp_dir:
        archive_path = os.path.join(temp_dir, asset_name or 'restic-download')
        for url in urls:
            try:
                server.logger.info(server_tr(server, 'info.restic.downloading', url=mask_download_url(url)))
                download_file(url, archive_path, timeout)
                install_restic_archive(archive_path, asset_keyword, output_name, target_path)
                server.logger.info(server_tr(server, 'info.restic.auto_download.completed', target_path=target_path))
                return
            except Exception as exc:
                last_error = str(exc)
                server.logger.warning(server_tr(server, 'warn.restic.auto_download_retry', error=last_error))
    raise BackupProblem(i18n_key='error.restic.auto_download_failed', error=last_error or asset_url)


def fetch_restic_release_metadata(version: str, timeout: int) -> Dict[str, Any]:
    url = restic_release_url(version)
    try:
        data = download_bytes(url, timeout)
    except Exception:
        if version.lower() == 'latest':
            return build_restic_fallback_release()
        raise
    return parse_restic_release_metadata(data)


def restic_release_url(version: str) -> str:
    if version.lower() == 'latest':
        return 'https://api.github.com/repos/restic/restic/releases/latest'
    return 'https://api.github.com/repos/restic/restic/releases/tags/{}'.format(version)


def parse_restic_release_metadata(data: bytes) -> Dict[str, Any]:
    try:
        metadata = json.loads(data.decode('utf-8'))
    except Exception as exc:
        raise BackupProblem(i18n_key='error.restic.release_parse_failed', error=exc)
    if not isinstance(metadata, dict) or not isinstance(metadata.get('assets'), list):
        raise BackupProblem(i18n_key='error.restic.release_invalid')
    return metadata


def select_restic_release_asset(release: Dict[str, Any], asset_keyword: str) -> Dict[str, Any]:
    for asset in release.get('assets', []):
        name = str(asset.get('name', '') or '')
        if asset_keyword in name:
            return asset
    raise BackupProblem(i18n_key='error.restic.release_asset_not_found', asset_keyword=asset_keyword)


def build_download_urls(asset_url: str, proxy_prefixes: Any) -> List[str]:
    urls = [asset_url]
    if isinstance(proxy_prefixes, str):
        proxy_prefixes = [proxy_prefixes]
    if isinstance(proxy_prefixes, (list, tuple, set)):
        urls.extend(build_proxy_urls(asset_url, proxy_prefixes))
    return unique_urls(urls)


def build_proxy_urls(asset_url: str, proxy_prefixes: Any) -> List[str]:
    urls: List[str] = []
    for prefix in proxy_prefixes:
        prefix_text = str(prefix or '').strip()
        if prefix_text:
            urls.append(prefix_text.rstrip('/') + '/' + asset_url)
    return urls


def unique_urls(urls: List[str]) -> List[str]:
    seen: Set[str] = set()
    unique: List[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


def download_file(url: str, path: str, timeout: int):
    data = download_bytes(url, timeout)
    with open(path, 'wb') as file:
        file.write(data)


def download_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'MCDR2Restic/{}'.format(PLUGIN_ID),
            'Accept': 'application/octet-stream, application/json'
        }
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise BackupProblem(
            i18n_key='error.restic.download_failed',
            url=mask_download_url(url),
            error=exc
        )


def install_restic_archive(archive_path: str, asset_keyword: str, output_name: str, target_path: str):
    if asset_keyword.endswith('.zip'):
        install_restic_zip(archive_path, output_name, target_path)
    elif asset_keyword.endswith('.bz2'):
        install_restic_bz2(archive_path, target_path)
    else:
        raise BackupProblem(i18n_key='error.restic.archive_unsupported', asset_keyword=asset_keyword)
    make_executable_on_posix(target_path)


def install_restic_zip(archive_path: str, output_name: str, target_path: str):
    with zipfile.ZipFile(archive_path, 'r') as archive:
        member = find_zip_member(archive, output_name)
        with archive.open(member, 'r') as source, open(target_path, 'wb') as target:
            shutil.copyfileobj(source, target)


def install_restic_bz2(archive_path: str, target_path: str):
    with open(archive_path, 'rb') as source:
        data = bz2.decompress(source.read())
    if not data:
        raise BackupProblem(i18n_key='error.restic.bz2_empty')
    with open(target_path, 'wb') as target:
        target.write(data)


def make_executable_on_posix(target_path: str):
    if os.name == 'nt':
        return
    mode = os.stat(target_path).st_mode
    os.chmod(target_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def find_zip_member(archive: zipfile.ZipFile, output_name: str) -> str:
    members = [
        name for name in archive.namelist()
        if not name.endswith('/') and not os.path.basename(name).startswith('__MACOSX')
    ]
    candidate = find_exact_zip_member(members, output_name)
    if candidate:
        return candidate
    candidate = find_executable_zip_member(members)
    if candidate:
        return candidate
    raise BackupProblem(
        i18n_key='error.restic.zip_executable_not_found',
        members=', '.join(members[:10])
    )


def find_exact_zip_member(members: List[str], output_name: str) -> str:
    output_name_lower = output_name.lower()
    for name in members:
        if os.path.basename(name).lower() == output_name_lower:
            return name
    return ''


def find_executable_zip_member(members: List[str]) -> str:
    exe_members = [
        name for name in members
        if os.path.basename(name).lower().endswith('.exe')
    ]
    restic_exe_members = [
        name for name in exe_members
        if 'restic' in os.path.basename(name).lower()
    ]
    if len(restic_exe_members) == 1:
        return restic_exe_members[0]
    if len(exe_members) == 1:
        return exe_members[0]
    restic_members = [
        name for name in members
        if 'restic' in os.path.basename(name).lower()
    ]
    return restic_members[0] if len(restic_members) == 1 else ''


def mask_download_url(url: str) -> str:
    return re.sub(r'([?&](?:token|access_token)=)[^&]+', r'\1***', url)
