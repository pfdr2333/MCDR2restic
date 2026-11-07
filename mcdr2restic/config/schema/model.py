# -*- coding: utf-8 -*-
"""配置 schema 的声明模型。

每个配置键只在此声明一次；运行时默认值、配置模板渲染与老配置
文件迁移全部由同一份声明推导，新增键不再需要多处手工登记。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class Field:
    """顶级配置段内的单个配置键。

    除默认值外还携带模板渲染元数据：注释键指向 lang 资源中的
    ``template.comment.*`` 文本，占位标记与逐字值行用于少数需要
    平台分支或特殊排版的键。
    """

    key: str
    default: Any
    # 键上方注释的 lang 键；行尾注释（comment_inline）也复用此键
    comment_key: Optional[str] = None
    # 注释写在键行尾而非上方（如 notification 段）
    comment_inline: bool = False
    # 键上方逐字输出的占位标记行（模板列 0，由渲染管线展开为平台相关注释）
    comment_markers: Tuple[str, ...] = ()
    # 值位置的占位标记（如 __MCDR2RESTIC_LANGUAGE__），渲染管线负责替换
    value_marker: Optional[str] = None
    # 逐字输出的值行（已含缩进）；仅用于手工排版的特殊值，值本身以 default 为准
    raw_value_lines: Optional[Tuple[str, ...]] = None
    # 序列项使用 YAML 单引号标量风格（正则等含反斜杠的值）
    quote_single_items: bool = False


@dataclass(frozen=True)
class Section:
    """一个顶级配置块；声明顺序即模板中的块顺序。"""

    key: str
    fields: Tuple[Field, ...]
    # 块内首条目上方的段级注释（如 notification 段的策略说明）
    intro_comment_key: Optional[str] = None

    def defaults(self) -> Dict[str, Any]:
        """返回本段默认值；可变默认值深拷贝，避免共享可变状态。"""
        return {item.key: deepcopy(item.default) for item in self.fields}
