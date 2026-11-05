# -*- coding: utf-8 -*-
from __future__ import annotations

import bz2
import contextlib
import hashlib
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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.defaults.default_constants import (
    DEFAULT_RESTIC_EXECUTABLE_POSIX,
    DEFAULT_RESTIC_EXECUTABLE_WINDOWS,
    PLUGIN_ID,
)
from mcdr2restic.defaults.restic_release_defaults import build_restic_fallback_release
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_config import resolve_path_against_working_directory
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_AUTO_DOWNLOAD,
    RESTIC_CFG_DOWNLOAD_PROXY_PREFIXES,
    RESTIC_CFG_DOWNLOAD_TIMEOUT_SECONDS,
    RESTIC_CFG_DOWNLOAD_VERSION,
    RESTIC_CFG_EXECUTABLE,
)


def ensure_default_restic_executable_available(
    server: PluginServerInterface, restic_cfg: Dict[str, Any]
):
    if not bool(restic_cfg.get(RESTIC_CFG_AUTO_DOWNLOAD, True)):
        return
    executable = str(
        restic_cfg.get(RESTIC_CFG_EXECUTABLE, DEFAULT_RESTIC_EXECUTABLE_POSIX) or ""
    ).strip()
    if not is_default_restic_executable_path(executable):
        return
    target_path = resolve_restic_executable_path(restic_cfg, executable)
    if os.path.exists(target_path):
        return
    install_default_restic_for_platform(server, restic_cfg, target_path)


def install_default_restic_for_platform(
    server: PluginServerInterface, restic_cfg: Dict[str, Any], target_path: str
):
    platform_info = get_restic_download_platform()
    if platform_info is None:
        server.logger.warning(
            server_tr(
                server, "warn.restic.auto_download.unsupported", target_path=target_path
            )
        )
        return
    system_name, asset_keyword, output_name = platform_info
    server.logger.info(
        server_tr(
            server,
            "info.restic.auto_download.start",
            system_name=system_name,
            target_path=target_path,
        )
    )
    download_and_install_restic(
        server, restic_cfg, asset_keyword, output_name, target_path
    )


def is_default_restic_executable_path(executable: str) -> bool:
    normalized = executable.replace("\\", "/").lower()
    return normalized in {
        "./restic",
        "restic",
        "./restic.exe",
        "restic.exe",
        DEFAULT_RESTIC_EXECUTABLE_POSIX.replace("\\", "/").lower(),
        DEFAULT_RESTIC_EXECUTABLE_WINDOWS.replace("\\", "/").lower(),
    }


def is_bare_restic_name(executable: str) -> bool:
    normalized = executable.replace("\\", "/").strip().lower()
    return normalized in {"restic", "restic.exe"}


def default_restic_executable_for_platform() -> str:
    return (
        DEFAULT_RESTIC_EXECUTABLE_WINDOWS
        if os.name == "nt"
        else DEFAULT_RESTIC_EXECUTABLE_POSIX
    )


def resolve_default_restic_install_path(executable: str) -> str:
    """裸名与默认路径族统一解析到当前平台默认安装位置，保证下载与执行一致。"""

    if is_bare_restic_name(executable):
        return os.path.abspath(default_restic_executable_for_platform())
    return os.path.abspath(executable)


def resolve_restic_executable_path(restic_cfg: Dict[str, Any], executable: str) -> str:
    expanded = os.path.expanduser(os.path.expandvars(executable))
    if is_config_folder_default_restic_path(expanded) or is_bare_restic_name(expanded):
        return resolve_default_restic_install_path(expanded)
    return resolve_path_against_working_directory(expanded, restic_cfg)


def is_config_folder_default_restic_path(executable: str) -> bool:
    normalized = executable.replace("\\", "/").lower()
    return normalized in {
        DEFAULT_RESTIC_EXECUTABLE_POSIX.replace("\\", "/").lower(),
        DEFAULT_RESTIC_EXECUTABLE_WINDOWS.replace("\\", "/").lower(),
    }


def get_restic_download_platform() -> Optional[Tuple[str, str, str]]:
    machine = platform.machine().lower()
    if machine not in ("x86_64", "amd64"):
        return None
    if sys.platform.startswith("linux"):
        return "linux", "linux_amd64.bz2", "restic"
    if os.name == "nt":
        return "windows", "windows_amd64.zip", "restic.exe"
    return None


def download_and_install_restic(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    asset_keyword: str,
    output_name: str,
    target_path: str,
):
    timeout = max(10, int(restic_cfg.get(RESTIC_CFG_DOWNLOAD_TIMEOUT_SECONDS, 120)))
    version = str(
        restic_cfg.get(RESTIC_CFG_DOWNLOAD_VERSION, "latest") or "latest"
    ).strip()
    release = fetch_restic_release_metadata(version, timeout)
    asset = select_restic_release_asset(release, asset_keyword)
    asset_url = str(asset.get("browser_download_url", "") or "")
    asset_name = str(asset.get("name", "") or "")
    if not asset_url:
        raise BackupProblem(
            i18n_key="error.restic.release_missing_download_url",
            asset_name=asset_name or asset_keyword,
        )
    install_restic_from_urls(
        server,
        restic_cfg,
        ResticDownloadTarget(
            asset_url=asset_url,
            asset_name=asset_name,
            asset_keyword=asset_keyword,
            output_name=output_name,
            target_path=target_path,
            timeout=timeout,
            version=resolve_restic_release_version(release, version),
            sha256=str(asset.get("sha256") or ""),
        ),
    )


def resolve_restic_release_version(release: Dict[str, Any], requested: str) -> str:
    """优先取 release 实际 tag，供固化校验表命中（latest 回退场景）。"""

    return str(release.get("tag_name") or "").strip() or requested


@dataclass(frozen=True)
class ResticDownloadTarget:
    """一次 restic 下载安装任务的资源描述。"""

    asset_url: str
    asset_name: str
    asset_keyword: str
    output_name: str
    target_path: str
    timeout: int
    version: str = ""
    sha256: str = ""


def install_restic_from_urls(
    server: PluginServerInterface,
    restic_cfg: Dict[str, Any],
    target: ResticDownloadTarget,
):
    urls = build_download_urls(
        target.asset_url, restic_cfg.get(RESTIC_CFG_DOWNLOAD_PROXY_PREFIXES, [])
    )
    last_error = ""
    os.makedirs(os.path.dirname(target.target_path) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mcdr2restic-") as temp_dir:
        archive_path = os.path.join(
            temp_dir, target.asset_name or "restic-download"
        )
        for url in urls:
            try:
                server.logger.info(
                    server_tr(
                        server, "info.restic.downloading", url=mask_download_url(url)
                    )
                )
                download_file(url, archive_path, target.timeout)
                verify_restic_download(target, archive_path)
                install_restic_archive(
                    archive_path,
                    target.asset_keyword,
                    target.output_name,
                    target.target_path,
                )
                server.logger.info(
                    server_tr(
                        server,
                        "info.restic.auto_download.completed",
                        target_path=target.target_path,
                    )
                )
                return
            except Exception as exc:
                last_error = str(exc)
                server.logger.warning(
                    server_tr(
                        server, "warn.restic.auto_download_retry", error=last_error
                    )
                )
    raise BackupProblem(
        i18n_key="error.restic.auto_download_failed",
        error=last_error or target.asset_url,
    )


def fetch_restic_release_metadata(version: str, timeout: int) -> Dict[str, Any]:
    # 内置信任版本直接使用清单数据，无需访问 GitHub API
    trusted = build_restic_fallback_release()
    if trusted and version.lower() == str(trusted.get("tag_name") or "").lower():
        return trusted
    url = restic_release_url(version)
    try:
        data = download_bytes(url, timeout)
    except Exception:
        if version.lower() == "latest":
            return trusted
        raise
    return parse_restic_release_metadata(data)


def restic_release_url(version: str) -> str:
    if version.lower() == "latest":
        return "https://api.github.com/repos/restic/restic/releases/latest"
    return "https://api.github.com/repos/restic/restic/releases/tags/{}".format(version)


def parse_restic_release_metadata(data: bytes) -> Dict[str, Any]:
    try:
        metadata = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise BackupProblem(i18n_key="error.restic.release_parse_failed", error=exc) from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("assets"), list):
        raise BackupProblem(i18n_key="error.restic.release_invalid")
    return metadata


SIGNATURE_ASSET_SUFFIXES = (".asc", ".sig")


def select_restic_release_asset(
    release: Dict[str, Any], asset_keyword: str
) -> Dict[str, Any]:
    for asset in release.get("assets", []):
        name = str(asset.get("name", "") or "")
        if name.lower().endswith(SIGNATURE_ASSET_SUFFIXES):
            # 跳过 GPG 签名文件，避免子串匹配选中签名而非二进制本体
            continue
        if asset_keyword in name:
            return asset
    raise BackupProblem(
        i18n_key="error.restic.release_asset_not_found", asset_keyword=asset_keyword
    )


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
        prefix_text = str(prefix or "").strip()
        if prefix_text:
            urls.append(prefix_text.rstrip("/") + "/" + asset_url)
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
    with open(path, "wb") as file:
        file.write(data)


def download_bytes(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MCDR2Restic/{}".format(PLUGIN_ID),
            "Accept": "application/octet-stream, application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise BackupProblem(
            i18n_key="error.restic.download_failed",
            url=mask_download_url(url),
            error=exc,
        ) from exc


SHA256SUMS_URL_TEMPLATE = (
    "https://github.com/restic/restic/releases/download/{version}/SHA256SUMS"
)


def verify_restic_download(target: ResticDownloadTarget, archive_path: str):
    """下载产物必须先通过 SHA256 校验才允许落盘执行，防镜像篡改。"""

    expected = expected_restic_sha256(target)
    if not expected:
        raise BackupProblem(
            i18n_key="error.restic.checksum_file_unavailable", version=target.version
        )
    actual = sha256_file(archive_path)
    if actual != expected:
        raise BackupProblem(
            i18n_key="error.restic.checksum_mismatch",
            asset_name=target.asset_name,
            expected=expected,
            actual=actual,
        )


def expected_restic_sha256(target: ResticDownloadTarget) -> str:
    if target.sha256:
        return target.sha256
    return fetch_official_sha256(target.asset_name, target.version, target.timeout)


def fetch_official_sha256(asset_name: str, version: str, timeout: int) -> str:
    # 校验文件只从 github.com 官方源获取，镜像源不作为信任锚点
    version_text = str(version or "").strip()
    if not version_text or version_text.lower() == "latest":
        return ""
    url = SHA256SUMS_URL_TEMPLATE.format(version=version_text)
    try:
        data = download_bytes(url, timeout)
    except Exception:
        return ""
    return extract_sha256_from_sums(data.decode("utf-8", "replace"), asset_name)


def extract_sha256_from_sums(text: str, asset_name: str) -> str:
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        filename = parts[1].strip().lstrip("*").strip()
        if filename != asset_name:
            continue
        digest = parts[0].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest
    return ""


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_restic_archive(
    archive_path: str, asset_keyword: str, output_name: str, target_path: str
):
    # 先解压到同目录临时文件再原子替换：中断不会在目标路径留下残缺二进制，
    # 存在性检查也不必区分"完整安装"与"半截文件"
    temp_path = build_install_temp_path(target_path)
    try:
        if asset_keyword.endswith(".zip"):
            install_restic_zip(archive_path, output_name, temp_path)
        elif asset_keyword.endswith(".bz2"):
            install_restic_bz2(archive_path, temp_path)
        else:
            raise BackupProblem(
                i18n_key="error.restic.archive_unsupported",
                asset_keyword=asset_keyword,
            )
        make_executable_on_posix(temp_path)
        os.replace(temp_path, target_path)
    except BaseException:
        remove_quietly(temp_path)
        raise


def build_install_temp_path(target_path: str) -> str:
    directory = os.path.dirname(target_path) or "."
    return os.path.join(
        directory, "{}.tmp-{}".format(os.path.basename(target_path), os.getpid())
    )


def remove_quietly(path: str):
    with contextlib.suppress(OSError):
        os.remove(path)


def install_restic_zip(archive_path: str, output_name: str, target_path: str):
    with zipfile.ZipFile(archive_path, "r") as archive:
        member = find_zip_member(archive, output_name)
        with archive.open(member, "r") as source, open(target_path, "wb") as target:
            shutil.copyfileobj(source, target)


def install_restic_bz2(archive_path: str, target_path: str):
    with open(archive_path, "rb") as source:
        data = bz2.decompress(source.read())
    if not data:
        raise BackupProblem(i18n_key="error.restic.bz2_empty")
    with open(target_path, "wb") as target:
        target.write(data)


def make_executable_on_posix(target_path: str):
    if os.name == "nt":
        return
    mode = os.stat(target_path).st_mode
    os.chmod(target_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def find_zip_member(archive: zipfile.ZipFile, output_name: str) -> str:
    members = [
        name
        for name in archive.namelist()
        if not name.endswith("/") and not os.path.basename(name).startswith("__MACOSX")
    ]
    candidate = find_exact_zip_member(members, output_name)
    if candidate:
        return candidate
    candidate = find_executable_zip_member(members)
    if candidate:
        return candidate
    raise BackupProblem(
        i18n_key="error.restic.zip_executable_not_found",
        members=", ".join(members[:10]),
    )


def find_exact_zip_member(members: List[str], output_name: str) -> str:
    output_name_lower = output_name.lower()
    for name in members:
        if os.path.basename(name).lower() == output_name_lower:
            return name
    return ""


def find_executable_zip_member(members: List[str]) -> str:
    exe_members = [
        name for name in members if os.path.basename(name).lower().endswith(".exe")
    ]
    restic_exe_members = [
        name for name in exe_members if "restic" in os.path.basename(name).lower()
    ]
    if len(restic_exe_members) == 1:
        return restic_exe_members[0]
    if len(exe_members) == 1:
        return exe_members[0]
    restic_members = [
        name for name in members if "restic" in os.path.basename(name).lower()
    ]
    return restic_members[0] if len(restic_members) == 1 else ""


def mask_download_url(url: str) -> str:
    return re.sub(r"([?&](?:token|access_token)=)[^&]+", r"\1***", url)
