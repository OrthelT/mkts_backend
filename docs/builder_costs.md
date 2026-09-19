# Builder costs

Manufacturing estimates live in the shared `buildcost` database. Its
`build_watchlist` is independent of the market watchlists. The collector reads
that list, SDE manufacturing metadata, and primary-market Jita prices, then
fetches estimates from EverRef.

## Refresh estimates

With buildcost, SDE, and primary-market database credentials configured:

```bash
uv run mkts-backend sync --market=primary
uv run mkts-backend update-builder-costs
```

This updates `builder_costs` in **buildcost.db**, not every market database.
`--market` does not narrow the manufacturing dataset. The command has no per-item
filter; it processes eligible items from `build_watchlist`.

## Choose items to track

```bash
# Add buildable EVE type IDs
uv run mkts-backend build-watchlist add --type-id=587,24690

# Add names using the multiline editor (Esc, then Enter submits)
uv run mkts-backend build-watchlist add --paste

# Import IDs from a CSV with a type_id or type_ids column
uv run mkts-backend build-watchlist add --file="build-items.csv"

# Remove an item from the manufacturing watchlist
uv run mkts-backend build-watchlist remove --type-id=587

# Add missing buildable items from the primary market's watchlist
uv run mkts-backend build-watchlist mirror

# Download existing shared builder data without reconciling watchlists
uv run mkts-backend build-watchlist sync
```

`mirror` adds missing items; it does not replace the list or remove custom items.
`add` skips items without a manufacturing blueprint unless `--force` is supplied.
Forcing an item onto the list does not guarantee it is eligible for an estimate.
Successful changes normally push to Turso; report any push warning to the maintainer.
`--no-sync` is a legacy flag: repository helpers still push writes, so it does
not provide a local-only editing mode.

Adding items through `add-watchlist` also attempts to mirror buildable items into
this list. Removing a builder item does not remove it from a market watchlist.

## Import manufacturing structures

```bash
uv run mkts-backend add-structure --dry-run
uv run mkts-backend add-structure
```

The import reads the Google Sheet configured in `[buildcost]`, shows new/changed
rows, and asks before saving. `--sheet-url` and `--worksheet` override the source;
`--file="structures.csv"` uses a local CSV. `--yes` bypasses confirmation.
Rows are upserted into shared `structures` data and pushed to Turso. Market
selection does not change the destination.

## Refresh behavior

The collector upserts successful estimates, including per-unit cost, time, ME,
runs, and fetch timestamp. It preserves previous estimates for fetch failures;
a partial fetch produces a failing CLI exit status so scheduled jobs expose the
problem. Rows no longer on the build watchlist are pruned during a refresh that
writes results. An empty list aborts; a run with no eligible items makes no changes.

The daily workflow runs at 06:45 UTC. See [GitHub Actions](GITHUB_ACTIONS_SETUP.md)
for setup. The implementation is in `src/mkts_backend/builder_costs/` and
`src/mkts_backend/esi/async_everref.py`.
