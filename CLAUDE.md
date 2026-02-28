# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Project Instructions

## COMPLETED: CLI Simplification & Refactoring (Feb 2026)
Centralized argument parsing, command registry, no-wrong-door routing, and "Did you mean?" suggestions. See `cli_refactor.md` in memory for architecture details.

# Development Guide

## Development Commands

**Run the main application:**
```bash
python main.py
```

**Dependencies are managed with uv:**
```bash
uv sync  # Install dependencies
uv add <package>  # Add new dependency
```

## Project Architecture

This is an **Eve Online market data collection and analysis system** that:

1. **Fetches market data** from Eve Online's ESI API for specific structures/regions
2. **Processes and stores** market orders, history, and calculated statistics in SQLite databases  
3. **Analyzes doctrine fits** and calculates market availability for ship loadouts
4. **Tracks regional/system market data** with automated Google Sheets integration

### Core Components

**Main Data Flow (`cli.py`):**
- `fetch_market_orders()` - Gets current market orders from ESI API with OAuth
- `fetch_history()` - Gets historical market data for watchlist items from primary region
- `fetch_jita_history()` - Gets comparative historical data from The Forge region (Jita)
- `calculate_market_stats()` - Computes statistics from orders and history
- `calculate_doctrine_stats()` - Analyzes ship fitting availability
- Regional order processing and system-specific market analysis

**Database Layer (`dbhandler.py`):**
- Handles both local SQLite and remote Turso database sync
- Functions for CRUD operations on market data tables
- Database sync functionality for production deployment
- ORM-based data insertion with chunking for large datasets

**Data Models (`models.py`):**
- SQLAlchemy ORM models: `MarketOrders`, `MarketHistory`, `MarketStats`, `Doctrines`, `Watchlist`
- Regional models: `RegionOrders`, `JitaHistory` (comparative pricing from The Forge)
- Organizational models: `ShipTargets`, `DoctrineMap`, `DoctrineInfo`
- All tables use primary database `wcmkt2.db`

**OAuth Authentication (`ESI_OAUTH_FLOW.py`):**
- Handles Eve Online SSO authentication for ESI API access
- Manages token refresh and storage in `token.json`

**Regional Market Processing (`nakah.py`):**
- `get_region_orders()` - Fetches all market orders for a region
- `process_system_orders()` - Processes orders for specific systems
- `calculate_total_market_value()` - Calculates total market value excluding blueprints/skills
- `calculate_total_ship_count()` - Counts ships available on the market

**Google Sheets Integration (`google_sheets_utils.py`):**
- Automated Google Sheets updates with market data
- Service account authentication
- Configurable append/replace data modes

**Data Processing (`data_processing.py`):**
- Market statistics calculation with 5th percentile pricing
- Doctrine availability analysis
- Historical data integration (30-day averages)

### Key Configuration

- **Structure ID:** `1035466617946` (4-HWWF Keepstar)
- **Region ID:** `10000003` (The Vale of Silent)
- **Deployment Region:** `10000001` (The Forge)
- **Deployment System:** `30000072` (Nakah)
- **Database:** Local SQLite (`wcmkt2.db`) with optional Turso sync
- **Watchlist:** CSV-based item tracking in `databackup/all_watchlist.csv`

### External Dependencies

- **EVE Static Data Export (SDE):** `sde_info.db` - game item/type information
- **Custom dbtools:** Local dependency at `../../tools/dbtools` for database utilities
- **Turso/libsql:** For remote database synchronization (optional in dev)
- **Google Sheets API:** For automated market data reporting

### Data Processing Flow

1. Authenticate with Eve SSO using required scopes
2. Fetch current market orders for configured structure
3. Fetch historical data for watchlist items (optional with `--history` flag)
   - Primary market history (Vale of Silent) → `MarketHistory` table
   - Jita comparative history (The Forge) → `JitaHistory` table
4. Calculate market statistics (price, volume, days remaining)
5. Calculate doctrine/fitting availability based on market data
6. Update regional orders for deployment region
7. Process system-specific orders and calculate market value/ship count
8. Update Google Sheets with system market data
9. Store all results in local database with optional cloud sync

### Environment Variables Required

```
CLIENT_ID=<eve_sso_client_id>
SECRET_KEY=<eve_sso_client_secret>
TURSO_URL=<optional_remote_db_url>
TURSO_AUTH_TOKEN=<optional_remote_db_token>
SDE_URL=<optional_sde_db_url>
SDE_AUTH_TOKEN=<optional_sde_db_token>
```

### Additional Features

- **Comparative Market Analysis:** Dual-region history tracking (primary market vs Jita) for price comparison charts
- **Market Value Calculation:** Filters out blueprints and skills for accurate market value assessment
- **Ship Count Tracking:** Specifically tracks ship availability on the market
- **Google Sheets Automation:** Automatically updates spreadsheets with latest market data
- **Multi-Region Support:** Handles both structure-specific and region-wide market data
- **Async Processing:** High-performance concurrent API requests with rate limiting and backoff
- **Error Handling:** Comprehensive logging and error recovery for API failures

### Usage Commands

```bash
# Basic market data processing
uv run mkts-backend

# Include historical data (both primary and Jita)
uv run mkts-backend --history

# Inspect database tables
uv run mkts-backend --check_tables
```

### Fit Check Command

The `fitcheck` command provides a standalone CLI for checking doctrine fit market availability.

```bash
# Check fit by ID (most common usage)
fitcheck --fit=42

# Check against deployment market
fitcheck --fit=42 --market=deployment

# Check from EFT file
fitcheck --file=fits/hurricane_fleet.txt

# Override target and export multi-buy list
fitcheck --fit=42 --target=50 --output=multibuy

# Export markdown for Discord
fitcheck --fit=42 --output=markdown

# Show help
fitcheck --help
```

**Options:**
- `--fit=<id>` - Look up fit by ID from doctrine tables
- `--file=<path>` - Path to EFT fit file
- `--paste` - Read EFT fit from stdin
- `--market=<alias>` - Market to check: primary, deployment (default: primary)
- `--target=<N>` - Override target quantity
- `--output=<format>` - Export format: csv, multibuy, or markdown
- `--no-jita` - Hide Jita price comparison columns
- `--no-legend` - Hide the legend
