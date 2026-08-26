from rich.console import Console

console = Console()
"""
CLI help commands.

This module contains functions for displaying help messages for the CLI commands.
"""

def _build_command_list() -> str:
    """Auto-generate the command list from the registry."""
    from mkts_backend.cli_tools.command_registry import get_registry

    reg = get_registry()
    lines = []
    for entry in reg.all_commands():
        name = entry.name
        aliases = f" ({', '.join(entry.aliases)})" if entry.aliases else ""
        lines.append(f"  {name:<20s}{entry.description}{aliases}")
    lines.sort()
    return "\n".join(lines)


def display_cli_help():
    console.print("\nUsage: mkts-backend [command] [options]\n")
    console.print(f"Commands:\n{_build_command_list()}")
    console.print("""
Global Options (accepted by most commands):
  --market=<alias>   Select market (primary, deployment, market3, all).
                     update-markets and sync default to all; other commands
                     default to primary unless noted.
  --primary          Shorthand for --market=primary
  --deployment       Shorthand for --market=deployment
  --all              Shorthand for --market=all (every configured market)
  --env=<env>        Override app.environment temporarily (production, development)
  --history          Include history processing (update-markets only)
  --check_tables     Check the tables in the database (supports --market)
  --validate-env     Validate environment credentials and exit
  --list-markets     List available market configurations
  --list-db-paths    Print all routed database paths (alias<TAB>file)
  --db-path=<name>   Print path for one database (by alias or market name)
  --help             Show this help message

Use 'mkts-backend <command> --help' for more information about a command.

Examples:
  mkts-backend update-markets                 # Run full pipeline for all markets
  mkts-backend update-markets --history       # With history processing
  mkts-backend update-markets --primary       # Primary market only
  mkts-backend sync                           # Pull every routed replica: all
                                               #   markets + shared sde/fittings/buildcost
                                               #   (excludes the dev/test DB)
  mkts-backend sync --deployment              # Sync deployment market only
  mkts-backend sync --markets-only            # Markets only, skip shared databases
  mkts-backend sync --no-buildcost            # Skip the optional buildcost replica
  mkts-backend sync --include-testing         # Also pull the dev/test database
  mkts-backend validate --market=all          # Validate all databases
  mkts-backend fit-check --file=fits/hfi.txt  # Check fit availability
  mkts-backend assets --name='Damage Control'   # Look up assets by partial name
  mkts-backend assets --id=11379                # Look up assets by type ID
  mkts-backend equiv list                       # List all module equivalence groups
  mkts-backend fit-update list-fits           # List all doctrine fits
  mkts-backend add_watchlist --type_id=12345,67890,11111 # Add items to watchlist
  mkts-backend add_structure --dry-run        # Preview structures import from sheet
  mkts-backend add_structure                  # Import structures (remote + local, with confirm)
  mkts-backend add_structure --local --yes    # Local-only import, skip confirm
""")

def display_builder_cost_help():
    console.print("[bold][cyan]update-builder-costs:[/bold][/cyan] Refresh manufacturing costs in buildcost.db")
    console.print("[bold][green]Usage:[/bold][/green] mkts-backend update-builder-costs")


def display_build_watchlist_help():
    """Top-level help for the build-watchlist subcommand."""
    console.print("\n[bold cyan]build-watchlist[/bold cyan] - Manage the [bold]build_watchlist[/bold] table in buildcost.db\n")
    console.print("[bold green]USAGE:[/bold green]")
    console.print("    mkts-backend build-watchlist <subcommand> \\[options]\n")
    console.print("[bold green]SUBCOMMANDS:[/bold green]")
    console.print("    [bold]add[/bold]      Write items to build_watchlist after looking up SDE metadata")
    console.print("    [bold]remove[/bold]   Delete items from build_watchlist")
    console.print("    [bold]mirror[/bold]   Reconcile build_watchlist against wcmktprod.watchlist")
    console.print("             (pulls missing buildable items; never removes anything)")
    console.print("    [bold]sync[/bold]     Pull the buildcost.db local mirror from the remote (db.sync())\n")
    console.print("[bold green]VERB DISTINCTION:[/bold green]")
    console.print(
        "    [bold]mirror[/bold] and [bold]sync[/bold] are deliberately separate verbs. "
        "[bold]mirror[/bold] is the\n"
        "    wcmktprod-into-buildcost reconciliation; [bold]sync[/bold] is the libsql remote→local\n"
        "    pull, matching how 'sync' is used elsewhere in the CLI.\n"
    )
    console.print("[bold green]EXAMPLES:[/bold green]")
    console.print("    mkts-backend build-watchlist add --type_id=12345,67890")
    console.print("    mkts-backend build-watchlist add --file=items.csv")
    console.print("    mkts-backend build-watchlist add --paste --force")
    console.print("    mkts-backend build-watchlist remove --type_id=12345")
    console.print("    mkts-backend build-watchlist mirror")
    console.print("    mkts-backend build-watchlist sync\n")
    console.print(
        "Use [bold]'mkts-backend build-watchlist <subcommand> --help'[/bold] for subcommand details.\n"
    )


def display_build_watchlist_add_help():
    console.print("\n[bold cyan]build-watchlist add[/bold cyan] - Add items to build_watchlist\n")
    console.print("[bold green]USAGE:[/bold green]")
    console.print("    mkts-backend build-watchlist add --type_id=<ids> \\[--force] \\[--no-sync]")
    console.print("    mkts-backend build-watchlist add --file=<path>   \\[--force] \\[--no-sync]")
    console.print("    mkts-backend build-watchlist add --paste         \\[--force] \\[--no-sync]\n")
    console.print("[bold green]OPTIONS:[/bold green]")
    console.print("    [bold]--type_id=<ids>[/bold]   Comma-separated type IDs to add")
    console.print("    [bold]--file=<path>[/bold]     CSV file with a 'type_ids' or 'type_id' column")
    console.print("    [bold]--paste[/bold]           Read item names/IDs from stdin (one per line)")
    console.print(
        "    [bold]--force[/bold]           Skip the buildable filter — add items even if they\n"
        "                      have no manufacturing blueprint in the SDE\n"
        "                      [yellow](EverRef will likely reject them on the next fetch)[/yellow]"
    )
    console.print(
        "    [bold]--no-sync[/bold]         Skip the local mirror pull after the write\n"
        "                      (use for batch adds; sync once at the end)"
    )
    console.print("    [bold]--help[/bold]            Show this help\n")
    console.print("[bold green]BEHAVIOR:[/bold green]")
    console.print(
        "    Looks up type_name, group_name, category_id from [bold]sdetypes[/bold]. By default,\n"
        "    items without a manufacturing blueprint ([bold]industryActivityProducts[/bold]) are\n"
        "    skipped. After a successful write, the local buildcost mirror is pulled\n"
        "    so subsequent local reads see the new rows.\n"
    )


def display_build_watchlist_remove_help():
    console.print("\n[bold cyan]build-watchlist remove[/bold cyan] - Delete items from build_watchlist\n")
    console.print("[bold green]USAGE:[/bold green]")
    console.print("    mkts-backend build-watchlist remove --type_id=<ids> \\[--no-sync]")
    console.print("    mkts-backend build-watchlist remove --file=<path>   \\[--no-sync]")
    console.print("    mkts-backend build-watchlist remove --paste         \\[--no-sync]\n")
    console.print("[bold green]OPTIONS:[/bold green]")
    console.print("    [bold]--type_id=<ids>[/bold]   Comma-separated type IDs to remove")
    console.print("    [bold]--file=<path>[/bold]     CSV file with a 'type_ids' or 'type_id' column")
    console.print("    [bold]--paste[/bold]           Read item names/IDs from stdin (one per line)")
    console.print("    [bold]--no-sync[/bold]         Skip the local mirror pull after the write")
    console.print("    [bold]--help[/bold]            Show this help\n")
    console.print("[bold green]BEHAVIOR:[/bold green]")
    console.print(
        "    Idempotent — type_ids that aren't present in build_watchlist are reported\n"
        "    in the summary but not treated as errors. After a successful delete, the\n"
        "    local buildcost mirror is pulled.\n"
    )


def display_build_watchlist_mirror_help():
    console.print(
        "\n[bold cyan]build-watchlist mirror[/bold cyan] - Reconcile build_watchlist against wcmktprod.watchlist\n"
    )
    console.print("[bold green]USAGE:[/bold green]")
    console.print("    mkts-backend build-watchlist mirror \\[--no-sync]\n")
    console.print("[bold green]OPTIONS:[/bold green]")
    console.print("    [bold]--no-sync[/bold]   Skip the local buildcost mirror pull after the write")
    console.print("    [bold]--help[/bold]      Show this help\n")
    console.print("[bold green]BEHAVIOR:[/bold green]")
    console.print(
        "    Pre-syncs buildcost and primary local mirrors, computes the set diff\n"
        "    against [bold]wcmktprod.watchlist[/bold], applies the buildable filter, and upserts\n"
        "    only the missing buildable items. [yellow]Never removes anything[/yellow] from\n"
        "    build_watchlist (use [bold]remove[/bold] for that).\n"
    )


def display_build_watchlist_sync_help():
    console.print(
        "\n[bold cyan]build-watchlist sync[/bold cyan] - Pull buildcost.db local mirror from remote\n"
    )
    console.print("[bold green]USAGE:[/bold green]")
    console.print("    mkts-backend build-watchlist sync\n")
    console.print("[bold green]BEHAVIOR:[/bold green]")
    console.print(
        "    Thin wrapper around [bold]DatabaseConfig('buildcost').sync()[/bold]. Use this when\n"
        "    another process or machine has written to the buildcost remote since\n"
        "    your last local read, and [bold]'build-watchlist add|remove'[/bold] didn't already\n"
        "    auto-sync (e.g. you used [bold]--no-sync[/bold], or the writer was outside this CLI).\n"
    )

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
    remove           Remove a fit from ALL doctrines and targets
    assign-market    Change the market assignment for an existing fit
    unassign-market  Remove a fit or doctrine from a specific market
    list-fits        List all fits in the doctrine tracking system

    Target Management:
    update-target    Update the target quantity for a fit
    update-lead-ship Set or change the lead ship for a doctrine

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
    --market=<flag>      Market flag: primary, deployment, all
    --interactive        Use interactive prompts for metadata
    --dry-run            Preview changes without saving
    --remote             Use remote database
    --local-only         Use local database only
    --db-alias=<alias>   Target database alias (a database_alias from
                         settings.toml [markets.*]; default: primary market)
    --north              Shorthand for the deployment market's database
    --name=<name>        Friendly display name (for update-friendly-name)
    --doctrine-id=<id>   Doctrine ID (for unassign-market, update-friendly-name, etc.)
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

    # Remove fit from all doctrines (primary market)
    mkts-backend fit-update remove --fit-id=123

    # Remove fit from deployment market
    mkts-backend fit-update remove --fit-id=123 --market=deployment

    # Assign fit to deployment market
    mkts-backend fit-update assign-market --fit-id=123 --market=deployment

    # Remove a single fit from deployment market
    mkts-backend fit-update unassign-market --fit-id=123 --market=deployment

    # Remove entire doctrine from deployment market
    mkts-backend fit-update unassign-market --doctrine-id=21 --market=deployment

    # Remove doctrine from all markets (requires confirmation)
    mkts-backend fit-update unassign-market --doctrine-id=21 --market=all

    # Update target for fit
    mkts-backend fit-update update --fit-id=550 --target=300

    # Set or change the lead ship for a doctrine
    mkts-backend fit-update update-lead-ship --doctrine-id=21 --fit-id=550

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
        --market=<alias>     Target market: primary, deployment, all
        --primary            Shorthand for --market=primary
        --deployment         Shorthand for --market=deployment
        --all                Update all configured markets

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

        # Update fit for all markets with ship targets
        mkts-backend update-fit --fit-file=fits/hfi.txt --meta-file=meta.json --all --update-targets

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
    --market=<flag>      Market flag: primary, deployment, all (default: primary)
    --remote             Use remote database (default: local)
    --local-only         Use local database only (default: no)
    --db-alias=<alias>   Target database alias (default: primary market)
    --north              Shorthand for the deployment market's database
    --primary            Shorthand for --market=primary
    """)
    console.print("""
    EXAMPLES:
    mkts-backend update-target --fit-id=123 --target=100 --market=primary
    mkts-backend update-target --fit-id=123 --target=100 --market=deployment
    mkts-backend update-target --fit-id=123 --target=100 --market=all

    DEFAULT:
    If no market flag is provided, the default is primary.
    If no remote flag is provided, the default is local.
    If no db-alias flag is provided, the default is the primary market's database.

    """)

if __name__ == "__main__":
    pass
