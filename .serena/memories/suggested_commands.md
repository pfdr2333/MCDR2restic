# Suggested Commands (Windows / Git Bash)

Run from repo root. venv: `source .venv/Scripts/activate` (Git Bash on Windows — note `Scripts`, not `bin`).

## Tests (CI parity — unittest, not pytest)
```
python -m coverage run --branch --source=mcdr2restic,tools -m unittest discover -s tests -p "test_*.py"
python -m coverage report --show-missing --fail-under=55
```
Must run from repo root: `tests/support.py` inserts REPO_ROOT into sys.path and installs the mcdreforged stub. Single module: `python -m unittest tests.test_restic_runner` (or `-k pattern` via `python -m unittest discover -s tests -p "test_*.py" -k name`).

## Language / template sync (after editing zh_cn or en_us strings)
```
python tools/sync_language_resources.py   # needs opencc (requirements-dev)
```

## Pack + verify plugin archive
```
python tools/pack_plugin.py --output-dir dist-local
python tools/verify_plugin.py --archive dist-local            # local check
python tools/verify_plugin.py --archive dist-ci --git-tag v0.5.0   # release/tag check
```

## Docs
```
python -m pip install -r requirements-docs.txt
mkdocs serve    # or: mkdocs build
```

## Windows notes
- Quote Windows paths (spaces, backslashes); forward slashes work in Git Bash.
- Use `python`/`python -m ...` (launcher resolves venv once activated); there is no Makefile/task runner.
