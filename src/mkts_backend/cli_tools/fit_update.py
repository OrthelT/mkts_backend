"""
Fit Update CLI commands.

Interactive tools for managing fits and doctrines:
- add: Add a new fit with optional interactive metadata prompts
- update: Update an existing fit
- assign-market: Assign market flags to fits
- list-fits: List all fits
- list-doctrines: List all doctrines
- create-doctrine: Create a new doctrine
- doctrine-add-fit: Add existing fit(s) to a doctrine
- doctrine-remove-fit: Remove fit(s) from a doctrine
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
    get_fit_target,
    upsert_doctrine_fits,
    upsert_doctrine_map,
    upsert_ship_target,
    refresh_doctrines_for_fit,
    remove_doctrine_fits,
    remove_doctrine_map,
    remove_doctrines_for_fit,
    DoctrineFit,
)
from mkts_backend.utils.parse_fits import (
    update_fit_workflow,
    parse_fit_metadata,
    FitMetadata,
    create_doctrine,
    get_next_doctrine_id,
    ensure_doctrine_link,
    remove_doctrine_link,
)
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
                SELECT fit_id, TRIM(fit_name), ship_name, TRIM(doctrine_name), target, market_flag
                FROM doctrine_fits
                ORDER BY ship_name, doctrine_name, fit_name
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
    """Display doctrines in a Rich table (filters out deprecated 'zz' prefixed doctrines)."""
    table = Table(
        title="[bold cyan]Available Doctrines[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )

    table.add_column("ID", style="dim", justify="right", width=6)
    table.add_column("Name", style="white", min_width=30)

    # Filter out deprecated doctrines (names starting with "zz")
    active_doctrines = [d for d in doctrines if not d["name"].lower().startswith("zz")]

    for doctrine in active_doctrines:
        table.add_row(
            str(doctrine["id"]),
            doctrine["name"],
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
        f"[bold]Items:[/bold] {len(parse_result.items)}\n"
        f"[bold]Remote:[/bold] {remote}\n",
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
    console.print()
    console.print(Panel(
        "[bold]Doctrine Assignment[/bold]\n\n"
        "A doctrine is a named group of fits. Select existing doctrine(s) to add this fit to,\n"
        "or create a new doctrine. A fit can belong to multiple doctrines.",
        border_style="dim",
    ))

    doctrines = get_available_doctrines(remote=remote)
    doctrine_ids = []

    if doctrines:
        console.print("\n[cyan]Existing doctrines:[/cyan]")
        display_doctrines_table(doctrines)
        console.print()

        # Ask if they want to use existing or create new
        action = Prompt.ask(
            "[bold]Choose action[/bold]",
            choices=["existing", "new", "skip"],
            default="existing"
        )

        if action == "existing":
            doctrine_input = Prompt.ask(
                "[bold]Enter doctrine ID(s)[/bold] (comma-separated for multiple)"
            )
            if doctrine_input:
                doctrine_ids = [int(d.strip()) for d in doctrine_input.split(",") if d.strip()]
                # Validate doctrine IDs exist
                existing_ids = {d["id"] for d in doctrines}
                invalid_ids = [did for did in doctrine_ids if did not in existing_ids]
                if invalid_ids:
                    console.print(f"[yellow]Warning: Doctrine ID(s) {invalid_ids} not found[/yellow]")
                    if not Confirm.ask("Continue with only valid IDs?"):
                        return False
                    doctrine_ids = [did for did in doctrine_ids if did in existing_ids]

        elif action == "new":
            console.print("\n[cyan]Creating a new doctrine:[/cyan]")
            next_id = get_next_doctrine_id(remote=remote)
            new_doctrine_id = IntPrompt.ask("[bold]New doctrine ID[/bold]", default=next_id)
            new_doctrine_name = Prompt.ask("[bold]New doctrine name[/bold]")
            if not new_doctrine_name:
                console.print("[red]Error: Doctrine name is required[/red]")
                return False
            new_doctrine_desc = Prompt.ask("[bold]Description[/bold]", default="")

            # Create the doctrine
            success = create_doctrine(
                doctrine_id=new_doctrine_id,
                name=new_doctrine_name,
                description=new_doctrine_desc,
                remote=remote,
            )
            if success:
                console.print(f"[green]Created doctrine {new_doctrine_id}: {new_doctrine_name}[/green]")
                doctrine_ids = [new_doctrine_id]
            else:
                console.print(f"[yellow]Doctrine {new_doctrine_id} already exists, using it[/yellow]")
                doctrine_ids = [new_doctrine_id]

        else:  # skip
            console.print("[yellow]Skipping doctrine assignment[/yellow]")
            if not Confirm.ask("Continue without doctrine assignment?"):
                return False

    else:
        console.print("[yellow]No doctrines found in database[/yellow]")
        if Confirm.ask("Create a new doctrine now?"):
            next_id = get_next_doctrine_id(remote=remote)
            new_doctrine_id = IntPrompt.ask("[bold]New doctrine ID[/bold]", default=next_id)
            new_doctrine_name = Prompt.ask("[bold]New doctrine name[/bold]")
            if not new_doctrine_name:
                console.print("[red]Error: Doctrine name is required[/red]")
                return False
            new_doctrine_desc = Prompt.ask("[bold]Description[/bold]", default="")

            success = create_doctrine(
                doctrine_id=new_doctrine_id,
                name=new_doctrine_name,
                description=new_doctrine_desc,
                remote=remote,
            )
            if success:
                console.print(f"[green]Created doctrine {new_doctrine_id}: {new_doctrine_name}[/green]")
                doctrine_ids = [new_doctrine_id]
            else:
                console.print(f"[red]Failed to create doctrine[/red]")
                return False
        else:
            console.print("[yellow]Warning: Continuing without doctrine assignment[/yellow]")

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
    """List all available doctrines (excludes deprecated 'zz' prefixed)."""
    doctrines = get_available_doctrines(remote=remote)
    # Filter out deprecated doctrines for count
    active_doctrines = [d for d in doctrines if not d["name"].lower().startswith("zz")]
    if active_doctrines:
        display_doctrines_table(doctrines)  # display_doctrines_table does its own filtering
        console.print(f"\n[dim]Total: {len(active_doctrines)} doctrines[/dim]")
    else:
        console.print("[yellow]No doctrines found[/yellow]")


def create_doctrine_command(
    name: Optional[str] = None,
    description: Optional[str] = None,
    doctrine_id: Optional[int] = None,
    remote: bool = False,
    interactive: bool = True,
) -> bool:
    """
    Create a new doctrine.

    Args:
        name: Doctrine name (prompted if interactive)
        description: Doctrine description (prompted if interactive)
        doctrine_id: Specific ID to use (auto-assigned if not provided)
        remote: Use remote database
        interactive: Use interactive prompts

    Returns:
        True if successful
    """
    if interactive:
        console.print(Panel(
            "[bold]Create a new doctrine[/bold]\n\n"
            "A doctrine is a named group of ship fits.\n"
            "Once created, you can add fits to this doctrine.",
            title="[bold cyan]New Doctrine[/bold cyan]",
            border_style="blue",
        ))

        # Show existing doctrines for reference
        doctrines = get_available_doctrines(remote=remote)
        if doctrines:
            console.print("\n[dim]Existing doctrines for reference:[/dim]")
            display_doctrines_table(doctrines)
            console.print()

        # Get doctrine ID
        next_id = get_next_doctrine_id(remote=remote)
        if doctrine_id is None:
            doctrine_id = IntPrompt.ask(
                "[bold]Doctrine ID[/bold]",
                default=next_id
            )

        # Get doctrine name
        if name is None:
            name = Prompt.ask("[bold]Doctrine name[/bold]")
            if not name:
                console.print("[red]Error: Doctrine name is required[/red]")
                return False

        # Get description
        if description is None:
            description = Prompt.ask(
                "[bold]Description[/bold]",
                default=""
            )

        # Confirm
        console.print()
        console.print(Panel(
            f"[bold]ID:[/bold] {doctrine_id}\n"
            f"[bold]Name:[/bold] {name}\n"
            f"[bold]Description:[/bold] {description or '(none)'}",
            title="[bold green]Doctrine Summary[/bold green]",
            border_style="green",
        ))

        if not Confirm.ask("Create this doctrine?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    else:
        # Non-interactive: require name
        if name is None:
            console.print("[red]Error: --name is required for non-interactive mode[/red]")
            return False
        if doctrine_id is None:
            doctrine_id = get_next_doctrine_id(remote=remote)

    try:
        success = create_doctrine(
            doctrine_id=doctrine_id,
            name=name,
            description=description or "",
            remote=remote,
        )
        if success:
            console.print(f"[green]Successfully created doctrine {doctrine_id}: {name}[/green]")
        else:
            console.print(f"[yellow]Doctrine {doctrine_id} already exists[/yellow]")
        return success
    except Exception as e:
        console.print(f"[red]Error creating doctrine: {e}[/red]")
        logger.exception("Error in create_doctrine_command")
        return False


def get_fit_info(fit_id: int, remote: bool = False) -> Optional[dict]:
    """Get fit info from fittings database."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, name, description, ship_type_id
            FROM fittings_fitting
            WHERE id = :fit_id
        """), {"fit_id": fit_id}).fetchone()

    engine.dispose()

    if result:
        # Get ship name from SDE
        sde_db = DatabaseConfig("sde")
        sde_engine = sde_db.engine
        with sde_engine.connect() as conn:
            ship_name = conn.execute(text("""
                SELECT typeName FROM inv_info WHERE typeID = :type_id
            """), {"type_id": result[3]}).scalar()
        sde_engine.dispose()

        return {
            "fit_id": result[0],
            "fit_name": result[1],
            "description": result[2],
            "ship_type_id": result[3],
            "ship_name": ship_name or "Unknown",
        }
    return None


def is_fit_in_doctrine(doctrine_id: int, fit_id: int, remote: bool = False) -> bool:
    """Check if a fit is already linked to a doctrine."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT 1 FROM fittings_doctrine_fittings
            WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id
        """), {"doctrine_id": doctrine_id, "fit_id": fit_id}).fetchone()

    engine.dispose()
    return result is not None


def get_doctrine_fits(doctrine_id: int, remote: bool = False) -> List[int]:
    """Get list of fit IDs already in a doctrine."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT fitting_id FROM fittings_doctrine_fittings
            WHERE doctrine_id = :doctrine_id
        """), {"doctrine_id": doctrine_id}).fetchall()

    engine.dispose()
    return [row[0] for row in result]


def doctrine_add_fit_command(
    doctrine_id: Optional[int] = None,
    fit_ids: Optional[List[int]] = None,
    target: int = 100,
    market_flag: str = "primary",
    remote: bool = False,
    interactive: bool = True,
    db_alias: str = "wcmkt",
    skip_targets: bool = False,
) -> bool:
    """
    Add existing fit(s) to a doctrine.

    This links fits that are already in the fittings database to a doctrine,
    and sets up tracking in the market database.

    Args:
        doctrine_id: Doctrine to add the fit(s) to
        fit_ids: List of fit IDs to add (or single ID will be wrapped)
        target: Default target quantity for new fits without existing targets
        market_flag: Market assignment (primary, deployment, both)
        remote: Use remote database
        interactive: Use interactive prompts
        db_alias: Target market database
        skip_targets: If True, preserve existing targets and skip target prompts

    Returns:
        True if at least one fit was successfully added
    """
    # Dictionary to hold per-fit targets
    fit_targets: dict[int, int] = {}

    if interactive:
        console.print(Panel(
            "[bold]Add fit(s) to a doctrine[/bold]\n\n"
            "Link existing fits to a doctrine for tracking.\n"
            "You can add multiple fits at once (comma-separated IDs).\n"
            "Targets are set per-fit (different ships may need different quantities).\n"
            "Fits already in the doctrine will be skipped.",
            title="[bold cyan]Doctrine Add Fit[/bold cyan]",
            border_style="blue",
        ))

        # Show available doctrines
        doctrines = get_available_doctrines(remote=remote)
        if doctrines:
            console.print()
            display_doctrines_table(doctrines)
            console.print()
        else:
            console.print("[yellow]No doctrines found. Create one first with 'create-doctrine'.[/yellow]")
            return False

        # Get doctrine ID
        if doctrine_id is None:
            doctrine_id = IntPrompt.ask("[bold]Doctrine ID[/bold] to add fit(s) to")

        # Verify doctrine exists
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
            return False

        console.print(f"\n[cyan]Selected doctrine:[/cyan] {doctrine_info['name']}")

        # Show fits already in this doctrine
        existing_fit_ids = get_doctrine_fits(doctrine_id, remote=remote)
        if existing_fit_ids:
            console.print(f"[dim]Currently has {len(existing_fit_ids)} fit(s): {existing_fit_ids}[/dim]")

        # Get fit IDs
        if fit_ids is None or len(fit_ids) == 0:
            fit_input = Prompt.ask("\n[bold]Fit ID(s)[/bold] to add (comma-separated for multiple)")
            if not fit_input:
                console.print("[red]Error: At least one fit ID is required[/red]")
                return False
            fit_ids = [int(f.strip()) for f in fit_input.split(",") if f.strip()]

        # Validate and categorize fits
        valid_fits = []
        invalid_fits = []
        already_added = []

        for fid in fit_ids:
            fit_info = get_fit_info(fid, remote=remote)
            if not fit_info:
                invalid_fits.append(fid)
            elif fid in existing_fit_ids:
                already_added.append(fid)
            else:
                valid_fits.append(fit_info)

        # Report validation results
        if invalid_fits:
            console.print(f"[red]Not found in fittings database:[/red] {invalid_fits}")
        if already_added:
            console.print(f"[yellow]Already in doctrine (skipping):[/yellow] {already_added}")

        if not valid_fits:
            console.print("[red]No valid fits to add[/red]")
            return False

        # Display valid fits with existing targets
        console.print(f"\n[green]Valid fits to add ({len(valid_fits)}):[/green]")
        fit_table = Table(box=box.SIMPLE)
        fit_table.add_column("Fit ID", style="dim")
        fit_table.add_column("Fit Name", style="white")
        fit_table.add_column("Ship", style="cyan")
        fit_table.add_column("Existing Target", style="yellow", justify="right")

        # Look up existing targets for each fit
        for fit in valid_fits:
            existing_target = get_fit_target(fit["fit_id"], remote=remote, db_alias=db_alias)
            fit["existing_target"] = existing_target
            target_display = str(existing_target) if existing_target is not None else "[dim]none[/dim]"
            fit_table.add_row(str(fit["fit_id"]), fit["fit_name"], fit["ship_name"], target_display)
        console.print(fit_table)

        # Get market assignment first (applies to all fits)
        market_choices = ["primary", "deployment", "both"]
        market_flag = Prompt.ask(
            "\n[bold]Market assignment[/bold]",
            choices=market_choices,
            default=market_flag
        )

        # Per-fit target collection
        if skip_targets:
            console.print("\n[dim]Skipping target prompts (--skip-targets). Existing targets will be preserved.[/dim]")
            for fit in valid_fits:
                # Use existing target or fall back to default
                fit_targets[fit["fit_id"]] = fit["existing_target"] if fit["existing_target"] is not None else target
        else:
            console.print("\n[bold]Set target for each fit[/bold] (press Enter to keep existing or use default):")
            for fit in valid_fits:
                existing = fit["existing_target"]
                default_val = existing if existing is not None else target
                fit_target = IntPrompt.ask(
                    f"  {fit['fit_name']} ({fit['ship_name']})",
                    default=default_val
                )
                fit_targets[fit["fit_id"]] = fit_target

        # Confirm with per-fit targets
        console.print()
        targets_summary = "\n".join(
            f"  • {fit['fit_name']}: {fit_targets[fit['fit_id']]}"
            for fit in valid_fits
        )
        console.print(Panel(
            f"[bold]Doctrine:[/bold] {doctrine_info['name']} (ID: {doctrine_id})\n"
            f"[bold]Fits to add:[/bold] {len(valid_fits)}\n"
            f"[bold]Market:[/bold] {market_flag}\n"
            f"[bold]Targets:[/bold]\n{targets_summary}",
            title="[bold green]Add Fits Summary[/bold green]",
            border_style="green",
        ))

        if not Confirm.ask(f"Add {len(valid_fits)} fit(s) to the doctrine?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    else:
        # Non-interactive mode: require both IDs
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required[/red]")
            return False
        if fit_ids is None or len(fit_ids) == 0:
            console.print("[red]Error: --fit-id is required (comma-separated for multiple)[/red]")
            return False

        doctrines = get_available_doctrines(remote=remote)
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
            return False

        existing_fit_ids = get_doctrine_fits(doctrine_id, remote=remote)

        # Validate fits
        valid_fits = []
        invalid_fits = []
        already_added = []

        for fid in fit_ids:
            fit_info = get_fit_info(fid, remote=remote)
            if not fit_info:
                invalid_fits.append(fid)
            elif fid in existing_fit_ids:
                already_added.append(fid)
            else:
                valid_fits.append(fit_info)

        if invalid_fits:
            console.print(f"[red]Not found: {invalid_fits}[/red]")
        if already_added:
            console.print(f"[yellow]Already in doctrine: {already_added}[/yellow]")

        if not valid_fits:
            console.print("[red]No valid fits to add[/red]")
            return False

        # Non-interactive: look up existing targets and apply skip_targets logic
        for fit in valid_fits:
            existing_target = get_fit_target(fit["fit_id"], remote=remote, db_alias=db_alias)
            fit["existing_target"] = existing_target
            if skip_targets and existing_target is not None:
                # Preserve existing target
                fit_targets[fit["fit_id"]] = existing_target
            else:
                # Use provided target or default
                fit_targets[fit["fit_id"]] = target

    # Process all valid fits
    success_count = 0
    fail_count = 0

    for fit_info in valid_fits:
        fit_id = fit_info["fit_id"]
        fit_target = fit_targets.get(fit_id, target)  # Get per-fit target
        try:
            # Link in fittings database
            ensure_doctrine_link(doctrine_id, fit_id, remote=remote)

            # Add to market database doctrine_fits table
            doctrine_fit = DoctrineFit(
                doctrine_id=doctrine_id,
                fit_id=fit_id,
                target=fit_target,
            )
            upsert_doctrine_fits(
                doctrine_fit=doctrine_fit,
                remote=remote,
                db_alias=db_alias,
                market_flag=market_flag,
            )

            # Add doctrine map entry
            upsert_doctrine_map(doctrine_id, fit_id, remote=remote, db_alias=db_alias)

            # Update ship_targets table
            upsert_ship_target(
                fit_id=fit_id,
                fit_name=doctrine_fit.fit_name,
                ship_id=doctrine_fit.ship_type_id,
                ship_name=doctrine_fit.ship_name,
                ship_target=fit_target,
                remote=remote,
                db_alias=db_alias,
            )

            # Refresh doctrines table with market data
            refresh_doctrines_for_fit(
                fit_id=fit_id,
                ship_id=doctrine_fit.ship_type_id,
                ship_name=doctrine_fit.ship_name,
                remote=remote,
                db_alias=db_alias,
            )

            console.print(f"[green]✓ Added fit {fit_id}: {doctrine_fit.fit_name} (target: {fit_target})[/green]")
            success_count += 1

        except Exception as e:
            console.print(f"[red]✗ Failed to add fit {fit_id}: {e}[/red]")
            logger.exception(f"Error adding fit {fit_id} to doctrine {doctrine_id}")
            fail_count += 1

    # Summary
    console.print()
    if success_count > 0:
        console.print(f"[green]Successfully added {success_count} fit(s) to doctrine {doctrine_id}[/green]")
    if fail_count > 0:
        console.print(f"[red]Failed to add {fail_count} fit(s)[/red]")

    return success_count > 0


def doctrine_remove_fit_command(
    doctrine_id: Optional[int] = None,
    fit_ids: Optional[List[int]] = None,
    remote: bool = False,
    interactive: bool = True,
    db_alias: str = "wcmkt",
) -> bool:
    """
    Remove fit(s) from a doctrine.

    This unlinks fits from a doctrine in both the fittings and market databases.
    The reverse operation of doctrine_add_fit_command.

    Args:
        doctrine_id: Doctrine to remove the fit(s) from
        fit_ids: List of fit IDs to remove
        remote: Use remote database
        interactive: Use interactive prompts
        db_alias: Target market database

    Returns:
        True if at least one fit was successfully removed
    """
    if interactive:
        console.print(Panel(
            "[bold]Remove fit(s) from a doctrine[/bold]\n\n"
            "Unlink fits from a doctrine.\n"
            "This removes tracking but does NOT delete the fit itself.\n"
            "You can add multiple fits at once (comma-separated IDs).",
            title="[bold cyan]Doctrine Remove Fit[/bold cyan]",
            border_style="yellow",
        ))

        # Show available doctrines
        doctrines = get_available_doctrines(remote=remote)
        if doctrines:
            console.print()
            display_doctrines_table(doctrines)
            console.print()
        else:
            console.print("[yellow]No doctrines found.[/yellow]")
            return False

        # Get doctrine ID
        if doctrine_id is None:
            doctrine_id = IntPrompt.ask("[bold]Doctrine ID[/bold] to remove fit(s) from")

        # Verify doctrine exists
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
            return False

        console.print(f"\n[cyan]Selected doctrine:[/cyan] {doctrine_info['name']}")

        # Show fits currently in this doctrine
        existing_fit_ids = get_doctrine_fits(doctrine_id, remote=remote)
        if not existing_fit_ids:
            console.print(f"[yellow]This doctrine has no fits to remove.[/yellow]")
            return False

        console.print(f"\n[dim]Current fits in doctrine ({len(existing_fit_ids)}):[/dim]")

        # Display existing fits with details
        fit_table = Table(box=box.SIMPLE)
        fit_table.add_column("Fit ID", style="dim")
        fit_table.add_column("Fit Name", style="white")
        fit_table.add_column("Ship", style="cyan")

        existing_fits_info = []
        for fid in existing_fit_ids:
            fit_info = get_fit_info(fid, remote=remote)
            if fit_info:
                existing_fits_info.append(fit_info)
                fit_table.add_row(str(fit_info["fit_id"]), fit_info["fit_name"], fit_info["ship_name"])
            else:
                fit_table.add_row(str(fid), "[dim]Unknown[/dim]", "[dim]Unknown[/dim]")
        console.print(fit_table)

        # Get fit IDs to remove
        if fit_ids is None or len(fit_ids) == 0:
            fit_input = Prompt.ask("\n[bold]Fit ID(s)[/bold] to remove (comma-separated for multiple)")
            if not fit_input:
                console.print("[red]Error: At least one fit ID is required[/red]")
                return False
            fit_ids = [int(f.strip()) for f in fit_input.split(",") if f.strip()]

        # Validate fits are in the doctrine
        valid_fits = []
        not_in_doctrine = []

        for fid in fit_ids:
            if fid in existing_fit_ids:
                fit_info = get_fit_info(fid, remote=remote)
                if fit_info:
                    valid_fits.append(fit_info)
                else:
                    # Fit is in doctrine but no details available
                    valid_fits.append({"fit_id": fid, "fit_name": "Unknown", "ship_name": "Unknown", "ship_type_id": 0})
            else:
                not_in_doctrine.append(fid)

        # Report validation results
        if not_in_doctrine:
            console.print(f"[yellow]Not in this doctrine (skipping):[/yellow] {not_in_doctrine}")

        if not valid_fits:
            console.print("[red]No valid fits to remove[/red]")
            return False

        # Display fits to be removed
        console.print(f"\n[yellow]Fits to remove ({len(valid_fits)}):[/yellow]")
        remove_table = Table(box=box.SIMPLE)
        remove_table.add_column("Fit ID", style="dim")
        remove_table.add_column("Fit Name", style="white")
        remove_table.add_column("Ship", style="cyan")
        for fit in valid_fits:
            remove_table.add_row(str(fit["fit_id"]), fit["fit_name"], fit["ship_name"])
        console.print(remove_table)

        # Confirm
        console.print()
        console.print(Panel(
            f"[bold]Doctrine:[/bold] {doctrine_info['name']} (ID: {doctrine_id})\n"
            f"[bold]Fits to remove:[/bold] {len(valid_fits)}",
            title="[bold yellow]Remove Fits Summary[/bold yellow]",
            border_style="yellow",
        ))

        if not Confirm.ask(f"Remove {len(valid_fits)} fit(s) from the doctrine?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    else:
        # Non-interactive mode: require both IDs
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required[/red]")
            return False
        if fit_ids is None or len(fit_ids) == 0:
            console.print("[red]Error: --fit-id is required (comma-separated for multiple)[/red]")
            return False

        doctrines = get_available_doctrines(remote=remote)
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
            return False

        existing_fit_ids = get_doctrine_fits(doctrine_id, remote=remote)

        # Validate fits
        valid_fits = []
        not_in_doctrine = []

        for fid in fit_ids:
            if fid in existing_fit_ids:
                fit_info = get_fit_info(fid, remote=remote)
                if fit_info:
                    valid_fits.append(fit_info)
                else:
                    valid_fits.append({"fit_id": fid, "fit_name": "Unknown", "ship_name": "Unknown", "ship_type_id": 0})
            else:
                not_in_doctrine.append(fid)

        if not_in_doctrine:
            console.print(f"[yellow]Not in doctrine: {not_in_doctrine}[/yellow]")

        if not valid_fits:
            console.print("[red]No valid fits to remove[/red]")
            return False

    # Process all valid fits - REVERSE ORDER of add operations
    success_count = 0
    fail_count = 0

    for fit_info in valid_fits:
        fit_id = fit_info["fit_id"]
        try:
            # Step 1: Remove from doctrines table (market data)
            rows_removed = remove_doctrines_for_fit(fit_id, remote=remote, db_alias=db_alias)

            # Step 2: Remove from doctrine_map
            remove_doctrine_map(doctrine_id, fit_id, remote=remote, db_alias=db_alias)

            # Step 3: Remove from doctrine_fits
            remove_doctrine_fits(doctrine_id, fit_id, remote=remote, db_alias=db_alias)

            # Step 4: Remove from fittings_doctrine_fittings
            remove_doctrine_link(doctrine_id, fit_id, remote=remote)

            console.print(f"[green]✓ Removed fit {fit_id}: {fit_info['fit_name']} ({rows_removed} doctrine rows)[/green]")
            success_count += 1

        except Exception as e:
            console.print(f"[red]✗ Failed to remove fit {fit_id}: {e}[/red]")
            logger.exception(f"Error removing fit {fit_id} from doctrine {doctrine_id}")
            fail_count += 1

    # Summary
    console.print()
    if success_count > 0:
        console.print(f"[green]Successfully removed {success_count} fit(s) from doctrine {doctrine_id}[/green]")
    if fail_count > 0:
        console.print(f"[red]Failed to remove {fail_count} fit(s)[/red]")

    return success_count > 0


def fit_update_command(
    subcommand: str,
    fit_id: Optional[int] = None,
    fit_ids: Optional[List[int]] = None,
    file_path: Optional[str] = None,
    meta_file: Optional[str] = None,
    market_flag: str = "primary",
    remote: bool = False,
    local_only: bool = False,
    dry_run: bool = False,
    interactive: bool = False,
    target_alias: str = "wcmkt",
    target: int = 100,
    skip_targets: bool = False,
) -> bool:
    """
    Main entry point for fit-update commands.

    Subcommands:
        add                  - Add a NEW fit from an EFT file and assign to doctrine(s)
        update               - Update an existing fit's items from an EFT file
        assign-market        - Change the market assignment for an existing fit
        list-fits            - List all fits in the doctrine tracking system
        list-doctrines       - List all available doctrines
        create-doctrine      - Create a new doctrine (group of fits)
        doctrine-add-fit     - Add existing fit(s) to a doctrine (supports multiple)
        doctrine-remove-fit  - Remove fit(s) from a doctrine (supports multiple)

    Args:
        subcommand: The subcommand to run
        fit_id: Fit ID for update/assign-market commands (single)
        fit_ids: List of fit IDs for doctrine-add-fit/doctrine-remove-fit (multiple)
        file_path: Path to EFT fit file
        meta_file: Path to metadata JSON file
        market_flag: Market assignment (primary, deployment, both)
        remote: Use remote database
        local_only: Use local database only (no Turso sync)
        dry_run: Preview without committing
        interactive: Use interactive prompts
        target_alias: Target database alias
        target: Default target quantity for new fits (used by doctrine-add-fit)
        skip_targets: Preserve existing targets, skip target prompts (doctrine-add-fit)

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

    elif subcommand == "create-doctrine":
        return create_doctrine_command(
            remote=use_remote,
            interactive=interactive or True,  # Default to interactive
        )

    elif subcommand == "doctrine-add-fit":
        # Use fit_ids if provided (comma-separated), otherwise wrap single fit_id
        if fit_ids is not None:
            fit_ids_list = fit_ids
        elif fit_id is not None:
            fit_ids_list = [fit_id]
        else:
            fit_ids_list = None  # Will prompt in interactive mode
        return doctrine_add_fit_command(
            doctrine_id=None,  # Will prompt in interactive mode
            fit_ids=fit_ids_list,
            target=target,
            market_flag=market_flag,
            remote=use_remote,
            interactive=interactive or True,  # Default to interactive
            db_alias=target_alias,
            skip_targets=skip_targets,
        )

    elif subcommand == "doctrine-remove-fit":
        # Use fit_ids if provided (comma-separated), otherwise wrap single fit_id
        if fit_ids is not None:
            fit_ids_list = fit_ids
        elif fit_id is not None:
            fit_ids_list = [fit_id]
        else:
            fit_ids_list = None  # Will prompt in interactive mode
        return doctrine_remove_fit_command(
            doctrine_id=None,  # Will prompt in interactive mode
            fit_ids=fit_ids_list,
            remote=use_remote,
            interactive=interactive or True,  # Default to interactive
            db_alias=target_alias,
        )

    else:
        console.print(f"[red]Unknown subcommand: {subcommand}[/red]")
        console.print("[dim]Available: add, update, assign-market, list-fits, list-doctrines, create-doctrine, doctrine-add-fit, doctrine-remove-fit[/dim]")
        return False
