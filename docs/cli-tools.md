
# CLI Tools Documentation


## fitcheck Command

The fitcheck command displays market availability and pricing for ship fittings from EFT-formatted files or from pre-calculated doctrine data.

**Basic Fit Checking:**
```bash
# Check fit availability against primary market from EFT file
uv run fitcheck --file=path/to/fit.txt

# Check fit by ID from doctrine_fits/doctrines tables (pre-calculated data)
uv run fitcheck --fit=42

# Check fit against specific market
uv run fitcheck --fit=42 --market=deployment

# Check against specific market with EFT file
uv run fitcheck --file=fit.txt --market=deployment

# Override target quantity
uv run fitcheck --file=fit.txt --target=50

# Export to CSV
uv run fitcheck --fit=42 --output=csv

# Show multibuy format for restocking
uv run fitcheck --file=fit.txt --output=multibuy

# Export markdown for Discord
uv run fitcheck --fit=42 --output=markdown

# Combine options
uv run fitcheck --file=fit.txt --market=deployment --target=100 --output=csv
```

**Subcommand: needed** - Show all items needed to reach ship targets:
```bash
# Show all items needed across all fits
uv run fitcheck needed

# Show needed items for a specific ship
uv run fitcheck needed --ship=Maelstrom

# Show needed items for fits below 50% of target
uv run fitcheck needed --target=0.5

# Filter by fit ID
uv run fitcheck needed --fit=550

# Check deployment market
uv run fitcheck needed --market=deployment

# Show per-character assets (cached for 1 hour)
uv run fitcheck needed --assets

# Force re-fetch assets from ESI (bypass cache)
uv run fitcheck needed --assets --refresh
```

The `needed` subcommand displays a comprehensive overview of items needed for restocking across all tracked fits. Results are grouped by fit with Rich sub-tables showing:
- Item name and type ID
- Current stock levels
- Fits available on market
- Target percentage achieved
- Quantity needed to reach target

**Subcommand: module** - Show which fits use a given module:
```bash
# Check module usage by type ID
uv run fitcheck module --id=11269

# Check module usage by name (exact or partial match)
uv run fitcheck module --name="Multispectrum Energized Membrane II"

# Check both markets simultaneously for comparison
uv run fitcheck module --id=11269 --market=both
```

The `module` subcommand helps identify which doctrine fits use a specific module and shows their market status. Useful for:
- Planning bulk purchases of common modules
- Identifying fits affected by module shortages
- Comparing module availability across markets

**Subcommand: list-fits** - List all tracked doctrine fits:
```bash
# List all fits in primary market
uv run fitcheck list-fits

# List fits in deployment market
uv run fitcheck list-fits --market=deployment
```

## update-fit Command

The update-fit command processes EFT fit files (or pasted EFT text) and updates doctrine tables across multiple databases. It supports file-based input, paste mode (multiline prompt), and interactive metadata input, with flexible market targeting.

**Basic Usage:**
```bash
# Update fit with metadata file (traditional workflow)
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=fits/hfi_meta.json

# Update fit by ID with interactive prompts
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive

# Update fit for deployment market
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive --deployment

# Update fit for both markets
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=meta.json --both

# Update fit with ship_targets table update
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive --update-targets

# Preview changes without saving (dry run)
uv run mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive --dry-run

# Paste EFT text directly (opens multiline prompt instead of reading a file)
uv run mkts-backend fit-update add --paste --interactive
uv run mkts-backend fit-update update --fit-id=313 --paste
```

**Command Options:**
- `--fit-file=<path>`: Path to EFT fit file (optional when using --paste)
- `--fit-id=<id>`: Fit ID to update (required if no --meta-file)
- `--meta-file=<path>`: Path to metadata JSON file (optional with --fit-id)
- `--paste`: Open a multiline prompt to paste EFT fit text directly (uses prompt_toolkit)
- `--interactive`: Prompt for metadata interactively (when no --meta-file)
- `--market=<alias>`: Target market (primary, deployment, both)
- `--primary`: Shorthand for --market=primary
- `--deployment`: Shorthand for --market=deployment
- `--both`: Update both primary and deployment markets
- `--update-targets`: Update ship_targets table (default: skip)
- `--remote`: Use remote database (default: local)
- `--no-clear`: Keep existing items (default: clear and replace)
- `--dry-run`: Preview changes without saving

**Metadata File Format (JSON):**
```json
{
  "fit_id": 313,
  "name": "Hurricane Fleet Issue - Arty",
  "description": "Standard doctrine fit",
  "doctrine_id": 42,
  "target": 300
}
```

**update-friendly-name Subcommand:**
```bash
# Set a friendly display name for all fits in a doctrine
uv run mkts-backend fit-update update-friendly-name --doctrine-id=21 --name="Hurricane"

# Target deployment market
uv run mkts-backend fit-update update-friendly-name --doctrine-id=21 --name="Hurricane" --north
```

Pushes the `friendly_name` value to both local and remote databases automatically.

**populate-friendly-names Subcommand:**
```bash
# Bulk populate from doctrine_names.json in working directory
uv run mkts-backend fit-update populate-friendly-names

# Target deployment database
uv run mkts-backend fit-update populate-friendly-names --north
```

Reads a `doctrine_names.json` file and updates `friendly_name` for all matching doctrines, then syncs to remote.

**Database Tables Updated:**
- **wcfitting.db:**
  - `fittings_doctrine` - doctrine records (auto-created if missing)
  - `fittings_fitting` - fit shell records
  - `fittings_fittingitem` - fit items
  - `fittings_doctrine_fittings` - doctrine-fit links
  - `watch_doctrines` - watched doctrines (auto-added for new doctrines)

- **wcmktprod.db / wcmktnorth2.db (based on --market flag):**
  - `doctrine_fits` - fit metadata with market_flag and friendly_name
  - `doctrine_map` - doctrine-fit links
  - `watchlist` - items to track
  - `ship_targets` (optional with --update-targets)
  - `doctrines` (optional with --update-targets)

**Input Modes (fitcheck):**
- `--file=<path>`: Parse an EFT-formatted fit file and query live market data
- `--fit=<id>`: Look up fit by ID from `doctrine_fits` table and display pre-calculated market data from `doctrines` table
- `--paste`: Open a multiline prompt to paste EFT fit text directly (uses prompt_toolkit); reads from stdin when invoked via `mkts-backend fit-check`

**Display Features:**
- **Header Section**: Shows fit name, ship name, ship type ID, total fit cost, fits available (bottleneck), and target quantity
- **Market Data Table**: Displays for each item:
  - `type_id`: Item type ID
  - `type_name`: Item name
  - `market_stock`: Current inventory on market
  - `fit_qty`: Quantity required per fit
  - `fits`: Number of complete fits available (bottleneck highlighted)
  - `price`: Market price (5th percentile from marketstats)
  - `fit_cost`: Total cost for this item in one fit
  - `avg_price`: 30-day average price
  - `qty_needed`: Quantity needed to meet target (only shown when target available)
- **Summary Section**: Shows item availability counts and missing items
- **Missing Items for Target**: Lists items below target with quantities needed

**Target Integration:**
- Automatically looks up target quantities from `doctrine_fits` table by fit_name or ship_type_id
- Use `--target=N` to override the database target
- Displays "Qty Needed" column when target is available
- Shows missing items list with quantities needed to reach target

**Export Options (`--output=<format>`):**
- `csv`: Exports the fit status table to CSV file for spreadsheet analysis (auto-named from fit)
- `multibuy`: Displays items below target in Eve Multi-buy/jEveAssets stockpile format:
  ```
  Damage Control II 15
  Gyrostabilizer II 30
  Large Shield Extender II 20
  ```
  This format can be copied directly into Eve Online or jEveAssets for easy restocking.
- `markdown`: Discord-friendly markdown format with bold formatting for sharing fit status:
  ```markdown
  # Hurricane Fleet Issue
  Target (**300**); Fits (**245**)

  - **Damage Control II**: 165 needed (current: 245.0 fits)
  - **Gyrostabilizer II**: 330 needed (current: 245.0 fits)
  ```

**Database Integration:**
- With `--file` or `--paste`:
  - Queries `marketstats` table for items on watchlist (uses pre-calculated pricing)
  - Falls back to `marketorders` table for non-watchlist items (calculates 5th percentile on-the-fly)
  - Looks up targets from `doctrine_fits` table
- With `--fit-id`:
  - Looks up fit metadata from `doctrine_fits` table (fit_name, ship_name, target, etc.)
  - Retrieves pre-calculated market data from `doctrines` table (fits_on_mkt, total_stock, price)
  - Uses cached data from the last backend run for faster results
- Uses SDE database for type name resolution when needed
- Fetches Jita prices for comparison in both modes

**Implementation Details:**
- Location: `/home/orthel/workspace/github/mkts_backend/src/mkts_backend/cli_tools/fit_check.py`
- Uses Rich library for beautiful console output with tables, panels, and color coding
- Handles missing items gracefully with fallback pricing
- Supports file input (`--file`), stdin (`--paste`), and doctrine lookup (`--fit=<id>`)
- Three main subcommands: `needed`, `module`, and `list-fits`
- Fetches Jita prices for comparison and highlights overpriced items (>120% Jita)

## update-fit Subcommands

The `update-fit` command supports multiple subcommands for managing fits and doctrines:

### Available Subcommands:
- `add` - Add a NEW fit from an EFT file or pasted text and assign to doctrine(s)
- `update` - Update an existing fit's items from an EFT file or pasted text
- `assign-market` - Change the market assignment for an existing fit
- `list-fits` - List all fits in the doctrine tracking system (includes `friendly_name` column)
- `list-doctrines` - List all available doctrines
- `create-doctrine` - Create a new doctrine (group of fits)
- `doctrine-add-fit` - Add existing fit(s) to a doctrine (supports multiple)
- `doctrine-remove-fit` - Remove fit(s) from a doctrine (supports multiple)
- `update-target` - Update the target quantity for a fit
- `update-friendly-name` - Set the friendly display name for all fits in a doctrine
- `populate-friendly-names` - Bulk populate friendly names from `doctrine_names.json`

### doctrine-add-fit Subcommand

Add existing fits that are already in the fittings database to a doctrine for tracking.

```bash
# Interactive mode (recommended) - prompts per-fit for targets
uv run mkts-backend update-fit doctrine-add-fit

# Non-interactive with fit ID
uv run mkts-backend update-fit doctrine-add-fit --doctrine-id=42 --fit-id=313

# Add multiple fits at once (comma-separated)
uv run mkts-backend update-fit doctrine-add-fit --doctrine-id=42 --fit-ids=313,314,315

# Specify default target and market (target applies to new fits only)
uv run mkts-backend update-fit doctrine-add-fit --doctrine-id=42 --fit-id=313 --target=300 --market=primary

# Preserve existing targets (don't prompt or update targets)
uv run mkts-backend update-fit doctrine-add-fit --doctrine-id=42 --fit-ids=313,314,315 --skip-targets
```

**Features:**
- Interactive prompts guide you through doctrine and fit selection
- **Per-fit target setting**: Each fit can have a different target quantity (e.g., 300 Muninns, 50 Huginns)
- Shows existing targets for fits that already have them
- `--skip-targets` preserves existing targets and skips prompts
- Supports adding multiple fits at once (comma-separated IDs)
- Validates fit IDs exist in fittings database
- Skips fits already in the doctrine
- Sets up tracking in both fittings and market databases
- Links fits to doctrines in `fittings_doctrine_fittings` table
- Adds entries to `doctrine_fits`, `doctrine_map`, and `doctrines` tables

### doctrine-remove-fit Subcommand

Remove fits from a doctrine (reverse operation of `doctrine-add-fit`). This unlinks fits from a doctrine but does NOT delete the fit itself.

```bash
# Interactive mode (recommended)
uv run mkts-backend update-fit doctrine-remove-fit

# Non-interactive with fit ID
uv run mkts-backend update-fit doctrine-remove-fit --doctrine-id=42 --fit-id=313

# Remove multiple fits at once (comma-separated)
uv run mkts-backend update-fit doctrine-remove-fit --doctrine-id=42 --fit-ids=313,314,315

# Use remote database
uv run mkts-backend update-fit doctrine-remove-fit --doctrine-id=42 --fit-id=313 --remote
```

**Features:**
- Interactive prompts display current fits in the doctrine
- Supports removing multiple fits at once (comma-separated IDs)
- Validates fit IDs are actually in the doctrine
- Removes tracking from both fittings and market databases
- Removes entries from `fittings_doctrine_fittings`, `doctrine_fits`, `doctrine_map`, and `doctrines` tables
- Safe operation: the fit itself remains in the fittings database

**Databases Affected:**
- `wcfitting.db`: Removes link in `fittings_doctrine_fittings`
- `wcmktprod.db` or `wcmktnorth2.db` (based on market): Removes entries from `doctrine_fits`, `doctrine_map`, and `doctrines`

### update-target Subcommand

Update the target quantity for an existing fit.

```bash
# Update target for a fit on primary market
uv run mkts-backend update-target --fit-id=313 --target=300

# Update target for deployment market
uv run mkts-backend update-target --fit-id=313 --target=300 --market=deployment
```

**Features:**
- Updates target in both `ship_targets` and `doctrine_fits` tables
- Shows the previous and new target values
- Validates the fit exists in the specified database before updating

## equiv Command

The `equiv` command manages module equivalence groups — sets of faction modules that are functionally identical and can substitute for each other in doctrine calculations.

```bash
# List all equivalence groups
uv run mkts-backend equiv list

# Find equivalent modules by type ID or name (uses attribute fingerprinting)
uv run mkts-backend equiv find 13984
uv run mkts-backend equiv find "Thermal Armor Hardener"

# Find equivalents and automatically add them as a group
uv run mkts-backend equiv find 13984 --add

# Create a new equivalence group manually with specific type IDs
uv run mkts-backend equiv add --type-ids=13984,17838,15705,28528,14065,13982

# Remove an equivalence group by group ID
uv run mkts-backend equiv remove --id=1

# Target a single market (default: all markets)
uv run mkts-backend equiv add --type-ids=13984,17838 --market=primary
```

**Notes:**
- `add` and `remove` operate on **all markets by default** (equivalents are universal game data)
- `find` uses SDE attribute fingerprinting (`dgmTypeAttributes`) to discover identical modules
- Multiple name matches show a selection table; use `--type-id=<id>` to disambiguate
- After changes, run `uv run mkts-backend sync` to push updates to Turso

**Subcommands:**
- `list` - Display all equivalence groups with member modules
- `find <type_id|name> [--add]` - Auto-discover equivalent modules by attribute matching
- `add --type-ids=<ids>` - Create a new group from comma-separated type IDs
- `remove --id=<group_id>` - Remove all members from a group

