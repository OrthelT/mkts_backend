# LLM Agent Guide: Eve Online Market Data System

This guide provides comprehensive documentation for LLM agents working with this Eve Online Market Data Collection and Analysis System. It covers both assisting users in implementing their own system and working with the existing codebase.

## Quick Start for Development

**Run the main application:**
```bash
uv run mkts-backend
```

**Include historical data:**
```bash
uv run mkts-backend --history
```

**Specify a market (uses primary market by default):**
```bash
uv run mkts-backend --market=deployment  # Uses deployment market config
```

**Check database tables:**
```bash
uv run mkts-backend --check_tables
uv run mkts-backend --check_tables --deployment  # Check deployment market tables
```

**Sync and validate databases:**
```bash
uv run mkts-backend sync              # Sync primary market database with Turso
uv run mkts-backend sync --deployment # Sync deployment market database
uv run mkts-backend sync --both       # Sync both primary and deployment markets
uv run mkts-backend validate          # Validate primary market database sync status
uv run mkts-backend validate --market=deployment  # Validate deployment market
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

### Database Layer (`config/config.py`, `db/db_handlers.py`)
Manages all database operations:
- **DatabaseConfig class**: Handles both local SQLite and remote Turso database sync
  - Supports MarketContext-based initialization (preferred) or legacy alias-based init
  - `verify_db_exists()`: Ensures database and metadata are in consistent state
    - Handles 4 cases: neither exists, both exist, db without metadata, metadata without db
    - Automatically syncs from remote or nukes inconsistent states
  - `sync()`: Pulls remote Turso data into local database (one-way: cloud → local)
  - `nuke()`: Safely removes local database and metadata files
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
- Tables stored in market-specific databases (e.g., `wcmktprod.db`, `wcmktnorth2.db`)

### OAuth Authentication (`ESI_OAUTH_FLOW.py` / `esi_auth.py`)
Handles Eve Online SSO authentication:
- Eve Online SSO authentication for ESI API access
- Token refresh and storage in `token.json`
- Manages OAuth flow for initial authorization

### Regional Market Processing (`nakah.py`)
Specialized regional market data handling:
- `process_system_orders()` - Processes orders for specific systems
- `calculate_total_market_value()` - Calculates total market value excluding blueprints/skills
- `calculate_total_ship_count()` - Counts ships available on the market

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

### Primary Market (Production)
- **Name:** 4-HWWF Keepstar
- **Structure ID:** `1035466617946`
- **Region ID:** `10000003` (The Vale of Silent)
- **System ID:** `30000240`
- **Database:** `wcmktprod.db` with Turso sync

### Deployment Market (Optional)
- **Name:** B-9C24 Keepstar
- **Region ID:** `10000023` (Pure Blind)
- **System ID:** `30002029` (B-9C24)
- **Structure ID:** `1046831245129`
- **Database:** `wcmktnorth2.db` with Turso sync

### Configuration Files
- **Market Settings:** `src/mkts_backend/config/settings.toml`
- **ESI Config:** Auto-generated from MarketContext based on settings.toml
- **Watchlist:** Database table with ~850 common items and WinterCo doctrine ships/fittings

## External Dependencies

- **EVE Static Data Export (SDE):** `sdelite.db` - game item/type information (synced from Turso), uses `sdetypes` table for type lookups
- **Custom dbtools:** External dependency package `mydbtools` for database utilities
- **Turso/libsql:** For remote database synchronization (optional in dev, required in production)
  - **IMPORTANT:** libsql `sync()` is **one-way: cloud → local** (pull only). It does NOT push local writes to Turso cloud. To write data to Turso, use `DatabaseConfig` with a remote engine (direct HTTP connection to the Turso URL). The new Turso Database sync engine (currently in alpha) will add push capability; we will adopt it when the stable beta is released.
- **Google Sheets API:** For automated market data reporting (optional)
- **prompt_toolkit:** For multiline input prompts (paste mode in fit-update)

## Data Processing Flow

The complete data pipeline when running the application:

1. **Initialize**: Load market configuration from settings.toml based on `--market` flag (defaults to primary)
2. **Database Setup**: Verify database exists with `verify_db_exists()` (syncs from Turso if needed)
3. **Authenticate**: Authenticate with Eve SSO using required scopes
4. **Market Orders**: Fetch current market orders for configured structure
5. **Historical Data** (optional with `--history` flag):
   - Primary market history → `MarketHistory` table
   - Jita comparative pricing fetched for watchlist items (if configured)
6. **Statistics**: Calculate market statistics (price, volume, days remaining)
7. **Doctrine Analysis**: Analyze ship fitting availability based on market data
8. **Regional Processing**: Update regional orders for the market's region
9. **System Analysis**: Process system-specific orders and calculate market value/ship count
10. **Google Sheets** (if enabled): Update spreadsheets with system market data
11. **Storage**: Store all results in local database with automatic Turso sync

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

# Turso Remote Database (Production)
TURSO_WCMKTPROD_URL=<production_market_db_url>
TURSO_WCMKTPROD_TOKEN=<production_market_db_token>

# Turso Remote Database (Optional - Testing/Development)
TURSO_WCMKTTEST_URL=<test_market_db_url>
TURSO_WCMKTTEST_TOKEN=<test_market_db_token>

# Turso Remote Database (Optional - Secondary "Deployment"" Market)
TURSO_WCMKTNORTH_URL=<deployment_market_db_url>
TURSO_WCMKTNORTH_TOKEN=<deployment_market_db_token>

# Turso Remote Database (Shared Resources)
TURSO_SDE_URL=<sde_db_url>
TURSO_SDE_TOKEN=<sde_db_token>
TURSO_FITTING_URL=<fitting_db_url>
TURSO_FITTING_TOKEN=<fitting_db_token>
```
**Important Notes**:
- `REFRESH_TOKEN` must be obtained through OAuth flow (see `src/mkts_backend/esi/esi_auth.py`)
- For local-only operation, Turso credentials are optional
- `GOOGLE_SHEET_KEY` can be the entire JSON content or the system will fall back to a file

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
- See `docs/cli_tools.md` for details on CLI tools and usage. 

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
[markets.primary]
name = "Your Structure Name"
region_id = 10000003        # Change to your region ID
system_id = 30000240        # Change to your system ID
structure_id = 1035466617946  # Change to your structure ID
database_alias = "wcmktprod"
database_file = "wcmktprod.db"
turso_url_env = "TURSO_WCMKTPROD_URL"
turso_token_env = "TURSO_WCMKTPROD_TOKEN"

[markets.deployment]  # Optional second market
name = "Deployment Market Name"
region_id = 10000023          # Pure Blind
system_id = 30002029
structure_id = 1046831245129  # Change to your structure ID
database_alias = "wcmktnorth"
database_file = "wcmktnorth2.db"
turso_url_env = "TURSO_WCMKTNORTH_URL"
turso_token_env = "TURSO_WCMKTNORTH_TOKEN"

# Optional: Configure Jita comparative pricing for each market
[markets.primary.jita_comparison]
enabled = true
region_id = 10000002  # The Forge

[markets.deployment.jita_comparison]
enabled = false
```

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
# First run will verify databases exist and sync from Turso if configured
uv run mkts-backend
```

The system will automatically:
1. Check if database files exist with proper metadata
2. Sync from Turso remote if files are missing or inconsistent
3. Create tables if needed

This creates local copies of:
- `wcmktprod.db` (primary market database)
- `wcmktnorth2.db` (deployment market database, if configured)
- `wcfitting.db` (fittings/doctrines)
- `sdelite.db` (Eve static data export)

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
uv run mkts-backend

# Run with historical data processing (recommended)
uv run mkts-backend --history

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
0 */4 * * * cd /path/to/mkts_backend && /path/to/uv run mkts-backend --history >> /path/to/logs/cron.log 2>&1
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

The frontend needs access to the backend database. Options:

1. **Local Database** (development):
   - Copy or symlink market database files (e.g., `wcmktprod.db`) from backend to frontend directory
   - Update database path in frontend config

2. **Remote Database** (production):
   - Use Turso database URLs
   - Configure Turso credentials in frontend `.env`

**Update Frontend Configuration**:

Edit configuration files to match your database structure and preferences:
- Database connection strings
- Region/structure names
- Display preferences

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
   from mkts_backend.config.config import DatabaseConfig

   # Pull from Turso cloud → local (libsql sync is one-way: cloud → local)
   db = DatabaseConfig("wcmkt")
   db.sync()

   # To push local data → Turso cloud, use a remote engine connection:
   db_remote = DatabaseConfig("wcmkt", remote=True)
   # Then execute writes against db_remote.engine
   ```

## Common Customizations

### Changing Market Structure

To switch to a different market structure:

1. Update `esi_config.py` with new structure/region/system IDs
2. Verify your ESI application has access (may need to re-authenticate)
3. Clear old market data or create new database
4. Run data collection: `uv run mkts-backend`

### Adding Custom Doctrines

1. Export fittings from Eve Online
2. Parse fittings using `parse_fits.py` utilities
3. Add to `wcfitting.db` database
4. Link doctrines in `doctrine_map` table
5. Run doctrine analysis: `uv run mkts-backend`

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
   TURSO_WCMKTPROD_URL=...
   TURSO_WCMKTPROD_TOKEN=...
   TURSO_WCMKTNORTH_URL=...
   TURSO_WCMKTNORTH_TOKEN=...
   ```

3. **Run Individual Markets**:
   ```bash
   # Process primary market (default)
   uv run mkts-backend --history

   # Process deployment market
   uv run mkts-backend --market=deployment --history
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
**Solution**: Run `uv run mkts-backend` to create initial database

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
3. SQLite Database (wcmktprod.db, wcmktnorth2.db, etc.)
   ↓ (libsql sync with verify_db_exists)
4. Turso Remote Database
   ↓ (SQLite connection)
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
- **config.py**: Database connection management
- **cli_tools/prompter.py**: Multiline input prompter for paste mode (uses prompt_toolkit)
- **cli_tools/fit_update.py**: Fit and doctrine management CLI commands (includes friendly name management)
- **cli_tools/equiv_manager.py**: Module equivalents CLI commands (list, find, add, remove)
- **esi/asset_cache.py**: Local SQLite cache for ESI character assets (1-hour TTL, auto-creates table)
- **cli_tools/args_parser.py**: CLI argument routing for all mkts-backend subcommands
- **cli_tools/cli_help.py**: Help text for all CLI commands

## Version Compatibility

- Python: 3.12+
- SQLAlchemy: 2.x
- libsql: Latest
- gspread: 5.x+
- pandas: 2.x
- prompt_toolkit: Latest
- Streamlit: 1.x+

## License and Disclaimer

This is an educational project for Eve Online market analysis. All Eve Online data is provided by CCP Games through their ESI API. Eve Online is a trademark of CCP Games.
