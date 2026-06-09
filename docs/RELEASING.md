# Releasing Ascot

This describes how to cut a new Ascot release: bump the version, build the
distribution artifacts, prune stale ones, and commit. The version lives in
exactly one place — `pyproject.toml`. `ascot/__init__.py` reads it at runtime via
`importlib.metadata`, so there is nothing else to edit.

## Versioning

Ascot uses `MAJOR.MINOR.PATCH`:

- **MINOR** bump for a new feature (e.g. a new CLI flag or subcommand).
- **PATCH** bump for a bug fix or small enhancement to existing behavior.

## Steps

### 1. Bump the version

Edit `version` in `pyproject.toml`:

```toml
[project]
version = "0.9.2"
```

### 2. Run the tests

```bash
python -m pytest tests/ -q
```

All tests must pass before building.

### 3. Build the distribution

Requires the `build` package (`pip install build` if missing):

```bash
python -m build
```

This writes a wheel and sdist to `dist/`, e.g.:

```
dist/ascot-0.9.2-py3-none-any.whl
dist/ascot-0.9.2.tar.gz
```

### 4. Prune old dist artifacts

`dist/` is committed, so it accumulates over time. Keep only the **latest patch
of each `MAJOR.MINOR` series** — for example keep `0.8.2` but drop `0.8.0` and
`0.8.1`. Remove the superseded files with `git rm`:

```bash
git rm dist/ascot-0.9.0* dist/ascot-0.9.1*
```

Older artifacts remain recoverable from git history if ever needed.

### 5. Commit

Use two commits, matching the existing history:

1. **Code + version bump** — the feature/fix changes together with the
   `pyproject.toml` bump:

   ```
   Add --grading-model to ascot run, fix judge events copy call, bump to 0.9.2
   ```

2. **Distribution** — the built artifacts and any pruning:

   ```
   dist 0.9.2, prune old dist artifacts
   ```

Do not push or tag unless explicitly coordinating a published release.

## Quick reference

```bash
# 1. edit pyproject.toml version
python -m pytest tests/ -q          # 2. tests green
python -m build                     # 3. build wheel + sdist
git rm dist/ascot-OLD*              # 4. prune superseded artifacts
# 5. commit code+bump, then commit dist
```
