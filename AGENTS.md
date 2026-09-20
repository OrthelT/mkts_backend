# Agent guide: EVE Market Tools

This file is for coding agents and maintainers. User instructions belong in
[README.md](README.md) and [docs/cli-tools.md](docs/cli-tools.md). Keep the README
focused on a non-programmer checking stock and managing fits; put implementation
notes here. Historical plans and duplicate feature notes are archived locally
under ignored `devfiles/`; they are not current specifications or clone dependencies.

## Development commands

```bash
uv sync
uv run mkts-backend --help
uv run fitcheck --help
uv run pytest -q
```

A bare `mkts-backend` prints help. Collection requires `update-markets` (alias
`update`), defaults to all configured markets, and writes/pushes shared data:

```bash
uv run mkts-backend update-markets --market=primary --history
```

Do not run collection or management writes just to validate documentation.
Prefer mocked tests and help/routing probes. If uv cannot access its cache,
use `.venv/bin/python -m pytest` from the existing environment. Inspect tests
for live database/API dependencies before running them with production credentials.
The CI test job runs the full suite with `COLUMNS=80` and `TERM=dumb`.

## Architecture and source of truth

All paths below are under `src/mkts_backend/` unless stated otherwise.

| Area | Source |
|---|---|
| Entry points | `pyproject.toml`: `mkts-backend` / `mkts` → `cli.main`; `fitcheck` → `cli_tools.fit_check.main` |
| Dispatch and typed argument parsing | `cli_tools/command_registry.py`, `args_parser.py`, `arg_utils.py` |
| Market selection | `cli_tools/market_args.py`, `config/market_context.py` |
| CLI help | `cli_tools/cli_help.py`, plus fitcheck/equiv help in their modules |
| Market pipeline | `cli.py`, `processing/data_processing.py`, `db/db_handlers.py` |
| Fit reports | `cli_tools/fit_check.py`, `fit_check_needed.py`, `fit_check_module.py` |
| Fit writes and assignment | `cli_tools/fit_update.py`, `utils/parse_fits.py`, `utils/doctrine_update.py` |
| Database routing/replicas | `config/settings_service.py`, `config/db_config.py` |
| Models and queries | `db/models.py`, `db/db_queries.py` |
| EVE requests and auth | `esi/esi_requests.py`, `esi/esi_auth.py`, `esi/async_*` |
| Builder costs | `builder_costs/`, `esi/async_everref.py`, `cli_tools/build_watchlist_cli.py` |
| Structure import | `cli_tools/add_structure.py`, `utils/build_cost_utils.py` |
| Google Sheets | `config/gsheets_config.py` |

The frontend is a separate repository, `OrthelT/wcmkts_new`. Do not infer its
current implementation from old backend feature notes.

## Configuration

Read all settings through `SettingsService`; do not parse TOML directly.
`src/mkts_backend/config/settings.toml` is authoritative. Avoid duplicating
market IDs, database filenames, and routing maps in code or documentation.

```python
from mkts_backend.config.settings_service import (
    SettingsService, get_all_market_contexts, get_all_characters, clear_cache,
)

s = SettingsService()
s.environment
s.log_level
s.esi_user_agent
s.jita_cache_ttl
s.wipe_replace_tables
s.database_routing()
s.settings_dict  # keys without a typed accessor
get_all_market_contexts()
get_all_characters()  # also merges the legacy [chareacters] spelling
```

The service caches settings at module level and resolves the TOML relative to
its module, not CWD. Call `clear_cache()` after settings/env changes in tests.
`MKTS_ENVIRONMENT` overrides `[app].environment` at load time. Some CLI market
maps are constructed at import time; clearing the settings cache alone does
not recreate those module constants.

`[markets.<alias>]` and `[shared.<name>]` jointly define database routing. Every
block needs unique `database_alias`, `database_file`, and appropriate credential
variable names. Malformed/duplicate routing fails with section-named errors.
Shared aliases are SDE, fittings, buildcost, and testing. Inspect routes with:

```bash
uv run mkts-backend --list-markets
uv run mkts-backend --list-db-paths
uv run mkts-backend --db-path=primary
```

Current market selectors are `primary` (4-HWWF), `deployment` (X47L-Q), and
`market3` (BKG-Q2). Production database aliases are currently `wcmktnewkeep`,
`wcmktnorth`, and `wcmktbkg`; do not restore the old `*test` routes from archived docs.
`MarketContext` in development routes only the configured default market to
`[shared.testing]`; other markets/shared DBs are not isolated automatically.
The environment flag is not a general no-production-writes switch.

Credential variable names come from settings; their environment values choose
the actual remote. `.env.example` lists the shipped names without values.
Collection validation requires EVE credentials and the selected markets plus
non-optional shared databases. `REFRESH_TOKEN` is required only when the market
token cache holds no refresh token, matching what `esi_auth.get_token` accepts.
`--validate-env` checks presence across all markets; it does not test connectivity. Do not describe normal collection as
credential-free local-only operation.

Google credential file variables are `GOOGLE_APPLICATION_CREDENTIALS` and
`GOOGLE_SERVICE_ACCOUNT_FILE`; literal JSON uses `GOOGLE_SHEET_KEY`.
The Actions workflow uses its own `GOOGLE_SA_JSON` secret and creates a file.
Auth reads callback and market data token-file settings through `SettingsService`.
`esi-auth` opens a Rich management menu; `--market-data` and `--char=<key>`
authorize directly, while `--status` is read-only and headless-safe. Setup saves
credentials and target refresh tokens to the project `.env`; caches are relative
to CWD. Its parser uses `allow_abbrev=False` so `--market` cannot abbreviate to
`--market-data`, and `parse_known_args` so global flags may follow the command.
Changing `CLIENT_ID` deletes the token caches, which the new application cannot
refresh. Collection never launches interactive authorization.

## CLI contracts and current limitations

Keep docs aligned with handler bodies, not just help strings or docstrings.
Use the canonical entry point, `--option=value`, and put flags after the command
in user examples. The registry is shared, but the two entry points do not have
identical default-market/help handling.

- `mkts-backend sync` pulls all markets and shared SDE/fittings/buildcost, excluding
  testing unless `--include-testing`. A market selector narrows only the market
  loop; add `--markets-only` to exclude shared DBs. `--no-buildcost` skips buildcost.
- `fitcheck` / `list-fits` / `needed` use a single market; `module` expands `all`.
  Reports read stored market data. `fitcheck --refresh` refreshes Jita comparison
  prices; `needed --refresh` affects assets only. Neither collects market orders.
- `fit-update` requires a subcommand. `update-fit` is a separate file/metadata
  workflow, not an alias. Its `--fit-id` requires `--interactive` or `--meta-file`.
- `fit-update update` finds every market containing the fit and updates all of
  them, irrespective of a narrower requested market. `add --interactive` prompts
  for metadata and market choice. Dry-run support is per-handler, not universal.
- The fit registry accepts comma-separated `--fit-id`, not documented legacy
  `--fit-ids`. `doctrine-add-fit` passes `doctrine_id=None` to its interactive
  handler even if supplied on the command line; do not promise unattended use.
- Assignment uses `_flag_to_aliases`: `primary` includes market3, `deployment`
  is separate, and `all` includes every configured market. Saved target updates
  share that mapping. Assignment/unassignment validators still restrict choices
  to primary/deployment/all; an arbitrary new configured market is not fully
  supported by that assignment model. `assign-market` replaces membership.
- `fit-update remove --market=primary` currently expands to all markets. Do not
  advertise it as a primary-only removal. `doctrine-remove-fit` only unlinks fits.
- Friendly-name commands write all configured markets, regardless of selection.
  `update-lead-ship` uses the selected market expansion instead.
- `equiv` defaults to all markets and reads its own literal `--market` value.
  Its handler does not expand `--market=all` or honor shorthand market flags;
  use an explicit concrete selector or omit the selector for all markets.
- `--remote` is an engine-selection compatibility flag, not direct cloud access.
  `--local`/`--local-only` do not reliably suppress management pushes. Never use
  these as a production isolation boundary. Likewise, `build-watchlist --no-sync`
  skips the handler's extra push but repository mutation helpers already push.
- Registered handlers should return bool, leaving exit codes to the entry point.
  Some report wrappers currently discard the inner result and return success;
  console errors therefore matter even when the shell status is zero.

## Data and writer ownership

The market run initializes shared replicas, pulls market replicas before its
first writes, refreshes Jita prices, then fetches orders, optionally history,
calculates statistics and doctrine availability, exports configured Sheets, and
pushes market writes. Jita fetching is independent of `--history`.

Market databases contain `marketorders`, `market_history`, `marketstats`,
`doctrines`, `watchlist`, `jita_prices`, `updatelog`, and reference tables such as
`doctrine_fits`, `doctrine_map`, `ship_targets`, `lead_ships`, `module_equivalents`.
`DoctrineFitItems` maps to `doctrine_fits`; `friendly_name` is nullable.
Shared fittings holds the fit/doctrine/item records; SDE supplies item metadata.
Character assets are cached in local-only `cli_cache.db`, not pushed to Turso.

Builder costs now have a dedicated shared database and independent
`build_watchlist`. The runner reads primary Jita prices and SDE, upserts successful
EverRef results, prunes orphan costs after a write, and stamps `updatelog`.
Partial fetches return a failing CLI status while retaining successful writes.
Do not route builder estimates through the market wipe-and-replace writer.
`build-watchlist mirror` adds missing primary-market items; `sync` only pulls.
`add-watchlist` also attempts to mirror buildable additions. Structure imports
upsert shared `structures`, not market databases.

## Replica verification and recovery

`DatabaseConfig.engine` and `remote_engine` share the same engine. Before engine
or raw connection access, `assert_remote_compatible()` rejects a known mismatch
between the remote in `-info` and the configured URL. Comparison uses host and
path, ignoring scheme and trailing slash; unknown metadata returns `None`, not
proof of compatibility.

`verify_db_exists()` handles missing DB/metadata pairs by rebuilding and pulling.
When both exist it checks remote compatibility and calls `heal_metadata()`.
Healing distinguishes genuine pyturso metadata from old libsql/corrupt metadata;
it removes just `-info` and re-pulls against the existing DB/WAL/change queue.
It is not a full database integrity check.

`nuke_db()` removes the DB plus any `-shm`, `-wal`, `-info`, `-changes`, and
`-wal-revert` files. Preserve needed pending local work before an explicitly
chosen rebuild. Never leave a stale change queue beside a fresh DB, and do not
use a plain `sqlite+turso` connection for a sync-managed replica.

### Turso sync model (pyturso)

pyturso is **local-first and bidirectional**, which inverts the old libsql rule:

- Every engine — `db.engine` and its alias `db.remote_engine` — writes to the
  **local** replica. There is no direct-to-cloud engine any more.
- A write lands in the local CDC queue on `commit()` and reaches Turso only when
  `db.push()` runs.
- `db.sync()` / `db.pull()` bring remote changes down.
- Sync-managed databases must be opened through the **sync dialect**
  (`sqlite+turso_sync`), which `DatabaseConfig.engine` does automatically. A plain
  `sqlite+turso` connection auto-checkpoints the WAL at 1000 frames, destroying the
  baseline `pull()` needs and panicking turso core (`wal.rs` `frame_watermark`).

**Consequence for writers:** any code path that writes must end with a `push()`.
Every CLI-reachable writer and operator script does so as of this branch. Many
call sites still name `remote_engine` (and still take a `remote=` parameter);
both are inert aliases of the local engine, so a new writer must add its own
`push()` rather than assume `remote_engine` reaches Turso.

**Convergence when a push is skipped or fails:** a stranded write sits in the
local CDC queue until some later command pushes that alias. Market databases
converge on the hourly market-data workflow and `buildcost` on the daily
builder-costs workflow, but **`fittings` has no scheduled push** — a stranded
fittings write converges only on the next manual fit command that pushes that
alias.

**Never restore a replica older than its own last push.** `push()` asks the
remote for the last change id recorded against this replica's `client_unique_id`
and sends only local CDC rows above it. A replica rolled back to an earlier
snapshot — a reused GitHub Actions cache key, a `cp` of an old bundle — has every
pending change at or below that watermark, so `push()` transfers nothing, logs a
sub-second sync time, and reports success. **Pull before writing**: a `pull()`
re-bases the replica and restores correct push behaviour (writes already made
from the rolled-back replica are lost for good). `run_market_update()` pulls each
market replica and `init_databases()` pulls the shared ones, both before the
run's first write; CI cache keys are per-run so the rollback cannot happen in the
first place.

**Known pyturso constraints:**
- `delete`+`insert` on a table with a secondary `UNIQUE` constraint churns primary
  keys and makes the next `push()` fail with `UNIQUE constraint failed`. Upsert in
  place instead.
- CDC replays DDL from `sqlite_schema` text but row inserts from the live local
  schema, and `ALTER … RENAME` emits no CDC at all. Migrate by
  drop → create-with-final-name → reinsert; never create-copy-drop-rename.
- **A push that fails a constraint is not rolled back.** The local replica runs
  with `PRAGMA foreign_keys` OFF, Turso cloud runs with it ON. When a replayed
  INSERT fails there (e.g. `FOREIGN KEY constraint failed`), SQLite aborts only
  that statement; the rest of the batch, including the client watermark in
  `turso_sync_last_change_id`, still commits. `push()` raises, but the next
  push sends nothing and "succeeds", so the rejected rows never reach the
  remote. Write parent rows before children — `ensure_fittings_types()`
  (`utils/parse_fits.py`) does this for `fittings_type` before any fit row is
  written. Repair a rejected row by delete + re-insert locally (fresh CDC
  INSERT), then push.

## ESI Request Caching (Conditional Requests)

Market order fetching uses a two-layer caching system to avoid redundant ESI requests and unnecessary database writes. Cache state is stored in the `esi_request_cache` table of the market database. Under pyturso both `load_orders_cache()` and `save_orders_cache()` use the **local** engine; the rows reach Turso with the pipeline's end-of-run `push()`.

### Cache Layers

**Layer 1 — Expires header:** If the cached `Expires` timestamp hasn't passed, the fetch is skipped entirely. ESI typically sets Expires ~5 minutes ahead for structure market endpoints.

**Layer 2 — Per-page ETags:** If the Expires window has passed, `fetch_market_orders` sends `If-None-Match` headers with cached ETags for each page. ESI returns `304 Not Modified` for unchanged pages. If all pages return 304, the database write is skipped.

### Cache Storage (Sentinel Scheme)

Page-level cache data is stored in the existing `esi_request_cache` table by repurposing the `type_id` column with sentinel values. The table's composite primary key is `(type_id, region_id)`.

| `type_id` | `region_id` | Purpose | Data stored |
|-----------|-------------|---------|-------------|
| `0` | `structure_id` | Expires timestamp | `last_modified` = HTTP Expires header value |
| `-1` | `structure_id` | Page 1 ETag | `etag` = ETag header from page 1 |
| `-2` | `structure_id` | Page 2 ETag | `etag` = ETag header from page 2 |
| `-N` | `structure_id` | Page N ETag | `etag` = ETag header from page N |
| `> 0` | `region_id` | Normal per-item cache | (unrelated — used by history fetching) |

The `region_id` column doubles as `structure_id` for sentinel rows. Queries filter on `type_id <= 0` to isolate page cache entries from normal per-item cache rows.

### Data Flow

```
process_market_orders (cli.py)
  │
  ├─ load_orders_cache(structure_id)     ← reads sentinels from local replica
  │    returns {"expires": "...", "pages": {1: "etag1", 2: "etag2", ...}}
  │
  ├─ Layer 1: check expires → skip fetch if within cache window
  │
  ├─ fetch_market_orders(esi, page_etags=...)   ← sends If-None-Match per page
  │    │
  │    ├─ max_pages seeded from max(page_etags.keys()) so 304s iterate all known pages
  │    ├─ Per page: 304 → skip; 200 → collect data + new etag
  │    ├─ Mixed 200/304 → discard partial data, re-fetch all pages clean (no etags)
  │    └─ returns {"status": 200/304, "data": [...], "page_etags": {...}, "expires": "..."}
  │
  ├─ status 304 → skip DB write entirely
  │
  ├─ status 200 → upsert orders into marketorders table
  │
  └─ save_orders_cache(structure_id, expires, page_etags)  ← writes sentinels to local replica
```

### Key Implementation Details

- **Headers:** `ESIConfig.headers` provides base headers (auth, user-agent, etc.) without `If-None-Match`. The `fetch_market_orders` loop manages `If-None-Match` per-page, setting it from `page_etags` or removing it for fresh requests.
- **User-Agent:** Loaded from `settings.toml` (`[esi] user_agent`), never hard-coded.
- **Mixed responses:** If some pages return 304 and others 200, page boundaries may have shifted (ESI rebalances pages). The function discards partial results and re-fetches all pages without etags to get a consistent dataset. A `_clean_retry` flag prevents infinite recursion.
- **Cache read/write engines:** Both `load_orders_cache` and `save_orders_cache` use `db.engine` (the local pyturso replica). Cache rows travel to Turso with the pipeline's `push()`. A failed push can leave both data and cache changes pending locally; do not assume a subsequent local run will re-fetch them.

### Related Files

- `src/mkts_backend/cli.py` — `process_market_orders()`: orchestrates cache check → fetch → save
- `src/mkts_backend/esi/esi_requests.py` — `fetch_market_orders()`: HTTP requests with conditional headers
- `src/mkts_backend/db/db_handlers.py` — `load_orders_cache()`, `save_orders_cache()`: sentinel read/write
- `src/mkts_backend/config/esi_config.py` — `ESIConfig.headers`: base request headers
- `src/mkts_backend/config/settings.toml` — `[esi] user_agent`: configurable User-Agent string

## Jita Price Caching (1-hour TTL)

Both Jita price paths — the pipeline and `fitcheck` — reuse the `jita_prices`
table while it is under an hour old, instead of calling Fuzzwork again.

Freshness comes from the `updatelog` row for `jita_prices`, written by
`log_update()` after a successful Jita price write. `get_update_age()`
(`db/db_queries.py`) returns that age, or `None` when the row or the table is
missing — which callers treat as "no cache, fetch".

**Pipeline** (`cli.py` `process_jita_prices()`): checks each market's own
`updatelog` row and writes only the markets that are stale, skipping the fetch
entirely when every requested market is fresh. So a manual re-run within the
hour is free, while a market whose write failed on the previous run — or one
added or wiped since — is refilled on the next run rather than waiting out the
TTL behind a fresh sibling. The `refresh=True` parameter bypasses the check and
exists for tests and internal callers; there is no CLI flag for it.

**fitcheck** (`cli_tools/fit_check.py` `_get_jita_prices()`): reads the table
while fresh, then live-fetches only the type_ids the table lacks — it covers
watchlist items only, so a fit can contain items it misses. `--refresh` forces a
full live fetch.

**fitcheck must never write `jita_prices`.** The table is in
`wipe_replace_tables` (`settings.toml`), so any partial write would
`DELETE FROM jita_prices` and reinsert only that fit's handful of items,
destroying the rest — see the wipe branch in `db/db_handlers.py`. It would also
strand an unpushed write in a production replica. The read path is read-only by
design.

**Configuring the TTL:** `[jita] cache_ttl_hours` in `settings.toml` (currently
`1`), read through `SettingsService().jita_cache_ttl`. It is required — a missing
key raises a `KeyError` naming the section rather than falling back to a silent
default. Read at access time, so a change takes effect on the next run with no
code edit. Fractional hours are allowed (`0.5` = 30 minutes).

The market workflow currently runs hourly. Cache reuse still depends on each
market's successful write timestamp and the configured TTL, not the schedule alone.

### Related Files

- `src/mkts_backend/db/db_queries.py` — `get_update_age()`, `read_jita_prices()`
- `src/mkts_backend/cli.py` — `process_jita_prices()`: pipeline TTL guard
- `src/mkts_backend/cli_tools/fit_check.py` — `_get_jita_prices()`: read-through cache
- `src/mkts_backend/utils/jita.py` — `fetch_jita_price_data()`: the shared fetcher
- `src/mkts_backend/config/settings.toml` — `[jita] cache_ttl_hours`: the TTL

## Validation and documentation maintenance

The focused CLI documentation review can be checked with:

```bash
uv run pytest -q tests/test_arg_utils.py tests/test_command_registry.py tests/test_cli_routing.py tests/test_cli_market_flag.py tests/test_sync_command.py tests/test_build_watchlist_cli.py tests/test_update_target_prompt.py tests/test_jita_cache.py
```

For writer changes, also inspect/run relevant `test_management_push.py`,
`test_fit_update_assign.py`, `test_pull_before_write.py`, and repository tests.
Use temporary SQLite fixtures or `FakeDatabaseConfig` to exercise transactions
and assert push/pull behavior without touching production.

The market Actions workflow currently runs hourly at minute 20 UTC; builder
costs run daily at 06:45 UTC. Read `.github/workflows/` for authoritative schedules,
secret mappings, and per-run replica cache ownership. Never reuse a cache key
that restores a replica older than its last push.

Keep user workflows in the CLI guide, installation in `docs/setup.md`, builder
usage in `docs/builder_costs.md`, and CI setup in `docs/GITHUB_ACTIONS_SETUP.md`.
When removing a duplicate doc, preserve any unique current instructions and
check links. Historical material may be moved to ignored `devfiles/`, but no
tracked user instructions should depend on those local files.
