# -*- coding: utf-8 -*-
"""配置 schema 汇总：全部顶级段与默认值推导的唯一事实源。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Tuple

from mcdr2restic.config.schema.model import Field, Section
from mcdr2restic.config.schema.sections_core import (
    COMMAND,
    FORCE_SCHEDULE,
    MAINTENANCE_SCHEDULE,
    MINECRAFT,
    SCHEDULE,
    UPDATE_CHECK,
)
from mcdr2restic.config.schema.sections_notify import (
    CUSTOM_WEBHOOK,
    DISCORD,
    NOTIFICATION,
    ONEBOT,
)
from mcdr2restic.config.schema.sections_restic import RESTIC
from mcdr2restic.config.schema.sections_service import RESTORE, SNAPSHOT_CACHE
from mcdr2restic.defaults.message_defaults import build_default_messages
from mcdr2restic.defaults.default_constants import CONFIG_VERSION

DEFAULT_LANGUAGE_CONFIG_VALUE = "zh_cn"

# 顶级标量键（不属于任何配置块），顺序即模板顺序
TOP_LEVEL_FIELDS: Tuple[Field, ...] = (
    Field("enabled", True),
    Field("language", DEFAULT_LANGUAGE_CONFIG_VALUE),
)

# 顶级配置块，顺序即模板与迁移插入顺序
SECTIONS: Tuple[Section, ...] = (
    COMMAND,
    SCHEDULE,
    FORCE_SCHEDULE,
    MAINTENANCE_SCHEDULE,
    UPDATE_CHECK,
    MINECRAFT,
    RESTIC,
    ONEBOT,
    DISCORD,
    CUSTOM_WEBHOOK,
    NOTIFICATION,
    SNAPSHOT_CACHE,
    RESTORE,
)

__all__ = [
    "DEFAULT_LANGUAGE_CONFIG_VALUE",
    "SECTIONS",
    "TOP_LEVEL_FIELDS",
    "derive_base_default_config",
    "find_section",
    "section_defaults",
]


def derive_base_default_config() -> Dict[str, Any]:
    """从 schema 推导基线默认配置（messages 固定为 zh_cn 文本）。"""

    cfg: Dict[str, Any] = {item.key: deepcopy(item.default) for item in TOP_LEVEL_FIELDS}
    for section in SECTIONS:
        cfg[section.key] = section.defaults()
    cfg["messages"] = build_default_messages("zh_cn")
    cfg["config_version"] = CONFIG_VERSION
    return cfg


def section_defaults(section_key: str) -> Dict[str, Any]:
    """返回指定顶级段的默认值副本。"""

    section = find_section(section_key)
    if section is None:
        raise KeyError("Unknown config section: {}".format(section_key))
    return section.defaults()


def find_section(section_key: str):
    for section in SECTIONS:
        if section.key == section_key:
            return section
    return None
