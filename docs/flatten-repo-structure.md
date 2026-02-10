# Flattening the Repository Structure

> **STATUS: PLANNING DOCUMENT - NOT IMPLEMENTED**
> This document outlines a potential repository restructuring to flatten the `src/` layout.
> The repository still uses the `src/mkts_backend/` structure and this change has not been implemented.
> This file is kept for future reference if the restructuring is desired.

This document explains how to convert from the current `src/` layout to a flat layout.

## Current Structure
```
mkts_backend/
├── src/
│   └── mkts_backend/
│       ├── cli_tools/
│       ├── config/
│       ├── db/
│       ├── processing/
│       ├── utils/
│       └── ...
├── tests/
├── pyproject.toml
└── ...
```

## Target Structure
```
mkts_backend/
├── mkts_backend/
│   ├── cli_tools/
│   ├── config/
│   ├── db/
│   ├── processing/
│   ├── utils/
│   └── ...
├── tests/
├── pyproject.toml
└── ...
```

## Steps

### 1. Move the package directory

```bash
# From the repo root
mv src/mkts_backend ./mkts_backend_new
rmdir src
mv mkts_backend_new mkts_backend
```

Or in one step:
```bash
mv src/mkts_backend . && rmdir src
```

### 2. Update pyproject.toml

Find the setuptools package configuration section and update it:

**Before:**
```toml
[tool.setuptools.packages.find]
where = ["src"]
```

**After:**
```toml
[tool.setuptools.packages.find]
where = ["."]
```

If using `[tool.setuptools]` with explicit packages, update paths accordingly.

### 3. Reinstall the package

```bash
# If using uv
uv sync

# Or with pip
pip install -e .
```

### 4. Verify imports still work

```bash
uv run python -c "from mkts_backend.cli_tools.fit_check import fit_check_command; print('OK')"
```

### 5. Run tests

```bash
uv run pytest tests/ --ignore=tests/test_region_history.py --ignore=tests/test_utils.py
```

### 6. Update any hardcoded paths

Search for any references to `src/mkts_backend` in:
- CI/CD configs (`.github/workflows/`)
- Documentation
- Scripts

```bash
grep -r "src/mkts_backend" .
```

## Why Flatten?

**Pros of flat layout:**
- Simpler navigation (one less directory level)
- Easier for small-to-medium projects
- Works fine for CLI tools that aren't libraries

**Cons (why `src/` exists):**
- Without `src/`, you can accidentally import the local uninstalled package
- Can mask packaging bugs (missing files in distribution)

For this project (primarily a CLI tool), the flat layout is reasonable.
