# Friendly Names Feature

## Overview

The `friendly_name` field in the `doctrine_fits` table provides a short, human-readable display name for a doctrine group. The frontend uses this field to display doctrine names in alphabetical order with consistent labeling.

**Example:**
- `doctrine_name` (internal): `"WC-HFI-2025"`
- `friendly_name` (display): `"Hurricane Fleet Issue"`

## Database Schema

The `doctrine_fits` table has a `friendly_name` column (nullable TEXT):

```sql
ALTER TABLE doctrine_fits ADD COLUMN friendly_name TEXT DEFAULT NULL;
```

The column is added automatically (via `ensure_friendly_name_column()`) if it does not already exist when using the `update-friendly-name` or `populate-friendly-names` commands.

## CLI Commands

Both commands are subcommands of `fit-update`. They update local and remote databases automatically.

### update-friendly-name

Set a friendly display name for all fits in a doctrine by doctrine ID.

```bash
# Primary market (default)
uv run mkts-backend fit-update update-friendly-name --doctrine-id=21 --name="Hurricane"

# Deployment market
uv run mkts-backend fit-update update-friendly-name --doctrine-id=21 --name="Hurricane" --north
```

**Options:**
- `--doctrine-id=<id>` (required): Doctrine ID to update
- `--name=<name>` (required): The friendly display name to set
- `--north` / `--market=deployment`: Target deployment database (default: primary)

**Behavior:**
- Updates all rows in `doctrine_fits` where `doctrine_id = <id>`
- Pushes changes to remote Turso database automatically
- Displays confirmation for both local and remote updates

### populate-friendly-names

Bulk populate `friendly_name` values from a JSON file.

```bash
# Primary market (default)
uv run mkts-backend fit-update populate-friendly-names

# Deployment market
uv run mkts-backend fit-update populate-friendly-names --north
```

**JSON file format** (`doctrine_names.json` in working directory):

```json
{
  "21": "Hurricane",
  "34": "Muninn",
  "42": "Huginn"
}
```

Keys are doctrine IDs (as strings or integers); values are the friendly names.

**Behavior:**
- Reads `doctrine_names.json` from the current working directory
- Updates local database first, then syncs to remote
- Reports the number of rows updated

## Implementation

- **ORM model:** `DoctrineFitItems.friendly_name` in `src/mkts_backend/db/models.py`
- **Command functions:** `update_friendly_name_command()` and `populate_friendly_names_command()` in `src/mkts_backend/cli_tools/fit_update.py`
- **Backend utilities:** `ensure_friendly_name_column()`, `update_doctrine_friendly_name()`, `populate_friendly_names_from_json()`, `sync_friendly_names_to_remote()` in `src/mkts_backend/utils/doctrine_update.py`

## Frontend Integration

The frontend reads `friendly_name` from the `doctrine_fits` table (synced from Turso) to display doctrine labels. When `friendly_name` is NULL, the frontend falls back to `doctrine_name`.
