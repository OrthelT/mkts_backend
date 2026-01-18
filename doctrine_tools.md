# Doctrine Tools CLI

This document tracks the development of doctrine management tools for the mkts_backend system.

## Fit Check ✓ IMPLEMENTED

A command-line interface that displays market availability for ship fittings from EFT-formatted files.

### Features Implemented:
- **Input Methods**: Accepts EFT-formatted .txt files via `--file` parameter or stdin via `--paste`
- **Market Selection**: Takes market as argument (`--market=primary` or `--market=deployment`)
- **Rich Table Display**: Uses Rich library for beautiful console output with the following columns:
  - `type_id`: Item type ID
  - `type_name`: Item name
  - `market_stock`: Current market inventory (total_volume_remain)
  - `fit_qty`: Quantity required per fit
  - `fits`: Number of complete fits available (market_stock / fit_qty)
  - `price`: Market price (5th percentile from marketstats)
  - `fit_cost`: Cost for this item in one fit (fit_qty × price)
  - `avg_price`: 30-day average price
  - `qty_needed`: Quantity needed to meet target (only shown when target available)
- **Header Information**: Displays fit name, ship name, ship type ID, total fit cost, fits available (bottleneck), and target quantity
- **Target Integration**: Automatically looks up target quantities from `doctrine_fits` table
- **Target Override**: `--target=N` parameter to override database target
- **Fallback Pricing**: For items not on watchlist, queries `marketorders` table and calculates 5th percentile pricing
- **Missing Items Report**: Shows items below target with quantity needed
- **Export Options** (`--output=<format>`):
  - `csv`: Export table to CSV file (auto-named from fit)
  - `multibuy`: Display Eve Multi-buy/jEveAssets stockpile format for items below target
  - `markdown`: Discord-friendly markdown format with bold formatting

### CLI Usage:
```bash
# Basic usage
fit-check --file=<path>

# With market selection
fit-check --file=<path> --market=deployment

# Override target quantity
fit-check --file=<path> --target=50

# Export to CSV
fit-check --file=<path> --output=csv

# Show multibuy format for restocking
fit-check --file=<path> --output=multibuy

# Export markdown for Discord
fit-check --file=<path> --output=markdown

# Read from stdin
cat fit.txt | fit-check --paste
```

### Database Integration:
- Queries `marketstats` table for watchlist items
- Falls back to `marketorders` for non-watchlist items
- Looks up targets from `doctrine_fits` table by fit_name or ship_type_id
- Uses SDE database for type name resolution 

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
