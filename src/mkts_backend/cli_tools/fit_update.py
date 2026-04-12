"""
Fit Update CLI commands.

Interactive tools for managing fits and doctrines:
- add: Add a new fit from an EFT file or pasted text
- update: Update an existing fit's items from file or pasted text
- remove: Completely remove a fit from all doctrines and targets
- assign-market: Assign market flags to fits
- list-fits: List all fits
- list-doctrines: List all doctrines
- create-doctrine: Create a new doctrine
- doctrine-add-fit: Add existing fit(s) to a doctrine
- doctrine-remove-fit: Remove fit(s) from a doctrine
- update-target: Update the target quantity for a fit

Supports --paste mode for pasting EFT text directly via multiline prompt
instead of requiring a file path.
"""

from typing import List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich import box
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, DatabaseError

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config import DatabaseConfig
from mkts_backend.utils.eft_parser import parse_eft_file, parse_eft_string
from mkts_backend.utils.doctrine_update import (
    update_fit_market_flag,
    get_fit_target,
    upsert_doctrine_fits,
    upsert_doctrine_map,
    upsert_ship_target,
    refresh_doctrines_for_fit,
    remove_doctrine_fits,
    remove_doctrine_map,
    remove_doctrines_for_fit,
    remove_ship_target,
    remove_all_doctrine_fits_for_fit,
    remove_all_doctrine_map_for_fit,
    upsert_lead_ship,
    set_lead_ship,
    DoctrineFit,
    ensure_friendly_name_column,
    update_doctrine_friendly_name,
    populate_friendly_names_from_json,
    sync_friendly_names_to_remote,
)
from mkts_backend.utils.parse_fits import (
    update_fit_workflow,
    parse_fit_metadata,
    create_doctrine,
    get_next_doctrine_id,
    ensure_doctrine_link,
    remove_doctrine_link,
    remove_all_doctrine_links_for_fit,
    get_doctrine_ids_for_fit,
)
from mkts_backend.cli_tools.prompter import get_multiline_input

logger = configure_logging(__name__)
console = Console()


def get_available_doctrines(remote: bool = False) -> List[dict]:
    """Get list of available doctrines from fittings database."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    doctrines = []
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name, description FROM fittings_doctrine ORDER BY name")
        )
        for row in result:
            doctrines.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2] or "",
                }
            )

    engine.dispose()
    return doctrines


def eft_text_to_file(eft_text: str) -> str:
    """Write EFT text to a temporary file and return the file path."""
    file_path = "temp_file.txt"
    with open(file_path, "w") as f:
        f.write(eft_text)
    return file_path


def get_fits_list(db_alias: str = "wcmkt", remote: bool = False) -> List[dict]:
    """Get list of fits from doctrine_fits table."""
    db = DatabaseConfig(db_alias)
    engine = db.remote_engine if remote else db.engine

    fits = []
    with engine.connect() as conn:
        # Check if market_flag column exists
        try:
            result = conn.execute(
                text("""
                SELECT fit_id, TRIM(fit_name), ship_name, TRIM(doctrine_name), target, market_flag, friendly_name
                FROM doctrine_fits
                ORDER BY ship_name, doctrine_name, fit_name
            """)
            )
            has_market_flag = True
        except Exception:
            # Fallback query without market_flag/friendly_name
            result = conn.execute(
                text("""
                SELECT fit_id, fit_name, ship_name, doctrine_name, target
                FROM doctrine_fits
                ORDER BY doctrine_name, fit_name
            """)
            )
            has_market_flag = False

        for row in result:
            fits.append(
                {
                    "fit_id": row[0],
                    "fit_name": row[1],
                    "ship_name": row[2],
                    "doctrine_name": row[3],
                    "target": row[4],
                    "market_flag": row[5]
                    if has_market_flag and len(row) > 5
                    else "primary",
                    "friendly_name": row[6]
                    if has_market_flag and len(row) > 6
                    else None,
                }
            )

    engine.dispose()
    return fits


def display_fits_table(fits: List[dict]) -> None:
    """Display fits in a Rich table with per-market target columns."""
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
    table.add_column("Primary", justify="right", style="green", width=9)
    table.add_column("North", justify="right", style="yellow", width=9)
    table.add_column("Friendly", style="dim", width=18)

    for fit in fits:
        primary_target = fit.get("primary_target")
        north_target = fit.get("north_target")
        primary_str = str(primary_target) if primary_target is not None else "[dim]--[/dim]"
        north_str = str(north_target) if north_target is not None else "[dim]--[/dim]"
        friendly = fit.get("friendly_name") or "--"

        table.add_row(
            str(fit["fit_id"]),
            fit["fit_name"],
            fit["ship_name"],
            fit["doctrine_name"],
            primary_str,
            north_str,
            friendly,
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
    active_doctrines = [
        d for d in doctrines if not d["name"].lower().startswith("zz")]

    for doctrine in active_doctrines:
        table.add_row(
            str(doctrine["id"]),
            doctrine["name"],
        )

    console.print(table)


def interactive_add_fit(
    fit_file: str = None,
    eft_text: str = None,
    remote: bool = False,
    dry_run: bool = False,
    target_alias: str = "wcmkt",
    market_flag: str = "primary",
) -> bool:
    """
    Interactively add a new fit with prompts for metadata.

    Args:
        fit_file: Path to EFT fit file (optional; None when using paste mode)
        eft_text: Raw EFT text from paste mode (used when fit_file is None)
        remote: Use remote database
        dry_run: Preview without committing
        target_alias: Target database alias
        market_flag: Market assignment

    Returns:
        True if successful
    """
    # Parse the fit file or pasted text
    try:
        if fit_file:
            parse_result = parse_eft_file(fit_file)
        elif eft_text:
            parse_result = parse_eft_string(eft_text)
        else:
            console.print("[red]Error: No fit file or pasted text provided[/red]")
            return False
    except FileNotFoundError:
        console.print(f"[red]Error: File not found: {fit_file}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Error parsing fit: {e}[/red]")
        return False

    # Display parsed fit info
    console.print(
        Panel(
            f"[bold]Ship:[/bold] {parse_result.ship_name}\n"
            f"[bold]Fit Name:[/bold] {parse_result.fit_name}\n"
            f"[bold]Items:[/bold] {len(parse_result.items)}\n"
            f"[bold]Remote:[/bold] {remote}\n",
            title="[bold cyan]Parsed Fit[/bold cyan]",
            border_style="blue",
        )
    )

    if parse_result.has_missing_types:
        console.print(
            "[yellow]Warning: Some items could not be resolved:[/yellow]")
        for item in parse_result.missing_types[:5]:
            console.print(f"  • {item}", style="yellow")
        if not Confirm.ask("Continue anyway?"):
            return False

    # Get fit description
    description = Prompt.ask(
        "[bold]Fit description[/bold]",
        default=f"{parse_result.fit_name} for {parse_result.ship_name}",
    )

    # Show available doctrines and select
    console.print()
    console.print(
        Panel(
            "[bold]Doctrine Assignment[/bold]\n\n"
            "A doctrine is a named group of fits. Select existing doctrine(s) to add this fit to,\n"
            "or create a new doctrine. A fit can belong to multiple doctrines.",
            border_style="dim",
        )
    )

    doctrines = get_available_doctrines(remote=remote)
    doctrine_ids = []
    # Track doctrine names for lead_ship population (includes newly created)
    doctrine_name_map = {d["id"]: d["name"] for d in doctrines} if doctrines else {}

    if doctrines:
        console.print("\n[cyan]Existing doctrines:[/cyan]")
        display_doctrines_table(doctrines)
        console.print()

        # Ask if they want to use existing or create new
        action = Prompt.ask(
            "[bold]Choose action[/bold]",
            choices=["existing", "new", "skip"],
            default="existing",
        )

        if action == "existing":
            doctrine_input = Prompt.ask(
                "[bold]Enter doctrine ID(s)[/bold] (comma-separated for multiple)"
            )
            if doctrine_input:
                doctrine_ids = [
                    int(d.strip()) for d in doctrine_input.split(",") if d.strip()
                ]
                # Validate doctrine IDs exist
                existing_ids = {d["id"] for d in doctrines}
                invalid_ids = [
                    did for did in doctrine_ids if did not in existing_ids]
                if invalid_ids:
                    console.print(
                        f"[yellow]Warning: Doctrine ID(s) {
                            invalid_ids
                        } not found[/yellow]"
                    )
                    if not Confirm.ask("Continue with only valid IDs?"):
                        return False
                    doctrine_ids = [
                        did for did in doctrine_ids if did in existing_ids]

        elif action == "new":
            console.print("\n[cyan]Creating a new doctrine:[/cyan]")
            next_id = get_next_doctrine_id(remote=remote)
            new_doctrine_id = IntPrompt.ask(
                "[bold]New doctrine ID[/bold]", default=next_id
            )
            new_doctrine_name = Prompt.ask("[bold]New doctrine name[/bold]")
            if not new_doctrine_name:
                console.print("[red]Error: Doctrine name is required[/red]")
                return False
            new_doctrine_desc = Prompt.ask(
                "[bold]Description[/bold]", default="")

            # Create the doctrine
            success = create_doctrine(
                doctrine_id=new_doctrine_id,
                name=new_doctrine_name,
                description=new_doctrine_desc,
                remote=remote,
            )
            if success:
                console.print(
                    f"[green]Created doctrine {new_doctrine_id}: {
                        new_doctrine_name
                    }[/green]"
                )
                doctrine_ids = [new_doctrine_id]
                doctrine_name_map[new_doctrine_id] = new_doctrine_name
            else:
                console.print(
                    f"[yellow]Doctrine {
                        new_doctrine_id
                    } already exists, using it[/yellow]"
                )
                doctrine_ids = [new_doctrine_id]

        else:  # skip
            console.print("[yellow]Skipping doctrine assignment[/yellow]")
            if not Confirm.ask("Continue without doctrine assignment?"):
                return False

    else:
        console.print("[yellow]No doctrines found in database[/yellow]")
        if Confirm.ask("Create a new doctrine now?"):
            next_id = get_next_doctrine_id(remote=remote)
            new_doctrine_id = IntPrompt.ask(
                "[bold]New doctrine ID[/bold]", default=next_id
            )
            new_doctrine_name = Prompt.ask("[bold]New doctrine name[/bold]")
            if not new_doctrine_name:
                console.print("[red]Error: Doctrine name is required[/red]")
                return False
            new_doctrine_desc = Prompt.ask(
                "[bold]Description[/bold]", default="")

            success = create_doctrine(
                doctrine_id=new_doctrine_id,
                name=new_doctrine_name,
                description=new_doctrine_desc,
                remote=remote,
            )
            if success:
                console.print(
                    f"[green]Created doctrine {new_doctrine_id}: {
                        new_doctrine_name
                    }[/green]"
                )
                doctrine_ids = [new_doctrine_id]
                doctrine_name_map[new_doctrine_id] = new_doctrine_name
            else:
                console.print(f"[red]Failed to create doctrine[/red]")
                return False
        else:
            console.print(
                "[yellow]Warning: Continuing without doctrine assignment[/yellow]"
            )

    # Get target quantity
    target = IntPrompt.ask("[bold]Target quantity[/bold]", default=100)

    # Get fit ID
    fit_id = IntPrompt.ask("[bold]Fit ID[/bold] (unique identifier)")

    # Market assignment
    market_choices = ["primary", "deployment", "both"]
    market_flag = Prompt.ask(
        "[bold]Market assignment[/bold]", choices=market_choices, default=market_flag
    )

    # Show summary
    console.print()
    console.print(
        Panel(
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
        )
    )

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
        "doctrine_id": doctrine_ids
        if len(doctrine_ids) > 1
        else (doctrine_ids[0] if doctrine_ids else 1),
        "target": target,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(metadata, f)
        meta_path = f.name

    # If paste mode, write EFT text to a temp file for the downstream pipeline
    eft_temp_path = None
    if not fit_file and eft_text:
        eft_temp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        )
        eft_temp.write(eft_text)
        eft_temp.close()
        eft_temp_path = eft_temp.name
        fit_file = eft_temp_path

    try:
        # Determine which databases to update
        if market_flag == "both":
            target_aliases = ["wcmkt", "wcmktnorth"]
        else:
            target_aliases = [target_alias]

        for alias in target_aliases:
            # Call the existing workflow for each target database
            update_fit_workflow(
                fit_id=fit_id,
                fit_file=fit_file,
                fit_metadata_file=meta_path,
                remote=remote,
                clear_existing=True,
                dry_run=False,
                target_alias=alias,
            )

            # Set market flag on this database
            update_fit_market_flag(
                fit_id, market_flag, remote=remote, db_alias=alias
            )

            console.print(
                f"[green]Successfully added fit {fit_id} to {alias}[/green]"
            )

            # Populate lead_ship for each doctrine (first fit becomes default)
            for did in doctrine_ids:
                d_name = doctrine_name_map.get(did, "")
                inserted = upsert_lead_ship(
                    doctrine_id=did,
                    doctrine_name=d_name,
                    fit_id=fit_id,
                    ship_type_id=parse_result.ship_type_id,
                    remote=remote,
                    db_alias=alias,
                )
                if inserted:
                    console.print(
                        f"[green]Set lead ship for doctrine {did}: "
                        f"{parse_result.ship_name}[/green]"
                    )

        return True

    except Exception as e:
        console.print(f"[red]Error adding fit: {e}[/red]")
        logger.exception("Error in interactive_add_fit")
        return False

    finally:
        os.unlink(meta_path)
        if eft_temp_path:
            os.unlink(eft_temp_path)


def assign_market_command(
    fit_id: int,
    market_flag: str,
    remote: bool = False,
    db_alias: str = "wcmkt",
    doctrine_id: Optional[int] = None,
    skip_confirm: bool = False,
) -> dict:
    """
    Assign a market flag to a fit with preview and confirmation.

    When doctrine_id is provided, only the specific doctrine_fits row is affected.
    Without doctrine_id, ALL doctrine_fits rows for the fit are processed.

    Args:
        fit_id: The fit ID to assign
        market_flag: Market to assign ('primary', 'deployment', or 'both')
        remote: Use remote database
        db_alias: Database alias
        doctrine_id: Optionally target a specific doctrine row only
        skip_confirm: Skip the confirmation prompt

    Returns:
        Dict with counts: {"updated": int, "skipped": int}
        Empty dict on error or cancellation.
    """
    if market_flag not in ("primary", "deployment", "both"):
        console.print(
            f"[red]Error: invalid market '{market_flag}'. "
            "Must be 'primary', 'deployment', or 'both'[/red]"
        )
        return {}

    try:
        rows = _get_doctrine_fits_rows(fit_id, db_alias, False, doctrine_id)
        if not rows:
            # Fit may live in the other market database — search all aliases
            for fallback in ("wcmktprod", "wcmktnorth"):
                if fallback == db_alias:
                    continue
                rows = _get_doctrine_fits_rows(fit_id, fallback, False, doctrine_id)
                if rows:
                    logger.info(
                        f"Fit {fit_id} not in {db_alias}, found in {fallback}"
                    )
                    break
        if not rows:
            console.print(f"[yellow]No doctrine_fits rows found for fit {fit_id}"
                           + (f" in doctrine {doctrine_id}" if doctrine_id else "") + "[/yellow]")
            return {}

        # Plan phase — check remote flags when remote=True
        plans = []
        for row in rows:
            rf = _get_remote_market_flags(row["fit_id"], row["doctrine_id"]) if remote else None
            plans.append(_plan_market_action(row, market_flag, mode="assign", remote_flags=rf))

        # Preview and confirm
        if not skip_confirm:
            _display_market_preview(plans, market_flag, mode="assign")
            if not Confirm.ask("[bold]Proceed with these changes?[/bold]"):
                console.print("[yellow]Cancelled[/yellow]")
                return {}

        # Execute
        return _execute_market_plan(plans, remote, db_alias)

    except Exception as e:
        console.print(f"[red]Error in assign-market: {e}[/red]")
        logger.exception("Error in assign_market_command")
        return {}


def assign_doctrine_market(
    doctrine_id: int,
    market_flag: str,
    remote: bool = False,
    db_alias: str = "wcmkt",
) -> bool:
    """
    Assign a market flag to all fits in a doctrine.

    Args:
        doctrine_id: The doctrine to assign
        market_flag: Market to assign ('primary', 'deployment', or 'both')
        remote: Use remote database
        db_alias: Database alias
    """
    if market_flag not in ("primary", "deployment", "both"):
        console.print(
            f"[red]Error: invalid market '{market_flag}'. "
            "Must be 'primary', 'deployment', or 'both'[/red]"
        )
        return False

    fit_ids = get_doctrine_fits_from_market(doctrine_id, db_alias, remote)
    if not fit_ids:
        console.print(
            f"[yellow]No fits found for doctrine {doctrine_id} in {db_alias}[/yellow]"
        )
        return False

    # Get doctrine name for display
    all_doctrines = get_available_doctrines(remote=remote)
    doctrine_name = next(
        (d["name"] for d in all_doctrines if d["id"] == doctrine_id),
        f"ID {doctrine_id}",
    )

    # Gather all rows and build the full plan
    all_plans = []
    for fid in fit_ids:
        rows = _get_doctrine_fits_rows(fid, db_alias, False, doctrine_id)
        if not rows:
            for fallback in ("wcmktprod", "wcmktnorth"):
                if fallback == db_alias:
                    continue
                rows = _get_doctrine_fits_rows(fid, fallback, False, doctrine_id)
                if rows:
                    logger.info(f"Fit {fid} not in {db_alias}, found in {fallback}")
                    break
        for row in rows:
            rf = _get_remote_market_flags(row["fit_id"], row["doctrine_id"]) if remote else None
            all_plans.append(_plan_market_action(row, market_flag, mode="assign", remote_flags=rf))

    if not all_plans:
        console.print(f"[yellow]No doctrine_fits rows found for doctrine {doctrine_id}[/yellow]")
        return False

    # Preview table showing every fit and its planned action
    _display_market_preview(all_plans, market_flag, mode="assign", doctrine_name=doctrine_name)

    # Confirmation
    prompt_msg = (
        f"[bold]Assign doctrine '{doctrine_name}' to "
        f"'{market_flag}' market?[/bold]"
    )
    if not Confirm.ask(prompt_msg):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    console.print()

    # Execute — skip per-fit confirmation since we just confirmed the whole batch
    result = _execute_market_plan(all_plans, remote, db_alias)

    console.print(
        f"\n[bold]Summary:[/bold] {result['updated']} updated, "
        f"{result['skipped']} skipped"
    )
    return result["updated"] > 0


def _flag_to_aliases(flag: str) -> set[str]:
    """Return the set of explicit database aliases a market_flag implies."""
    _FLAG_ALIAS_MAP = {
        "primary": {"wcmktprod"},
        "deployment": {"wcmktnorth"},
        "both": {"wcmktprod", "wcmktnorth"},
    }
    if flag not in _FLAG_ALIAS_MAP:
        logger.warning(f"Unexpected market_flag '{flag}' — treating as empty alias set")
    return _FLAG_ALIAS_MAP.get(flag, set())


def _needs_provisioning(
    fit_id: int,
    db_alias: str,
    remote: bool = False,
    engine=None,
) -> bool:
    """Return True if a fit is missing doctrines or ship_targets rows in a database."""
    if engine is None:
        db = DatabaseConfig(db_alias)
        _engine = db.remote_engine if remote else db.engine
    else:
        _engine = engine
    try:
        with _engine.connect() as conn:
            doc_count = conn.execute(
                text("SELECT COUNT(*) FROM doctrines WHERE fit_id = :fit_id"),
                {"fit_id": fit_id},
            ).fetchone()[0]
            st = conn.execute(
                text("SELECT 1 FROM ship_targets WHERE fit_id = :fit_id"),
                {"fit_id": fit_id},
            ).fetchone()
        return doc_count == 0 or st is None
    finally:
        if engine is None:
            _engine.dispose()


def _check_fit_orphaned(
    fit_id: int,
    db_alias: str = "wcmkt",
    remote: bool = False,
    engine=None,
) -> bool:
    """Return True if fit_id has no remaining doctrine_fits rows."""
    if engine is None:
        db = DatabaseConfig(db_alias)
        _engine = db.remote_engine if remote else db.engine
    else:
        _engine = engine
    with _engine.connect() as conn:
        result = conn.execute(
            text("SELECT COUNT(*) FROM doctrine_fits WHERE fit_id = :fit_id"),
            {"fit_id": fit_id},
        ).fetchone()
    if engine is None:
        _engine.dispose()
    return result[0] == 0


def _get_doctrine_fits_rows(
    fit_id: int,
    db_alias: str = "wcmkt",
    remote: bool = False,
    doctrine_id: Optional[int] = None,
) -> List[dict]:
    """Get doctrine_fits rows for a fit, optionally filtered by doctrine_id."""
    db = DatabaseConfig(db_alias)
    engine = db.remote_engine if remote else db.engine
    with engine.connect() as conn:
        if doctrine_id is not None:
            result = conn.execute(
                text(
                    "SELECT doctrine_id, fit_id, market_flag, doctrine_name, "
                    "fit_name, ship_name, ship_type_id, target "
                    "FROM doctrine_fits WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"
                ),
                {"fit_id": fit_id, "doctrine_id": doctrine_id},
            ).fetchall()
        else:
            result = conn.execute(
                text(
                    "SELECT doctrine_id, fit_id, market_flag, doctrine_name, "
                    "fit_name, ship_name, ship_type_id, target "
                    "FROM doctrine_fits WHERE fit_id = :fit_id"
                ),
                {"fit_id": fit_id},
            ).fetchall()
    engine.dispose()
    return [
        {
            "doctrine_id": r[0], "fit_id": r[1], "market_flag": r[2],
            "doctrine_name": r[3], "fit_name": r[4], "ship_name": r[5],
            "ship_type_id": r[6], "target": r[7],
        }
        for r in result
    ]


def _get_remote_market_flags(
    fit_id: int,
    doctrine_id: int,
) -> List[str]:
    """Fetch market_flag for a (fit_id, doctrine_id) from both remote databases.

    Returns a list of flag values found (0-2 entries).
    """
    flags = []
    for target in ("wcmktprod", "wcmktnorth"):
        try:
            db = DatabaseConfig(target)
            engine = db.remote_engine
            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT market_flag FROM doctrine_fits "
                        "WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"
                    ),
                    {"fit_id": fit_id, "doctrine_id": doctrine_id},
                ).fetchone()
            engine.dispose()
            if result:
                flags.append(result[0])
        except Exception:
            pass  # remote unavailable — will be handled at execute time
    return flags


def _cleanup_orphaned_fit(fit_id: int, db_alias: str, remote: bool, engine=None) -> None:
    """Remove doctrines and ship_targets rows for a fit that has no remaining doctrine_fits."""
    removed = remove_doctrines_for_fit(fit_id, remote=remote, db_alias=db_alias, engine=engine)
    if removed:
        console.print(f"  [dim]Cleaned up {removed} orphaned doctrines rows for fit {fit_id}[/dim]")
    remove_ship_target(fit_id, remote=remote, db_alias=db_alias, engine=engine)
    console.print(f"  [dim]Cleaned up ship_targets for fit {fit_id}[/dim]")


def _plan_market_action(
    row: dict,
    target_market: str,
    mode: str = "unassign",
    remote_flags: Optional[List[str]] = None,
) -> dict:
    """Compute the planned action for a single doctrine_fits row.

    Works for both assign and unassign operations.

    Args:
        row: doctrine_fits row dict
        target_market: 'primary', 'deployment', or 'both'
        mode: "assign" or "unassign"
        remote_flags: market_flag values from remote databases (assign only).
            When provided, a row is only skipped if local AND all remotes
            already match the target.

    Returns a dict with the original row data plus:
        action: "update", "remove", or "skip"
        new_flag: the resulting market_flag
        reason: human-readable explanation
    """
    current_flag = row["market_flag"]
    plan = {**row}

    if mode == "assign":
        all_flags = [current_flag] + (remote_flags or [])
        all_match = all(f == target_market for f in all_flags)
        if all_match:
            plan["action"] = "skip"
            plan["new_flag"] = current_flag
            plan["reason"] = f"Already '{current_flag}'"
        else:
            mismatched = [f for f in all_flags if f != target_market]
            plan["action"] = "update"
            plan["new_flag"] = target_market
            plan["reason"] = f"Change '{current_flag}' → '{target_market}'"
            if remote_flags and current_flag == target_market:
                plan["reason"] = (
                    f"Local '{current_flag}' OK, "
                    f"remote {'/' .join(mismatched)} → '{target_market}'"
                )
    else:
        # unassign logic (unchanged)
        if target_market == "both":
            plan["action"] = "remove"
            plan["new_flag"] = None
            plan["reason"] = "Remove from both markets"
        elif current_flag == "both":
            new_flag = "deployment" if target_market == "primary" else "primary"
            plan["action"] = "update"
            plan["new_flag"] = new_flag
            plan["reason"] = f"Change '{current_flag}' → '{new_flag}'"
        elif current_flag == target_market:
            plan["action"] = "remove"
            plan["new_flag"] = None
            plan["reason"] = f"Remove — only on '{current_flag}'"
        else:
            plan["action"] = "skip"
            plan["new_flag"] = current_flag
            plan["reason"] = f"Not on '{target_market}' (is '{current_flag}')"

    return plan


def _display_market_preview(
    plans: List[dict],
    target_market: str,
    mode: str = "unassign",
    doctrine_name: Optional[str] = None,
) -> None:
    """Display a Rich table previewing what each fit will experience."""
    label = "Assign" if mode == "assign" else "Unassign"
    title = f"[bold cyan]{label} Preview[/bold cyan]"
    if doctrine_name:
        title += f" — {doctrine_name}"

    table = Table(title=title, box=box.ROUNDED, show_header=True, header_style="bold magenta")
    table.add_column("Fit ID", style="dim", justify="right", width=8)
    table.add_column("Fit Name", style="white", min_width=20)
    table.add_column("Ship", style="cyan", min_width=14)
    table.add_column("Current", justify="center", width=12)
    table.add_column("Action", justify="center", width=10)
    table.add_column("Result", min_width=20)

    action_styles = {"update": "green", "remove": "red", "skip": "dim"}

    for p in plans:
        action_style = action_styles.get(p["action"], "white")
        table.add_row(
            str(p["fit_id"]),
            p.get("fit_name") or "?",
            p.get("ship_name") or "?",
            p["market_flag"],
            f"[{action_style}]{p['action'].upper()}[/{action_style}]",
            p["reason"],
        )

    console.print(table)

    # Summary line
    counts = {}
    for p in plans:
        counts[p["action"]] = counts.get(p["action"], 0) + 1
    parts = []
    if counts.get("update"):
        parts.append(f"[green]{counts['update']} update[/green]")
    if counts.get("remove"):
        parts.append(f"[red]{counts['remove']} remove[/red]")
    if counts.get("skip"):
        parts.append(f"[dim]{counts['skip']} skip[/dim]")
    console.print(f"  Planned: {', '.join(parts)}\n")


def _provision_market_db(
    p: dict,
    alias: str,
    new_flag: str,
    remote: bool,
) -> None:
    """Provision all tables for a fit in a newly-assigned market database.

    Creates a single engine for all operations to avoid multiple concurrent
    connections to the same SQLite file.
    """
    db = DatabaseConfig(alias)
    engine = db.remote_engine if remote else db.engine
    try:
        doctrine_fit = DoctrineFit.from_resolved(
            doctrine_id=p["doctrine_id"],
            fit_id=p["fit_id"],
            target=p["target"],
            doctrine_name=p["doctrine_name"],
            fit_name=p["fit_name"],
            ship_type_id=p["ship_type_id"],
            ship_name=p["ship_name"],
        )
        upsert_doctrine_fits(doctrine_fit, remote=remote, db_alias=alias, market_flag=new_flag, engine=engine)
        upsert_doctrine_map(p["doctrine_id"], p["fit_id"], remote=remote, db_alias=alias, engine=engine)
        upsert_ship_target(
            p["fit_id"], p["fit_name"], p["ship_type_id"], p["ship_name"],
            p["target"], remote=remote, db_alias=alias, engine=engine,
        )
        refresh_doctrines_for_fit(
            p["fit_id"], p["ship_type_id"], p["ship_name"],
            remote=remote, db_alias=alias, engine=engine,
        )
    finally:
        engine.dispose()


def _cleanup_market_db(
    fit_id: int,
    doctrine_id: int,
    alias: str,
    remote: bool,
) -> None:
    """Remove a fit's doctrine_fits/doctrine_map from a market database,
    then clean up doctrines/ship_targets if the fit is orphaned.

    Creates a single engine for all operations.
    """
    db = DatabaseConfig(alias)
    engine = db.remote_engine if remote else db.engine
    try:
        remove_doctrine_fits(doctrine_id, fit_id, remote=remote, db_alias=alias, engine=engine)
        remove_doctrine_map(doctrine_id, fit_id, remote=remote, db_alias=alias, engine=engine)
        if _check_fit_orphaned(fit_id, alias, remote=remote, engine=engine):
            _cleanup_orphaned_fit(fit_id, alias, remote=remote, engine=engine)
    finally:
        engine.dispose()


def _execute_market_plan(
    plans: List[dict],
    remote: bool,
    db_alias: str,
) -> dict:
    """Execute a list of planned assign or unassign actions.

    For "update" actions, computes which databases are newly added, removed,
    or unchanged by the flag transition, then provisions/cleans up accordingly.

    Always writes to the local database first.  When remote=True, mirrors
    every change to both remote databases (wcmktprod, wcmktnorth).

    Returns aggregate counts: {"updated": int, "deleted": int, "skipped": int}
    """
    updated = 0
    deleted = 0
    skipped = 0
    deleted_fit_ids = set()

    for p in plans:
        fit_id = p["fit_id"]
        row_doctrine_id = p["doctrine_id"]

        if p["action"] == "update":
            old_flag = p["market_flag"]
            new_flag = p["new_flag"]
            old_aliases = _flag_to_aliases(old_flag)
            new_aliases = _flag_to_aliases(new_flag)
            newly_added = new_aliases - old_aliases
            newly_removed = old_aliases - new_aliases
            unchanged = old_aliases & new_aliases

            # Update flag in databases that remain active, heal if needed
            for alias in unchanged:
                update_fit_market_flag(
                    fit_id, new_flag, remote=False, db_alias=alias,
                    doctrine_id=row_doctrine_id,
                )
                if _needs_provisioning(fit_id, alias, remote=False):
                    _provision_market_db(p, alias, new_flag, remote=False)
                    console.print(f"  [green]Provisioned[/green] missing data for fit {fit_id} in {alias}")

            # Full provisioning in newly-added databases
            for alias in newly_added:
                _provision_market_db(p, alias, new_flag, remote=False)

            # Full cleanup in newly-removed databases
            for alias in newly_removed:
                _cleanup_market_db(fit_id, row_doctrine_id, alias, remote=False)

            # Remote mirroring — reconcile each remote DB to match new_flag,
            # regardless of what the remote's current state is (handles drift).
            if remote:
                for target in ("wcmktprod", "wcmktnorth"):
                    try:
                        if target in new_aliases:
                            # This DB should have the fit — update flag and heal if needed
                            update_fit_market_flag(
                                fit_id, new_flag, remote=True, db_alias=target,
                                doctrine_id=row_doctrine_id,
                            )
                            if _needs_provisioning(fit_id, target, remote=True):
                                _provision_market_db(p, target, new_flag, remote=True)
                                console.print(f"  [green]Provisioned[/green] missing remote data for fit {fit_id} in {target}")
                        else:
                            # This DB should NOT have the fit — clean up if present
                            _cleanup_market_db(fit_id, row_doctrine_id, target, remote=True)
                    except (OperationalError, DatabaseError, ConnectionError, TimeoutError) as e:
                        console.print(
                            f"[yellow]Remote update skipped for {target}: {e}[/yellow]"
                        )

            updated += 1
            console.print(
                f"  [green]Updated[/green] fit {fit_id}: "
                f"market_flag '{old_flag}' -> '{new_flag}'"
            )

        elif p["action"] == "remove":
            # Local
            remove_doctrine_fits(row_doctrine_id, fit_id, remote=False, db_alias=db_alias)
            remove_doctrine_map(row_doctrine_id, fit_id, remote=False, db_alias=db_alias)
            # Remote
            if remote:
                for target in ("wcmktprod", "wcmktnorth"):
                    try:
                        remove_doctrine_fits(row_doctrine_id, fit_id, remote=True, db_alias=target)
                        remove_doctrine_map(row_doctrine_id, fit_id, remote=True, db_alias=target)
                    except (OperationalError, DatabaseError, ConnectionError, TimeoutError) as e:
                        console.print(
                            f"[yellow]Remote remove skipped for {target}: {e}[/yellow]"
                        )
            deleted += 1
            deleted_fit_ids.add(fit_id)
            console.print(
                f"  [red]Removed[/red] fit {fit_id} from doctrine {row_doctrine_id} "
                f"({p.get('doctrine_name', '?')})"
            )
        else:
            # Heal: even when flag matches, reconcile all databases to match
            target_aliases = _flag_to_aliases(p["new_flag"])
            non_target_aliases = {"wcmktprod", "wcmktnorth"} - target_aliases
            healed = False
            for alias in target_aliases:
                if _needs_provisioning(fit_id, alias, remote=False):
                    _provision_market_db(p, alias, p["new_flag"], remote=False)
                    console.print(f"  [green]Provisioned[/green] missing data for fit {fit_id} in {alias}")
                    healed = True
            if remote:
                for alias in target_aliases:
                    try:
                        update_fit_market_flag(
                            fit_id, p["new_flag"], remote=True, db_alias=alias,
                            doctrine_id=row_doctrine_id,
                        )
                        if _needs_provisioning(fit_id, alias, remote=True):
                            _provision_market_db(p, alias, p["new_flag"], remote=True)
                            console.print(f"  [green]Provisioned[/green] missing remote data for fit {fit_id} in {alias}")
                            healed = True
                    except (OperationalError, DatabaseError, ConnectionError, TimeoutError) as e:
                        console.print(f"[yellow]Remote provisioning skipped for {alias}: {e}[/yellow]")
                for alias in non_target_aliases:
                    try:
                        _cleanup_market_db(fit_id, row_doctrine_id, alias, remote=True)
                    except (OperationalError, DatabaseError, ConnectionError, TimeoutError) as e:
                        console.print(f"[yellow]Remote cleanup skipped for {alias}: {e}[/yellow]")
            if healed:
                updated += 1
            else:
                skipped += 1

    # Orphan cleanup for any deleted fits (check all local alias databases)
    for fit_id in deleted_fit_ids:
        for local_alias in ("wcmktprod", "wcmktnorth"):
            if _check_fit_orphaned(fit_id, local_alias, remote=False):
                _cleanup_orphaned_fit(fit_id, local_alias, remote=False)
        if remote:
            for target in ("wcmktprod", "wcmktnorth"):
                try:
                    if _check_fit_orphaned(fit_id, target, remote=True):
                        _cleanup_orphaned_fit(fit_id, target, remote=True)
                except (OperationalError, DatabaseError, ConnectionError, TimeoutError) as e:
                    console.print(
                        f"[yellow]Remote orphan cleanup skipped for {target}: {e}[/yellow]"
                    )

    return {"updated": updated, "deleted": deleted, "skipped": skipped}


def unassign_market_command(
    fit_id: int,
    market_to_remove: str,
    remote: bool = False,
    db_alias: str = "wcmkt",
    doctrine_id: Optional[int] = None,
    skip_confirm: bool = False,
) -> dict:
    """
    Remove a fit from a specific market (or both).

    When doctrine_id is provided, only the specific doctrine_fits row is affected.
    Without doctrine_id, ALL doctrine_fits rows for the fit are processed.

    Args:
        fit_id: The fit ID to unassign
        market_to_remove: Market to remove from ('primary', 'deployment', or 'both')
        remote: Use remote database
        db_alias: Database alias
        doctrine_id: Optionally target a specific doctrine row only
        skip_confirm: Skip the confirmation prompt (used when called from
            unassign_doctrine_market which does its own confirmation)

    Returns:
        Dict with counts: {"updated": int, "deleted": int, "skipped": int}
        Empty dict on error or cancellation.
    """
    if market_to_remove not in ("primary", "deployment", "both"):
        console.print(
            f"[red]Error: invalid market '{market_to_remove}'. "
            "Must be 'primary', 'deployment', or 'both'[/red]"
        )
        return {}

    try:
        rows = _get_doctrine_fits_rows(fit_id, db_alias, remote, doctrine_id)
        if not rows:
            for fallback in ("wcmktprod", "wcmktnorth"):
                if fallback == db_alias:
                    continue
                rows = _get_doctrine_fits_rows(fit_id, fallback, remote, doctrine_id)
                if rows:
                    logger.info(
                        f"Fit {fit_id} not in {db_alias}, found in {fallback}"
                    )
                    break
        if not rows:
            console.print(f"[yellow]No doctrine_fits rows found for fit {fit_id}"
                           + (f" in doctrine {doctrine_id}" if doctrine_id else "") + "[/yellow]")
            return {}

        # Plan phase — compute what will happen to each row
        plans = [_plan_market_action(row, market_to_remove, mode="unassign") for row in rows]

        # Preview and confirm
        if not skip_confirm:
            _display_market_preview(plans, market_to_remove)
            if not Confirm.ask("[bold]Proceed with these changes?[/bold]"):
                console.print("[yellow]Cancelled[/yellow]")
                return {}

        # Execute
        return _execute_market_plan(plans, remote, db_alias)

    except Exception as e:
        console.print(f"[red]Error in unassign-market: {e}[/red]")
        logger.exception("Error in unassign_market_command")
        return {}


def unassign_doctrine_market(
    doctrine_id: int,
    market_to_remove: str,
    remote: bool = False,
    db_alias: str = "wcmkt",
) -> bool:
    """
    Remove an entire doctrine from a specific market (or both).

    Args:
        doctrine_id: The doctrine to unassign
        market_to_remove: 'primary', 'deployment', or 'both'
        remote: Use remote database
        db_alias: Database alias
    """
    if market_to_remove not in ("primary", "deployment", "both"):
        console.print(
            f"[red]Error: invalid market '{market_to_remove}'. "
            "Must be 'primary', 'deployment', or 'both'[/red]"
        )
        return False

    fit_ids = get_doctrine_fits_from_market(doctrine_id, db_alias, remote)
    if not fit_ids:
        console.print(f"[yellow]No fits found for doctrine {doctrine_id} in {db_alias}[/yellow]")
        return False

    # Get doctrine name for display
    all_doctrines = get_available_doctrines(remote=remote)
    doctrine_name = next(
        (d["name"] for d in all_doctrines if d["id"] == doctrine_id), f"ID {doctrine_id}"
    )

    # Gather all rows and build the full plan
    all_plans = []
    for fid in fit_ids:
        rows = _get_doctrine_fits_rows(fid, db_alias, remote, doctrine_id)
        if not rows:
            for fallback in ("wcmktprod", "wcmktnorth"):
                if fallback == db_alias:
                    continue
                rows = _get_doctrine_fits_rows(fid, fallback, remote, doctrine_id)
                if rows:
                    logger.info(f"Fit {fid} not in {db_alias}, found in {fallback}")
                    break
        for row in rows:
            all_plans.append(_plan_market_action(row, market_to_remove, mode="unassign"))

    if not all_plans:
        console.print(f"[yellow]No doctrine_fits rows found for doctrine {doctrine_id}[/yellow]")
        return False

    # Preview table showing every fit and its planned action
    _display_market_preview(all_plans, market_to_remove, doctrine_name=doctrine_name)

    # Confirmation
    action_counts = {}
    for p in all_plans:
        action_counts[p["action"]] = action_counts.get(p["action"], 0) + 1

    if market_to_remove == "both":
        prompt_msg = (
            f"[bold red]Remove doctrine '{doctrine_name}' from BOTH markets? "
            "This cannot be undone.[/bold red]"
        )
    else:
        prompt_msg = (
            f"[bold]Remove doctrine '{doctrine_name}' from "
            f"'{market_to_remove}' market?[/bold]"
        )

    if not Confirm.ask(prompt_msg):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    console.print()

    # Execute — skip per-fit confirmation since we just confirmed the whole batch
    result = _execute_market_plan(all_plans, remote, db_alias)

    console.print(
        f"\n[bold]Summary:[/bold] {result['updated']} updated, "
        f"{result['deleted']} removed, {result['skipped']} skipped"
    )
    return result["updated"] > 0 or result["deleted"] > 0


def list_fits_command(db_alias: str = "wcmkt", remote: bool = False) -> None:
    """List all fits, showing targets for both primary and north markets."""
    primary_fits = get_fits_list(db_alias="wcmkt", remote=remote)
    north_fits = get_fits_list(db_alias="wcmktnorth", remote=remote)

    merged: dict[int, dict] = {}
    for fit in primary_fits:
        merged[fit["fit_id"]] = {**fit, "primary_target": fit["target"], "north_target": None}
    for fit in north_fits:
        fid = fit["fit_id"]
        if fid in merged:
            merged[fid]["north_target"] = fit["target"]
        else:
            merged[fid] = {**fit, "primary_target": None, "north_target": fit["target"]}

    fits = sorted(merged.values(), key=lambda f: (f["ship_name"], f["fit_name"]))
    if fits:
        display_fits_table(fits)
        console.print(f"\n[dim]Total: {len(fits)} fits[/dim]")
    else:
        console.print("[yellow]No fits found[/yellow]")


def list_doctrines_command(remote: bool = False) -> None:
    """List all available doctrines (excludes deprecated 'zz' prefixed)."""
    doctrines = get_available_doctrines(remote=remote)
    # Filter out deprecated doctrines for count
    active_doctrines = [
        d for d in doctrines if not d["name"].lower().startswith("zz")]
    if active_doctrines:
        display_doctrines_table(
            doctrines
        )  # display_doctrines_table does its own filtering
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
        console.print(
            Panel(
                "[bold]Create a new doctrine[/bold]\n\n"
                "A doctrine is a named group of ship fits.\n"
                "Once created, you can add fits to this doctrine.",
                title="[bold cyan]New Doctrine[/bold cyan]",
                border_style="blue",
            )
        )

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
                "[bold]Doctrine ID[/bold]", default=next_id)

        # Get doctrine name
        if name is None:
            name = Prompt.ask("[bold]Doctrine name[/bold]")
            if not name:
                console.print("[red]Error: Doctrine name is required[/red]")
                return False

        # Get description
        if description is None:
            description = Prompt.ask("[bold]Description[/bold]", default="")

        # Confirm
        console.print()
        console.print(
            Panel(
                f"[bold]ID:[/bold] {doctrine_id}\n"
                f"[bold]Name:[/bold] {name}\n"
                f"[bold]Description:[/bold] {description or '(none)'}",
                title="[bold green]Doctrine Summary[/bold green]",
                border_style="green",
            )
        )

        if not Confirm.ask("Create this doctrine?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    else:
        # Non-interactive: require name
        if name is None:
            console.print(
                "[red]Error: --name is required for non-interactive mode[/red]"
            )
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
            console.print(
                f"[green]Successfully created doctrine {
                    doctrine_id}: {name}[/green]"
            )
        else:
            console.print(f"[yellow]Doctrine {
                          doctrine_id} already exists[/yellow]")
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
        result = conn.execute(
            text("""
            SELECT id, name, description, ship_type_id
            FROM fittings_fitting
            WHERE id = :fit_id
        """),
            {"fit_id": fit_id},
        ).fetchone()

    engine.dispose()

    if result:
        # Get ship name from SDE
        sde_db = DatabaseConfig("sde")
        sde_engine = sde_db.engine
        with sde_engine.connect() as conn:
            ship_name = conn.execute(
                text("""
                SELECT typeName FROM sdetypes WHERE typeID = :type_id
            """),
                {"type_id": result[3]},
            ).scalar()
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
        result = conn.execute(
            text("""
            SELECT 1 FROM fittings_doctrine_fittings
            WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id
        """),
            {"doctrine_id": doctrine_id, "fit_id": fit_id},
        ).fetchone()

    engine.dispose()
    return result is not None


def get_doctrine_fits(doctrine_id: int, remote: bool = False) -> List[int]:
    """Get list of fit IDs already in a doctrine (from fittings DB link table)."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        result = conn.execute(
            text("""
            SELECT fitting_id FROM fittings_doctrine_fittings
            WHERE doctrine_id = :doctrine_id
        """),
            {"doctrine_id": doctrine_id},
        ).fetchall()

    engine.dispose()
    return [row[0] for row in result]


def get_doctrine_fits_from_market(
    doctrine_id: int, db_alias: str = "wcmkt", remote: bool = False
) -> List[int]:
    """Get list of fit IDs already in a doctrine from the market database."""
    db = DatabaseConfig(db_alias)
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        result = conn.execute(
            text("""
            SELECT fit_id FROM doctrine_fits
            WHERE doctrine_id = :doctrine_id
        """),
            {"doctrine_id": doctrine_id},
        ).fetchall()

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

    # Determine target market databases up front (used for both validation and writes)
    if market_flag == "both":
        target_aliases = ["wcmkt", "wcmktnorth"]
    else:
        target_aliases = [db_alias]

    # A fit is "already added" only if it is present in ALL target market databases
    def _get_existing_fit_ids_for_doctrine(doctrine_id: int) -> set[int]:
        fits_per_alias = [
            set(get_doctrine_fits_from_market(doctrine_id, alias, remote=remote))
            for alias in target_aliases
        ]
        return set.intersection(*fits_per_alias) if fits_per_alias else set()

    if interactive:
        console.print(
            Panel(
                "[bold]Add fit(s) to a doctrine[/bold]\n\n"
                "Link existing fits to a doctrine for tracking.\n"
                "You can add multiple fits at once (comma-separated IDs).\n"
                "Targets are set per-fit (different ships may need different quantities).\n"
                "Fits already in the doctrine will be skipped.",
                title="[bold cyan]Doctrine Add Fit[/bold cyan]",
                border_style="blue",
            )
        )

        # Show available doctrines
        doctrines = get_available_doctrines(remote=remote)
        if doctrines:
            console.print()
            display_doctrines_table(doctrines)
            console.print()
        else:
            console.print(
                "[yellow]No doctrines found. Create one first with 'create-doctrine'.[/yellow]"
            )
            return False

        # Get doctrine ID
        if doctrine_id is None:
            doctrine_id = IntPrompt.ask(
                "[bold]Doctrine ID[/bold] to add fit(s) to")

        # Verify doctrine exists
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {
                          doctrine_id} not found[/red]")
            return False

        console.print(
            f"\n[cyan]Selected doctrine:[/cyan] {doctrine_info['name']}")

        # Show fits already in this doctrine (check market db, not fittings link table)
        existing_fit_ids = _get_existing_fit_ids_for_doctrine(doctrine_id)
        if existing_fit_ids:
            console.print(
                f"[dim]Currently has {len(existing_fit_ids)} fit(s): {
                    existing_fit_ids
                }[/dim]"
            )

        # Get fit IDs
        if fit_ids is None or len(fit_ids) == 0:
            fit_input = Prompt.ask(
                "\n[bold]Fit ID(s)[/bold] to add (comma-separated for multiple)"
            )
            if not fit_input:
                console.print(
                    "[red]Error: At least one fit ID is required[/red]")
                return False
            fit_ids = [int(f.strip())
                       for f in fit_input.split(",") if f.strip()]

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
            console.print(
                f"[red]Not found in fittings database:[/red] {invalid_fits}")
        if already_added:
            console.print(
                f"[yellow]Already in doctrine (skipping):[/yellow] {
                    already_added}"
            )

        if not valid_fits:
            console.print("[red]No valid fits to add[/red]")
            return False

        # Display valid fits with existing targets
        console.print(f"\n[green]Valid fits to add ({
                      len(valid_fits)}):[/green]")
        fit_table = Table(box=box.SIMPLE)
        fit_table.add_column("Fit ID", style="dim")
        fit_table.add_column("Fit Name", style="white")
        fit_table.add_column("Ship", style="cyan")
        fit_table.add_column(
            "Existing Target", style="yellow", justify="right")

        # Look up existing targets for each fit
        for fit in valid_fits:
            existing_target = get_fit_target(
                fit["fit_id"], remote=remote, db_alias=db_alias
            )
            fit["existing_target"] = existing_target
            target_display = (
                str(existing_target)
                if existing_target is not None
                else "[dim]none[/dim]"
            )
            fit_table.add_row(
                str(fit["fit_id"]), fit["fit_name"], fit["ship_name"], target_display
            )
        console.print(fit_table)

        # Get market assignment first (applies to all fits)
        market_choices = ["primary", "deployment", "both"]
        market_flag = Prompt.ask(
            "\n[bold]Market assignment[/bold]",
            choices=market_choices,
            default=market_flag,
        )

        # Per-fit target collection
        if skip_targets:
            console.print(
                "\n[dim]Skipping target prompts (--skip-targets). Existing targets will be preserved.[/dim]"
            )
            for fit in valid_fits:
                # Use existing target or fall back to default
                fit_targets[fit["fit_id"]] = (
                    fit["existing_target"]
                    if fit["existing_target"] is not None
                    else target
                )
        else:
            console.print(
                "\n[bold]Set target for each fit[/bold] (press Enter to keep existing or use default):"
            )
            for fit in valid_fits:
                existing = fit["existing_target"]
                default_val = existing if existing is not None else target
                fit_target = IntPrompt.ask(
                    f"  {fit['fit_name']} ({fit['ship_name']})", default=default_val
                )
                fit_targets[fit["fit_id"]] = fit_target

        # Confirm with per-fit targets
        console.print()
        targets_summary = "\n".join(
            f"  • {fit['fit_name']}: {fit_targets[fit['fit_id']]}" for fit in valid_fits
        )
        console.print(
            Panel(
                f"[bold]Doctrine:[/bold] {doctrine_info['name']
                                          } (ID: {doctrine_id})\n"
                f"[bold]Fits to add:[/bold] {len(valid_fits)}\n"
                f"[bold]Market:[/bold] {market_flag}\n"
                f"[bold]Targets:[/bold]\n{targets_summary}",
                title="[bold green]Add Fits Summary[/bold green]",
                border_style="green",
            )
        )

        if not Confirm.ask(f"Add {len(valid_fits)} fit(s) to the doctrine?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    else:
        # Non-interactive mode: require both IDs
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required[/red]")
            return False
        if fit_ids is None or len(fit_ids) == 0:
            console.print(
                "[red]Error: --fit-id is required (comma-separated for multiple)[/red]"
            )
            return False

        doctrines = get_available_doctrines(remote=remote)
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {
                          doctrine_id} not found[/red]")
            return False

        existing_fit_ids = _get_existing_fit_ids_for_doctrine(doctrine_id)

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
            console.print(f"[yellow]Already in doctrine: {
                          already_added}[/yellow]")

        if not valid_fits:
            console.print("[red]No valid fits to add[/red]")
            return False

        # Non-interactive: look up existing targets and apply skip_targets logic
        for fit in valid_fits:
            existing_target = get_fit_target(
                fit["fit_id"], remote=remote, db_alias=db_alias
            )
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
            # Link in fittings database (shared, only done once)
            ensure_doctrine_link(doctrine_id, fit_id, remote=remote)

            doctrine_fit = DoctrineFit(
                doctrine_id=doctrine_id,
                fit_id=fit_id,
                target=fit_target,
            )

            # Add to each target market database
            for alias in target_aliases:
                upsert_doctrine_fits(
                    doctrine_fit=doctrine_fit,
                    remote=remote,
                    db_alias=alias,
                    market_flag=market_flag,
                )

                upsert_doctrine_map(doctrine_id, fit_id,
                                    remote=remote, db_alias=alias)

                upsert_ship_target(
                    fit_id=fit_id,
                    fit_name=doctrine_fit.fit_name,
                    ship_id=doctrine_fit.ship_type_id,
                    ship_name=doctrine_fit.ship_name,
                    ship_target=fit_target,
                    remote=remote,
                    db_alias=alias,
                )

                refresh_doctrines_for_fit(
                    fit_id=fit_id,
                    ship_id=doctrine_fit.ship_type_id,
                    ship_name=doctrine_fit.ship_name,
                    remote=remote,
                    db_alias=alias,
                )

            db_label = " + ".join(target_aliases)
            console.print(
                f"[green]✓ Added fit {fit_id}: {doctrine_fit.fit_name} (target: {
                    fit_target
                }) [{db_label}][/green]"
            )
            success_count += 1

        except Exception as e:
            console.print(f"[red]✗ Failed to add fit {fit_id}: {e}[/red]")
            logger.exception(f"Error adding fit {
                             fit_id} to doctrine {doctrine_id}")
            fail_count += 1

    # Summary
    console.print()
    if success_count > 0:
        console.print(
            f"[green]Successfully added {success_count} fit(s) to doctrine {
                doctrine_id
            }[/green]"
        )
    if fail_count > 0:
        console.print(f"[red]Failed to add {fail_count} fit(s)[/red]")

    return success_count > 0

    """
    Display help for the update-fit command.
    """
    print("""
    update-fit - Update the target quantity for a fit.
    """)
    print("""
    USAGE:
    mkts-backend update-fit --fit-id=<id> --target=<qty>
    """)
    print("""
    OPTIONS:
    --fit-id=<id>        Fit ID to update
    --target=<qty>       Target quantity
    """)
    print("""
    EXAMPLES:
    mkts-backend update-fit --fit-id=123 --target=100
    """)
    return True


def remove_fit_command(
    fit_id: int,
    remote: bool = False,
    db_alias: str = "wcmkt",
) -> bool:
    """
    Completely remove a fit from the system.

    This is the inverse of the add command — it removes the fit from ALL
    doctrines, ship_targets, and market data tables.

    Tables affected:
        1. doctrines       - market analysis rows for this fit
        2. doctrine_map    - doctrine-to-fit mappings (all doctrines)
        3. doctrine_fits   - fit tracking entries (all doctrines)
        4. fittings_doctrine_fittings - doctrine link table (all doctrines)
        5. ship_targets    - target quantity for this fit

    Args:
        fit_id: The fit ID to remove
        remote: Use remote database
        db_alias: Target market database alias

    Returns:
        True if the fit was successfully removed
    """
    # Get fit info for display
    fit_info = get_fit_info(fit_id, remote=remote)
    if not fit_info:
        console.print(f"[red]Error: Fit {fit_id} not found in fittings database[/red]")
        return False

    # Find all doctrines containing this fit
    doctrine_ids = get_doctrine_ids_for_fit(fit_id, remote=remote)

    # Get doctrine names for display
    all_doctrines = get_available_doctrines(remote=remote)
    doctrine_names = {}
    for d in all_doctrines:
        doctrine_names[d["id"]] = d["name"]

    # Display what will be removed
    console.print(
        Panel(
            "[bold]Remove fit from ALL doctrines[/bold]\n\n"
            "This completely removes a fit from the system:\n"
            "  - Removes from all doctrines\n"
            "  - Removes ship target\n"
            "  - Removes market analysis data\n\n"
            "[bold red]This cannot be undone.[/bold red]",
            title="[bold cyan]Remove Fit[/bold cyan]",
            border_style="red",
        )
    )

    console.print()
    info_table = Table(box=box.SIMPLE, show_header=False)
    info_table.add_column("Field", style="dim")
    info_table.add_column("Value", style="white")
    info_table.add_row("Fit ID", str(fit_info["fit_id"]))
    info_table.add_row("Fit Name", fit_info["fit_name"])
    info_table.add_row("Ship", fit_info["ship_name"])
    info_table.add_row("Market DB", db_alias)

    if doctrine_ids:
        doctrine_list = ", ".join(
            f"{did} ({doctrine_names.get(did, '?')})" for did in doctrine_ids
        )
        info_table.add_row("Doctrines", doctrine_list)
    else:
        info_table.add_row("Doctrines", "[dim]None[/dim]")

    console.print(info_table)
    console.print()

    if not Confirm.ask(
        f"[bold red]Remove fit {fit_id} ({fit_info['fit_name']}) from {db_alias}?[/bold red]"
    ):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    # Execute removal in reverse order of creation
    try:
        # Step 1: Remove from doctrines table (market data)
        doctrines_removed = remove_doctrines_for_fit(
            fit_id, remote=remote, db_alias=db_alias
        )
        console.print(f"  [dim]doctrines:[/dim] {doctrines_removed} rows removed")

        # Step 2: Remove from doctrine_map (all doctrines)
        map_removed = remove_all_doctrine_map_for_fit(
            fit_id, remote=remote, db_alias=db_alias
        )
        console.print(f"  [dim]doctrine_map:[/dim] {map_removed} rows removed")

        # Step 3: Remove from doctrine_fits (all doctrines)
        fits_removed = remove_all_doctrine_fits_for_fit(
            fit_id, remote=remote, db_alias=db_alias
        )
        console.print(f"  [dim]doctrine_fits:[/dim] {fits_removed} rows removed")

        # Step 4: Remove from fittings_doctrine_fittings (all doctrines)
        links_removed = remove_all_doctrine_links_for_fit(fit_id, remote=remote)
        console.print(
            f"  [dim]fittings_doctrine_fittings:[/dim] {links_removed} rows removed"
        )

        # Step 5: Remove from ship_targets
        target_removed = remove_ship_target(
            fit_id, remote=remote, db_alias=db_alias
        )
        console.print(
            f"  [dim]ship_targets:[/dim] {'removed' if target_removed else 'not found'}"
        )

        console.print()
        console.print(
            f"[green]✓ Fit {fit_id} ({fit_info['fit_name']}) completely removed[/green]"
        )
        return True

    except Exception as e:
        console.print(f"[red]✗ Error removing fit {fit_id}: {e}[/red]")
        logger.exception(f"Error in remove_fit_command for fit_id {fit_id}")
        return False


def update_lead_ship_command(
    doctrine_id: int,
    fit_id: int,
    remote: bool = False,
    db_alias: str = "wcmkt",
) -> bool:
    """
    Set or change the lead ship for a doctrine.

    Args:
        doctrine_id: The doctrine to update
        fit_id: The fit whose ship becomes the lead
        remote: Use remote database
        db_alias: Target market database alias

    Returns:
        True if successful
    """
    # Validate doctrine exists
    doctrines = get_available_doctrines(remote=remote)
    doctrine_info = None
    for d in doctrines:
        if d["id"] == doctrine_id:
            doctrine_info = d
            break
    if not doctrine_info:
        console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
        return False

    # Validate fit exists and get ship info
    fit_info = get_fit_info(fit_id, remote=remote)
    if not fit_info:
        console.print(f"[red]Error: Fit {fit_id} not found[/red]")
        return False

    # Show what will happen
    console.print()
    info_table = Table(box=box.SIMPLE, show_header=False)
    info_table.add_column("Field", style="dim")
    info_table.add_column("Value", style="white")
    info_table.add_row("Doctrine", f"{doctrine_info['name']} (ID: {doctrine_id})")
    info_table.add_row("Fit", f"{fit_info['fit_name']} (ID: {fit_id})")
    info_table.add_row("Lead Ship", f"{fit_info['ship_name']} ({fit_info['ship_type_id']})")
    info_table.add_row("Market DB", db_alias)
    console.print(info_table)
    console.print()

    if not Confirm.ask(
        f"Set lead ship for doctrine {doctrine_id} to {fit_info['ship_name']}?"
    ):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    try:
        set_lead_ship(
            doctrine_id=doctrine_id,
            doctrine_name=doctrine_info["name"],
            fit_id=fit_id,
            ship_type_id=fit_info["ship_type_id"],
            remote=remote,
            db_alias=db_alias,
        )
        console.print(
            f"[green]✓ Lead ship for doctrine {doctrine_id} set to "
            f"{fit_info['ship_name']} (fit {fit_id})[/green]"
        )
        return True

    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        logger.exception(f"Error in update_lead_ship_command")
        return False


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
        console.print(
            Panel(
                "[bold]Remove fit(s) from a doctrine[/bold]\n\n"
                "Unlink fits from a doctrine.\n"
                "This removes tracking but does NOT delete the fit itself.\n"
                "You can add multiple fits at once (comma-separated IDs).",
                title="[bold cyan]Doctrine Remove Fit[/bold cyan]",
                border_style="yellow",
            )
        )

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
            doctrine_id = IntPrompt.ask(
                "[bold]Doctrine ID[/bold] to remove fit(s) from"
            )

        # Verify doctrine exists
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {
                          doctrine_id} not found[/red]")
            return False

        console.print(
            f"\n[cyan]Selected doctrine:[/cyan] {doctrine_info['name']}")

        # Show fits currently in this doctrine
        existing_fit_ids = get_doctrine_fits(doctrine_id, remote=remote)
        if not existing_fit_ids:
            console.print(
                f"[yellow]This doctrine has no fits to remove.[/yellow]")
            return False

        console.print(
            f"\n[dim]Current fits in doctrine ({len(existing_fit_ids)}):[/dim]"
        )

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
                fit_table.add_row(
                    str(fit_info["fit_id"]
                        ), fit_info["fit_name"], fit_info["ship_name"]
                )
            else:
                fit_table.add_row(
                    str(fid), "[dim]Unknown[/dim]", "[dim]Unknown[/dim]")
        console.print(fit_table)

        # Get fit IDs to remove
        if fit_ids is None or len(fit_ids) == 0:
            fit_input = Prompt.ask(
                "\n[bold]Fit ID(s)[/bold] to remove (comma-separated for multiple)"
            )
            if not fit_input:
                console.print(
                    "[red]Error: At least one fit ID is required[/red]")
                return False
            fit_ids = [int(f.strip())
                       for f in fit_input.split(",") if f.strip()]

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
                    valid_fits.append(
                        {
                            "fit_id": fid,
                            "fit_name": "Unknown",
                            "ship_name": "Unknown",
                            "ship_type_id": 0,
                        }
                    )
            else:
                not_in_doctrine.append(fid)

        # Report validation results
        if not_in_doctrine:
            console.print(
                f"[yellow]Not in this doctrine (skipping):[/yellow] {
                    not_in_doctrine}"
            )

        if not valid_fits:
            console.print("[red]No valid fits to remove[/red]")
            return False

        # Display fits to be removed
        console.print(f"\n[yellow]Fits to remove ({
                      len(valid_fits)}):[/yellow]")
        remove_table = Table(box=box.SIMPLE)
        remove_table.add_column("Fit ID", style="dim")
        remove_table.add_column("Fit Name", style="white")
        remove_table.add_column("Ship", style="cyan")
        for fit in valid_fits:
            remove_table.add_row(
                str(fit["fit_id"]), fit["fit_name"], fit["ship_name"])
        console.print(remove_table)

        # Confirm
        console.print()
        console.print(
            Panel(
                f"[bold]Doctrine:[/bold] {doctrine_info['name']
                                          } (ID: {doctrine_id})\n"
                f"[bold]Fits to remove:[/bold] {len(valid_fits)}",
                title="[bold yellow]Remove Fits Summary[/bold yellow]",
                border_style="yellow",
            )
        )

        if not Confirm.ask(f"Remove {len(valid_fits)} fit(s) from the doctrine?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    else:
        # Non-interactive mode: require both IDs
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required[/red]")
            return False
        if fit_ids is None or len(fit_ids) == 0:
            console.print(
                "[red]Error: --fit-id is required (comma-separated for multiple)[/red]"
            )
            return False

        doctrines = get_available_doctrines(remote=remote)
        doctrine_info = None
        for d in doctrines:
            if d["id"] == doctrine_id:
                doctrine_info = d
                break
        if not doctrine_info:
            console.print(f"[red]Error: Doctrine {
                          doctrine_id} not found[/red]")
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
                    valid_fits.append(
                        {
                            "fit_id": fid,
                            "fit_name": "Unknown",
                            "ship_name": "Unknown",
                            "ship_type_id": 0,
                        }
                    )
            else:
                not_in_doctrine.append(fid)

        if not_in_doctrine:
            console.print(f"[yellow]Not in doctrine: {
                          not_in_doctrine}[/yellow]")

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
            rows_removed = remove_doctrines_for_fit(
                fit_id, remote=remote, db_alias=db_alias
            )

            # Step 2: Remove from doctrine_map
            remove_doctrine_map(doctrine_id, fit_id,
                                remote=remote, db_alias=db_alias)

            # Step 3: Remove from doctrine_fits
            remove_doctrine_fits(doctrine_id, fit_id,
                                 remote=remote, db_alias=db_alias)

            # Step 4: Remove from fittings_doctrine_fittings ONLY if the fit
            # is no longer in this doctrine on ANY market database.
            still_in_other_market = False
            for other_alias in ["wcmkt", "wcmktnorth"]:
                if other_alias == db_alias:
                    continue
                other_fits = get_doctrine_fits_from_market(
                    doctrine_id, db_alias=other_alias, remote=remote
                )
                if fit_id in other_fits:
                    still_in_other_market = True
                    break

            if not still_in_other_market:
                remove_doctrine_link(doctrine_id, fit_id, remote=remote)
            else:
                logger.info(
                    f"Fit {fit_id} still in doctrine {doctrine_id} on another market; "
                    f"keeping fittings link"
                )

            console.print(
                f"[green]✓ Removed fit {fit_id}: {fit_info['fit_name']} ({
                    rows_removed
                } doctrine rows)[/green]"
            )
            success_count += 1

        except Exception as e:
            console.print(f"[red]✗ Failed to remove fit {fit_id}: {e}[/red]")
            logger.exception(f"Error removing fit {
                             fit_id} from doctrine {doctrine_id}")
            fail_count += 1

    # Summary
    console.print()
    if success_count > 0:
        console.print(
            f"[green]Successfully removed {success_count} fit(s) from doctrine {
                doctrine_id
            }[/green]"
        )
    if fail_count > 0:
        console.print(f"[red]Failed to remove {fail_count} fit(s)[/red]")

    return success_count > 0


def update_target_command(
    fit_id: int,
    target: int,
    remote: bool = False,
    market_flag: str = "primary",
    db_alias: str = "wcmkt",
) -> bool:
    """
    Update the target quantity for a fit.
    """
    if market_flag == "both":
        primary_ok = _update_target_single(
            fit_id, target, remote=remote, market_flag="primary", db_alias="wcmkt"
        )
        deploy_ok = _update_target_single(
            fit_id, target, remote=remote, market_flag="deployment", db_alias="wcmktnorth"
        )
        return primary_ok and deploy_ok

    if market_flag == "deployment":
        db_alias = "wcmktnorth"
    elif market_flag not in ["primary", "deployment"]:
        db_alias = "wcmkt"

    return _update_target_single(
        fit_id, target, remote=remote, market_flag=market_flag, db_alias=db_alias
    )


def _update_target_single(
    fit_id: int,
    target: int,
    remote: bool = False,
    market_flag: str = "primary",
    db_alias: str = "wcmkt",
) -> bool:
    """Update the target quantity for a fit in a single database.

    Updates both doctrine_fits.target and ship_targets.ship_target.
    If the fit does not exist in ship_targets, creates the record using
    metadata from doctrine_fits.
    """
    db = DatabaseConfig(db_alias)
    engine = db.remote_engine if remote else db.engine
    try:
        existing_target = get_fit_target(
            fit_id, remote=remote, db_alias=db_alias)
        if existing_target is None:
            console.print(
                f"[red]Fit {fit_id} not present in doctrine_fits for {db_alias} database[/red]"
            )
            return False

        # Get fit metadata from doctrine_fits for ship_targets upsert
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT fit_name, ship_type_id, ship_name "
                    "FROM doctrine_fits WHERE fit_id = :fit_id LIMIT 1"
                ),
                {"fit_id": fit_id},
            ).fetchone()

        if not row:
            console.print(f"[red]Fit {fit_id} not found in doctrine_fits[/red]")
            return False

        fit_name, ship_id, ship_name = row[0], row[1], row[2]

        # Update doctrine_fits
        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE doctrine_fits SET target = :target, market_flag = :market_flag "
                    "WHERE fit_id = :fit_id"
                ),
                {"target": target, "market_flag": market_flag, "fit_id": fit_id},
            )
            conn.commit()

        # Upsert ship_targets (creates if missing)
        upsert_ship_target(
            fit_id, fit_name, ship_id, ship_name, target,
            remote=remote, db_alias=db_alias,
        )

        console.print(
            f"[green]Updated target for fit {fit_id} from [yellow]{existing_target}[/yellow] "
            f"to [yellow]{target}[/yellow] for {db_alias} (remote: {remote})[/green]"
        )
        return True
    except Exception as e:
        console.print(f"[red]Failed to update target for fit {fit_id} to {target}: {e}[/red]")
        logger.exception("Error in _update_target_single")
        return False


def update_friendly_name_command(
    doctrine_id: int,
    friendly_name: str,
    remote: bool = False,
    db_alias: str = "wcmkt",
) -> bool:
    """Update friendly_name for all fits in a doctrine (local + remote)."""
    ensure_friendly_name_column(db_alias=db_alias, remote=False)
    ok = update_doctrine_friendly_name(doctrine_id, friendly_name, db_alias=db_alias, remote=False)
    if ok:
        console.print(f"[green]Updated friendly_name for doctrine_id {doctrine_id} to '{friendly_name}' (local)[/green]")
    else:
        console.print(f"[red]No rows found for doctrine_id {doctrine_id}[/red]")
        return False

    # Push to both remotes
    for target in ("wcmkt", "wcmktnorth"):
        try:
            ensure_friendly_name_column(db_alias=target, remote=True)
            remote_ok = update_doctrine_friendly_name(doctrine_id, friendly_name, db_alias=target, remote=True)
            if remote_ok:
                console.print(f"[green]Updated friendly_name on remote ({target})[/green]")
            else:
                console.print(f"[yellow]No remote rows for doctrine_id {doctrine_id} on {target}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Remote update skipped for {target}: {e}[/yellow]")

    return True


def populate_friendly_names_command(
    json_path: str = "doctrine_names.json",
    db_alias: str = "wcmkt",
) -> bool:
    """Bulk populate friendly_names from JSON — auto-syncs local + remote."""
    import os
    if not os.path.exists(json_path):
        console.print(f"[red]JSON file not found: {json_path}[/red]")
        return False

    # Local update
    ensure_friendly_name_column(db_alias=db_alias, remote=False)
    count = populate_friendly_names_from_json(json_path, db_alias=db_alias, remote=False)
    console.print(f"[green]Updated {count} rows locally ({db_alias})[/green]")

    # Sync local → both remotes (doctrine_fits should be identical on both)
    for target in ("wcmkt", "wcmktnorth"):
        ok = sync_friendly_names_to_remote(source_alias=db_alias, target_alias=target)
        if ok:
            console.print(f"[green]Synced friendly_names to remote ({target})[/green]")
        else:
            console.print(f"[yellow]Remote sync skipped for {target}[/yellow]")

    return True


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
    paste_mode: bool = False,
    friendly_name: Optional[str] = None,
    doctrine_id: Optional[int] = None,
) -> bool:
    """
    Main entry point for fit-update commands.

    Subcommands:
        add                  - Add a NEW fit from an EFT file and assign to doctrine(s)
        update               - Update an existing fit's items from an EFT file
        remove               - Completely remove a fit from ALL doctrines and targets
        assign-market        - Change the market assignment for an existing fit
        unassign-market      - Remove a fit or doctrine from a specific market
        list-fits            - List all fits in the doctrine tracking system
        list-doctrines       - List all available doctrines
        create-doctrine      - Create a new doctrine (group of fits)
        doctrine-add-fit     - Add existing fit(s) to a doctrine (supports multiple)
        doctrine-remove-fit  - Remove fit(s) from a doctrine (supports multiple)
        update-target             - Update the target quantity for a fit
        update-lead-ship          - Set or change the lead ship for a doctrine
        update-friendly-name      - Set the friendly display name for a fit
        populate-friendly-names   - Bulk populate friendly names from JSON

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
        paste_mode: whether to use pasted fit.

    Returns:
        True if command succeeded
    """
    # Determine remote flag
    use_remote = remote and not local_only

    if paste_mode and subcommand not in ("add", "update"):
        eft_text = get_multiline_input()
        if eft_text:
            print("EFT text input registered")
            file_path = "temp_file.txt"
            with open(file_path, "w") as f:
                f.write(eft_text)

        else:
            console.print("[orange]warning: eft_text not recorded")

    if subcommand == "list-fits":
        list_fits_command(db_alias=target_alias, remote=use_remote)
        return True

    elif subcommand == "list-doctrines":
        list_doctrines_command(remote=use_remote)
        return True

    elif subcommand == "assign-market":
        if doctrine_id is not None:
            return assign_doctrine_market(
                doctrine_id, market_flag, remote=use_remote, db_alias=target_alias
            )
        if fit_id is None:
            console.print(
                "[red]Error: --fit-id or --doctrine-id is required for assign-market[/red]"
            )
            return False
        result = assign_market_command(
            fit_id, market_flag, remote=use_remote, db_alias=target_alias
        )
        return bool(result.get("updated", 0))

    elif subcommand == "unassign-market":
        if doctrine_id is not None:
            return unassign_doctrine_market(
                doctrine_id, market_flag, remote=use_remote, db_alias=target_alias
            )
        if fit_id is None:
            console.print(
                "[red]Error: --fit-id or --doctrine-id is required for unassign-market[/red]"
            )
            return False
        result = unassign_market_command(
            fit_id, market_flag, remote=use_remote, db_alias=target_alias
        )
        return bool(result.get("updated", 0) or result.get("deleted", 0))

    elif subcommand == "add":
        eft_text = None
        if not file_path:
            eft_text = get_multiline_input()

        if interactive:
            return interactive_add_fit(
                fit_file=file_path,
                eft_text=eft_text,
                remote=use_remote,
                dry_run=dry_run,
                target_alias=target_alias,
                market_flag=market_flag,
            )
        else:
            if not meta_file:
                console.print(
                    "[red]Error: --meta-file is required for non-interactive add[/red]"
                )
                console.print(
                    "[dim]Use --interactive for prompted input[/dim]")
                return False

            try:
                metadata = parse_fit_metadata(meta_file)

                # Determine which databases to update
                if market_flag == "both":
                    aliases = ["wcmkt", "wcmktnorth"]
                else:
                    aliases = [target_alias]

                for alias in aliases:
                    result = update_fit_workflow(
                        fit_id=metadata.fit_id,
                        fit_file=file_path,
                        fit_metadata_file=meta_file,
                        remote=use_remote,
                        clear_existing=True,
                        dry_run=dry_run,
                        target_alias=alias,
                    )

                if dry_run:
                    console.print("[yellow]DRY RUN complete[/yellow]")
                    console.print(
                        f"Ship: {result['ship_name']
                                 } ({result['ship_type_id']})"
                    )
                    console.print(f"Items: {len(result['items'])}")
                else:
                    console.print(
                        f"[green]Successfully added fit {
                            metadata.fit_id}[/green]"
                    )

                return True

            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                logger.exception("Error in fit_update_command add")
                return False

    elif subcommand == "update":
        if fit_id is None:
            console.print(
                "[red]Error: --fit-id is required for update command[/red]")
            return False

        if not file_path:
            eft_text = get_multiline_input()
            if eft_text:
                file_path = eft_text_to_file(eft_text)

        if not file_path:
            console.print(
                "[red]Error: --file or pasted EFT text is required for update command[/red]")
            return False

        # Look up existing metadata from doctrine_fits (source of truth)
        meta_data_dict = None
        if not meta_file:
            # Query doctrine_fits from wcmkt (primary source of truth)
            db = DatabaseConfig("wcmkt")
            mkt_engine = db.remote_engine if use_remote else db.engine
            with mkt_engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT doctrine_id, fit_name, ship_name, target FROM doctrine_fits WHERE fit_id = :fit_id"),
                    {"fit_id": fit_id},
                ).fetchall()
            mkt_engine.dispose()

            if not rows:
                # Fallback: check wcmktnorth
                db_north = DatabaseConfig("wcmktnorth")
                north_engine = db_north.remote_engine if use_remote else db_north.engine
                with north_engine.connect() as conn:
                    rows = conn.execute(
                        text("SELECT doctrine_id, fit_name, ship_name, target FROM doctrine_fits WHERE fit_id = :fit_id"),
                        {"fit_id": fit_id},
                    ).fetchall()
                north_engine.dispose()

            if not rows:
                console.print(f"[red]Error: fit {fit_id} not found in doctrine_fits[/red]")
                return False

            existing_doctrine_ids = list({row[0] for row in rows})
            fit_name = rows[0][1] or f"Fit {fit_id}"
            fit_target = rows[0][3] if rows[0][3] is not None else 100

            meta_data_dict = {
                "fit_id": fit_id,
                "name": fit_name,
                "description": f"{fit_name} doctrine fit",
                "doctrine_id": existing_doctrine_ids,
                "target": fit_target,
            }

        try:
            # Update all markets where the fit currently exists
            aliases = []
            for alias in ["wcmkt", "wcmktnorth"]:
                existing = get_fit_target(fit_id, remote=use_remote, db_alias=alias)
                if existing is not None:
                    aliases.append(alias)
            if not aliases:
                aliases = [target_alias]

            result = None
            for alias in aliases:
                result = update_fit_workflow(
                    fit_id=fit_id,
                    fit_file=file_path,
                    fit_metadata_file=meta_file,
                    remote=use_remote,
                    clear_existing=True,
                    dry_run=dry_run,
                    target_alias=alias,
                    metadata_override=meta_data_dict,
                )

            if dry_run and result:
                console.print("[yellow]DRY RUN complete[/yellow]")
                console.print(f"Ship: {result['ship_name']} ({
                              result['ship_type_id']})")
                console.print(f"Items: {len(result['items'])}")
            else:
                display_names = []
                for a in aliases:
                    resolved = DatabaseConfig(a).alias
                    label = "primary" if a == "wcmkt" else "deployment"
                    display_names.append(f"{resolved} ({label})")
                console.print(f"[green]Successfully updated fit {
                              fit_id} on {', '.join(display_names)}[/green]")

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

    elif subcommand == "remove":
        if fit_id is None:
            console.print(
                "[red]Error: --fit-id is required for remove command[/red]"
            )
            return False
        # Default to both markets unless a specific market was given
        if market_flag in ("both", "primary"):
            # "primary" is the default when no --market flag is passed,
            # so treat it the same as "both" for remove.
            aliases = ["wcmkt", "wcmktnorth"]
        else:
            aliases = [target_alias]
        success = True
        for alias in aliases:
            if not remove_fit_command(
                fit_id=fit_id,
                remote=use_remote,
                db_alias=alias,
            ):
                success = False
        return success

    elif subcommand == "doctrine-remove-fit":
        # Use fit_ids if provided (comma-separated), otherwise wrap single fit_id
        if fit_ids is not None:
            fit_ids_list = fit_ids
        elif fit_id is not None:
            fit_ids_list = [fit_id]
        else:
            fit_ids_list = None  # Will prompt in interactive mode
        return doctrine_remove_fit_command(
            doctrine_id=doctrine_id,
            fit_ids=fit_ids_list,
            remote=use_remote,
            interactive=interactive or True,  # Default to interactive
            db_alias=target_alias,
        )

    elif subcommand == "update-target":
        if fit_id is None:
            console.print(
                "[red]Error: --fit-id is required for update-target command[/red]"
            )
            return False
        if not target:
            console.print(
                "[red]Error: --target is required for update-target command[/red]"
            )
            return False
        return update_target_command(
            fit_id,
            target,
            market_flag=market_flag,
            remote=use_remote,
            db_alias=target_alias,
        )
    elif subcommand == "update-lead-ship":
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required for update-lead-ship[/red]")
            return False
        if fit_id is None:
            console.print("[red]Error: --fit-id is required for update-lead-ship[/red]")
            return False
        return update_lead_ship_command(
            doctrine_id=doctrine_id,
            fit_id=fit_id,
            remote=use_remote,
            db_alias=target_alias,
        )

    elif subcommand == "update-friendly-name":
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required for update-friendly-name[/red]")
            return False
        if not friendly_name:
            console.print("[red]Error: --name is required for update-friendly-name[/red]")
            return False
        return update_friendly_name_command(
            doctrine_id, friendly_name, remote=use_remote, db_alias=target_alias,
        )

    elif subcommand == "populate-friendly-names":
        return populate_friendly_names_command(
            json_path="doctrine_names.json", db_alias=target_alias,
        )

    else:
        console.print(f"[red]Unknown subcommand: {subcommand}[/red]")
        console.print(
            "[dim]Available: add, update, remove, assign-market, unassign-market, list-fits, "
            "list-doctrines, create-doctrine, doctrine-add-fit, doctrine-remove-fit, "
            "update-target, update-lead-ship, update-friendly-name, populate-friendly-names[/dim]"
        )
        console.print(
            "[dim]Use --help for more information about a command.[/dim]")
        return False


def collect_fit_metadata_interactive(
    fit_id: int, fit_file: str, remote: bool = False
) -> dict:
    """
    Interactively collect metadata for a fit update.

    Args:
        fit_id: The fit ID being updated
        fit_file: Path to the EFT fit file (used to extract ship/fit name)
        remote: Whether to use remote database for doctrine checks

    Returns:
        Dictionary with metadata fields matching FitMetadata expectations
    """
    from mkts_backend.utils.parse_fits import (
        doctrine_exists,
        create_doctrine,
        get_next_doctrine_id,
    )

    print(f"\n--- Interactive Metadata Collection for fit_id={fit_id} ---\n")

    # Try to extract ship and fit name from the EFT file
    ship_name = ""
    fit_name = ""
    try:
        with open(fit_file, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            if first_line.startswith("[") and first_line.endswith("]"):
                clean_name = first_line.strip("[]")
                parts = clean_name.split(",")
                ship_name = parts[0].strip()
                fit_name = parts[1].strip() if len(parts) > 1 else ""
                print(f"Detected from fit file: {ship_name}, {fit_name}")
    except Exception as e:
        print(f"Could not parse fit file header: {e}")

    # Prompt for fit name (with default from file)
    default_name = fit_name if fit_name else f"{ship_name} Fit"
    name_input = input(f"Fit name [{default_name}]: ").strip()
    name = name_input if name_input else default_name

    # Prompt for description
    default_desc = f"{name} doctrine fit"
    desc_input = input(f"Description [{default_desc}]: ").strip()
    description = desc_input if desc_input else default_desc

    # Prompt for doctrine ID(s)
    next_id = get_next_doctrine_id(remote=remote)
    print(f"(Next available doctrine ID: {next_id})")
    doctrine_input = input(
        "Doctrine ID(s) (comma-separated for multiple, or 'new' to create): "
    ).strip()

    if not doctrine_input:
        raise ValueError("Doctrine ID is required")

    doctrine_ids = []
    if doctrine_input.lower() == "new":
        # Create a new doctrine
        print(f"\n--- Creating New Doctrine (ID: {next_id}) ---")
        doctrine_name = input(f"Doctrine name [{name}]: ").strip() or name
        doctrine_desc = input("Doctrine description []: ").strip()
        create_doctrine(next_id, doctrine_name, doctrine_desc, remote=remote)
        print(f"Created doctrine {next_id}: {doctrine_name}")
        doctrine_ids = [next_id]
    else:
        doctrine_ids = [int(d.strip())
                        for d in doctrine_input.split(",") if d.strip()]
        if not doctrine_ids:
            raise ValueError("At least one valid doctrine ID is required")

        # Check each doctrine exists, offer to create if not
        for doc_id in doctrine_ids:
            if not doctrine_exists(doc_id, remote=remote):
                print(f"\nDoctrine {
                      doc_id} does not exist in fittings_doctrine.")
                create_it = (
                    input(f"Create doctrine {
                          doc_id}? (y/n) [n]: ").strip().lower()
                )
                if create_it == "y":
                    doctrine_name = input(
                        f"Doctrine name [{name}]: ").strip() or name
                    doctrine_desc = input("Doctrine description []: ").strip()
                    create_doctrine(doc_id, doctrine_name,
                                    doctrine_desc, remote=remote)
                    print(f"Created doctrine {doc_id}: {doctrine_name}")
                else:
                    print(f"Warning: Doctrine {
                          doc_id} will be skipped during linking")

    doctrine_id = doctrine_ids if len(doctrine_ids) > 1 else doctrine_ids[0]

    # Prompt for target quantity
    target_input = input("Target quantity [100]: ").strip()
    target = int(target_input) if target_input else 100

    print("\nMetadata collected:")
    print(f"  fit_id: {fit_id}")
    print(f"  name: {name}")
    print(f"  description: {description}")
    print(f"  doctrine_id: {doctrine_id}")
    print(f"  target: {target}")
    print()

    return {
        "fit_id": fit_id,
        "name": name,
        "description": description,
        "doctrine_id": doctrine_id,
        "target": target,
    }
