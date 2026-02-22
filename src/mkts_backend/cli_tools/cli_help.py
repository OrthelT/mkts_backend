from rich.console import Console

console = Console()
"""
CLI help commands.

This module contains functions for displaying help messages for the CLI commands.
"""

def display_cli_help():
    console.print("\nUsage: mkts-backend [command] [options]\n")
    console.print("""Commands:
  fit-check          Display market availability for an EFT fit file
  fit-update         Interactive tool for managing fits and doctrines
  update-fit         Process an EFT fit file and update doctrine tables
  add_watchlist      Add items to watchlist by type IDs
  parse-items        Parse Eve structure data and create CSV with pricing
  assets             Look up character assets by type ID or name
  equiv              Manage module equivalence groups (list, find, add, remove)
  esi-auth           Re-authorize ESI tokens with expanded scopes
  sync               Sync the database (supports --market/--deployment)
  validate           Validate the database (supports --market/--deployment)

Global Options (apply to main workflow and most commands):
  --market=<alias>   Select market (primary, deployment, both). Default: primary
  --primary          Shorthand for --market=primary
  --deployment       Shorthand for --market=deployment
  --both             Shorthand for --market=both (process both markets in sequence)
  --env=<env>        Override app.environment temporarily (production, development)
  --history          Include history processing (main workflow)
  --check_tables     Check the tables in the database (supports --market)
  --validate-env     Validate environment credentials and exit
  --list-markets     List available market configurations
  --help             Show this help message

Use 'mkts-backend <command> --help' for more information about a command.

Examples:
  mkts-backend --history                      # Run main workflow with history
  mkts-backend --history --deployment         # Run for deployment market
  mkts-backend --both --history               # Run both markets with history
  mkts-backend --market=both                  # Run both markets (no history)
  mkts-backend --env=development              # Run against testing database
  mkts-backend sync --both                    # Sync both databases
  mkts-backend sync --deployment              # Sync deployment database
  mkts-backend validate --market=deployment   # Validate deployment database
  mkts-backend fit-check --file=fits/hfi.txt  # Check fit availability
  mkts-backend assets --name='Damage Control'   # Look up assets by partial name
  mkts-backend assets --id=11379                # Look up assets by type ID
  mkts-backend equiv list                       # List all module equivalence groups
  mkts-backend fit-update list-fits           # List all doctrine fits
  mkts-backend add_watchlist --type_id=12345,67890,11111 # Add items to watchlist
""")


def display_fit_check_help():
    """Display help for the fit-check subcommand."""
    console.print("""
fit-check - Display market availability for items in an EFT-formatted ship fit

USAGE:
    mkts-backend fit-check --file=<path> [options]
    mkts-backend fit-check --paste [options]
    mkts-backend fit-check --fit-id=<id> [options]

DESCRIPTION:
    Analyzes an EFT (Eve Fitting Tool) formatted ship fit and displays market
    availability for each item. Shows how many complete fits can be built from
    current market stock, with color-coded status indicators.

    If the fit exists in the doctrine_fits table, the target quantity is
    automatically loaded and used to calculate items needed.

    When using --fit-id, the command retrieves pre-calculated market data from
    the doctrines table instead of querying live market data. This is useful
    for quickly checking the status of fits that have already been processed
    by the main backend workflow.

OPTIONS:
    --file=<path>        Path to EFT fit file
    --paste              Read EFT fit from stdin instead of file
    --fit-id=<id>        Look up fit by ID from doctrine_fits/doctrines tables
                         (uses pre-calculated market data)
    --market=<alias>     Market to check: primary, deployment (default: primary)
    --target=<N>         Override target quantity (default: from doctrine_fits)
    --output=<format>    Export format: csv, multibuy, or markdown
    --no-jita            Hide Jita price comparison columns
    --help               Show this help message

    Note: One of --file, --paste, or --fit-id is required.

OUTPUT:
    Header displays:
      - Ship name and type ID
      - Market being queried
      - Total fit cost (sum of all items at current prices)
      - Fits Available (minimum fits across all items - the bottleneck)
      - Target (from doctrine_fits table, if available)

    Table columns:
      - Type ID      Item's Eve Online type ID
      - Item Name    Name of the module/ship
      - Stock        Current market stock
      - Fit Qty      Quantity needed per fit
      - Fits         How many complete fits this item supports
      - Qty Needed   Items needed to reach target (only if target set)
      - Price        Current 5th percentile price
      - Fit Cost     Price × Fit Qty
      - Source       ✓ = marketstats/doctrines, * = fallback data

EXPORT FORMATS (--output):
    csv       Exports items below target to a CSV file (auto-named from fit)
    multibuy  Eve Multi-buy/jEveAssets stockpile format (ItemName qty)
    markdown  Discord-friendly markdown with bold formatting

EXAMPLES:
    # Basic fit check from EFT file
    mkts-backend fit-check --file=fits/hurricane_fleet.txt

    # Check fit by ID from doctrines table
    mkts-backend fit-check --fit-id=42

    # Check fit by ID against deployment market
    mkts-backend fit-check --fit-id=42 --market=deployment

    # Check against deployment market with EFT file
    mkts-backend fit-check --file=fits/hfi.txt --market=deployment

    # Override target to 50 and show multi-buy list
    mkts-backend fit-check --file=fits/hfi.txt --target=50 --output=multibuy

    # Export to CSV for spreadsheet analysis
    mkts-backend fit-check --fit-id=42 --output=csv

    # Export markdown for Discord
    mkts-backend fit-check --fit-id=42 --output=markdown

    # Paste fit directly (end with two blank lines or Ctrl+D)
    mkts-backend fit-check --paste --market=primary
""")


def display_fit_update_help():
    """Display help for the fit-update subcommand."""
    console.print("""
fit-update - Interactive tool for managing fits and doctrines

USAGE:
    mkts-backend fit-update <subcommand> [options]

SUBCOMMANDS:
    Fit Management:
    add              Add a NEW fit from an EFT file and assign to doctrine(s)
    update           Update an existing fit's items from an EFT file
    assign-market    Change the market assignment for an existing fit
    list-fits        List all fits in the doctrine tracking system

    Target Management:
    update-target    Update the target quantity for a fit

    Friendly Name Management:
    update-friendly-name      Set the friendly display name for a fit
    populate-friendly-names   Bulk populate friendly names from doctrine_names.json

    Doctrine Management:
    list-doctrines    List all available doctrines
    create-doctrine   Create a new doctrine (group of fits)
    doctrine-add-fit  Add existing fit(s) to a doctrine (supports multiple)
    doctrine-remove-fit Remove a fit from a doctrine

OPTIONS:
    --file=<path>        Path to EFT fit file (for add/update)
    --meta-file=<path>   Path to metadata JSON file
    --fit-id=<id>        Fit ID to update or modify (can be comma-separated)
    --market=<flag>      Market flag: primary, deployment, both
    --interactive        Use interactive prompts for metadata
    --dry-run            Preview changes without saving
    --remote             Use remote database
    --local-only         Use local database only
    --db-alias=<alias>   Target database: wcmkt, wcmktnorth
    --north              Shorthand for --db-alias=wcmktnorth
    --name=<name>        Friendly display name (for update-friendly-name)
    --doctrine-id=<id>   Doctrine ID (for update-friendly-name)
    --target=<qty>       Default target quantity for new fits (default: 100)
    --skip-targets       Preserve existing targets, skip target prompts
    --help               Show this help message

EXAMPLES:
    # List all fits and doctrines
    mkts-backend fit-update list-fits
    mkts-backend fit-update list-doctrines

    # Create a new doctrine (group of fits)
    mkts-backend fit-update create-doctrine

    # Add new fit interactively (prompts for doctrine assignment)
    mkts-backend fit-update add --file=fits/new_fit.txt --interactive

    # Add fit with metadata file
    mkts-backend fit-update add --file=fits/hfi.txt --meta-file=fits/hfi_meta.json

    # Add existing fit(s) to a doctrine (interactive, per-fit targets)
    mkts-backend fit-update doctrine-add-fit
    mkts-backend fit-update doctrine-add-fit --fit-id=123
    mkts-backend fit-update doctrine-add-fit --fit-id=123,456,789

    # Add fits without changing existing targets
    mkts-backend fit-update doctrine-add-fit --fit-id=123,456 --skip-targets

    # Add fits with a specific default target
    mkts-backend fit-update doctrine-add-fit --fit-id=123 --target=300

    # Update existing fit's items
    mkts-backend fit-update update --fit-id=123 --file=fits/updated.txt --meta-file=meta.json

    # Assign fit to deployment market
    mkts-backend fit-update assign-market --fit-id=123 --market=deployment

    # Update target for fit
    mkts-backend fit-update update --fit-id=550 --target=300

    # Set a doctrine's friendly name
    mkts-backend fit-update update-friendly-name --doctrine-id=21 --name="Hurricane"

    # Bulk populate friendly names from JSON (auto-syncs to remote)
    mkts-backend fit-update populate-friendly-names
    mkts-backend fit-update populate-friendly-names --north

WORKFLOW:
    1. Create a doctrine:     fit-update create-doctrine
    2. Add a new fit:         fit-update add --file=<eft> --interactive
       (you can create a doctrine inline during this step)
    3. Add existing fits:     fit-update doctrine-add-fit
       (prompts per-fit for targets, validates and skips duplicates)

NOTE: Targets are set per-fit, not per-doctrine. Use --skip-targets to preserve
existing targets when re-adding fits to doctrines.
""")


def display_update_fit_help():
    """Display help for the update-fit subcommand."""
    console.print("""
    update-fit - Process an EFT fit file and metadata to update doctrine tables

    USAGE:
        mkts-backend update-fit --fit-file=<path> [options]

    OPTIONS:
        --fit-file=<path>    Path to EFT fit file (required)
        --fit-id=<id>        Fit ID to update (required if no --meta-file)
        --meta-file=<path>   Path to metadata JSON file (optional with --fit-id)
        --interactive        Prompt for metadata interactively (when no --meta-file)

        Market Selection (default: primary):
        --market=<alias>     Target market: primary, deployment, both
        --primary            Shorthand for --market=primary
        --deployment         Shorthand for --market=deployment
        --both               Update both primary and deployment markets

        Database Options:
        --remote             Use remote database (default: local)
        --no-clear           Keep existing items (default: clear and replace)
        --update-targets     Update ship_targets table (default: skip)
        --dry-run            Preview changes without saving
        --help               Show this help message

    METADATA FILE FORMAT (JSON):
        {
        "fit_id": 313,
        "name": "Hurricane Fleet Issue - Arty",
        "description": "Standard doctrine fit",
        "doctrine_id": 42,        // or [42, 43] for multiple doctrines
        "target": 300
        }

    EXAMPLES:
        # Update fit with metadata file (original workflow)
        mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=fits/hfi_meta.json

        # Update fit by ID with interactive prompts
        mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive

        # Update fit for deployment market
        mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --deployment

        # Update fit for both markets with ship targets
        mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=meta.json --both --update-targets

        # Preview changes (dry run)
        mkts-backend update-fit --fit-file=fits/hfi.txt --fit-id=313 --interactive --dry-run
    """)


def display_update_target_help():
    """
    Display help for the update-target command.
    """
    console.print("""
    update-target - Update the target quantity for a fit.
    USAGE:
    mkts-backend update-target --fit-id=<id> --target=<qty>
    """)
    console.print("""
    Arguments:
    --fit-id=<id>        Fit ID to update (required)
    --target=<qty>       Target quantity (required)
    --market=<flag>      Market flag: primary, deployment, both (default: primary)
    --remote             Use remote database (default: local)
    --local-only         Use local database only (default: no)
    --db-alias=<alias>   Target database alias (default: wcmkt)
    --north              Shorthand for --db-alias=wcmktnorth
    --primary            Shorthand for --market=primary
    """)
    console.print("""
    EXAMPLES:
    mkts-backend update-target --fit-id=123 --target=100 --market=primary
    mkts-backend update-target --fit-id=123 --target=100 --market=deployment
    mkts-backend update-target --fit-id=123 --target=100 --market=both

    DEFAULT:
    If no market flag is provided, the default is primary.
    If no remote flag is provided, the default is local.
    If no db-alias flag is provided, the default is wcmkt.

    """)

if __name__ == "__main__":
    pass
