# Project Core — MCDR2restic

MCDReforged plugin (id `mcdr2restic`) wrapping the external `restic` binary for Minecraft server backups: scheduling, restore workflow, snapshots cache, multi-channel notifications (OneBot, Discord webhook).

## Entry chain (non-obvious)
- `mcdr2restic/__init__.py`: module-level MCDR hooks (`on_load`, `on_server_stop`, …) are built in closures at import time; runtime entrypoint object is deliberately NOT a module global (helper builders are `del`'d). Don't "simplify" this into globals.
- Import of `core.plugin` happens only AFTER `ensure_runtime_dependencies()` (bootstrap check) succeeds.
- `mcdr2restic/core/plugin.py::PluginEntrypoint` holds all real lifecycle logic (~29 methods); `create_plugin_entrypoint` is the factory.

## Source map (package `mcdr2restic/`)
- `backup/` — BackupRunner, BackupScheduler, cron/scheduling
- `commands/` — MCDR command tree (backup/restic/restore/status), CommandHandlers + per-domain command classes
- `config/` — loader, migration, template, state store (config snapshot pattern)
- `core/` — plugin entrypoint, runtime, i18n, bootstrap, models
- `defaults/` — default config/constants/messages (generated-ish, large)
- `minecraft/` — server state, player activity tracking
- `notifications/` — dispatcher + OneBot client + Discord webhook
- `restic/` — process/runner/service, lock recovery, download, maintenance; `restic_process.py` is a re-export facade over `restic_runner.py` etc.
- `restore/` — restore workflow + task repository
- `snapshots/` — snapshot db/importer/cache
- `update/` — plugin update checker

## Critical invariants
- Language files exist TWICE: root `lang/` (packed plugin resource) and `mcdr2restic/lang/` (runtime). They are generated/synced — see `mem:conventions` for the sync rule; never edit `zh_tw` by hand.
- `mcdr2restic/config_templates/` is generated from `template.message.`/`template.snippet.` lang keys (platform suffixes `.posix`/`.windows`).
- restic binary is downloaded/managed at runtime (`restic/restic_download.py`), not a Python dependency.
- Tests run WITHOUT mcdreforged installed — `tests/support.py::install_mcdr_stub()` injects a stub into `sys.modules` (see `mem:tech_stack`).

## Deeper reference
- Commands to build/test/pack: `mem:suggested_commands`; final checklist: `mem:task_completion`; style rules: `mem:conventions`; deps and versions: `mem:tech_stack`.
