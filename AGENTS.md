# LLM Agent Guide: Eve Online Market Data System

This guide provides comprehensive documentation for LLM agents working with this Eve Online Market Data Collection and Analysis System. It covers both assisting users in implementing their own system and working with the existing codebase.

## Quick Start for Development

**Run the main application:**
```bash
uv run mkts-backend update-markets   # all configured markets
```
A bare `uv run mkts-backend` prints help; the pipeline needs the `update-markets`
subcommand (aliases: `update`).

**Include historical data:**
```bash
uv run mkts-backend update-markets --history
```

**Specify a market (update-markets defaults to every market):**
```bash
uv run mkts-backend update-markets --market=deployment
uv run mkts-backend update-markets --primary      # shorthand
```

**Check database tables:**
```bash
uv run mkts-backend --check_tables
uv run mkts-backend --check_tables --deployment  # Check deployment market tables
```

**Sync databases:**
```bash
uv run mkts-backend sync              # Pull every routed replica: all markets +
                                       # shared sde/fittings/buildcost (excludes
                                       # the dev/test DB, [shared.testing])
uv run mkts-backend sync --deployment # Pull the deployment market only
uv run mkts-backend sync --no-buildcost    # Skip the optional buildcost replica
uv run mkts-backend sync --markets-only    # Markets only, skip shared databases
uv run mkts-backend sync --include-testing # Also pull [shared.testing]
# NOTE: `sync` is a PULL (Turso → local). Local writes reach Turso only via push();
# see "Turso sync model" below.
# NOTE: --both is a legacy market-alias synonym for --all. It now spans all three
# markets, not just primary+deployment. Use --all.
```

**Look up routed database paths** (global flags, not specific to `sync`):
```bash
uv run mkts-backend --list-db-paths     # Print every routed alias and file (alias<TAB>file)
uv run mkts-backend --db-path=primary   # Print the file path for one database (by alias or market name)
```

**Check market availability for a ship fit:**
```bash
uv run fitcheck --file=path/to/fit.txt --market=primary
uv run fitcheck --fit=42  # Check by fit ID
uv run fitcheck needed    # Show all items needed across fits
uv run fitcheck module --id=11269  # Show which fits use a module
```

**Look up character assets:**
```bash
uv run mkts-backend assets --id=11379        # By type ID (cached for 1 hour)
uv run mkts-backend assets --name='Damage Control'  # By name
uv run mkts-backend assets --id=11379 --refresh     # Bypass cache, re-fetch from ESI
```

**Dependencies are managed with uv:**
```bash
uv sync  # Install dependencies
uv add <package>  # Add new dependency
```

## System Overview

This is a comprehensive Eve Online market data collection and analysis system consisting of two repositories:

1. **mkts_backend** (this repo): Backend data collection, processing, and storage
   - Fetches market data from Eve Online ESI API for specific structures/regions
   - Processes and stores market orders, history, and calculated statistics in SQLite databases
   - Analyzes doctrine fits and calculates market availability for ship loadouts
   - Tracks regional/system market data with automated Google Sheets integration
   - Supports local and remote (Turso) database sync

2. **wcmkts_new** (frontend): Streamlit web application for data visualization
   - Repository: https://github.com/OrthelT/wcmkts_new
   - Displays market statistics and trends
   - Shows doctrine/fitting availability
   - Provides interactive data exploration

## Core Components and Architecture

### Main Data Flow (`cli.py`)
The primary orchestration file that coordinates all data collection and processing:
- `fetch_market_orders()` - Gets current market orders from ESI API with OAuth
- `fetch_history()` - Gets historical market data for watchlist items from primary region
- `calculate_market_stats()` - Computes statistics from orders and history
- `calculate_doctrine_stats()` - Analyzes ship fitting availability
- Regional order processing and system-specific market analysis

### Database Layer (`config/db_config.py`, `db/db_handlers.py`)
Manages all database operations:
- **DatabaseConfig class** (in `config/db_config.py`): Manages the local pyturso replica and its Turso remote
  - Supports MarketContext-based initialization (preferred) or alias-based init
  - `engine` / `remote_engine`: both return the **same** `sqlite+turso_sync` engine.
    `remote_engine` is a backwards-compatible alias kept during the migration; it
    no longer opens a direct HTTP connection to Turso.
  - `sync()` / `pull()`: pull remote changes into the local replica
  - `push()`: send local writes to Turso. **Required** — a `commit()` alone leaves
    the write in the local CDC queue.
  - `verify_db_exists()`: Ensures database and metadata are in a consistent state
    - Handles 4 cases: neither exists, both exist, db without metadata, metadata without db
    - When both exist, calls `assert_remote_compatible()` then `heal_metadata()`
      instead of assuming the pair is valid; otherwise nukes the inconsistent
      state and syncs from remote
  - `heal_metadata()`: confirms the replica's `-info` sidecar is genuine pyturso
    metadata (not libsql-era or corrupt). If not, deletes just the `-info`
    sidecar and re-pulls to rebuild it against the existing `.db`/`-wal`/
    `-changes`. Returns `True` once the replica has pyturso metadata, `False` if
    the repair pull fails.
  - `remote_matches_metadata()`: compares the Turso remote recorded in `-info` at
    bootstrap time against the currently configured remote (host + path only,
    ignoring scheme and trailing slash). Returns `True`/`False`, or `None` if
    either side is unknown. `assert_remote_compatible()` raises when it returns
    `False` — the guard against reading or pushing a replica bootstrapped
    against a different environment (e.g. a test replica opened under a
    production config after cutover).
  - `nuke_db()`: removes the database and each of its pyturso sidecars found on
    disk — up to five (`-shm`, `-wal`, `-info`, `-changes`, `-wal-revert`). Not
    every replica has all five: `-shm` is the WAL shared-memory index and is
    usually absent once a connection closes cleanly. They must be deleted
    together — a stale change queue beside a freshly pulled database replays
    local state that is no longer there.
- **db_handlers.py**: CRUD operations on market data tables
- ORM-based data insertion with chunking for large datasets

### Data Models (`models.py`)
SQLAlchemy ORM model definitions (at `src/mkts_backend/db/models.py`):
- **Core Models:** `MarketOrders`, `MarketHistory`, `MarketStats`, `Doctrines`, `Watchlist`
- **Organizational Models:** `ShipTargets`, `DoctrineMap`, `DoctrineFitItems`, `LeadShips`
- **Utility Models:** `UpdateLog`, `ESIRequestCache`
- **Module Equivalents:** `ModuleEquivalents` - maps interchangeable faction modules by `equiv_group_id`
- **Asset Cache:** Stored in local-only `cli_cache.db` (not synced to Turso); schema managed by `asset_cache._ensure_table()`
- `DoctrineFitItems` maps to `doctrine_fits` table; includes `friendly_name` field (nullable) added in Feb 2026
- Tables stored in market-specific databases (e.g., `wcmktnewkeeptest.db`, `wcmktnorth2test.db`)

### OAuth Authentication (`ESI_OAUTH_FLOW.py` / `esi_auth.py`)
Handles Eve Online SSO authentication:
- Eve Online SSO authentication for ESI API access
- Token refresh and storage in `token.json`
- Manages OAuth flow for initial authorization

### Regional Market Processing (`esi/esi_requests.py`)
Regional market data fetching:
- `fetch_region_orders()` - Fetches all market orders for a region by order type

### Google Sheets Integration (`google_sheets_utils.py` / `gsheets_config.py`)
Automated spreadsheet updates:
- Automated Google Sheets updates with market data
- Service account authentication
- Configurable append/replace data modes

### Data Processing (`data_processing.py`)
Statistics and analysis calculations:
- Market statistics calculation with 5th percentile pricing
- Doctrine availability analysis
- Historical data integration (30-day averages)

## Key Configuration Values

Configuration is now managed through `settings.toml` with market-specific configs:

Values below are the current contents of `settings.toml` in this worktree.
`settings.toml` is authoritative — read it rather than trusting this table.

### Primary Market (`markets.primary`)
- **Name:** 4-HWWF - WinterCo. Central Station
- **Region ID:** `10000003` (Vale of the Silent)
- **System ID:** `30000240`
- **Structure ID:** `1053970513596`
- **Database:** alias `wcmktnewkeeptest`, file `wcmktnewkeeptest.db`

### Deployment Market (`markets.deployment`)
- **Name:** X47L-Q - Rogue Threshold
- **Region ID:** `10000023` (Pure Blind)
- **System ID:** `30001967`
- **Structure ID:** `1041669946862`
- **Database:** alias `wcmktnorthtest`, file `wcmktnorth2test.db`

### Third Market (`markets.market3`)
- **Name:** BKG-Q2 - Insidious Prime
- **Region ID:** `10000055` (Branch)
- **System ID:** `30004333`
- **Structure ID:** `1032721770598`
- **Database:** alias `wcmktbkgtest`, file `wcmktbkgtest.db`

### Configuration Files
- **Market Settings:** `src/mkts_backend/config/settings.toml`
- **ESI Config:** Auto-generated from MarketContext based on settings.toml
- **Watchlist:** Database table with ~850 common items and WinterCo doctrine ships/fittings

### Settings Access (`config/settings_service.py`)

All `settings.toml` reads go through a single centralized service. **Do not parse the TOML
directly** — import `SettingsService` and use a typed property or `settings_dict` for raw access.

```python
from mkts_backend.config.settings_service import (
    SettingsService,
    get_all_market_contexts,
    get_all_characters,
    clear_cache,
)

s = SettingsService()
s.environment              # "production" or "development"
s.log_level                # "INFO" / "DEBUG" / ...
s.esi_user_agent           # User-Agent string for ESI requests
s.wipe_replace_tables      # ["marketstats", "doctrines", "jita_prices", "builder_costs"]
s.database_routing()       # {alias: {file, turso_url_env, turso_token_env, optional}}
                           #   for every [markets.*] and [shared.*] block
s.settings_dict            # Read-only view, for keys without a typed accessor

get_all_market_contexts()  # {"primary": …, "deployment": …, "market3": MarketContext}
get_all_characters()       # list[CharacterConfig], merges legacy [chareacters] typo section
```

Behavior:
- **Module-level cache.** First call parses the TOML; subsequent calls return the cached dict.
- **Test reload.** Call `settings_service.clear_cache()` after mutating env vars or settings.toml in tests.
- **Path resolution.** Uses `Path(__file__).parent / "settings.toml"` so it works from any CWD.
- **Env override.** `MKTS_ENVIRONMENT=development` overrides `[app][environment]` at load time.

### TOML Structure

| Section | Purpose |
|---|---|
| `[app]` | Environment + log level |
| `[esi]` | User-Agent, compatibility date |
| `[auth]` | OAuth callback + token storage |
| `[markets.<alias>]` | Per-market configuration (primary, deployment, market3) — the **single source** for all per-market DB config (alias, file, turso env vars, gsheets) |
| `[shared.<name>]` | Market-independent databases — `sde`, `fittings`, `buildcost`, `testing`. Each block has the same shape as a market DB block (`database_alias`, `database_file`, `turso_url_env`, `turso_token_env`, optional `optional = true`), so `database_routing()` emits markets and shared DBs through one code path. `[shared.testing]` is the dev/test DB the default market routes to when `environment="development"`. |
| `[wipe_replace]` | `tables` — list of tables fully wiped/re-inserted on each upsert run (vs. incrementally upserted). Useful for resetting deployment history when switching regions. |
| `[google_sheets]` | Sheets integration toggle + legacy URLs |
| `[buildcost]` | `add_structure` CLI source sheet |
| `[characters.<key>]` | Character definitions for asset checks |
| `[corporations.<key>]` | Corporation definitions for asset checks |

## External Dependencies

- **EVE Static Data Export (SDE):** `sdelite.db` - game item/type information (synced from Turso), uses `sdetypes` table for type lookups
- **Custom dbtools:** Database utility functions in `utils/db_utils.py`
- **pyturso:** Remote database synchronization (optional in dev, required in production).
  Provides the `sqlite+turso`, `sqlite+turso_sync`, and `sqlite+aioturso` SQLAlchemy
  dialects. The `libsql` and `sqlalchemy-libsql` packages have been removed.
- **Google Sheets API:** For automated market data reporting (optional)
- **prompt_toolkit:** For multiline input prompts (paste mode in fit-update)

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
converge on the 4-hourly market-data workflow and `buildcost` on the daily
builder-costs workflow, but **`fittings` has no scheduled push** — a stranded
fittings write converges only on the next manual fit command that pushes that
alias.

**Known pyturso constraints:**
- `delete`+`insert` on a table with a secondary `UNIQUE` constraint churns primary
  keys and makes the next `push()` fail with `UNIQUE constraint failed`. Upsert in
  place instead.
- CDC replays DDL from `sqlite_schema` text but row inserts from the live local
  schema, and `ALTER … RENAME` emits no CDC at all. Migrate by
  drop → create-with-final-name → reinsert; never create-copy-drop-rename.

## Data Processing Flow

The complete data pipeline when running the application:

1. **Initialize**: Load market configuration from settings.toml. `update-markets` runs every configured market unless `--market=<alias>` narrows it
2. **Database Setup**: Verify database exists with `verify_db_exists()` (syncs from Turso if needed)
3. **Authenticate**: Authenticate with Eve SSO using required scopes
4. **Market Orders**: Fetch current market orders for configured structure
5. **Historical Data** (optional with `--history` flag):
   - Primary market history → `MarketHistory` table
   - Jita comparative pricing fetched for watchlist items (if configured)
6. **Statistics**: Calculate market statistics (price, volume, days remaining)
7. **Doctrine Analysis**: Analyze ship fitting availability based on market data
8. **Google Sheets** (if enabled): Update spreadsheets with market data (primary market only, non-dev)
9. **Storage**: Write all results to the local replica, then `db.push()` them to Turso (`cli.py`)

## Environment Variables Required

```env
# Eve Online ESI Credentials (Required)
CLIENT_ID=<eve_sso_client_id>
SECRET_KEY=<eve_sso_client_secret>
REFRESH_TOKEN=<your_refresh_token_here> # this is uded in automated workflows where a token.json file cannot be stored persistently. copy the refresh_token field from token.json.

# Google Sheets (Optional)
GOOGLE_SHEET_KEY={"type":"service_account"...}  # Entire JSON key file content
# OR
GGOOGLE_APPLICATION_CREDENTIALS=<filename.json>  # Path to service account key file

# Janice API key for Jita price fallback (optional)
JANICE_KEY=<janice_api_key>

# Turso — one URL/token pair per database. The var NAMES are set by
# turso_url_env / turso_token_env in settings.toml; the VALUES decide whether
# this checkout talks to production or test remotes.
TURSO_WCMKTNEWKEEP_URL=<primary market db url>
TURSO_WCMKTNEWKEEP_TOKEN=<primary market db token>
TURSO_WCMKTNORTH_URL=<deployment market db url>
TURSO_WCMKTNORTH_TOKEN=<deployment market db token>
TURSO_WCMKTBKG_URL=<market3 db url>
TURSO_WCMKTBKG_TOKEN=<market3 db token>

# Turso — shared, market-independent databases
TURSO_SDE_URL=<sde db url>
TURSO_SDE_TOKEN=<sde db token>
TURSO_FITTING_URL=<fitting db url>
TURSO_FITTING_TOKEN=<fitting db token>

# Turso — optional; a market run proceeds without these
TURSO_BUILDCOST_URL=<buildcost db url>
TURSO_BUILDCOST_TOKEN=<buildcost db token>
TURSO_WCMKTTEST_URL=<dev/test db url>
TURSO_WCMKTTEST_TOKEN=<dev/test db token>
```

Check what is actually loaded with `uv run mkts-backend --validate-env`.
**Important Notes**:
- `REFRESH_TOKEN` must be obtained through OAuth flow (see `src/mkts_backend/esi/esi_auth.py`)
- For local-only operation, Turso credentials are optional
- `GOOGLE_SHEET_KEY` can be the entire JSON content or the system will fall back to a file

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
- **Cache read/write engines:** Both `load_orders_cache` and `save_orders_cache` use `db.engine` (the local pyturso replica). Cache rows travel to Turso with the pipeline's `push()`, so a run that dies before the push re-fetches those pages next time — the safe direction to fail.

### Related Files

- `src/mkts_backend/cli.py` — `process_market_orders()`: orchestrates cache check → fetch → save
- `src/mkts_backend/esi/esi_requests.py` — `fetch_market_orders()`: HTTP requests with conditional headers
- `src/mkts_backend/db/db_handlers.py` — `load_orders_cache()`, `save_orders_cache()`: sentinel read/write
- `src/mkts_backend/config/esi_config.py` — `ESIConfig.headers`: base request headers
- `src/mkts_backend/config/settings.toml` — `[esi] user_agent`: configurable User-Agent string

## Additional Features

- **Multi-Market Support:** Configure and process multiple markets independently via `--market` flag
- **Fit Checking Tool:** CLI command to check market availability for ship fittings with export options
- **Comparative Market Analysis:** Dual-region history tracking (primary market vs Jita) for price comparison charts
- **Market Value Calculation:** Filters out blueprints and skills for accurate market value assessment
- **Ship Count Tracking:** Specifically tracks ship availability on the market
- **Google Sheets Automation:** Automatically updates spreadsheets with latest market data
- **Database State Management:** Automatic verification and sync of database consistency
- **Multi-Region Support:** Handles both structure-specific and region-wide market data
- **Async Processing:** High-performance concurrent API requests with rate limiting and backoff
- **Error Handling:** Comprehensive logging and error recovery for API failures
- **GitHub Actions Integration:** Automated scheduled data collection via workflows
- **Module Equivalents:** Aggregate stock across interchangeable faction modules; managed via `equiv` CLI commands
- **Friendly Names:** Per-doctrine display names stored in `doctrine_fits.friendly_name`; managed via `fit-update update-friendly-name`
- **Asset Cache:** ESI character asset data cached in local-only `cli_cache.db` (1-hour TTL); used by `assets` and `fitcheck needed --assets` commands; bypass with `--refresh` flag

## CLI Tools
This project includes an extensive set of CLI tools.
- See `docs/cli-tools.md` for details on CLI tools and usage. 

## User Implementation Guide

## Prerequisites Checklist

Before starting, ensure the user has:

- [ ] Python 3.12 or higher
- [ ] Git installed
- [ ] An Eve Online character with market access to their target structure
- [ ] Access to create Eve Online developer applications
- [ ] (Optional) Google account for Sheets integration
- [ ] (Optional) Turso account for remote database hosting

## Implementation Steps

### Step 1: Eve Online ESI Application Setup

Guide the user through creating an ESI application:

1. **Navigate to Eve Developers Portal**:
   - URL: https://developers.eveonline.com/
   - Have user log in with their Eve Online account

2. **Create New Application**:
   - Click "Create New Application"
   - Application Name: Choose descriptive name (e.g., "My Market Data Collector")
   - Description: Brief description of purpose
   - Callback URL: `http://localhost:8000/callback`
   - Required Scopes:
     - `esi-markets.structure_markets.v1` (for structure market access)
   - Connection Type: "Authentication & API Access"

3. **Save Credentials**:
   - Note the Client ID
   - Note the Secret Key
   - These will be needed for `.env` file

4. **Generate Refresh Token**:
   - User needs to authenticate once to get a refresh token
   - This requires running an OAuth flow locally (documented in ESI_OAUTH_FLOW.py)
   - The refresh token allows unattended operation

### Step 2: Google Service Account Setup (Optional)

If user wants Google Sheets integration:

1. **Create Google Cloud Project**:
   - Navigate to: https://console.cloud.google.com/
   - Create new project or select existing
   - Note the project name

2. **Enable APIs**:
   - Enable "Google Sheets API"
   - Enable "Google Drive API"

3. **Create Service Account**:
   - Navigate to: IAM & Admin > Service Accounts
   - Click "Create Service Account"
   - Name: "market-data-sheets" (or similar)
   - Role: Leave as default or "Editor"
   - Click "Done"

4. **Generate Key**:
   - Click on the created service account
   - Go to "Keys" tab
   - Click "Add Key" > "Create New Key"
   - Choose JSON format
   - Download and save the JSON file
   - Rename to something recognizable (e.g., `market-service-account.json`)

5. **Share Spreadsheet**:
   - Create a Google Sheet for market data
   - Share it with the service account email (found in JSON file, looks like `xxx@xxx.iam.gserviceaccount.com`)
   - Give "Editor" permissions

### Step 3: Clone and Setup Backend Repository

```bash
# Clone the repository
git clone https://github.com/OrthelT/mkts_backend.git
cd mkts_backend

# Install dependencies using uv
pip install uv  # if not already installed
uv sync
```

### Step 4: Configure Environment Variables
Create a `.env` file in the repository root:
- See Environment Variables section above for required .env variables. 

### Step 5: Customize Market Configuration
Edit `src/mkts_backend/config/settings.toml` to match user's markets:

```toml
[markets]
default = "primary"

[markets.primary]
name = "Your Structure Name"
region_id = 10000003          # Change to your region ID
system_id = 30000240          # Change to your system ID
structure_id = 1053970513596  # Change to your structure ID
database_alias = "wcmktnewkeeptest"
database_file = "wcmktnewkeeptest.db"
turso_url_env = "TURSO_WCMKTNEWKEEP_URL"
turso_token_env = "TURSO_WCMKTNEWKEEP_TOKEN"
gsheets_url = "https://docs.google.com/spreadsheets/d/…/edit"

[markets.deployment]  # Optional second market
name = "Deployment Market Name"
region_id = 10000023          # Pure Blind
system_id = 30001967
structure_id = 1041669946862  # Change to your structure ID
database_alias = "wcmktnorthtest"
database_file = "wcmktnorth2test.db"
turso_url_env = "TURSO_WCMKTNORTH_URL"
turso_token_env = "TURSO_WCMKTNORTH_TOKEN"
gsheets_url = "https://docs.google.com/spreadsheets/d/…/edit"

# Optional per-market worksheet names
[markets.primary.gsheets_worksheets]
market_orders = "market_orders_4h"
market_data = "market_data_4h"
doctrines = "doctrines_mkt_4H"
```

Adding a market needs no code change: `database_routing()` picks up any new
`[markets.<alias>]` block, and `DatabaseConfig` resolves the alias by lookup. A
malformed block (missing `database_alias`/`database_file`, or a duplicate
`database_alias`) fails at import with a section-named error.

Jita comparative pricing is no longer per-market — `process_jita_prices()` fetches
once for the union of every market's watchlist and writes the result to each market
database.

### Finding IDs:
- **Structure ID**: In-game, right-click structure > Copy > Copy Info > paste somewhere > extract ID from `showinfo:` link
- **Region ID**: Use ESI endpoint: `https://esi.evetech.net/latest/universe/regions/` and search
- **System ID**: Use ESI endpoint: `https://esi.evetech.net/latest/search/?categories=solar_system&search=SystemName`
- **tip**: Search Zkillboard.com for an item, ship, system, or character. The string of numbers at the end of the URL is the type_id for the item you searched for. 
- **SDE**: The Eve Online [Static Data Export](https://developers.eveonline.com/docs/services/static-data/) is the authoritative source for mapping between items and their type_ids. 
- **Excel Eve Plugin**: IDs can also be obtained from the Eve Excel Plugin's search functions.
- **ESI**: The ESI, Eve's API, includes search endpoints that can queried from the browser with the [ESI API Explorer](https://developers.eveonline.com/api-explorer)

### Step 6: Setup Initial Data

#### 6.1 Create Watchlist

The watchlist defines which items to track. Create or edit `databackup/all_watchlist.csv`:

```csv
type_id,type_name,group_id,group_name,category_id,category_name
34,Tritanium,18,Mineral,4,Material
35,Pyerite,18,Mineral,4,Material
36,Mexallon,18,Mineral,4,Material
```

**Tips for Watchlist Creation**:
- Start with common items (minerals, ships, modules)
- Use Eve's "Show Info" > "Copy Type ID" to get type_ids
- Or use the methods in the Finding IDs section. 

#### 6.2 Add Fittings (Optional)

If tracking doctrine availability, add ship fittings:

1. Export fittings from Eve Online (in-game: Fitting window > Import/Export > Copy to Clipboard)
2. Place fitting files in a designated folder
3. Use the fitting parser utilities in `src/mkts_backend/utils/parse_fits.py`

### Step 7: Initialize Databases

```bash
# Pulls every configured database from Turso; skips any already initialized
uv run mkts-backend sync
```

The system will automatically:
1. Check if database files exist with proper metadata
2. Sync from Turso remote if files are missing or inconsistent
3. Create tables if needed

This creates local copies of (names from `settings.toml`):
- `wcmktnewkeeptest.db` (primary market)
- `wcmktnorth2test.db` (deployment market)
- `wcmktbkgtest.db` (market3)
- `wcfittingtest.db` (fittings/doctrines)
- `sdelitetest.db` (Eve static data export)
- `buildcosttest.db` (manufacturing costs; optional credentials)

Each arrives with up to five pyturso sidecars (`-shm`, `-wal`, `-info`,
`-changes`, `-wal-revert`) — `-shm` is usually absent once a connection closes
cleanly. Never move or delete one without the others; use `nuke_db()` or
`./dbdeltest.sh` instead.

**Database Schema**:
- `marketorders`: Current market orders
- `market_history`: Historical price/volume data
- `marketstats`: Calculated statistics
- `doctrines`: Fitting availability analysis
- `watchlist`: Items being tracked
- `ship_targets`: Ship production targets
- `doctrine_map`: Doctrine to fitting mappings
- `character_asset_cache`: Cached per-character ESI asset data (in `cli_cache.db`, auto-created, 1-hour TTL)
- `doctrine_fits`: Doctrine fitting configurations with target quantities and market flags
  - Fields: `id`, `doctrine_name`, `fit_name`, `ship_type_id`, `doctrine_id`, `fit_id`, `ship_name`, `target`, `market_flag`, `friendly_name`
  - Used by fit-check to retrieve target quantities for fits
  - `target`: Number of fits to maintain in stock
  - `market_flag`: Market assignment (primary, deployment, or both)
  - `friendly_name`: Optional short display name for the doctrine (e.g., "Hurricane"); managed via `fit-update update-friendly-name` or `fit-update populate-friendly-names`

**Database State Management**:
The system uses `verify_db_exists()` to ensure database consistency:
- If neither database nor metadata exists: syncs from remote
- If both exist: validates and continues
- If database exists without metadata: nukes and re-syncs
- If metadata exists without database: nukes metadata and re-syncs

### Step 8: Configure Google Sheets Integration (Optional)

Edit `src/mkts_backend/config/gsheets_config.py`:

```python
class GoogleSheetConfig:
    _google_private_key_file = "your-service-account.json"  # Path to your JSON key file
    _google_sheet_url = "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit"
    _default_sheet_name = "market_data"  # Sheet tab name
```

### Step 9: Run Backend Data Collection

```bash
# Run basic market data collection
uv run mkts-backend update-markets

# Run with historical data processing (recommended)
uv run mkts-backend update-markets --history

# Check database contents
uv run mkts-backend --check_tables
```

**Schedule Regular Updates**:

Option A - GitHub Actions (recommended for remote deployment):
- Configure secrets in GitHub repository settings
- See `docs/GITHUB_ACTIONS_SETUP.md` for detailed guide
- Workflow file: `.github/workflows/market-data-collection.yml`

Option B - Cron job (for local server):
```bash
# Edit crontab
crontab -e

# Add entry (runs every 4 hours)
0 */4 * * * cd /path/to/mkts_backend && /path/to/uv run mkts-backend update-markets --history >> /path/to/logs/cron.log 2>&1
```

### Step 10: Setup Streamlit Frontend

Clone and setup the frontend application:

```bash
# Clone frontend repository
cd ..
git clone https://github.com/OrthelT/wcmkts_new.git
cd wcmkts_new

# Install dependencies
pip install -r requirements.txt
```

**Configure Database Connection**:

The frontend keeps its **own** pyturso replica of each market database and pulls it
from the same Turso remotes the backend pushes to. Turso is the meeting point; the
two repos never share a file.

**Do not copy or symlink a `.db` between the backend and frontend directories.** A
pyturso replica is the database plus its sidecars (up to five), including
per-client sync watermarks in `-info`. Two processes pointed at one file will
corrupt each other's sync state.

Frontend configuration lives in two files:
- `settings.toml` — `[markets.<alias>]` (name, IDs, `database_alias`,
  `database_file`, `turso_secret_key`) and `[db_paths]` (alias → filename). Every
  `database_alias` must appear in both.
- `.streamlit/secrets.toml` — one `[<key>_turso]` section per database with `url`
  and `token`, keyed by the market's `turso_secret_key` (or `[db_turso_keys]` for
  shared DBs, else the `{alias}_turso` convention).

Guard test: `uv run pytest tests/test_settings_toml.py`.

**Run Streamlit App**:

```bash
streamlit run app.py
```

The app will be available at `http://localhost:8501`

### Step 11: Turso Remote Database Setup (Optional)

For production deployment with remote database access:

1. **Create Turso Account**:
   - Visit: https://turso.tech/
   - Sign up for free account

2. **Create Databases**:
   ```bash
   # Install Turso CLI
   curl -sSfL https://get.tur.so/install.sh | bash

   # Login
   turso auth login

   # Create databases
   turso db create market-data
   turso db create market-fittings
   turso db create eve-sde

   # Get connection strings
   turso db show market-data
   ```

3. **Generate Tokens**:
   ```bash
   turso db tokens create market-data
   turso db tokens create market-fittings
   turso db tokens create eve-sde
   ```

4. **Update .env**:
   - Add Turso URLs and tokens to `.env` file

5. **Initial Sync**:
   ```python
   from mkts_backend.config.db_config import DatabaseConfig
   from mkts_backend.config.market_context import MarketContext

   db = DatabaseConfig(market_context=MarketContext.from_settings("primary"))

   db.verify_db_exists()   # bootstrap the replica if it is missing or inconsistent
   db.pull()               # Turso → local

   # Writes go to the same local engine, then push them up:
   with db.engine.begin() as conn:
       ...                 # INSERT / UPDATE / DELETE
   db.push()               # local → Turso; without this the write never leaves the box
   ```

## Common Customizations

### Changing Market Structure

To switch to a different market structure:

1. Update `settings.toml` with new structure/region/system IDs
2. Verify your ESI application has access (may need to re-authenticate)
3. Clear old market data or create new database
4. Run data collection: `uv run mkts-backend update-markets`

### Adding Custom Doctrines

1. Export fittings from Eve Online
2. Parse fittings using `parse_fits.py` utilities
3. Add to `wcfitting.db` database
4. Link doctrines in `doctrine_map` table
5. Run doctrine analysis: `uv run mkts-backend update-markets`

### Multi-Market Support

To track multiple markets simultaneously:

1. **Configure Markets**: Add market configurations to `settings.toml`
   ```toml
   [markets.primary]
   name = "Primary Market"
   # ... configuration

   [markets.deployment]
   name = "Deployment Market"
   # ... configuration
   ```

2. **Set Environment Variables**: Add Turso credentials for each market
   ```env
   TURSO_WCMKTNEWKEEP_URL=...
   TURSO_WCMKTNEWKEEP_TOKEN=...
   TURSO_WCMKTNORTH_URL=...
   TURSO_WCMKTNORTH_TOKEN=...
   ```

3. **Run Individual Markets**:
   ```bash
   # Process primary market (default)
   uv run mkts-backend update-markets --history

   # Process deployment market
   uv run mkts-backend update-markets --market=deployment --history
   ```

4. **GitHub Actions Parallel Processing**:
   - Use matrix strategy in `.github/workflows/market-data-collection.yml`
   - Process multiple markets in parallel jobs
   - Each job runs independently with its own database

## Troubleshooting Guide

### Authentication Issues

**Problem**: "CLIENT_ID environment variable is not set"
**Solution**: Verify `.env` file exists and contains CLIENT_ID

**Problem**: "Failed to refresh token"
**Solution**:
- Verify CLIENT_ID and SECRET_KEY are correct
- Check if REFRESH_TOKEN is valid (may need to regenerate)
- Ensure ESI application has correct scopes

**Problem**: "Forbidden" errors when fetching structure markets
**Solution**:
- Character must have docking access to structure
- Structure must allow market access
- ESI application needs `esi-markets.structure_markets.v1` scope

### Database Issues

**Problem**: "Database file does not exist"
**Solution**: Run `uv run mkts-backend update-markets` to create initial database

**Problem**: "Table not found"
**Solution**: Database schema may be outdated, check migrations or recreate

**Problem**: Turso sync fails
**Solution**:
- Verify Turso credentials in `.env`
- Check network connectivity
- Verify database exists on Turso

### Google Sheets Issues

**Problem**: "Failed to initialize Google Sheets client"
**Solution**:
- Verify JSON key file exists and path is correct
- Check GOOGLE_SHEET_KEY environment variable if using that method
- Verify service account has access to spreadsheet

**Problem**: "Insufficient permission" when updating sheets
**Solution**: Share spreadsheet with service account email with Editor permissions

### Data Collection Issues

**Problem**: No data being collected
**Solution**:
- Verify market structure has orders
- Check watchlist contains valid type_ids
- Review logs in `logs/mkts-backend.log`

**Problem**: Historical data not updating
**Solution**:
- Run with `--history` flag
- Verify region_id is correct
- Check ESI API status: https://esi.evetech.net/status.json

### GitHub Actions Cache Issues

**Problem**: Scheduled `Market Data Collection` runs fail because a cached DB (e.g., `wcmktnorth2test.db`) has drifted out of sync with Turso cloud, or carries libsql-era `-info` metadata that pyturso rejects.
**Solution**: Wipe the cached DB bundle for the affected leg. Caches are immutable bundles keyed per leg per UTC date, so individual files cannot be removed — the whole entry must go, after which the next run cold-starts and re-pulls from Turso. (The date bucket means at most one new cache per leg per day; restore-keys prefix-matches the most recent.)

Three key families, across `.github/workflows/market-data-collection.yml` and `.github/workflows/builder-costs-collection.yml`:
- `turso-dbs-v4-mkt-<primary|deployment|market3>-<YYYY-MM-DD>` — one market DB, written only by its own matrix leg
- `turso-dbs-v4-shared-<YYYY-MM-DD>` — the SDE + fitting DBs, written only by the primary leg
- `builder-cost-dbs-v4-<YYYY-MM-DD>` — the buildcost DB, from `builder-costs-collection.yml`

```bash
# Requires `gh` authenticated against the repo
scripts/wipe_gha_db_cache.sh deployment   # wipe the wcmktnorth2test leg only
scripts/wipe_gha_db_cache.sh primary      # wipe the wcmktnewkeeptest leg only
scripts/wipe_gha_db_cache.sh shared       # wipe the SDE + fitting bundle
scripts/wipe_gha_db_cache.sh buildercost  # wipe the buildcost bundle
scripts/wipe_gha_db_cache.sh all          # wipe all five
```

The cache-save steps are gated on `if: success()`, so a failed run cannot poison the cache for the next run. Env overrides for the script: `GHA_CACHE_REF` (required, no default — the git ref whose caches to target; use `refs/heads/main` for production or `refs/heads/mkts-turso-main` on the staging repo) and `GHA_CACHE_PREFIX` (default `turso-dbs-v4`).

## Agent Workflow for User Support

When helping a user implement this system:

1. **Assess Requirements**:
   - What market structure/region are they tracking?
   - Do they need Google Sheets integration?
   - Local only or remote database?
   - Single structure or multi-region?

2. **Validate Prerequisites**:
   - Check Python version
   - Verify Eve Online account access
   - Confirm structure access permissions

3. **Guide Through Setup**:
   - Follow steps 1-11 in order
   - Don't skip configuration customization
   - Test each component before moving to next

4. **Test Data Collection**:
   - Run first data collection manually
   - Verify data appears in database
   - Check logs for errors

5. **Setup Automation**:
   - Configure scheduled runs
   - Test automated updates
   - Monitor for issues

6. **Configure Frontend**:
   - Setup database connection
   - Customize display settings
   - Test visualization

7. **Provide Documentation**:
   - Document custom configuration choices
   - Note any deviations from standard setup
   - Create troubleshooting notes for their specific setup

## Best Practices

1. **Start Local**: Begin with local-only setup before adding Turso/Sheets
2. **Small Watchlist**: Start with 10-20 items to test, expand gradually
3. **Test Data Flow**: Verify data flows from ESI > Database > Frontend
4. **Monitor Logs**: Check logs regularly for errors or warnings
5. **Backup Databases**: Regular backups of `.db` files
6. **Version Control**: Track configuration changes in git
7. **Security**: Never commit `.env` file or service account keys

## Additional Resources

- **ESI Documentation**: https://esi.evetech.net/ui/
- **Eve SDE**: https://developers.eveonline.com/resource/resources
- **Turso Documentation**: https://docs.turso.tech/
- **Google Sheets API**: https://developers.google.com/sheets/api
- **Streamlit Documentation**: https://docs.streamlit.io/

## Support and Contact

- Backend Repository Issues: https://github.com/OrthelT/mkts_backend/issues
- Frontend Repository Issues: https://github.com/OrthelT/wcmkts_new/issues
- Discord: orthel_toralen

## Architecture Summary for Agents

When explaining the system architecture:

```
Data Flow:
1. ESI API (Eve Online)
   ↓ (OAuth authenticated requests)
2. Backend Data Collection (mkts_backend)
   ↓ (SQLAlchemy ORM)
3. Local pyturso replica (wcmktnewkeeptest.db, wcmktnorth2test.db, …)
   ↓ (db.push() — local CDC queue → cloud)
4. Turso Remote Database
   ↓ (db.pull() into the frontend's own replica)
5. Streamlit Frontend (wcmkts_new)
   ↓ (Visualization)
6. User Browser

Side Channel:
3. SQLite Database
   ↓ (gspread API)
7. Google Sheets
   ↓ (Manual viewing)
8. User
```

**Key Components**:
- **cli.py**: Main orchestration and entry point
- **esi_auth.py**: OAuth token management
- **esi_config.py**: Market configuration
- **models.py**: Database schema definitions
- **data_processing.py**: Statistics calculation
- **gsheets_config.py**: Google Sheets integration
- **db_config.py**: Database connection management (`DatabaseConfig` class)
- **settings_service.py**: Centralized `settings.toml` loader (`SettingsService` class, module-level cache)
- **cli_tools/prompter.py**: Multiline input prompter for paste mode (uses prompt_toolkit)
- **cli_tools/fit_update.py**: Fit and doctrine management CLI commands (includes friendly name management)
- **cli_tools/equiv_manager.py**: Module equivalents CLI commands (list, find, add, remove)
- **esi/asset_cache.py**: Local SQLite cache for ESI character assets (1-hour TTL, auto-creates table)
- **cli_tools/args_parser.py**: CLI argument routing for all mkts-backend subcommands
- **cli_tools/cli_help.py**: Help text for all CLI commands

## Version Compatibility

- Python: 3.12+
- SQLAlchemy: >=2.0.42 (floor imposed by the pyturso dialect)
- pyturso: >=0.7.2 (0.7.2 in use; provides the `sqlite+turso*` dialects)
- gspread: 5.x+
- pandas: 2.x
- prompt_toolkit: Latest
- Streamlit: 1.x+

## License and Disclaimer

This is an educational project for Eve Online market analysis. All Eve Online data is provided by CCP Games through their ESI API. Eve Online is a trademark of CCP Games.
