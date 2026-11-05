# -*- coding: utf-8 -*-
"""内置信任的 restic 版本清单（restic_release.yml）与回退 release 数据。

信任锚点以数据文件维护：升级 restic 时只需更新 restic_release.yml 的
version 与各资产的 name/url/sha256，无需改动代码。
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any, Dict

import yaml


RELEASE_RESOURCE_PACKAGE = "mcdr2restic.defaults"
TRUSTED_RELEASE_RESOURCE = "restic_release.yml"


@lru_cache(maxsize=None)
def load_trusted_restic_release() -> Dict[str, Any]:
    """读取内置信任的 restic 版本清单；缺失或格式异常时返回空 dict。"""

    try:
        with resources.open_text(
            RELEASE_RESOURCE_PACKAGE, TRUSTED_RELEASE_RESOURCE, encoding="utf8"
        ) as file:
            data = yaml.safe_load(file)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def get_default_restic_version() -> str:
    """默认下载并校验的 restic 版本，即内置信任版本。"""

    version = str(load_trusted_restic_release().get("version") or "").strip()
    return version or "latest"


def build_restic_fallback_release() -> Dict[str, Any]:
    """把信任清单转换为 GitHub release 元数据形状，供下载流程复用。"""

    release = load_trusted_restic_release()
    version = str(release.get("version") or "").strip()
    if not version:
        return {}
    assets = []
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        assets.append(
            {
                "name": str(asset.get("name") or ""),
                "browser_download_url": str(asset.get("url") or ""),
                "sha256": str(asset.get("sha256") or ""),
            }
        )
    return {"tag_name": version, "assets": assets}
