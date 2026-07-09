# -*- coding: utf-8 -*-
"""Freeze mutable default data so callers cannot mutate shared templates."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, TypeAlias


FrozenScalar: TypeAlias = str | int | float | bool | None
MutableDefaultValue: TypeAlias = (
    FrozenScalar | dict[str, "MutableDefaultValue"] | list["MutableDefaultValue"]
)
FrozenDefaultValue: TypeAlias = (
    FrozenScalar
    | Mapping[str, "FrozenDefaultValue"]
    | tuple["FrozenDefaultValue", ...]
)


def freeze_default(value: MutableDefaultValue) -> FrozenDefaultValue:
    """Recursively convert mutable default containers into immutable ones."""

    if isinstance(value, dict):
        return MappingProxyType(
            {key: freeze_default(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(freeze_default(item) for item in value)
    return value
