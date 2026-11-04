# Task Completion Checklist

Run from repo root (venv activated). Mirror of `.github/workflows/ci.yml`:

1. Tests + coverage (fail-under 55):
```
python -m coverage run --branch --source=mcdr2restic,tools -m unittest discover -s tests -p "test_*.py"
python -m coverage report --show-missing --fail-under=55
```
2. Syntax gate: `python -m compileall -q mcdr2restic tests tools`
3. If ANY lang string or `template.*` key changed: `python tools/sync_language_resources.py` — verify `zh_tw` regenerated and both `lang/` + `mcdr2restic/lang/` copies updated; never leave them diverged.
4. If packaging/verify behavior changed or preparing a release:
```
python tools/pack_plugin.py --output-dir dist-local
python tools/verify_plugin.py --archive dist-local
```
(release: also `--git-tag vX.Y.Z` matching `mcdreforged.plugin.json` version)

Notes:
- CI runs the same steps on ubuntu AND windows, Python 3.10/3.11 — Windows-specific code paths (paths, `.posix`/`.windows` template suffixes, process handling) must pass locally on Windows before pushing.
- No linter/formatter gate exists; match surrounding style manually.
