"""
Fit Update CLI commands.

Interactive tools for managing fits and doctrines:
- add: Add a new fit with optional interactive metadata prompts
- update: Update an existing fit
- assign-market: Assign market flags to fits
- list-fits: List all fits
- list-doctrines: List all doctrines
- create-doctrine: Create a new doctrine
"""

from typing import List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich import box
from sqlalchemy import text

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config import DatabaseConfig
from mkts_backend.utils.eft_parser import parse_eft_file, FitParseResult
from mkts_backend.utils.doctrine_update import (
    update_fit_market_flag,
    get_fit_market_flag,
)
from mkts_backend.utils.parse_fits import update_fit_workflow, parse_fit_metadata, FitMetadata
from mkts_backend.cli_tools.fit_check import display_fit_status

logger = configure_logging(__name__)
console = Console()


def get_available_doctrines(remote: bool = False) -> List[dict]:
    """Get list of available doctrines from fittings database."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    doctrines = []
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, name, description FROM fittings_doctrine ORDER BY name"))
        for row in result:
            doctrines.append({
                "id": row[0],
                "name": row[1],
                "description": row[2] or "",
            })

    engine.dispose()
    return doctrines


def get_fits_list(db_alias: str = "wcmkt", remote: bool = False) -> List[dict]:
    """Get list of fits from doctrine_fits table."""
    db = DatabaseConfig(db_alias)
    engine = db.remote_engine if remote else db.engine

    fits = []
    with engine.connect() as conn:
        # Check if market_flag column exists
        try:
            result = conn.execute(text("""
                SELECT fit_id, fit_name, ship_name, doctrine_name, target, market_flag
                FROM doctrine_fits
                ORDER BY doctrine_name, fit_name
            """))
            has_market_flag = True
        except Exception:
            # Fallback query without market_flag
            result = conn.execute(text("""
                SELECT fit_id, fit_name, ship_name, doctrine_name, target
                FROM doctrine_fits
                ORDER BY doctrine_name, fit_name
            """))
            has_market_flag = False

        for row in result:
            fits.append({
                "fit_id": row[0],
                "fit_name": row[1],
                "ship_name": row[2],
                "doctrine_name": row[3],
                "target": row[4],
                "market_flag": row[5] if has_market_flag and len(row) > 5 else "primary",
            })

    engine.dispose()
    return fits


def display_fits_table(fits: List[dict]) -> None:
    """Display fits in a Rich table."""
    table = Table(
        title="[bold cyan]Doctrine Fits[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )

    table.add_column("Fit ID", style="dim", justify="right", width=8)
    table.add_column("Fit Name", style="white", min_width=30)
    table.add_column("Ship", style="cyan", min_width=20)
    table.add_column("Doctrine", style="green", min_width=20)
    table.add_column("Target", justify="right", width=8)
    table.add_column("Market", justify="center", width=12)

    for fit in fits:
        market_style = {
            "primary": "green",
            "deployment": "yellow",
            "both": "blue",
        }.get(fit["market_flag"], "white")

        table.add_row(
            str(fit["fit_id"]),
            fit["fit_name"],
            fit["ship_name"],
            fit["doctrine_name"],
            str(fit["target"]),
            f"[{market_style}]{fit['market_flag']}[/{market_style}]",
        )

    console.print(table)


def display_doctrines_table(doctrines: List[dict]) -> None:
    """Display doctrines in a Rich table."""
    table = Table(
        title="[bold cyan]Available Doctrines[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )

    table.add_column("ID", style="dim", justify="right", width=6)
    table.add_column("Name", style="white", min_width=30)
    table.add_column("Description", style="dim", min_width=40)

    for doctrine in doctrines:
        desc = doctrine["description"][:50] + "..." if len(doctrine["description"]) > 50 else doctrine["description"]
        table.add_row(
            str(doctrine["id"]),
            doctrine["name"],
            desc,
        )

    console.print(table)


def interactive_add_fit(
    fit_file: str,
    remote: bool = False,
    dry_run: bool = False,
    target_alias: str = "wcmkt",
    market_flag: str = "primary",
) -> bool:
    """
    Interactively add a new fit with prompts for metadata.

    Args:
        fit_file: Path to EFT fit file
        remote: Use remote database
        dry_run: Preview without committing
        target_alias: Target database alias
        market_flag: Market assignment

    Returns:
        True if successful
    """
    # Parse the fit file first
    try:
        parse_result = parse_eft_file(fit_file)
    except FileNotFoundError:
        console.print(f"[red]Error: File not found: {fit_file}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Error parsing fit: {e}[/red]")
        return False

    # Display parsed fit info
    console.print(Panel(
        f"[bold]Ship:[/bold] {parse_result.ship_name}\n"
        f"[bold]Fit Name:[/bold] {parse_result.fit_name}\n"
        f"[bold]Items:[/bold] {len(parse_result.items)}",
        title="[bold cyan]Parsed Fit[/bold cyan]",
        border_style="blue",
    ))

    if parse_result.has_missing_types:
        console.print("[yellow]Warning: Some items could not be resolved:[/yellow]")
        for item in parse_result.missing_types[:5]:
            console.print(f"  • {item}", style="yellow")
        if not Confirm.ask("Continue anyway?"):
            return False

    # Get fit description
    description = Prompt.ask(
        "[bold]Fit description[/bold]",
        default=f"{parse_result.fit_name} for {parse_result.ship_name}"
    )

    # Show available doctrines and select
    doctrines = get_available_doctrines(remote=remote)
    if doctrines:
        console.print()
        display_doctrines_table(doctrines)
        console.print()

        doctrine_input = Prompt.ask(
            "[bold]Doctrine ID(s)[/bold] (comma-separated for multiple)",
            default=""
        )
        if doctrine_input:
            doctrine_ids = [int(d.strip()) for d in doctrine_input.split(",") if d.strip()]
        else:
            console.print("[yellow]Warning: No doctrine selected[/yellow]")
            if not Confirm.ask("Continue without doctrine assignment?"):
                return False
            doctrine_ids = []
    else:
        console.print("[yellow]No doctrines found in database[/yellow]")
        doctrine_ids = []

    # Get target quantity
    target = IntPrompt.ask("[bold]Target quantity[/bold]", default=100)

    # Get fit ID
    fit_id = IntPrompt.ask("[bold]Fit ID[/bold] (unique identifier)")

    # Market assignment
    market_choices = ["primary", "deployment", "both"]
    market_flag = Prompt.ask(
        "[bold]Market assignment[/bold]",
        choices=market_choices,
        default=market_flag
    )

    # Show summary
    console.print()
    console.print(Panel(
        f"[bold]Fit ID:[/bold] {fit_id}\n"
        f"[bold]Name:[/bold] {parse_result.fit_name}\n"
        f"[bold]Ship:[/bold] {parse_result.ship_name}\n"
        f"[bold]Description:[/bold] {description}\n"
        f"[bold]Doctrine(s):[/bold] {doctrine_ids or 'None'}\n"
        f"[bold]Target:[/bold] {target}\n"
        f"[bold]Market:[/bold] {market_flag}\n"
        f"[bold]Remote:[/bold] {remote}\n"
        f"[bold]Database:[/bold] {target_alias}",
        title="[bold green]Fit Summary[/bold green]",
        border_style="green",
    ))

    if dry_run:
        console.print("[yellow]DRY RUN - No changes will be made[/yellow]")
        return True

    if not Confirm.ask("Proceed with adding this fit?"):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    # Create temporary metadata file for the workflow
    import json
    import tempfile
    import os

    metadata = {
        "fit_id": fit_id,
        "name": parse_result.fit_name,
        "description": description,
        "doctrine_id": doctrine_ids if len(doctrine_ids) > 1 else (doctrine_ids[0] if doctrine_ids else 1),
        "target": target,
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(metadata, f)
        meta_path = f.name

    try:
        # Call the existing workflow
        update_fit_workflow(
            fit_id=fit_id,
            fit_file=fit_file,
            fit_metadata_file=meta_path,
            remote=remote,
            clear_existing=True,
            dry_run=False,
            target_alias=target_alias,
        )

        # Update market flag if needed
        if market_flag != "primary":
            update_fit_market_flag(fit_id, market_flag, remote=remote, db_alias=target_alias)

        console.print(f"[green]Successfully added fit {fit_id}[/green]")
        return True

    except Exception as e:
        console.print(f"[red]Error adding fit: {e}[/red]")
        logger.exception("Error in interactive_add_fit")
        return False

    finally:
        os.unlink(meta_path)


def assign_market_command(
    fit_id: int,
    market_flag: str,
    remote: bool = False,
    db_alias: str = "wcmkt",
) -> bool:
    """
    Assign a market flag to a fit.

    Args:
        fit_id: The fit ID to update
        market_flag: New market assignment
        remote: Use remote database
        db_alias: Database alias

    Returns:
        True if successful
    """
    try:
        success = update_fit_market_flag(fit_id, market_flag, remote=remote, db_alias=db_alias)
        if success:
            console.print(f"[green]Successfully updated fit {fit_id} to market '{market_flag}'[/green]")
        else:
            console.print(f"[yellow]No fit found with ID {fit_id}[/yellow]")
        return success
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Error updating market flag: {e}[/red]")
        logger.exception("Error in assign_market_command")
        return False


def list_fits_command(db_alias: str = "wcmkt", remote: bool = False) -> None:
    """List all fits in the doctrine_fits table."""
    fits = get_fits_list(db_alias=db_alias, remote=remote)
    if fits:
        display_fits_table(fits)
        console.print(f"\n[dim]Total: {len(fits)} fits[/dim]")
    else:
        console.print("[yellow]No fits found[/yellow]")


def list_doctrines_command(remote: bool = False) -> None:
    """List all available doctrines."""
    doctrines = get_available_doctrines(remote=remote)
    if doctrines:
        display_doctrines_table(doctrines)
        console.print(f"\n[dim]Total: {len(doctrines)} doctrines[/dim]")
    else:
        console.print("[yellow]No doctrines found[/yellow]")


def fit_update_command(
    subcommand: str,
    fit_id: Optional[int] = None,
    file_path: Optional[str] = None,
    meta_file: Optional[str] = None,
    market_flag: str = "primary",
    remote: bool = False,
    local_only: bool = False,
    dry_run: bool = False,
    interactive: bool = False,
    target_alias: str = "wcmkt",
) -> bool:
    """
    Main entry point for fit-update commands.

    Args:
        subcommand: The subcommand to run (add, update, assign-market, list-fits, list-doctrines)
        fit_id: Fit ID for update/assign-market commands
        file_path: Path to EFT fit file
        meta_file: Path to metadata JSON file
        market_flag: Market assignment
        remote: Use remote database
        local_only: Use local database only (no Turso sync)
        dry_run: Preview without committing
        interactive: Use interactive prompts
        target_alias: Target database alias

    Returns:
        True if command succeeded
    """
    # Determine remote flag
    use_remote = remote and not local_only

    if subcommand == "list-fits":
        list_fits_command(db_alias=target_alias, remote=use_remote)
        return True

    elif subcommand == "list-doctrines":
        list_doctrines_command(remote=use_remote)
        return True

    elif subcommand == "assign-market":
        if fit_id is None:
            console.print("[red]Error: --fit-id is required for assign-market[/red]")
            return False
        return assign_market_command(fit_id, market_flag, remote=use_remote, db_alias=target_alias)

    elif subcommand == "add":
        if not file_path:
            console.print("[red]Error: --file is required for add command[/red]")
            return False

        if interactive:
            return interactive_add_fit(
                fit_file=file_path,
                remote=use_remote,
                dry_run=dry_run,
                target_alias=target_alias,
                market_flag=market_flag,
            )
        else:
            if not meta_file:
                console.print("[red]Error: --meta-file is required for non-interactive add[/red]")
                console.print("[dim]Use --interactive for prompted input[/dim]")
                return False

            try:
                metadata = parse_fit_metadata(meta_file)
                result = update_fit_workflow(
                    fit_id=metadata.fit_id,
                    fit_file=file_path,
                    fit_metadata_file=meta_file,
                    remote=use_remote,
                    clear_existing=True,
                    dry_run=dry_run,
                    target_alias=target_alias,
                )

                if dry_run:
                    console.print("[yellow]DRY RUN complete[/yellow]")
                    console.print(f"Ship: {result['ship_name']} ({result['ship_type_id']})")
                    console.print(f"Items: {len(result['items'])}")
                else:
                    console.print(f"[green]Successfully added fit {metadata.fit_id}[/green]")

                return True

            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                logger.exception("Error in fit_update_command add")
                return False

    elif subcommand == "update":
        if fit_id is None:
            console.print("[red]Error: --fit-id is required for update command[/red]")
            return False
        if not file_path:
            console.print("[red]Error: --file is required for update command[/red]")
            return False

        # For update, we need a metadata file
        if not meta_file:
            console.print("[red]Error: --meta-file is required for update command[/red]")
            return False

        try:
            result = update_fit_workflow(
                fit_id=fit_id,
                fit_file=file_path,
                fit_metadata_file=meta_file,
                remote=use_remote,
                clear_existing=True,
                dry_run=dry_run,
                target_alias=target_alias,
            )

            if dry_run:
                console.print("[yellow]DRY RUN complete[/yellow]")
                console.print(f"Ship: {result['ship_name']} ({result['ship_type_id']})")
                console.print(f"Items: {len(result['items'])}")
            else:
                console.print(f"[green]Successfully updated fit {fit_id}[/green]")

            return True

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            logger.exception("Error in fit_update_command update")
            return False

    else:
        console.print(f"[red]Unknown subcommand: {subcommand}[/red]")
        console.print("[dim]Available: add, update, assign-market, list-fits, list-doctrines[/dim]")
        return False
