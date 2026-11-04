# Conventions

## File/module style
- Every module starts with `# -*- coding: utf-8 -*-` then `from __future__ import annotations`.
- Type hints: `typing.Optional/Dict/Tuple/Callable` style (3.10-compatible), `typing.Final` for constants.
- Logic lives in small module-level functions; classes only for stateful services (`PluginEntrypoint`, `BackupScheduler`, runners, clients) with `create_*` factory functions.
- Facade pattern: e.g. `restic/restic_process.py` re-exports symbols from `restic_runner.py` with explicit `__all__` — import from the facade, not the implementation module, when both exist.

## i18n (strict)
- All user-facing strings go through lang keys: `server_tr(server, "key.path")`; message templates in `defaults/message_defaults.py`.
- `zh_tw.json` is GENERATED from `zh_cn.json` via OpenCC (s2twp) by `tools/sync_language_resources.py` — never hand-edit zh_tw.
- Lang JSONs live in both root `lang/` (plugin resource, packed) and `mcdr2restic/lang/` (runtime) — sync tool keeps them identical; edit source, run tool, commit both.
- `mcdr2restic/config_templates/` is generated from lang keys prefixed `template.message.`/`template.snippet.`; platform-specific variants use suffix `.posix`/`.windows`.

## Config
- YAML config with safe migration + fail-fast on corruption (`config/config_migration.py`, `config_loader.py`); runtime reads via config snapshots (`state_store.get_config_snapshot`), not mutable globals.

## Tests
- stdlib `unittest.TestCase` + `unittest.mock`; shared stubs/fakes in `tests/support.py` (installs mcdreforged stub at import).
- New tests: `tests/test_<domain>.py`, import support first if you need stubs/fakes.
- Never import real `mcdreforged` in tests (not installed).

## Commit style
- Short Chinese imperative subject lines (see git log), e.g. "修复语言文件不一致…".
