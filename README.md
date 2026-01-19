# Eve Online Market Data Collection & Analysis System

A comprehensive market data collection and analysis system for Eve Online that fetches market data from the ESI API, processes it, and provides insights for market analysis and fleet doctrine planning.

## Features

- **Market Data Collection**: Fetches real-time market orders from Eve Online's ESI API
- **Historical Analysis**: Collects and analyzes market history for trend analysis
- **Doctrine Analysis**: Calculates ship fitting availability and market depth
- **Fit Checking**: CLI tool to check market availability for ship fittings with export options
- **Regional Processing**: Handles both structure-specific and region-wide market data
- **Google Sheets Integration**: Automatically updates spreadsheets with market data
- **Market Value Calculation**: Calculates total market value excluding blueprints/skills
- **Ship Count Tracking**: Tracks ship availability on the market
- **Multi-Database Support**: Local SQLite with optional remote Turso sync

## Quick Start

### Prerequisites

- Python 3.12+
- Eve Online Developer Application (for ESI API access)
- Google Service Account (for Sheets integration)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd mkts_backend
```

2. Install dependencies using uv:
```bash
uv sync
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your credentials
```

### Configuration

Create a `.env` file with the following variables:

```env
CLIENT_ID=<eve_client_id>
SECRET_KEY=<eve_client_secret>
REFRESH_TOKEN=<eve_sso_token[refresh_token]>

TURSO_WCMKTPROD_URL=turso db url (production)
TURSO_WCMKTPROD_TOKEN=turso db auth token (production)
TURSO_WCMKTTEST_URL=turso db url (development/optional)
TURSO_WCMKTTEST_TOKEN=turso db token (development/optional)
TURSO_FITTING_URL=turso fitting db url
TURSO_FITTING_TOKEN=turso fitting db token
TURSO_SDE_URL=turso sde db url
TURSO_SDE_TOKEN=turso sde db token

(optional)
GOOGLE_SHEETS_PRIVATE_KEY = <filename.json>
```

### Configure setting for your app:
Please update these settings for your application here. Settings for the ESI, market data, etc.

**location:** 'src/mkts_backend/config/settings.toml'

### Running the Application

```bash
# Run with market orders only (primary market)
uv run mkts-backend

# Run with historical data processing
uv run mkts-backend --history

# Process a specific market
uv run mkts-backend --market=deployment --history
```

## CLI Commands

### fit-check - Check Market Availability for Ship Fittings

Display market availability and pricing for ship fits from EFT-formatted files or from pre-calculated doctrine data.

```bash
# Basic usage - check fit availability from EFT file
uv run fit-check --file=path/to/fit.txt

# Check fit by ID from doctrine_fits/doctrines tables (uses pre-calculated data)
uv run fit-check --fit-id=42

# Check fit_id against specific market
uv run fit-check --fit-id=42 --market=deployment

# Check against specific market with EFT file
uv run fit-check --file=fit.txt --market=deployment

# Override target quantity
uv run fit-check --file=fit.txt --target=50

# Export results to CSV
uv run fit-check --fit-id=42 --output=csv

# Show multibuy format for restocking items below target
uv run fit-check --file=fit.txt --output=multibuy

# Export markdown for Discord sharing
uv run fit-check --fit-id=42 --output=markdown

# Read from stdin
cat fit.txt | uv run fit-check --paste
```

### update-fit - Update Doctrine Fits

Process EFT fit files and update doctrine tables. Supports interactive metadata input and multi-market targeting.

```bash
# Update fit with metadata file
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=fits/hfi_meta.json

# Update fit with interactive prompts
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive

# Update for deployment market
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --deployment

# Update for both markets with ship_targets
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=meta.json --both --update-targets

# Preview changes (dry run)
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive --dry-run
```

**Available Subcommands:**
- `add` - Add a NEW fit from an EFT file
- `update` - Update an existing fit's items
- `assign-market` - Change market assignment
- `list-fits` - List all fits in tracking system
- `list-doctrines` - List all available doctrines
- `create-doctrine` - Create a new doctrine
- `doctrine-add-fit` - Add existing fit(s) to a doctrine
- `doctrine-remove-fit` - Remove fit(s) from a doctrine

```bash
# Add existing fits to a doctrine (supports multiple)
uv run mkts-backend update-fit doctrine-add-fit --doctrine-id=42 --fit-ids=313,314,315

# Remove fits from a doctrine (reverse of doctrine-add-fit)
uv run mkts-backend update-fit doctrine-remove-fit --doctrine-id=42 --fit-id=313
```

**Input Modes:**
- `--file=<path>`: Parse an EFT-formatted fit file and query live market data
- `--fit-id=<id>`: Look up fit by ID from doctrine_fits table and display pre-calculated market data from doctrines table
- `--paste`: Read EFT fit from stdin

**Features:**
- Displays complete fit breakdown with market availability
- Shows bottleneck items (lowest fits available)
- Automatically retrieves target quantities from doctrine_fits table
- Calculates quantity needed to reach target
- Exports to CSV for spreadsheet analysis
- Generates Eve Multi-buy format for easy restocking
- Falls back to live market data for items not on watchlist (when using --file)
- Fast lookups using pre-calculated doctrine data (when using --fit-id)

## Architecture

### Core Components

- **`mkts_backend/cli.py`**: CLI entrypoint (`mkts-backend`) orchestrating jobs
- **`mkts_backend/db/`**: ORM models, handlers, and query utilities
- **`mkts_backend/esi/`**: ESI auth, requests, and async history clients
- **`mkts_backend/processing/`**: Market stats and doctrine analysis pipelines
- **`mkts_backend/utils/`**: Utility modules (names, parsing, db helpers, various legacy functions)
- **`mkts_backend/config/`**: DB, ESI, Google Sheets, and logging config

### Data Flow

1. **Authentication**: Authenticate with Eve SSO using required scopes
2. **Market Orders**: Fetch current market orders for configured structure
3. **Historical Data**: Optionally fetch historical data for watchlist items
4. **Statistics**: Calculate market statistics (price, volume, days remaining)
5. **Doctrine Analysis**: Analyze ship fitting availability based on market data
6. **Regional Processing**: Update regional orders for deployment region
7. **System Analysis**: Process system-specific orders and calculate market metrics
8. **Google Sheets**: Update spreadsheets with system market data
9. **Storage**: Store all results in local database with optional cloud sync

## Configuration

### Key Settings

Configuration is managed through `settings.toml` with support for multiple markets:

**Primary Market (Default)**:
- **Structure ID**: `1035466617946` (4-HWWF Keepstar)
- **Region ID**: `10000003` (The Vale of Silent)
- **System ID**: `30000240`
- **Database**: Local SQLite (`wcmktprod.db`) with Turso sync

**Deployment Market (Optional)**:
- **Region ID**: `10000023` (Pure Blind)
- **System ID**: `30002029` (B-9C24)
- **Database**: Local SQLite (`wcmktnorth2.db`) with Turso sync

**Watchlist**: DB table with ~850 common items and all WinterCo Doctrine ships and fittings.

### Google Sheets Integration (optional)

1. Enable/Disable in 'settings.toml'.
2. Create a Google Service Account
3. Download the service account key file
4. Place the key file as `<filename>.json` in the project root
5. Configure the spreadsheet URL in 'settings.toml'

## Database Schema

### Primary Tables

- **`marketorders`**: Current market orders from ESI API
- **`market_history`**: Historical market data for trend analysis
- **`marketstats`**: Calculated market statistics and metrics
- **`doctrines`**: Ship fitting availability and doctrine analysis
- **`region_orders`**: Regional market orders for broader analysis
- **`watchlist`**: Items being tracked for market analysis

### Support Tables

- **`ship_targets`**: Ship production targets and goals
- **`doctrine_map`**: Mapping between doctrines and fittings
- **`doctrine_info`**: Doctrine metadata and information
- **`doctrine_fits`**: Doctrine fitting configurations with target quantities
  - Stores fit_name, ship_type_id, target quantity, and market_flag
  - Used by fit-check command to retrieve target quantities
  - Market flag indicates which markets track this doctrine (primary, deployment, both)

## API Integration

### Eve Online ESI API

- **Market Orders**: Real-time market data from structures
- **Market History**: Historical price and volume data
- **Universe Names**: Item name resolution
- **OAuth Flow**: Secure authentication for protected endpoints

### Google Sheets API

- **Service Account**: Authentication using service account credentials
- **Batch Updates**: Efficient bulk data updates
- **Configurable Modes**: Append or replace data options

## Development

### Dependencies

The project uses modern Python dependencies managed with uv:

- **SQLAlchemy**: ORM and database operations
- **Pandas**: Data manipulation and analysis
- **Requests**: HTTP client for API calls
- **libsql**: SQLite with sync capabilities
- **gspread**: Google Sheets API integration
- **mydbtools**: Custom database utilities

### Logging

Comprehensive logging is configured with rotating file handlers:

- **Log Files**: `logs/mkts-backend.log`
- **Rotation**: 1MB per file, 5 backup files
- **Levels**: INFO for file, ERROR for console

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is developed as a learning project for Eve Online market analysis. Contact orthel_toralen on Discord with questions.

## Disclaimer

This tool is designed for educational and analysis purposes. All Eve Online data is provided by CCP Games through their ESI API. Eve Online is a trademark of CCP Games.
