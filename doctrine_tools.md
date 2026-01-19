# Doctrine Tools CLI

This document tracks the development of doctrine management tools for the mkts_backend system.

## Fit Check ✓ IMPLEMENTED

A command-line interface that displays market availability for ship fittings from EFT-formatted files or from pre-calculated doctrine data.

### Features Implemented:
- **Input Methods**:
  - `--file=<path>`: Parse EFT-formatted .txt files and query live market data
  - `--fit-id=<id>`: Look up fit by ID from doctrine_fits table and display pre-calculated market data from doctrines table
  - `--paste`: Read EFT fit from stdin
- **Market Selection**: Takes market as argument (`--market=primary` or `--market=deployment`)
- **Rich Table Display**: Uses Rich library for beautiful console output with the following columns:
  - `type_id`: Item type ID
  - `type_name`: Item name
  - `market_stock`: Current market inventory (total_volume_remain)
  - `fit_qty`: Quantity required per fit
  - `fits`: Number of complete fits available (market_stock / fit_qty)
  - `price`: Market price (5th percentile from marketstats)
  - `fit_cost`: Cost for this item in one fit (fit_qty × price)
  - `avg_price`: 30-day average price (only available in --file mode)
  - `qty_needed`: Quantity needed to meet target (only shown when target available)
- **Header Information**: Displays fit name, ship name, ship type ID, total fit cost, fits available (bottleneck), and target quantity
- **Target Integration**: Automatically looks up target quantities from `doctrine_fits` table
- **Target Override**: `--target=N` parameter to override database target
- **Jita Price Comparison**: Displays Jita prices and highlights items priced >120% above Jita
- **Fallback Pricing**: For items not on watchlist (--file mode only), queries `marketorders` table and calculates 5th percentile pricing
- **Missing Items Report**: Shows items below target with quantity needed
- **Export Options** (`--output=<format>`):
  - `csv`: Export table to CSV file (auto-named from fit)
  - `multibuy`: Display Eve Multi-buy/jEveAssets stockpile format for items below target
  - `markdown`: Discord-friendly markdown format with bold formatting

### CLI Usage:
```bash
# Basic usage - from EFT file
fit-check --file=<path>

# Check by fit_id (uses pre-calculated doctrine data)
fit-check --fit-id=42

# With market selection (works with both modes)
fit-check --file=<path> --market=deployment
fit-check --fit-id=42 --market=deployment

# Override target quantity
fit-check --file=<path> --target=50
fit-check --fit-id=42 --target=50

# Export to CSV
fit-check --fit-id=42 --output=csv

# Show multibuy format for restocking
fit-check --file=<path> --output=multibuy

# Export markdown for Discord
fit-check --fit-id=42 --output=markdown

# Read from stdin
cat fit.txt | fit-check --paste
```

### Database Integration:
- **With `--file` or `--paste`**:
  - Queries `marketstats` table for watchlist items (uses pre-calculated pricing)
  - Falls back to `marketorders` for non-watchlist items (calculates 5th percentile on-the-fly)
  - Looks up targets from `doctrine_fits` table by fit_name or ship_type_id
  - Uses SDE database for type name resolution
  - Fetches Jita prices for comparison
- **With `--fit-id`**:
  - Looks up fit metadata from `doctrine_fits` table (fit_name, ship_name, target, etc.)
  - Retrieves pre-calculated market data from `doctrines` table (fits_on_mkt, total_stock, price)
  - Uses cached data from the last backend run for faster results
  - Fetches Jita prices for comparison 

## Update-Fit Command ✓ IMPLEMENTED

The `update-fit` command provides comprehensive fit management with interactive and file-based workflows.

### Features Implemented:

**Input Options:**
- `--fit-file=<path>`: Path to EFT fit file (required)
- `--fit-id=<id>`: Fit ID to update (required if no --meta-file)
- `--meta-file=<path>`: Path to metadata JSON file (optional with --fit-id)
- `--interactive`: Prompt for metadata interactively (when no --meta-file)

**Market Targeting:**
- `--market=<alias>`: Target market (primary, deployment, both)
- `--primary`: Shorthand for --market=primary
- `--deployment`: Shorthand for --market=deployment
- `--both`: Update both primary and deployment markets

**Database Options:**
- `--remote`: Use remote database (default: local)
- `--no-clear`: Keep existing items (default: clear and replace)
- `--update-targets`: Update ship_targets table (default: skip)
- `--dry-run`: Preview changes without saving

**Automatic Features:**
- Auto-creates doctrines in `fittings_doctrine` if they don't exist
- Auto-adds new doctrines to `watch_doctrines`
- Handles FK constraints during upsert operations
- Updates `doctrine_fits` with `market_flag` for market assignment

### Usage Examples:
```bash
# Update fit with metadata file
mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=fits/hfi_meta.json

# Update fit with interactive prompts
mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive

# Update for deployment market
mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --deployment

# Update for both markets with ship targets
mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=meta.json --both --update-targets

# Preview changes (dry run)
mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive --dry-run
```

## Doctrine Market Assignment ✓ IMPLEMENTED
- Implemented via `market_flag` column in `doctrine_fits` table
- Values: "primary", "deployment", or "both"
- Set via `--market`, `--primary`, `--deployment`, or `--both` flags
- Frontend can read this flag to determine which fits to display for each market 

## Project Plan and Rules
- First, create a plan that divides the implementation into several phases. Extend this file with your plan, and use it to track progress.
- Write and execute tests prior to concluding each phase and document the work completed in this file. Include any information that a fresh instance of Claude will need to begin the next phase. 
- Use sub-agents to make your work more efficient and preserve your context window. Deploy them concurrently when appropriate to allow faster progress. 
- Call the documentation sub-agent as features are completed to update user and LLM documentation as features are completed. Check documentation at the end of each phase to see if any changes should be documented. 
- IMPORTANT: Avoid complexity. Ensure that new features are implemented with simple solutions that are understandable and do not add unnecessary complexity. 
- If an existing function is modified, be sure to write tests to confirm that 1) it continues to work properly after any changes and 2) that the new functionality works properly.
