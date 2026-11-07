# -*- coding: utf-8 -*-
"""配置 schema 的声明模型。

每个配置键只在此声明一次；运行时默认值、配置模板渲染与老配置
文件迁移全部由同一份声明推导，新增键不再需要多处手工登记。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class Field:
    """顶级配置段内的单个配置键。"""

    key: str
    default: Any


@dataclass(frozen=True)
class Section:
    """一个顶级配置块；声明顺序即模板中的块顺序。"""

    key: str
    fields: Tuple[Field, ...]

    def defaults(self) -> Dict[str, Any]:
        """返回本段默认值；可变默认值按次深拷贝，避免共享可变状态。"""
        return {item.key: deepcopy(item.default) for item in self.fields}
