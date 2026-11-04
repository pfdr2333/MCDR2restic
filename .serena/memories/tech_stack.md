# Tech Stack

- Python >= 3.10 (CI matrix: 3.10 + 3.11 on ubuntu-latest AND windows-latest; cross-platform matters — path/process code must work on both).
- Host dev environment: Windows + Git Bash; venv at `.venv`.
- MCDReforged plugin API >= 2.12.0 (`mcdreforged.plugin.json`, entrypoint `mcdr2restic`). **mcdreforged is NOT installed in the dev venv** — tests stub it via `tests/support.py::install_mcdr_stub()`; importing `mcdreforged.api.all` outside tests only happens on a real MCDR server.
- Runtime deps (packed into plugin): PyYAML>=6.0, websocket-client>=1.8.0. Keep minimal — MCDR installs from `requirements.txt` inside the archive.
- Dev deps (`requirements-dev.txt`): coverage>=7.6,<8; opencc-python-reimplemented>=0.1.7,<0.2 (zh_tw generation via OpenCC s2twp).
- Docs (`requirements-docs.txt`): mkdocs>=1.6 + mkdocs-static-i18n>=1.3 (`mkdocs.yml`, `docs/`, output `site/`).
- External tool: `restic` — downloaded at plugin runtime, never a pip dep.
- Test framework: stdlib `unittest` discovered under coverage (NOT pytest, despite stale `.pytest_cache/`).
- No linter config in repo; CI syntax gate is `python -m compileall`. `.ruff_cache/` is stale leftover from local runs.
