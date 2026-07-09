# -*- coding: utf-8 -*-
"""Expose MCDReforged module-level hooks without leaking runtime globals."""

from __future__ import annotations

from typing import Any, Callable, Final

from mcdr2restic.core.bootstrap import ensure_runtime_dependencies


_HOOK_METHODS: Final[tuple[tuple[str, str], ...]] = (
    ("_shutdown_runtime", "shutdown_runtime"),
    ("on_load", "on_load"),
    ("on_unload", "on_unload"),
    ("on_server_startup", "on_server_startup"),
    ("on_server_stop", "on_server_stop"),
    ("on_mcdr_stop", "on_mcdr_stop"),
    ("on_player_joined", "on_player_joined"),
    ("on_player_left", "on_player_left"),
)


def _create_entrypoint() -> Any:
    """Build the plugin entrypoint after bootstrap dependency checks."""

    bootstrap_result = ensure_runtime_dependencies()
    from mcdr2restic.core.plugin import create_plugin_entrypoint

    return create_plugin_entrypoint(bootstrap_result)


def _make_entrypoint_hook(entrypoint: Any, method_name: str) -> Callable[..., Any]:
    """Bind a module-level hook name to the concrete entrypoint method."""

    def hook(*args: Any) -> Any:
        """Delegate a discovered MCDR hook call to the runtime entrypoint."""

        return getattr(entrypoint, method_name)(*args)

    return hook


def _build_hooks() -> tuple[Callable[..., Any], ...]:
    """Create the module-level hook callables that MCDR will import."""

    entrypoint = _create_entrypoint()
    return tuple(
        _make_entrypoint_hook(entrypoint, method_name)
        for _, method_name in _HOOK_METHODS
    )


# MCDR only discovers module-level hook names. Keep the runtime object in a closure so
# other modules do not treat it as shared global state.
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
    "_shutdown_runtime",
    "on_load",
    "on_unload",
    "on_server_startup",
    "on_server_stop",
    "on_mcdr_stop",
    "on_player_joined",
    "on_player_left",
]
