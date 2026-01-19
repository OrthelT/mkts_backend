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

## Fit Update Tool
Extend the update_fit_workflow() in parse_fits.py with an interactive interface that:
- allows a user to add a new fit from an EFT formatted txt file or by pasting text in the cli (if possible). Reuse the functionality from Fit Check. It should update all appropriate database tables with the new fit information.
- allows the user to create a new doctrine and choosing the fits that will be used with it interactively.
- allows the user to input the fitting metadata in an interactive interface in the CLI or read it from a fit_metadata file. 
- allows the user to update an existing fit from an EFT formatted fitting or interactively change elements of a fit. 
- allows the user to assign the market that a new or existing fit will be assigned to. 
- confirms the changes before committing them to the database.
- there should be dry-run and local-only options for testing. 

## Doctrine Market Assignment
- Add functionality to configure which markets a doctrine will be tracked in: primary, secondary, or both.
- This can be implemented with a simple flag in the doctrine_fits that can be read by the front end when determining which fits to display. 

## Project Plan and Rules
- First, create a plan that divides the implementation into several phases. Extend this file with your plan, and use it to track progress.
- Write and execute tests prior to concluding each phase and document the work completed in this file. Include any information that a fresh instance of Claude will need to begin the next phase. 
- Use sub-agents to make your work more efficient and preserve your context window. Deploy them concurrently when appropriate to allow faster progress. 
- Call the documentation sub-agent as features are completed to update user and LLM documentation as features are completed. Check documentation at the end of each phase to see if any changes should be documented. 
- IMPORTANT: Avoid complexity. Ensure that new features are implemented with simple solutions that are understandable and do not add unnecessary complexity. 
- If an existing function is modified, be sure to write tests to confirm that 1) it continues to work properly after any changes and 2) that the new functionality works properly.
