# -*- coding: utf-8 -*-
from __future__ import annotations

from mcdr2restic.core.bootstrap import ensure_runtime_dependencies


_HOOK_METHODS = (
    ('_shutdown_runtime', 'shutdown_runtime'),
    ('on_load', 'on_load'),
    ('on_unload', 'on_unload'),
    ('on_server_startup', 'on_server_startup'),
    ('on_server_stop', 'on_server_stop'),
    ('on_mcdr_stop', 'on_mcdr_stop'),
    ('on_player_joined', 'on_player_joined'),
    ('on_player_left', 'on_player_left'),
)


def _create_entrypoint():
    bootstrap_result = ensure_runtime_dependencies()
    from mcdr2restic.core.plugin import create_plugin_entrypoint
    return create_plugin_entrypoint(bootstrap_result)


def _make_entrypoint_hook(entrypoint, method_name):
    def hook(*args):
        return getattr(entrypoint, method_name)(*args)

    return hook


def _build_hooks():
    entrypoint = _create_entrypoint()
    return tuple(_make_entrypoint_hook(entrypoint, method_name) for _, method_name in _HOOK_METHODS)

# MCDR 只能发现模块级 hook 名称；运行时对象留在闭包内，避免被其它模块当作全局状态引用。
(
    _shutdown_runtime,
    on_load,
    on_unload,
    on_server_startup,
    on_server_stop,
    on_mcdr_stop,
    on_player_joined,
    on_player_left,
) = _build_hooks()

del _build_hooks
del _create_entrypoint
del _make_entrypoint_hook

__all__ = [
    '_shutdown_runtime',
    'on_load',
    'on_unload',
    'on_server_startup',
    'on_server_stop',
    'on_mcdr_stop',
    'on_player_joined',
    'on_player_left',
]
