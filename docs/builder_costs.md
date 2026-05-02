# Builder Costs Feature

## Overview

The builder costs feature fetches manufacturing cost estimates from Everef for manufacturable watchlist items and stores the results in the backend databases. It is exposed through the `update-builder-costs` command and is intended to support both manual runs and scheduled collection.

The dataset is market-independent, so the command writes the same results to every configured market database.

## What It Collects

For each eligible watchlist item, the pipeline stores:

- `type_id`
- `total_cost_per_unit`
- `time_per_unit`
- `me`
- `runs`
- `fetched_at`

These values are persisted in the `builder_costs` table.

## Command Usage

```bash
# Fetch builder costs for all configured markets
uv run mkts-backend update-builder-costs

# Alias forms also work
uv run mkts-backend builder-costs
uv run mkts-backend ubc
```

The command does not take item filters. It inspects the configured market databases, reads their watchlists, and fetches cost data for the eligible items automatically.

## Data Sources

The pipeline combines three inputs:

- Watchlist rows from each configured market database
- Jita prices from the first available `jita_prices` table
- SDE metadata from `sdelite.db` to determine which items are manufacturable

The Everef API is then queried asynchronously with controlled concurrency and rate limiting.

## Item Selection Rules

Not every watchlist item is fetched. The async fetch layer filters items using:

- Meta group checks for manufacturable items
- Category allow-lists
- Exclusion lists for known unsupported groups and item names
- Jita price-aware parameter selection for some T2 modules

This keeps collection focused on items that can realistically be modeled as builder costs.

## Database Schema

```sql
CREATE TABLE builder_costs (
    type_id INTEGER PRIMARY KEY,
    total_cost_per_unit FLOAT NOT NULL,
    time_per_unit FLOAT NOT NULL,
    me INTEGER NOT NULL,
    runs INTEGER NOT NULL,
    fetched_at DATETIME NOT NULL
);
```

The table is treated as a wipe-and-replace dataset, so a fresh fetch replaces the previous contents.

## Collection Flow

1. Initialize and verify all configured market databases.
2. Sync the databases locally so the current watchlist and Jita price data are available.
3. Merge watchlist items across markets.
4. Read the first available `jita_prices` table.
5. Load SDE metadata from `sdelite.db`.
6. Fetch builder costs asynchronously from Everef.
7. Create `builder_costs` if needed.
8. Replace the table contents in each market database.
9. Write an update log entry for each successful market write.

## Implementation Notes

- CLI routing is registered in `src/mkts_backend/cli_tools/command_registry.py`.
- Argument parsing recognizes `update-builder-costs` as a help-aware subcommand.
- The database model lives in `src/mkts_backend/db/models.py` as `BuilderCosts`.
- Upsert behavior is handled through the generic database layer in `src/mkts_backend/db/db_handlers.py`.
- Async Everef fetch logic lives in `src/mkts_backend/esi/async_everref.py`.
- The main orchestration entry point is `process_builder_costs()` in `src/mkts_backend/cli.py`.

## Scheduled Run

The new GitHub Actions workflow `builder-costs-collection.yml` runs this command on a 6-hour schedule and also supports manual dispatch. It restores cached SQLite databases, runs the collection job, then saves the updated databases and logs.

## Related Docs

- [docs/cli-tools.md](cli-tools.md): Full CLI reference.
- [docs/doctrine_cli_tools.md](doctrine_cli_tools.md): Broader doctrine tooling notes.
