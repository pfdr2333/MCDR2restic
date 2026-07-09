# -*- coding: utf-8 -*-
from __future__ import annotations

from types import MappingProxyType
from typing import Any


def freeze_default(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: freeze_default(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_default(item) for item in value)
    return value
