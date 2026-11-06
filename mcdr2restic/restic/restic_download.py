# -*- coding: utf-8 -*-
from __future__ import annotations

import bz2
import contextlib
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from mcdreforged.api.all import PluginServerInterface

from mcdr2restic.core.i18n import server_tr
from mcdr2restic.defaults.default_constants import (
    DEFAULT_PROXY_PREFIXES,
    DEFAULT_RESTIC_EXECUTABLE_POSIX,
    DEFAULT_RESTIC_EXECUTABLE_WINDOWS,
    DEFAULT_RESTIC_MIRROR_BASE,
    PLUGIN_ID,
    RESTIC_PROGRESS_INTERVAL_SECONDS,
)
from mcdr2restic.defaults.restic_release_defaults import (
    build_restic_fallback_release,
    get_default_restic_version,
)
from mcdr2restic.core.models import BackupProblem
from mcdr2restic.restic.restic_config import resolve_path_against_working_directory
from mcdr2restic.restic.restic_constants import (
    RESTIC_CFG_EXECUTABLE,
    RESTIC_CFG_USE_PATH_RESTIC,
)


# 自动下载为固化行为：版本与校验值取内置信任清单，超时与加速前缀不再开放配置
RESTIC_DOWNLOAD_TIMEOUT_SECONDS = 120
# timeout 只约束单次 socket 操作，连接存活但缓慢滴流时可无限挂死，
# 因此每次尝试另设整体 deadline
RESTIC_DOWNLOAD_ATTEMPT_DEADLINE_SECONDS = 300
DOWNLOAD_CHUNK_BYTES = 256 * 1024
# 版本探测是本地进程调用，等待预算远小于网络下载；探测失败一律按跳过升级处理
RESTIC_VERSION_PROBE_TIMEOUT_SECONDS = 30
# 匹配 "restic 0.18.0" / "restic v0.19.1" 形式的版本输出
RESTIC_VERSION_OUTPUT_PATTERN = re.compile(r"restic\s+v?(\d+(?:\.\d+)+)")


def find_system_restic() -> Optional[str]:
    """探测系统 PATH 中的 restic（Windows 按 PATHEXT 自动匹配 restic.exe）。"""

    return shutil.which("restic")


def system_restic_preferred(restic_cfg: Dict[str, Any]) -> bool:
    return bool(restic_cfg.get(RESTIC_CFG_USE_PATH_RESTIC, False))


def ensure_default_restic_executable_available(
    server: PluginServerInterface, restic_cfg: Dict[str, Any]
):
    executable = str(
        restic_cfg.get(RESTIC_CFG_EXECUTABLE, DEFAULT_RESTIC_EXECUTABLE_POSIX) or ""
    ).strip()
    if not is_default_restic_executable_path(executable):
        return
    target_path = resolve_restic_executable_path(restic_cfg, executable)
    # 系统 PATH 的 restic 不归插件管；开关开启且系统存在时，
    # 即使配置目录留有插件下载的旧副本也只会使用系统的
    if system_restic_preferred(restic_cfg) and find_system_restic():
        return
    if os.path.exists(target_path):
        upgrade_default_restic_if_outdated(server, target_path)
        return
    install_default_restic_for_platform(server, target_path)


def upgrade_default_restic_if_outdated(
    server: PluginServerInterface, target_path: str
):
    """把插件自管路径上过旧的 restic 升级到内置信任版本。

    仅向上升级：版本相同、更新或无法比较时保留现状；探测与升级
    失败都只告警，不阻断后续 restic 操作。
    """
    trusted_version = get_default_restic_version()
    if parse_restic_version_number(trusted_version) is None:
        return
    try:
        installed_version = probe_restic_version(target_path)
    except ResticVersionProbeError as exc:
        server.logger.warning(
            server_tr(server, "warn.restic.auto_upgrade.probe_failed", error=exc)
        )
        return
    order = compare_restic_versions(installed_version, trusted_version)
    if order > 0:
        # 用户手动放置了更高版本，尊重现状
        server.logger.info(
            server_tr(
                server,
                "info.restic.auto_upgrade.skipped_newer",
                installed_version=installed_version,
                trusted_version=trusted_version,
            )
        )
        return
    if order < 0:
        server.logger.info(
            server_tr(
                server,
                "info.restic.auto_upgrade.start",
                installed_version=installed_version,
                trusted_version=trusted_version,
            )
        )
        try:
            install_default_restic_for_platform(server, target_path)
        except Exception as exc:
            server.logger.warning(
                server_tr(
                    server,
                    "warn.restic.auto_upgrade.failed",
                    installed_version=installed_version,
                    error=exc,
                )
            )


class ResticVersionProbeError(Exception):
    """restic 版本探测失败：进程无法启动、超时或输出无法识别。"""


def probe_restic_version(executable_path: str) -> str:
    """运行 restic version 并返回形如 0.18.0 的版本号。

    进程无法启动、超时或输出无法识别时抛 ResticVersionProbeError。
    """
    try:
        completed = subprocess.run(
            [executable_path, "version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=RESTIC_VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ResticVersionProbeError(
            str(exc) or exc.__class__.__name__
        ) from exc
    for output in (completed.stdout, completed.stderr):
        match = RESTIC_VERSION_OUTPUT_PATTERN.search(output or "")
        if match:
            return match.group(1)
    detail = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    raise ResticVersionProbeError(detail[:200] or "no restic version output")


def parse_restic_version_number(version: str) -> Optional[Tuple[int, ...]]:
    """把 v0.19.1 形式的版本串解析为数字元组，无法解析时返回 None。"""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)+)", str(version or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def compare_restic_versions(left: str, right: str) -> int:
    """逐段数值比较版本，缺失段按 0 补齐；任一无法解析时返回 0。"""
    left_parts = parse_restic_version_number(left)
    right_parts = parse_restic_version_number(right)
    if left_parts is None or right_parts is None:
        return 0
    width = max(len(left_parts), len(right_parts))
    left_padded = left_parts + (0,) * (width - len(left_parts))
    right_padded = right_parts + (0,) * (width - len(right_parts))
    return (left_padded > right_padded) - (left_padded < right_padded)


def install_default_restic_for_platform(
    server: PluginServerInterface, target_path: str
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
    download_and_install_restic(server, asset_keyword, output_name, target_path)


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
    asset_keyword: str,
    output_name: str,
    target_path: str,
):
    # 版本与校验值完全由内置信任清单固化，不再请求 GitHub API 元数据
    release = build_restic_fallback_release()
    if not release:
        raise BackupProblem(i18n_key="error.restic.release_manifest_missing")
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
        ResticDownloadTarget(
            asset_url=asset_url,
            asset_name=asset_name,
            asset_keyword=asset_keyword,
            output_name=output_name,
            target_path=target_path,
            timeout=RESTIC_DOWNLOAD_TIMEOUT_SECONDS,
            version=str(release.get("tag_name") or ""),
            sha256=str(asset.get("sha256") or ""),
        ),
    )


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
    target: ResticDownloadTarget,
):
    urls = build_download_urls(target.asset_url, DEFAULT_PROXY_PREFIXES)
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
                download_file(
                    url,
                    archive_path,
                    target.timeout,
                    progress=make_download_progress_reporter(server),
                )
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


def build_self_mirror_url(
    asset_url: str, mirror_base: str = DEFAULT_RESTIC_MIRROR_BASE
) -> str:
    # 自建镜像按 GitHub release 资产原路径映射：{base}/v<版本>/<原文件名>
    match = re.search(r"/releases/download/([^/]+)/([^/?#]+)", asset_url)
    if not match:
        return ""
    return "{}/{}/{}".format(mirror_base.rstrip("/"), match.group(1), match.group(2))


def build_download_urls(asset_url: str, proxy_prefixes: Any) -> List[str]:
    # 候选顺序：GitHub 直连 → 自建镜像 → 公共加速前缀
    urls = [asset_url]
    mirror_url = build_self_mirror_url(asset_url)
    if mirror_url:
        urls.append(mirror_url)
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


def build_download_request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": "MCDR2Restic/{}".format(PLUGIN_ID),
            "Accept": "application/octet-stream, application/json",
        },
    )


def download_file(
    url: str,
    path: str,
    timeout: int,
    progress: Optional[Callable[[int, int], None]] = None,
):
    # 分块流式落盘并施加单次尝试整体 deadline：timeout 只约束单次 socket
    # 操作，连接存活但缓慢滴流时若无整体截止时间会无限挂死
    request = build_download_request(url)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(
            path, "wb"
        ) as file:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                if (
                    time.monotonic() - started
                    > RESTIC_DOWNLOAD_ATTEMPT_DEADLINE_SECONDS
                ):
                    raise TimeoutError(
                        "exceeded the {}s per-attempt deadline".format(
                            RESTIC_DOWNLOAD_ATTEMPT_DEADLINE_SECONDS
                        )
                    )
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException) as exc:
        # 读中段的超时/断连不走 URLError 分支，统一包装以保留 URL 上下文
        raise BackupProblem(
            i18n_key="error.restic.download_failed",
            url=mask_download_url(url),
            error=exc,
        ) from exc


def download_bytes(url: str, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(
            build_download_request(url), timeout=timeout
        ) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError, HTTPException) as exc:
        raise BackupProblem(
            i18n_key="error.restic.download_failed",
            url=mask_download_url(url),
            error=exc,
        ) from exc


def format_byte_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "{:.1f}{}".format(value, unit)
        value /= 1024
    return "{:.1f}GB".format(value)


def make_download_progress_reporter(
    server: PluginServerInterface,
) -> Callable[[int, int], None]:
    """按固定间隔输出下载进度；最后一包完成时必报一次。"""

    started = time.monotonic()
    last_reported = 0.0

    def report(downloaded: int, total: int):
        nonlocal last_reported
        now = time.monotonic()
        if 0 < downloaded < total and now - last_reported < (
            RESTIC_PROGRESS_INTERVAL_SECONDS
        ):
            return
        last_reported = now
        elapsed = max(now - started, 0.001)
        server.logger.info(
            server_tr(
                server,
                "info.restic.download_progress",
                downloaded=format_byte_size(downloaded),
                total=format_byte_size(total) if total else "?",
                percent="{}".format(int(downloaded * 100 / total)) if total else "?",
                speed="{}/s".format(format_byte_size(int(downloaded / elapsed))),
            )
        )

    return report


def verify_restic_download(target: ResticDownloadTarget, archive_path: str):
    """下载产物必须先通过 SHA256 校验才允许落盘执行，防镜像篡改。"""

    # 校验值随资产固化在内置清单中，不再向网络拉取校验文件
    expected = target.sha256
    if not expected:
        raise BackupProblem(
            i18n_key="error.restic.checksum_manifest_missing", version=target.version
        )
    actual = sha256_file(archive_path)
    if actual != expected:
        raise BackupProblem(
            i18n_key="error.restic.checksum_mismatch",
            asset_name=target.asset_name,
            expected=expected,
            actual=actual,
        )


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
