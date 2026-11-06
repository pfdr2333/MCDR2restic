# -*- coding: utf-8 -*-
"""构建发布产物时把 restic 最新稳定版注入内置信任清单。

用法：python tools/pin_latest_restic.py [--manifest PATH]

从 GitHub API 查询 restic 最新稳定 release，配合该版本官方 SHA256SUMS
生成 mcdr2restic/defaults/restic_release.yml，使发布产物自动追新 restic。
仓库内提交的同名文件仅作开发基线；注入失败时保留基线并告警，不阻塞发布。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "mcdr2restic" / "defaults" / "restic_release.yml"

RELEASE_API_URL = "https://api.github.com/repos/restic/restic/releases/latest"
PLATFORM_ASSET_SUFFIXES = ("linux_amd64.bz2", "windows_amd64.zip")
SUMS_ASSET_NAME = "SHA256SUMS"
DIGEST_PATTERN = re.compile(r"^([0-9a-f]{64})\s+\*?(.+)$", re.MULTILINE)

MANIFEST_HEADER = """\
# MCDR2restic 内置信任的 restic 版本与校验值（插件初始化自动下载时使用）。
# 发布产物由 CI 构建时自动注入 restic 最新版（tools/pin_latest_restic.py）；
# 仓库内版本为开发基线：升级 restic 时更新 version 与各资产 name/url/sha256。
"""

BASE_REQUEST_HEADERS = {
    "User-Agent": "MCDR2Restic-release-pinning",
    "Accept": "application/vnd.github+json, application/octet-stream",
}


def build_request(url: str) -> urllib.request.Request:
    headers = dict(BASE_REQUEST_HEADERS)
    # GITHUB_TOKEN 仅经环境变量流转，用于提升 API 限流配额
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer {}".format(token)
    return urllib.request.Request(url, headers=headers)


def fetch_latest_release() -> dict:
    with urllib.request.urlopen(build_request(RELEASE_API_URL), timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))
    if not isinstance(release, dict) or not release.get("tag_name"):
        raise ValueError("latest release payload missing tag_name")
    return release


def select_platform_assets(release: dict) -> list:
    assets = []
    for suffix in PLATFORM_ASSET_SUFFIXES:
        matches = [
            asset
            for asset in release.get("assets", [])
            if str(asset.get("name", "")).endswith(suffix)
        ]
        if len(matches) != 1:
            raise ValueError(
                "expected exactly one {} asset, got {}".format(suffix, len(matches))
            )
        assets.append(matches[0])
    return assets


def fetch_sums_text(release: dict) -> str:
    matches = [
        asset
        for asset in release.get("assets", [])
        if str(asset.get("name", "")) == SUMS_ASSET_NAME
    ]
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one {} asset, got {}".format(
                SUMS_ASSET_NAME, len(matches)
            )
        )
    request = build_request(str(matches[0]["browser_download_url"]))
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def parse_sha256sums(text: str) -> dict:
    return {
        name: digest
        for digest, name in DIGEST_PATTERN.findall(text)
    }


def render_manifest(version: str, assets: list, digests: dict) -> str:
    lines = [MANIFEST_HEADER, "version: {}".format(version), "assets:"]
    for asset in assets:
        name = str(asset["name"])
        url = str(asset["browser_download_url"])
        digest = digests.get(name, "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("missing sha256 for asset {}".format(name))
        lines.append("  - name: {}".format(name))
        lines.append("    url: {}".format(url))
        lines.append("    sha256: {}".format(digest))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="trusted release manifest path",
    )
    args = parser.parse_args()

    try:
        release = fetch_latest_release()
        version = str(release["tag_name"])
        assets = select_platform_assets(release)
        digests = parse_sha256sums(fetch_sums_text(release))
        rendered = render_manifest(version, assets, digests)
    except Exception as exc:
        # 注入失败保留仓库内开发基线，发布不因此阻塞，但要在 Actions 里显眼
        print("::warning::restic pinning failed, keeping committed baseline: {}".format(exc))
        return 0

    if args.manifest.exists() and args.manifest.read_text(encoding="utf-8") == rendered:
        print("restic manifest already pinned to {}".format(version))
        return 0

    args.manifest.write_text(rendered, encoding="utf-8")
    print("restic manifest pinned to {} ({} platform assets)".format(version, len(assets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
