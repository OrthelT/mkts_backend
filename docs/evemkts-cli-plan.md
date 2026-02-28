# Plan: Create `evemkts` — Standalone Eve Online Market CLI

## Context

The `mkts_backend` project has organically grown a feature-rich CLI (fit checking, doctrine management, module equivalents, asset lookups, watchlist management) that deserves to be its own standalone project. Meanwhile, `esi-market-tool` is a clean, modular market data collector with async ESI client, OAuth, rate limiting, and Rich UI — but no database layer or analysis tools.

**Goal:** Create `evemkts`, a standalone CLI tool that merges the best of both:
- **From esi-market-tool:** Async ESI client, OAuth flow, rate limiter, cache, config system, setup wizard, headless mode, Google Sheets integration
- **From mkts_backend CLI tools:** Fit checking, fit/doctrine management, module equivalents, asset lookups, watchlist management, Rich display formatting

The new project should be completely independent — no imports from `mkts_backend`. Any shared code gets replicated and adapted. The `mkts_backend` project continues unchanged as the production pipeline feeding the Streamlit frontend.

## Architecture

```
evemkts/                              (new repo)
├── pyproject.toml
├── config.toml                       (user config — structure IDs, markets, features)
├── config.toml.example
├── .env.example
├── data/
│   └── type_ids.csv                  (watchlist items)
├── src/evemkts/
│   ├── __init__.py
│   ├── cli.py                        (main entry point + subcommand routing)
│   ├── setup.py                      (interactive setup wizard from esi-market-tool)
│   │
│   ├── core/                         (from esi-market-tool — data collection)
│   │   ├── esi_client.py             (async ESI HTTP client)
│   │   ├── esi_auth.py               (OAuth2 flow + token management)
│   │   ├── rate_limiter.py           (async token bucket)
│   │   ├── cache.py                  (HTTP conditional request caching)
│   │   ├── market_data.py            (order aggregation, history stats)
│   │   ├── jita.py                   (Jita/Fuzzworks price fetching)
│   │   └── export.py                 (CSV + Google Sheets output)
│   │
│   ├── config/                       (merged config system)
│   │   ├── config.py                 (TOML-based config with frozen dataclasses)
│   │   ├── market_context.py         (multi-market config — adapted from mkts_backend)
│   │   └── logging_config.py
│   │
│   ├── db/                           (local SQLite layer — simplified from mkts_backend)
│   │   ├── database.py               (SQLite connection manager — no Turso, no libsql)
│   │   ├── models.py                 (SQLAlchemy ORM models — subset needed for CLI)
│   │   └── queries.py                (read queries for fit check, equiv, etc.)
│   │
│   ├── tools/                        (from mkts_backend CLI tools)
│   │   ├── fit_check.py              (check fit market availability)
│   │   ├── fit_update.py             (add/update/manage fits and doctrines)
│   │   ├── equiv_manager.py          (module equivalence groups)
│   │   ├── asset_check.py            (character asset lookup)
│   │   ├── add_watchlist.py          (watchlist management)
│   │   └── db_inspect.py            (display DB contents)
│   │
│   ├── utils/                        (shared utilities)
│   │   ├── eft_parser.py             (EFT format parsing)
│   │   ├── type_info.py              (SDE type lookups)
│   │   ├── parse_fits.py             (fit file parsing)
│   │   └── doctrine_update.py        (doctrine DB updates)
│   │
│   └── display/                      (Rich output formatting)
│       ├── rich_display.py           (ISK formatting, table builders)
│       ├── progress.py               (progress bars from esi-market-tool)
│       └── prompter.py               (multiline input for EFT paste)
```

## What Comes From Where

### From esi-market-tool (foundation)
| Module | Purpose | Adaptation needed |
|--------|---------|-------------------|
| `esi_client.py` | Async ESI HTTP client (aiohttp) | Minor — add structure market scope |
| `ESI_OAUTH_FLOW.py` → `esi_auth.py` | OAuth2 + auto callback server | Merge with mkts_backend's token refresh logic |
| `rate_limiter.py` | Async token bucket | Use as-is |
| `cache.py` | ETag/Last-Modified HTTP caching | Use as-is |
| `market_data.py` | Order aggregation, history stats | Use as-is, add doctrine-aware calculations |
| `get_jita_prices.py` → `jita.py` | Fuzzworks API | Use as-is |
| `export.py` | CSV + Google Sheets | Use as-is |
| `config.py` | Frozen dataclass config | Extend with market context, DB settings |
| `setup.py` | Interactive TUI setup wizard | Extend for multi-market + DB setup |
| `progress_display.py` | Rich progress bars | Use as-is |
| `logging_utils.py` | Logging config | Use as-is |

### From mkts_backend CLI tools (features)
| Module | Purpose | Adaptation needed |
|--------|---------|-------------------|
| `fit_check.py` | Fit market availability (1950 lines) | Replace `mkts_backend` imports with `evemkts` equivalents |
| `fit_update.py` | Fit/doctrine management | Same — rewire imports |
| `equiv_manager.py` | Module equivalence groups | Same — rewire imports |
| `asset_check.py` | Character asset lookup | Same — rewire imports |
| `add_watchlist.py` | Watchlist management | Same — rewire imports |
| `cli_db_commands.py` | DB table inspection | Simplify — no Turso |
| `rich_display.py` | ISK/quantity formatting | Use as-is |
| `prompter.py` | Multiline EFT input | Use as-is |
| `market_args.py` | Market flag parsing | Use as-is |
| `eft_parser.py` | EFT format parsing | Replace SDE lookup to use local DB |
| `get_type_info.py` | SDE type lookups | Adapt for local SDE file |
| `equiv_handlers.py` | Module equivalence DB queries | Adapt for simplified DB layer |

### New / Adapted
| Module | Purpose | Notes |
|--------|---------|-------|
| `db/database.py` | SQLite connection manager | **New** — simplified from mkts_backend's DatabaseConfig. No Turso, no libsql, no remote sync. Pure SQLite. |
| `db/models.py` | ORM models | **Subset** — only models needed by CLI tools (MarketStats, Doctrines, DoctrineFitItems, Watchlist, ModuleEquivalents, MarketOrders, MarketHistory) |
| `config/market_context.py` | Multi-market config | **Adapted** — simplified, reads from config.toml instead of settings.toml |
| `cli.py` | Entry point + routing | **New** — CLI-only routing (no pipeline orchestration) |

## Key Design Decisions

### 1. Database: Pure SQLite, No Turso
The CLI reads from local `.db` files. Users either:
- **Option A:** Run the `evemkts fetch` command to populate a local DB from ESI (using the esi-market-tool data collection pipeline)
- **Option B:** Copy/symlink `.db` files from an existing mkts_backend installation
- **Option C:** Use the CLI tools against DB files synced by other means

No libsql, no Turso dependency. This eliminates the heaviest infrastructure coupling.

### 2. SDE Data
The CLI needs the SDE for type name lookups. Options:
- Bundle a minimal SDE extract (type_ids → names mapping) as package data or CSV
- Support loading a full `sdelite.db` file if available
- Fetch type names from ESI `/universe/names` endpoint as fallback

Recommend: Ship a `sde_types.csv` (or small SQLite) with the package, with an `evemkts update-sde` command to refresh it.

### 3. Config System: Extend esi-market-tool's config.toml
The esi-market-tool already has a clean TOML config with frozen dataclasses. Extend it:

```toml
[esi]
structure_id = 1035466617946
region_id = 10000003

[markets.primary]
name = "4-HWWF Keepstar"
structure_id = 1035466617946
region_id = 10000003
database = "primary_market.db"

[markets.deployment]              # optional
name = "B-9C24 Keepstar"
structure_id = 1046831245129
region_id = 10000023
database = "deployment_market.db"

[database]
sde_file = "sde_types.db"        # bundled or user-provided

[rate_limiting]
burst_size = 10
tokens_per_second = 5.0

[google_sheets]
enabled = false
```

### 4. Entry Points

```toml
[project.scripts]
evemkts = "evemkts.cli:main"
fitcheck = "evemkts.tools.fit_check:main"
evemkts-setup = "evemkts.setup:main"
```

### 5. Command Structure

```
evemkts
├── fetch                          (from esi-market-tool: collect market data)
│   └── --market=<alias>, --history, --headless
├── fit-check                      (from mkts_backend)
│   └── --file=, --fit=, --market=, --target=, --output=
├── fit-update <subcommand>        (from mkts_backend)
│   └── add, update, list-fits, list-doctrines, ...
├── equiv <subcommand>             (from mkts_backend)
│   └── list, find, add, remove
├── assets                         (from mkts_backend)
│   └── --id=, --name=, --refresh
├── watchlist                      (from mkts_backend: add_watchlist)
│   └── add --type_id=, list
├── db                             (inspect/manage)
│   └── check, import <file>
├── auth                           (from esi-market-tool: OAuth)
│   └── login, refresh, status
├── setup                          (from esi-market-tool: TUI wizard)
└── update-sde                     (refresh SDE data)
```

## Dependencies

```toml
dependencies = [
    "aiohttp>=3.9",           # async ESI client (from esi-market-tool)
    "pandas>=2.2",            # data processing
    "requests>=2.32",         # sync HTTP (Jita prices, OAuth)
    "requests-oauthlib>=2.0", # OAuth2 flow
    "python-dotenv>=1.0",     # env vars
    "rich>=13.7",             # CLI UI
    "sqlalchemy>=2.0",        # ORM for local SQLite
    "prompt-toolkit>=3.0",    # multiline input
    "gspread>=6.2.1",         # Google Sheets (optional)
]
```

Notable: No libsql, no sqlalchemy-libsql, no matplotlib/seaborn/plotly, no httpx, no aiolimiter, no backoff, no millify, no google-auth (gspread handles this). Much lighter than mkts_backend.

## Implementation Phases

### Phase 1: Scaffold + Core Infrastructure
1. Create new repo `evemkts` (or rename/fork `esi-market-tool`)
2. Set up `pyproject.toml` with `src/evemkts/` layout
3. Copy esi-market-tool modules into `core/` and `config/`
4. Rename imports from flat namespace to `evemkts.core.*`, `evemkts.config.*`
5. Create `db/database.py` — simplified SQLite connection manager
6. Create `db/models.py` — copy needed ORM models from mkts_backend
7. Verify: `evemkts fetch` works (esi-market-tool pipeline under new name)

### Phase 2: Port CLI Tools
1. Copy CLI tool files from mkts_backend into `tools/`
2. Copy supporting utils (eft_parser, type_info, rich_display, prompter)
3. Rewire all imports from `mkts_backend.*` → `evemkts.*`
4. Adapt DB access: replace `DatabaseConfig(market_context=ctx)` with simplified `Database(path)`
5. Remove all Turso/libsql/remote references
6. Create `cli.py` entry point with subcommand routing
7. Verify: `evemkts fit-check --file=<test>` works

### Phase 3: Integrate Data Pipeline with CLI Tools
1. Connect `evemkts fetch` output to the SQLite database (currently outputs CSV)
2. Add DB write step after market data collection: orders → `marketorders`, history → `market_history`, stats → `marketstats`
3. Verify: `evemkts fetch && evemkts fit-check --fit=42` works end-to-end

### Phase 4: Polish
1. Extend setup wizard for multi-market + database configuration
2. Add `evemkts update-sde` command (fetch/bundle SDE type data)
3. Write README with installation + usage docs
4. Add CLI help text for all commands
5. Port/write tests

## Files to Create/Modify

### New repo files to create
- `pyproject.toml` — package definition
- `config.toml.example` — config template
- `.env.example` — secrets template
- `src/evemkts/cli.py` — main entry point
- `src/evemkts/db/database.py` — simplified SQLite manager
- `src/evemkts/db/models.py` — ORM model subset
- `src/evemkts/db/queries.py` — read queries

### Files copied from esi-market-tool (with import updates)
- `cli.py` → `src/evemkts/core/pipeline.py` (the fetch pipeline)
- `esi_client.py` → `src/evemkts/core/esi_client.py`
- `ESI_OAUTH_FLOW.py` → `src/evemkts/core/esi_auth.py`
- `rate_limiter.py` → `src/evemkts/core/rate_limiter.py`
- `cache.py` → `src/evemkts/core/cache.py`
- `market_data.py` → `src/evemkts/core/market_data.py`
- `get_jita_prices.py` → `src/evemkts/core/jita.py`
- `export.py` → `src/evemkts/core/export.py`
- `config.py` → `src/evemkts/config/config.py`
- `setup.py` → `src/evemkts/setup.py`
- `progress_display.py` → `src/evemkts/display/progress.py`
- `logging_utils.py` → `src/evemkts/config/logging_config.py`

### Files copied from mkts_backend (with import rewiring)
- `cli_tools/fit_check.py` → `src/evemkts/tools/fit_check.py`
- `cli_tools/fit_update.py` → `src/evemkts/tools/fit_update.py`
- `cli_tools/equiv_manager.py` → `src/evemkts/tools/equiv_manager.py`
- `cli_tools/asset_check.py` → `src/evemkts/tools/asset_check.py`
- `cli_tools/add_watchlist.py` → `src/evemkts/tools/add_watchlist.py`
- `cli_tools/cli_db_commands.py` → `src/evemkts/tools/db_inspect.py`
- `cli_tools/rich_display.py` → `src/evemkts/display/rich_display.py`
- `cli_tools/prompter.py` → `src/evemkts/display/prompter.py`
- `cli_tools/market_args.py` → `src/evemkts/config/market_args.py`
- `utils/eft_parser.py` → `src/evemkts/utils/eft_parser.py`
- `utils/get_type_info.py` → `src/evemkts/utils/type_info.py`
- `utils/parse_fits.py` → `src/evemkts/utils/parse_fits.py`
- `utils/doctrine_update.py` → `src/evemkts/utils/doctrine_update.py`
- `db/models.py` → `src/evemkts/db/models.py` (subset)
- `db/equiv_handlers.py` → `src/evemkts/db/equiv_handlers.py`

## Key Risks

| Risk | Mitigation |
|------|------------|
| Large port effort (~20 files need import rewiring) | Methodical: copy, update imports, test each tool individually |
| SDE data availability | Bundle minimal CSV extract; add ESI fallback for name lookups |
| DB schema drift between evemkts and mkts_backend | Document schema version; evemkts reads same tables but doesn't define the pipeline |
| esi-market-tool outputs CSV, not SQLite | Phase 3 adds DB write step to bridge this |
| fit_check.py is 1950 lines with deep dependency chains | Port incrementally; this is the integration test |

## What Stays in mkts_backend (unchanged)
- Main data pipeline (`cli.py` orchestration)
- Turso sync/validate
- Google Sheets automation
- GitHub Actions workflows
- Processing layer (data_processing.py)
- All existing CLI tools (remain functional but may be deprecated in favor of evemkts over time)
- CLAUDE.md, settings.toml, all config

## Verification Plan
1. `evemkts fetch` — collects market data from ESI, writes to local DB
2. `evemkts fit-check --file=<test_fit>` — parses EFT, queries DB, displays results
3. `evemkts fit-check --fit=42` — queries pre-calculated doctrine data
4. `evemkts fit-update list-fits` — lists fits from DB
5. `evemkts equiv list` — shows module equivalence groups
6. `evemkts assets --id=11379` — looks up character assets
7. `evemkts auth status` — shows OAuth token state
8. `evemkts setup` — interactive configuration wizard
9. Run test suite: `uv run pytest`
