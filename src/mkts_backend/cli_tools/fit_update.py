"""
Fit and doctrine management: the database work behind ``fit-manager``.

``fit_manager.py`` owns the CLI surface (subcommands, options, help) and
calls the ``*_command`` functions here. Writers run through
:class:`~mkts_backend.db.unit_of_work.ReplicaUnitOfWork`, which owns engine
lifecycle, per-alias transactions, and pushing every committed replica once.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from contextlib import nullcontext
from typing import List, Optional

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError, OperationalError

from mkts_backend.config import DatabaseConfig
from mkts_backend.config import db_config as _db_config_module
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.market_context import MarketContext
from mkts_backend.db.unit_of_work import ReplicaUnitOfWork
from mkts_backend.utils.db_utils import add_missing_items_to_watchlist
from mkts_backend.utils.doctrine_update import (
    DoctrineFit,
    ensure_friendly_name_column,
    get_fit_target,
    populate_friendly_names_from_json,
    refresh_doctrines_for_fit,
    remove_all_doctrine_fits_for_fit,
    remove_all_doctrine_map_for_fit,
    remove_doctrine_fits,
    remove_doctrine_map,
    remove_doctrines_for_fit,
    remove_ship_target,
    set_lead_ship,
    update_doctrine_friendly_name,
    update_fit_market_flag,
    upsert_doctrine_fits,
    upsert_doctrine_map,
    upsert_lead_ship,
    upsert_ship_target,
)
from mkts_backend.utils.eft_parser import parse_eft_file, parse_eft_string
from mkts_backend.utils.parse_fits import (
    create_doctrine,
    doctrine_exists,
    ensure_doctrine_link,
    get_doctrine_ids_for_fit,
    get_next_doctrine_id,
    remove_all_doctrine_links_for_fit,
    remove_doctrine_link,
    update_fit_workflow,
)

logger = configure_logging(__name__)
console = Console()

_DB_ERRORS = (OperationalError, DatabaseError, ConnectionError, TimeoutError)


def _uow(**kwargs) -> ReplicaUnitOfWork:
    """Create a ReplicaUnitOfWork honoring monkeypatches on either this module or db_config."""
    if DatabaseConfig is not _db_config_module.DatabaseConfig:
        kwargs.setdefault("db_factory", DatabaseConfig)
    return ReplicaUnitOfWork(**kwargs)


def _configured_market_db_aliases(market_flag: Optional[str] = None) -> List[str]:
    """Return DB aliases for configured markets, optionally filtered by ``market_flag``."""
    if market_flag is None:
        markets = MarketContext.list_available()
    else:
        from mkts_backend.cli_tools.market_args import expand_market_alias
        markets = expand_market_alias(market_flag)
    aliases: List[str] = []
    for market in markets:
        try:
            db_alias = MarketContext.from_settings(market).database_alias
        except Exception as e:
            raise RuntimeError(
                f"Failed to resolve DB alias for market '{market}' while "
                f"enumerating configured markets: {e}"
            ) from e
        if db_alias not in aliases:
            aliases.append(db_alias)
    return aliases


def _market_name_for_db_alias(db_alias: str) -> str:
    """Map a resolved DB alias back to its market name (primary, market3, …)."""
    for market in MarketContext.list_available():
        try:
            if MarketContext.from_settings(market).database_alias == db_alias:
                return market
        except Exception:
            continue
    return db_alias


def _discover_fits_across_markets(
    doctrine_id: int,
) -> tuple[List[int], List[str], List[tuple[str, str]]]:
    """Query every configured market for fits in ``doctrine_id``."""
    fit_ids: List[int] = []
    queried: List[str] = []
    skipped: List[tuple[str, str]] = []
    for source_alias in _configured_market_db_aliases():
        try:
            rows = get_doctrine_fits_from_market(doctrine_id, source_alias)
        except Exception as e:
            logger.warning(
                "Skipping market '%s' during fit discovery for doctrine %s: %s",
                source_alias, doctrine_id, e,
            )
            skipped.append((source_alias, str(e)))
            continue
        queried.append(source_alias)
        for fid in rows:
            if fid not in fit_ids:
                fit_ids.append(fid)
    return fit_ids, queried, skipped


def _render_no_fits_message(
    doctrine_id: int, queried: List[str], skipped: List[tuple[str, str]]
) -> None:
    """Render the 'no fits found' warning, distinguishing queried vs skipped."""
    parts = [f"No fits found for doctrine {doctrine_id}"]
    if queried:
        parts.append(f"in ({', '.join(queried)})")
    if skipped:
        skipped_str = ", ".join(f"{a}: {msg}" for a, msg in skipped)
        parts.append(f"(skipped: {skipped_str})")
    console.print(f"[yellow]{' '.join(parts)}[/yellow]")


def get_available_doctrines() -> List[dict]:
    """Get list of available doctrines from fittings database."""
    with _uow(auto_push=False) as uow:
        with uow.read("fittings") as conn:
            result = conn.execute(
                text("SELECT id, name, description FROM fittings_doctrine ORDER BY name")
            )
            return [
                {"id": row[0], "name": row[1], "description": row[2] or ""}
                for row in result
            ]


def get_market_doctrines(db_alias: str) -> List[dict]:
    """Get doctrines assigned to a market from its ``doctrine_fits`` table."""
    with _uow(auto_push=False) as uow:
        with uow.read(db_alias) as conn:
            result = conn.execute(
                text(
                    """
                    SELECT doctrine_id, MIN(TRIM(doctrine_name)) AS name, COUNT(DISTINCT fit_id)
                    FROM doctrine_fits
                    GROUP BY doctrine_id
                    ORDER BY name
                    """
                )
            )
            return [
                {"id": row[0], "name": row[1] or "", "fit_count": row[2]}
                for row in result
            ]


def eft_text_to_file(eft_text: str) -> str:
    """Write EFT text to a temporary file and return the file path."""
    file_path = "temp_file.txt"
    with open(file_path, "w") as f:
        f.write(eft_text)
    return file_path


def get_fits_list(db_alias: str) -> List[dict]:
    """Get list of fits from ``doctrine_fits`` table."""
    with _uow(auto_push=False) as uow:
        with uow.read(db_alias) as conn:
            try:
                result = conn.execute(
                    text(
                        """
                        SELECT fit_id, TRIM(fit_name), ship_name, TRIM(doctrine_name),
                               target, market_flag, friendly_name
                        FROM doctrine_fits
                        ORDER BY ship_name, doctrine_name, fit_name
                        """
                    )
                )
                has_market_flag = True
            except Exception:
                result = conn.execute(
                    text(
                        """
                        SELECT fit_id, fit_name, ship_name, doctrine_name, target
                        FROM doctrine_fits
                        ORDER BY doctrine_name, fit_name
                        """
                    )
                )
                has_market_flag = False

            return [
                {
                    "fit_id": row[0],
                    "fit_name": row[1],
                    "ship_name": row[2],
                    "doctrine_name": row[3],
                    "target": row[4],
                    "market_flag": row[5] if has_market_flag and len(row) > 5 else "primary",
                    "friendly_name": row[6] if has_market_flag and len(row) > 6 else None,
                }
                for row in result
            ]


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
    table.add_column("Target", justify="right", style="green", width=9)
    table.add_column("Friendly", style="dim", width=18)

    for fit in fits:
        target = fit.get("target")
        target_str = str(target) if target is not None else "[dim]--[/dim]"
        friendly = fit.get("friendly_name") or "--"

        table.add_row(
            str(fit["fit_id"]),
            fit["fit_name"],
            fit["ship_name"],
            fit["doctrine_name"],
            target_str,
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

    active_doctrines = [d for d in doctrines if not d["name"].lower().startswith("zz")]
    for doctrine in active_doctrines:
        table.add_row(str(doctrine["id"]), doctrine["name"])

    console.print(table)


def _prompt_new_doctrine() -> tuple[Optional[int], Optional[str], Optional[dict]]:
    """Prompt for a new doctrine. Writes nothing.

    Returns ``(doctrine_id, name, create_args)``. ``create_args`` holds the
    ``create_doctrine`` arguments for the caller to write once the user has
    confirmed the fit. It is ``None`` when the ID already exists, and ``name``
    is then the stored name.
    """
    next_id = get_next_doctrine_id()
    new_doctrine_id = IntPrompt.ask("[bold]New doctrine ID[/bold]", default=next_id)
    new_doctrine_name = Prompt.ask("[bold]New doctrine name[/bold]")
    if not new_doctrine_name:
        console.print("[red]Error: Doctrine name is required[/red]")
        return None, None, None
    new_doctrine_desc = Prompt.ask("[bold]Description[/bold]", default="")

    with _uow(auto_push=False) as uow:
        with uow.read("fittings") as fconn:
            existing_row = fconn.execute(
                text("SELECT name FROM fittings_doctrine WHERE id = :doctrine_id"),
                {"doctrine_id": new_doctrine_id},
            ).fetchone()

    if existing_row is not None:
        console.print(
            f"[yellow]Doctrine {new_doctrine_id} ('{existing_row[0]}') already exists, "
            "using it[/yellow]"
        )
        return new_doctrine_id, existing_row[0], None

    console.print(
        f"[dim]Doctrine {new_doctrine_id} ({new_doctrine_name}) will be created "
        "with the fit[/dim]"
    )
    return new_doctrine_id, new_doctrine_name, {
        "doctrine_id": new_doctrine_id,
        "name": new_doctrine_name,
        "description": new_doctrine_desc,
    }


def interactive_add_fit(
    fit_file: Optional[str] = None,
    eft_text: Optional[str] = None,
    dry_run: bool = False,
    target_alias: str = "wcmkt",
    market_flag: str = "primary",
) -> bool:
    """Interactively add a new fit with prompts for metadata."""
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

    console.print(
        Panel(
            f"[bold]Ship:[/bold] {parse_result.ship_name}\n"
            f"[bold]Fit Name:[/bold] {parse_result.fit_name}\n"
            f"[bold]Items:[/bold] {len(parse_result.items)}\n",
            title="[bold cyan]Parsed Fit[/bold cyan]",
            border_style="blue",
        )
    )

    if parse_result.has_missing_types:
        console.print("[yellow]Warning: Some items could not be resolved:[/yellow]")
        for item in parse_result.missing_types[:5]:
            console.print(f"  • {item}", style="yellow")
        if not Confirm.ask("Continue anyway?"):
            return False

    description = Prompt.ask(
        "[bold]Fit description[/bold]",
        default=f"{parse_result.fit_name} for {parse_result.ship_name}",
    )

    console.print()
    console.print(
        Panel(
            "[bold]Doctrine Assignment[/bold]\n\n"
            "A doctrine is a named group of fits. Select existing doctrine(s) to add this fit to,\n"
            "or create a new doctrine. A fit can belong to multiple doctrines.",
            border_style="dim",
        )
    )

    doctrines = get_available_doctrines()
    doctrine_ids: list[int] = []
    doctrine_name_map = {d["id"]: d["name"] for d in doctrines} if doctrines else {}
    new_doctrine: Optional[dict] = None

    if doctrines:
        console.print("\n[cyan]Existing doctrines:[/cyan]")
        display_doctrines_table(doctrines)
        console.print()

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
                doctrine_ids = [int(d.strip()) for d in doctrine_input.split(",") if d.strip()]
                existing_ids = {d["id"] for d in doctrines}
                invalid_ids = [did for did in doctrine_ids if did not in existing_ids]
                if invalid_ids:
                    console.print(f"[yellow]Warning: Doctrine ID(s) {invalid_ids} not found[/yellow]")
                    if not Confirm.ask("Continue with only valid IDs?"):
                        return False
                    doctrine_ids = [did for did in doctrine_ids if did in existing_ids]
        elif action == "new":
            console.print("\n[cyan]Creating a new doctrine:[/cyan]")
            new_id, new_name, new_doctrine = _prompt_new_doctrine()
            if new_id is None:
                return False
            doctrine_ids = [new_id]
            doctrine_name_map[new_id] = new_name
        else:
            console.print("[yellow]Skipping doctrine assignment[/yellow]")
            if not Confirm.ask("Continue without doctrine assignment?"):
                return False
    else:
        console.print("[yellow]No doctrines found in database[/yellow]")
        if Confirm.ask("Create a new doctrine now?"):
            new_id, new_name, new_doctrine = _prompt_new_doctrine()
            if new_id is None:
                return False
            doctrine_ids = [new_id]
            doctrine_name_map[new_id] = new_name
        else:
            console.print("[yellow]Warning: Continuing without doctrine assignment[/yellow]")

    target = IntPrompt.ask("[bold]Target quantity[/bold]", default=100)
    fit_id = IntPrompt.ask("[bold]Fit ID[/bold] (unique identifier)")

    market_choices = ["primary", "deployment", "all"]
    market_flag = Prompt.ask(
        "[bold]Market assignment[/bold]",
        choices=market_choices,
        default="all" if market_flag in ("both", "all") else (market_flag or "primary"),
    )

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

    metadata = {
        "fit_id": fit_id,
        "name": parse_result.fit_name,
        "description": description,
        "doctrine_id": (
            doctrine_ids
            if len(doctrine_ids) > 1
            else (doctrine_ids[0] if doctrine_ids else 1)
        ),
        "target": target,
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(metadata, f)
        meta_path = f.name

    eft_temp_path = None
    if not fit_file and eft_text:
        eft_temp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        eft_temp.write(eft_text)
        eft_temp.close()
        eft_temp_path = eft_temp.name
        fit_file = eft_temp_path

    try:
        if market_flag in ("both", "all"):
            target_aliases = _configured_market_db_aliases()
        else:
            target_aliases = [target_alias]

        with _uow() as uow:
            if new_doctrine:
                with uow.tx("fittings") as fconn:
                    create_doctrine(**new_doctrine, conn=fconn)
                console.print(
                    f"[green]Created doctrine {new_doctrine['doctrine_id']}: "
                    f"{new_doctrine['name']}[/green]"
                )

            update_fit_workflow(
                fit_id=fit_id,
                fit_file=fit_file,
                fit_metadata_file=meta_path,
                clear_existing=True,
                dry_run=False,
                target_aliases=target_aliases,
                uow=uow,
            )

            for alias in target_aliases:
                if alias in uow.failed:
                    continue
                try:
                    if uow.db(alias).alias in uow.failed:
                        continue
                    with uow.tx(alias) as conn:
                        update_fit_market_flag(conn, fit_id, market_flag, db_label=alias)
                        for did in doctrine_ids:
                            d_name = doctrine_name_map.get(did, "")
                            inserted = upsert_lead_ship(
                                conn,
                                doctrine_id=did,
                                doctrine_name=d_name,
                                fit_id=fit_id,
                                ship_type_id=parse_result.ship_type_id,
                            )
                            if inserted:
                                console.print(
                                    f"[green]Set lead ship for doctrine {did}: "
                                    f"{parse_result.ship_name}[/green]"
                                )
                    console.print(f"[green]Successfully added fit {fit_id} to {alias}[/green]")
                except Exception as exc:
                    console.print(f"[red]Error updating market metadata on {alias}: {exc}[/red]")
                    logger.exception("Error updating market metadata on %s in interactive_add_fit", alias)

        return uow.ok

    except Exception as e:
        console.print(f"[red]Error adding fit: {e}[/red]")
        logger.exception("Error in interactive_add_fit")
        return False

    finally:
        os.unlink(meta_path)
        if eft_temp_path:
            os.unlink(eft_temp_path)


def _find_doctrine_fits_rows_with_fallback(
    fit_id: int,
    db_alias: str,
    doctrine_id: Optional[int] = None,
) -> List[dict]:
    """Query ``doctrine_fits`` in ``db_alias``, falling back to other configured markets."""
    rows = _get_doctrine_fits_rows(fit_id, db_alias, doctrine_id)
    if rows:
        return rows
    for fallback in _configured_market_db_aliases():
        if fallback == db_alias:
            continue
        rows = _get_doctrine_fits_rows(fit_id, fallback, doctrine_id)
        if rows:
            logger.info(f"Fit {fit_id} not in {db_alias}, found in {fallback}")
            return rows
    return []


def assign_market_command(
    fit_id: int,
    market_flag: str,
    db_alias: str = "wcmkt",
    doctrine_id: Optional[int] = None,
    skip_confirm: bool = False,
) -> dict:
    """Assign a market flag to a fit with preview and confirmation."""
    if market_flag not in ("primary", "deployment", "both", "all"):
        console.print(
            f"[red]Error: invalid market '{market_flag}'. "
            "Must be 'primary', 'deployment', or 'all'[/red]"
        )
        return {}

    try:
        rows = _find_doctrine_fits_rows_with_fallback(fit_id, db_alias, doctrine_id)
        if not rows:
            console.print(
                f"[yellow]No doctrine_fits rows found for fit {fit_id}"
                + (f" in doctrine {doctrine_id}" if doctrine_id else "")
                + "[/yellow]"
            )
            return {}

        plans = [_plan_market_action(row, market_flag, mode="assign") for row in rows]

        if not skip_confirm:
            _display_market_preview(plans, market_flag, mode="assign")
            if not Confirm.ask("[bold]Proceed with these changes?[/bold]"):
                console.print("[yellow]Cancelled[/yellow]")
                return {}

        return _execute_market_plan(plans, db_alias=db_alias)

    except Exception as e:
        console.print(f"[red]Error in assign-market: {e}[/red]")
        logger.exception("Error in assign_market_command")
        return {}


def assign_doctrine_market(
    doctrine_id: int,
    market_flag: str,
    db_alias: str = "wcmkt",
) -> bool:
    """Assign a market flag to all fits in a doctrine."""
    if market_flag not in ("primary", "deployment", "both", "all"):
        console.print(
            f"[red]Error: invalid market '{market_flag}'. "
            "Must be 'primary', 'deployment', or 'all'[/red]"
        )
        return False

    fit_ids, queried_aliases, skipped = _discover_fits_across_markets(doctrine_id)
    if not fit_ids:
        _render_no_fits_message(doctrine_id, queried_aliases, skipped)
        return False

    all_doctrines = get_available_doctrines()
    doctrine_name = next(
        (d["name"] for d in all_doctrines if d["id"] == doctrine_id),
        f"ID {doctrine_id}",
    )

    all_plans = []
    for fid in fit_ids:
        for row in _find_doctrine_fits_rows_with_fallback(fid, db_alias, doctrine_id):
            all_plans.append(_plan_market_action(row, market_flag, mode="assign"))

    if not all_plans:
        console.print(f"[yellow]No doctrine_fits rows found for doctrine {doctrine_id}[/yellow]")
        return False

    _display_market_preview(all_plans, market_flag, mode="assign", doctrine_name=doctrine_name)

    prompt_msg = (
        f"[bold]Assign doctrine '{escape(doctrine_name)}' to "
        f"'{market_flag}' market?[/bold]"
    )
    if not Confirm.ask(prompt_msg):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    console.print()
    result = _execute_market_plan(all_plans, db_alias=db_alias)

    summary = (
        f"\n[bold]Summary:[/bold] {result['updated']} updated, "
        f"{result['skipped']} skipped"
    )
    if result.get("step_failures"):
        summary += f", [red]{result['step_failures']} step failures[/red]"
    console.print(summary)
    return (
        result["updated"] > 0
        and not result.get("push_failed")
        and not result.get("bucket_failures")
        and not result.get("step_failures")
    )


def _flag_to_aliases(flag: str) -> set[str]:
    """Return the set of explicit database aliases a ``market_flag`` implies.

    ``market3`` is shimmed to ``primary`` — a fit stocked in ``primary`` is also
    stocked in ``market3``. ``both`` (legacy) and ``all`` cover every configured
    market.
    """
    available = set(MarketContext.list_available())

    def _alias(market: str) -> Optional[str]:
        return MarketContext.from_settings(market).database_alias if market in available else None

    primary = _alias("primary")
    deployment = _alias("deployment")
    market3 = _alias("market3")
    all_markets = set(_configured_market_db_aliases())

    flag_map: dict[str, set[str]] = {
        "primary": {a for a in (primary, market3) if a},
        "deployment": {a for a in (deployment,) if a},
        "market3": {a for a in (market3,) if a},
        "both": all_markets,
        "all": all_markets,
    }
    if flag not in flag_map:
        logger.warning(f"Unexpected market_flag '{flag}' — treating as empty alias set")
    return flag_map.get(flag, set())


def _needs_provisioning(conn, fit_id: int, doctrine_id: int | None = None) -> bool:
    """Return True if a fit is missing doctrine_fits, doctrines, ship_targets, or lead_ships rows."""
    if doctrine_id is not None:
        df = conn.execute(
            text(
                "SELECT 1 FROM doctrine_fits "
                "WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"
            ),
            {"fit_id": fit_id, "doctrine_id": doctrine_id},
        ).fetchone()
    else:
        df = conn.execute(
            text("SELECT 1 FROM doctrine_fits WHERE fit_id = :fit_id"),
            {"fit_id": fit_id},
        ).fetchone()
    doc_count = conn.execute(
        text("SELECT COUNT(*) FROM doctrines WHERE fit_id = :fit_id"),
        {"fit_id": fit_id},
    ).fetchone()[0]
    st = conn.execute(
        text("SELECT 1 FROM ship_targets WHERE fit_id = :fit_id"),
        {"fit_id": fit_id},
    ).fetchone()
    ls_missing = False
    if doctrine_id is not None:
        ls = conn.execute(
            text("SELECT 1 FROM lead_ships WHERE doctrine_id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()
        ls_missing = ls is None
    return df is None or doc_count == 0 or st is None or ls_missing


def _check_fit_orphaned(conn, fit_id: int) -> bool:
    """Return True if ``fit_id`` has no remaining ``doctrine_fits`` rows."""
    return (
        conn.execute(
            text("SELECT COUNT(*) FROM doctrine_fits WHERE fit_id = :fit_id"),
            {"fit_id": fit_id},
        ).fetchone()[0]
        == 0
    )


def _get_doctrine_fits_rows(
    fit_id: int,
    db_alias: str = "wcmkt",
    doctrine_id: Optional[int] = None,
) -> List[dict]:
    """Get ``doctrine_fits`` rows for a fit, optionally filtered by ``doctrine_id``."""
    with _uow(auto_push=False) as uow:
        with uow.read(db_alias) as conn:
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
    return [
        {
            "doctrine_id": r[0],
            "fit_id": r[1],
            "market_flag": r[2],
            "doctrine_name": r[3],
            "fit_name": r[4],
            "ship_name": r[5],
            "ship_type_id": r[6],
            "target": r[7],
        }
        for r in result
    ]


def find_tracked_fit(fit_id: int) -> tuple[List[str], Optional[dict]]:
    """Find the markets that track ``fit_id`` and the metadata they store."""
    db_aliases: List[str] = []
    rows: List[dict] = []
    for alias in _configured_market_db_aliases():
        alias_rows = _get_doctrine_fits_rows(fit_id, alias)
        if alias_rows:
            db_aliases.append(alias)
            rows = rows or alias_rows
    if not rows:
        return [], None

    fit_name = rows[0]["fit_name"] or f"Fit {fit_id}"
    target = rows[0]["target"]
    return db_aliases, {
        "fit_id": fit_id,
        "name": fit_name,
        "description": f"{fit_name} doctrine fit",
        "doctrine_id": sorted({row["doctrine_id"] for row in rows}),
        "target": target if target is not None else 100,
    }


def _plan_market_action(
    row: dict,
    target_market: str,
    mode: str = "unassign",
) -> dict:
    """Compute the planned action for a single ``doctrine_fits`` row."""
    current_flag = row["market_flag"]
    plan = {**row}

    if mode == "assign":
        if current_flag == target_market:
            plan["action"] = "skip"
            plan["new_flag"] = current_flag
            plan["reason"] = f"Already '{current_flag}'"
        else:
            plan["action"] = "update"
            plan["new_flag"] = target_market
            plan["reason"] = f"Change '{current_flag}' → '{target_market}'"
    else:
        if target_market in ("both", "all"):
            plan["action"] = "remove"
            plan["new_flag"] = None
            plan["reason"] = "Remove from all markets"
        elif current_flag in ("both", "all"):
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

    counts: dict[str, int] = {}
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


def _prepare_watchlist_for_fit(
    p: dict,
    alias: str,
    *,
    conn=None,
    fittings_conn=None,
) -> None:
    """Ensure all component type_ids for a fit are present in ``alias``'s watchlist."""
    if fittings_conn is not None:
        item_rows = fittings_conn.execute(
            text("SELECT DISTINCT type_id FROM fittings_fittingitem WHERE fit_id = :fit_id"),
            {"fit_id": p["fit_id"]},
        ).fetchall()
    else:
        with _uow(auto_push=False) as uow:
            with uow.read("fittings") as fconn:
                item_rows = fconn.execute(
                    text("SELECT DISTINCT type_id FROM fittings_fittingitem WHERE fit_id = :fit_id"),
                    {"fit_id": p["fit_id"]},
                ).fetchall()
    component_ids = [r.type_id for r in item_rows]
    component_ids.append(p["ship_type_id"])
    add_missing_items_to_watchlist(component_ids, db_alias=alias, conn=conn)


def _provision_fit_in_market(
    conn,
    p: dict,
    market_flag: str,
    *,
    fittings_conn=None,
) -> bool:
    """Write every per-fit-per-market row inside the caller's transaction."""
    doctrine_fit = DoctrineFit.from_resolved(
        doctrine_id=p["doctrine_id"],
        fit_id=p["fit_id"],
        target=p["target"],
        doctrine_name=p["doctrine_name"],
        fit_name=p["fit_name"],
        ship_type_id=p["ship_type_id"],
        ship_name=p["ship_name"],
    )
    upsert_doctrine_fits(conn, doctrine_fit, market_flag=market_flag)
    upsert_doctrine_map(conn, p["doctrine_id"], p["fit_id"])
    upsert_ship_target(
        conn,
        p["fit_id"],
        p["fit_name"],
        p["ship_type_id"],
        p["ship_name"],
        p["target"],
    )
    refresh_doctrines_for_fit(
        conn,
        p["fit_id"],
        p["ship_type_id"],
        p["ship_name"],
        fittings_conn=fittings_conn,
    )
    return upsert_lead_ship(
        conn,
        doctrine_id=p["doctrine_id"],
        doctrine_name=p["doctrine_name"],
        fit_id=p["fit_id"],
        ship_type_id=p["ship_type_id"],
    )


def _cleanup_fit_in_market(conn, fit_id: int, doctrine_id: int) -> None:
    """Remove doctrine_fits / doctrine_map for a fit; strip doctrines + ship_targets if orphaned."""
    remove_doctrine_fits(conn, doctrine_id, fit_id)
    remove_doctrine_map(conn, doctrine_id, fit_id)
    if _check_fit_orphaned(conn, fit_id):
        removed = remove_doctrines_for_fit(conn, fit_id)
        if removed:
            console.print(
                f"  [dim]Cleaned up {removed} orphaned doctrines rows for fit {fit_id}[/dim]"
            )
        remove_ship_target(conn, fit_id)
        console.print(f"  [dim]Cleaned up ship_targets for fit {fit_id}[/dim]")


def _report_lead_ship(adopted: bool, fit_id: int, doctrine_id: int) -> None:
    """Emit a user-visible message describing ``lead_ships`` outcome."""
    if adopted:
        console.print(
            f"  [green]Adopted[/green] fit {fit_id} as lead ship for doctrine {doctrine_id}"
        )
    else:
        console.print(
            f"  [dim]Lead ship for doctrine {doctrine_id} already set — preserved[/dim]"
        )


def _apply_step(conn, step_type: str, p: dict, arg, alias: str = "") -> bool:
    """Apply one provisioning step within a caller-owned transaction."""
    fit_id = p["fit_id"]
    doctrine_id = p["doctrine_id"]

    if step_type == "update_and_heal":
        new_flag = arg
        update_fit_market_flag(
            conn, fit_id, new_flag, doctrine_id=doctrine_id, db_label=alias
        )
        if _needs_provisioning(conn, fit_id, doctrine_id=doctrine_id):
            adopted = _provision_fit_in_market(conn, p, market_flag=new_flag)
            console.print(f"  [green]Provisioned[/green] missing data for fit {fit_id}")
            _report_lead_ship(adopted, fit_id, doctrine_id)
            return True
        return False

    if step_type == "provision":
        adopted = _provision_fit_in_market(conn, p, market_flag=arg)
        _report_lead_ship(adopted, fit_id, doctrine_id)
        return True

    if step_type == "cleanup":
        _cleanup_fit_in_market(conn, fit_id, doctrine_id)
        return True

    if step_type == "heal_if_needed":
        new_flag = arg
        if _needs_provisioning(conn, fit_id, doctrine_id=doctrine_id):
            adopted = _provision_fit_in_market(conn, p, market_flag=new_flag)
            console.print(f"  [green]Provisioned[/green] missing data for fit {fit_id}")
            _report_lead_ship(adopted, fit_id, doctrine_id)
            return True
        return False

    if step_type == "remove_row":
        remove_doctrine_fits(conn, doctrine_id, fit_id)
        remove_doctrine_map(conn, doctrine_id, fit_id)
        return True

    raise ValueError(f"Unknown step type: {step_type}")


def _execute_market_plan(plans: List[dict], db_alias: str = "wcmkt") -> dict:
    """Execute a list of planned assign / unassign / heal actions.

    Groups every write by market DB alias so each replica is opened once,
    written inside one transaction per phase via :class:`ReplicaUnitOfWork`,
    and pushed once at exit (skipping any alias whose transaction rolled back).
    """
    configured_aliases = _configured_market_db_aliases()
    if not configured_aliases:
        console.print(
            "[red]Error: no markets configured — aborting plan execution.[/red] "
            "Check `settings.toml` for a `[market.*]` section."
        )
        logger.error("_execute_market_plan aborted: _configured_market_db_aliases() returned []")
        return {"updated": 0, "deleted": 0, "skipped": len(plans), "step_failures": 0}

    buckets: dict[str, list] = defaultdict(list)
    counters = {"updated": 0, "deleted": 0, "skipped": 0, "step_failures": 0}
    deleted_fit_ids: set[int] = set()
    heal_worked: dict[int, bool] = {}
    plan_by_fit: dict[int, dict] = {}

    with _uow() as uow:
        canonical_db_alias = uow.db(db_alias).alias

        # Phase 1: bucket every action by target alias
        for p in plans:
            fit_id = p["fit_id"]
            doc_id = p["doctrine_id"]
            plan_by_fit[fit_id] = p
            action = p["action"]

            if action == "update":
                old_aliases = _flag_to_aliases(p["market_flag"])
                new_aliases = _flag_to_aliases(p["new_flag"])
                new_flag = p["new_flag"]

                for alias in old_aliases & new_aliases:
                    buckets[alias].append(("update_and_heal", p, new_flag))
                for alias in new_aliases - old_aliases:
                    buckets[alias].append(("provision", p, new_flag))
                for alias in old_aliases - new_aliases:
                    buckets[alias].append(("cleanup", p, None))

                counters["updated"] += 1
                console.print(
                    f"  [green]Planned update[/green] fit {fit_id}: "
                    f"market_flag '{p['market_flag']}' -> '{new_flag}'"
                )

            elif action == "remove":
                buckets[canonical_db_alias].append(("remove_row", p, None))
                deleted_fit_ids.add(fit_id)
                counters["deleted"] += 1
                console.print(
                    f"  [red]Planned remove[/red] fit {fit_id} from doctrine {doc_id} "
                    f"({p.get('doctrine_name', '?')})"
                )

            else:  # skip / heal
                heal_worked[id(p)] = False
                for alias in _flag_to_aliases(p["new_flag"]):
                    buckets[alias].append(("heal_if_needed", p, p["new_flag"]))

        # Phase 2: collect watchlist needs per alias (written inside Phase 3's UoW tx)
        watchlist_needs_by_alias: dict[str, list[int]] = defaultdict(list)
        for alias, steps in buckets.items():
            seen_w: set[int] = set()
            for step_type, p, _ in steps:
                if step_type in ("provision", "heal_if_needed", "update_and_heal"):
                    fid_w = p["fit_id"]
                    if fid_w not in seen_w:
                        seen_w.add(fid_w)
                        watchlist_needs_by_alias[alias].append(fid_w)

        # Phase 3: execute each bucket (including watchlist prep) in a single transaction
        fittings_cm = uow.read("fittings") if watchlist_needs_by_alias else nullcontext(None)
        with fittings_cm as fconn:
            for alias, steps in buckets.items():
                current_fit_id: int | None = None
                try:
                    with uow.tx(alias) as conn:
                        for fit_id_w in watchlist_needs_by_alias.get(alias, ()):
                            current_fit_id = fit_id_w
                            try:
                                _prepare_watchlist_for_fit(
                                    plan_by_fit[fit_id_w],
                                    alias,
                                    conn=conn,
                                    fittings_conn=fconn,
                                )
                            except _DB_ERRORS:
                                raise
                            except Exception as e:
                                console.print(
                                    f"[red]Watchlist prep failed for fit {fit_id_w} on {alias}: {e}[/red]"
                                )
                                logger.exception(
                                    "Unexpected error preparing watchlist for fit %s on %s",
                                    fit_id_w,
                                    alias,
                                )

                        for step_type, p, arg in steps:
                            current_fit_id = p["fit_id"]
                            try:
                                did_work = _apply_step(conn, step_type, p, arg, alias=alias)
                            except _DB_ERRORS:
                                raise
                            except Exception as step_err:
                                console.print(
                                    f"[red]Step {step_type} failed for fit {current_fit_id} on {alias}: "
                                    f"{step_err}[/red] (bucket continues)"
                                )
                                logger.exception(
                                    "Non-DB error in %s for fit %s on %s",
                                    step_type, current_fit_id, alias,
                                )
                                counters["step_failures"] += 1
                                continue
                            if step_type == "heal_if_needed" and did_work:
                                heal_worked[id(p)] = True
                except _DB_ERRORS as e:
                    console.print(
                        f"[yellow]Skipped {alias} (rolled back at fit {current_fit_id}): {e}[/yellow]"
                    )

        for did in heal_worked.values():
            if did:
                counters["updated"] += 1
            else:
                counters["skipped"] += 1

        # Phase 4: orphan cleanup for deleted fits (one transaction per configured alias)
        if deleted_fit_ids:
            for alias in configured_aliases:
                current_fit_id = None
                try:
                    with uow.tx(alias) as conn:
                        for fid in deleted_fit_ids:
                            current_fit_id = fid
                            if _check_fit_orphaned(conn, fid):
                                removed = remove_doctrines_for_fit(conn, fid)
                                st_removed = remove_ship_target(conn, fid)
                                if removed:
                                    console.print(
                                        f"  [dim]Cleaned up {removed} orphaned doctrines rows "
                                        f"for fit {fid} on {alias}[/dim]"
                                    )
                                if st_removed:
                                    console.print(
                                        f"  [dim]Cleaned up ship_targets for fit {fid} on {alias}[/dim]"
                                    )
                except _DB_ERRORS as e:
                    console.print(
                        f"[yellow]Orphan cleanup skipped for {alias} "
                        f"(rolled back at fit {current_fit_id}): {e}[/yellow]"
                    )

    counters["push_failed"] = uow.push_failed
    counters["bucket_failures"] = len(uow.failed)
    return counters


def unassign_market_command(
    fit_id: int,
    market_to_remove: str,
    db_alias: str = "wcmkt",
    doctrine_id: Optional[int] = None,
    skip_confirm: bool = False,
) -> dict:
    """Remove a fit from a specific market (or all)."""
    if market_to_remove not in ("primary", "deployment", "both", "all"):
        console.print(
            f"[red]Error: invalid market '{market_to_remove}'. "
            "Must be 'primary', 'deployment', or 'all'[/red]"
        )
        return {}

    try:
        rows = _find_doctrine_fits_rows_with_fallback(fit_id, db_alias, doctrine_id)
        if not rows:
            console.print(
                f"[yellow]No doctrine_fits rows found for fit {fit_id}"
                + (f" in doctrine {doctrine_id}" if doctrine_id else "")
                + "[/yellow]"
            )
            return {}

        plans = [_plan_market_action(row, market_to_remove, mode="unassign") for row in rows]

        if not skip_confirm:
            _display_market_preview(plans, market_to_remove)
            if not Confirm.ask("[bold]Proceed with these changes?[/bold]"):
                console.print("[yellow]Cancelled[/yellow]")
                return {}

        return _execute_market_plan(plans, db_alias=db_alias)

    except Exception as e:
        console.print(f"[red]Error in unassign-market: {e}[/red]")
        logger.exception("Error in unassign_market_command")
        return {}


def unassign_doctrine_market(
    doctrine_id: int,
    market_to_remove: str,
    db_alias: str = "wcmkt",
) -> bool:
    """Remove an entire doctrine from a specific market (or all)."""
    if market_to_remove not in ("primary", "deployment", "both", "all"):
        console.print(
            f"[red]Error: invalid market '{market_to_remove}'. "
            "Must be 'primary', 'deployment', or 'all'[/red]"
        )
        return False

    fit_ids, queried_aliases, skipped = _discover_fits_across_markets(doctrine_id)
    if not fit_ids:
        _render_no_fits_message(doctrine_id, queried_aliases, skipped)
        return False

    all_doctrines = get_available_doctrines()
    doctrine_name = next(
        (d["name"] for d in all_doctrines if d["id"] == doctrine_id), f"ID {doctrine_id}"
    )

    all_plans = []
    for fid in fit_ids:
        for row in _find_doctrine_fits_rows_with_fallback(fid, db_alias, doctrine_id):
            all_plans.append(_plan_market_action(row, market_to_remove, mode="unassign"))

    if not all_plans:
        console.print(f"[yellow]No doctrine_fits rows found for doctrine {doctrine_id}[/yellow]")
        return False

    _display_market_preview(all_plans, market_to_remove, doctrine_name=doctrine_name)

    shown_name = escape(doctrine_name)
    if market_to_remove in ("both", "all"):
        prompt_msg = (
            f"[bold red]Remove doctrine '{shown_name}' from ALL markets? "
            "This cannot be undone.[/bold red] "
            "Fits will be maintained for other doctrines that use them."
        )
    else:
        prompt_msg = (
            f"[bold]Remove doctrine '{shown_name}' from "
            f"'{market_to_remove}' market?[/bold]"
        )

    if not Confirm.ask(prompt_msg):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    console.print()
    result = _execute_market_plan(all_plans, db_alias=db_alias)

    summary = (
        f"\n[bold]Summary:[/bold] {result['updated']} updated, "
        f"{result['deleted']} removed, {result['skipped']} skipped"
    )
    if result.get("step_failures"):
        summary += f", [red]{result['step_failures']} step failures[/red]"
    console.print(summary)
    return (
        (result["updated"] > 0 or result["deleted"] > 0)
        and not result.get("push_failed")
        and not result.get("bucket_failures")
        and not result.get("step_failures")
    )


def list_fits_command(db_alias: str = "wcmkt") -> None:
    """List all doctrine fits and their target quantity for the given market."""
    fits = get_fits_list(db_alias=db_alias)
    fits.sort(key=lambda f: (f["ship_name"], f["fit_name"]))
    if fits:
        display_fits_table(fits)
        console.print(f"\n[dim]Total: {len(fits)} fits[/dim]")
    else:
        console.print("[yellow]No fits found[/yellow]")


def list_doctrines_command(db_alias: str) -> None:
    """List doctrines assigned to the given market (excludes deprecated 'zz' prefixed)."""
    doctrines = get_market_doctrines(db_alias=db_alias)
    active_doctrines = [d for d in doctrines if not d["name"].lower().startswith("zz")]
    if active_doctrines:
        display_doctrines_table(doctrines)
        console.print(f"\n[dim]Total: {len(active_doctrines)} doctrines[/dim]")
    else:
        console.print("[yellow]No doctrines found[/yellow]")


def create_doctrine_command(
    name: Optional[str] = None,
    description: Optional[str] = None,
    doctrine_id: Optional[int] = None,
    interactive: bool = True,
) -> bool:
    """Create a new doctrine in ``fittings`` and push it to Turso."""
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

        doctrines = get_available_doctrines()
        if doctrines:
            console.print("\n[dim]Existing doctrines for reference:[/dim]")
            display_doctrines_table(doctrines)
            console.print()

        next_id = get_next_doctrine_id()
        if doctrine_id is None:
            doctrine_id = IntPrompt.ask("[bold]Doctrine ID[/bold]", default=next_id)

        if name is None:
            name = Prompt.ask("[bold]Doctrine name[/bold]")
            if not name:
                console.print("[red]Error: Doctrine name is required[/red]")
                return False

        if description is None:
            description = Prompt.ask("[bold]Description[/bold]", default="")

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
        if name is None:
            console.print("[red]Error: --name is required for non-interactive mode[/red]")
            return False
        if doctrine_id is None:
            doctrine_id = get_next_doctrine_id()

    try:
        with _uow() as uow:
            with uow.read("fittings") as fconn:
                if doctrine_exists(doctrine_id, conn=fconn):
                    console.print(f"[yellow]Doctrine {doctrine_id} already exists[/yellow]")
                    return False
            with uow.tx("fittings", auto_touch=False) as fconn:
                success = create_doctrine(
                    doctrine_id=doctrine_id,
                    name=name,
                    description=description or "",
                    conn=fconn,
                )
            if success:
                uow.mark_touched("fittings")
        if not success or not uow.ok:
            return False
        console.print(f"[green]Successfully created doctrine {doctrine_id}: {name}[/green]")
        return True
    except Exception as e:
        console.print(f"[red]Error creating doctrine: {e}[/red]")
        logger.exception("Error in create_doctrine_command")
        return False


def get_fit_info(fit_id: int) -> Optional[dict]:
    """Get fit info from fittings database (with ship name from SDE)."""
    with _uow(auto_push=False) as uow:
        with uow.read("fittings") as fconn:
            result = fconn.execute(
                text(
                    """
                    SELECT id, name, description, ship_type_id
                    FROM fittings_fitting
                    WHERE id = :fit_id
                    """
                ),
                {"fit_id": fit_id},
            ).fetchone()
        if not result:
            return None

        with uow.read("sde") as sconn:
            ship_name = sconn.execute(
                text("SELECT typeName FROM sdetypes WHERE typeID = :type_id"),
                {"type_id": result[3]},
            ).scalar()

    return {
        "fit_id": result[0],
        "fit_name": result[1],
        "description": result[2],
        "ship_type_id": result[3],
        "ship_name": ship_name or "Unknown",
    }


def get_doctrine_fits(doctrine_id: int) -> List[int]:
    """Get list of fit IDs already in a doctrine (from fittings DB link table)."""
    with _uow(auto_push=False) as uow:
        with uow.read("fittings") as conn:
            result = conn.execute(
                text(
                    "SELECT fitting_id FROM fittings_doctrine_fittings "
                    "WHERE doctrine_id = :doctrine_id"
                ),
                {"doctrine_id": doctrine_id},
            ).fetchall()
    return [row[0] for row in result]


def get_doctrine_fits_from_market(doctrine_id: int, db_alias: str = "wcmkt") -> List[int]:
    """Get list of fit IDs already in a doctrine from the market database."""
    with _uow(auto_push=False) as uow:
        with uow.read(db_alias) as conn:
            result = conn.execute(
                text("SELECT fit_id FROM doctrine_fits WHERE doctrine_id = :doctrine_id"),
                {"doctrine_id": doctrine_id},
            ).fetchall()
    return [row[0] for row in result]


def _lookup_fit_target(fit_id: int, db_alias: str) -> Optional[int]:
    """Read existing target for ``fit_id`` from ``db_alias``."""
    with _uow(auto_push=False) as uow:
        with uow.read(db_alias) as conn:
            return get_fit_target(conn, fit_id)


def doctrine_add_fit_command(
    doctrine_id: Optional[int] = None,
    fit_ids: Optional[List[int]] = None,
    target: int = 100,
    market_flag: str = "primary",
    interactive: bool = True,
    db_alias: str = "wcmkt",
    skip_targets: bool = False,
) -> bool:
    """Link existing fit(s) to a doctrine and provision market tracking."""
    fit_targets: dict[int, int] = {}

    if market_flag in ("both", "all"):
        target_aliases = _configured_market_db_aliases()
    else:
        target_aliases = [db_alias]

    def _get_existing_fit_ids_for_doctrine(did: int) -> set[int]:
        fits_per_alias = [
            set(get_doctrine_fits_from_market(did, alias))
            for alias in target_aliases
        ]
        return set.intersection(*fits_per_alias) if fits_per_alias else set()

    doctrines = get_available_doctrines()
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
        if not doctrines:
            console.print(
                "[yellow]No doctrines found. Create one first with 'create-doctrine'.[/yellow]"
            )
            return False
        console.print()
        display_doctrines_table(doctrines)
        console.print()
        if doctrine_id is None:
            doctrine_id = IntPrompt.ask("[bold]Doctrine ID[/bold] to add fit(s) to")
    else:
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required[/red]")
            return False
        if not fit_ids:
            console.print("[red]Error: --fit-id is required (comma-separated for multiple)[/red]")
            return False

    doctrine_info = next((d for d in doctrines if d["id"] == doctrine_id), None)
    if not doctrine_info:
        console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
        return False

    existing_fit_ids = _get_existing_fit_ids_for_doctrine(doctrine_id)

    if interactive:
        console.print(f"\n[cyan]Selected doctrine:[/cyan] {doctrine_info['name']}")
        if existing_fit_ids:
            console.print(
                f"[dim]Currently has {len(existing_fit_ids)} fit(s): {existing_fit_ids}[/dim]"
            )
        if not fit_ids:
            fit_input = Prompt.ask(
                "\n[bold]Fit ID(s)[/bold] to add (comma-separated for multiple)"
            )
            if not fit_input:
                console.print("[red]Error: At least one fit ID is required[/red]")
                return False
            fit_ids = [int(f.strip()) for f in fit_input.split(",") if f.strip()]

    valid_fits: list[dict] = []
    invalid_fits: list[int] = []
    already_added: list[int] = []
    for fid in fit_ids:
        fit_info = get_fit_info(fid)
        if not fit_info:
            invalid_fits.append(fid)
        elif fid in existing_fit_ids:
            already_added.append(fid)
        else:
            valid_fits.append(fit_info)

    if invalid_fits:
        console.print(f"[red]Not found in fittings database:[/red] {invalid_fits}")
    if already_added:
        console.print(f"[yellow]Already in doctrine (skipping):[/yellow] {already_added}")
    if not valid_fits:
        console.print("[red]No valid fits to add[/red]")
        return False

    for fit in valid_fits:
        fit["existing_target"] = _lookup_fit_target(fit["fit_id"], db_alias)

    if interactive:
        console.print(f"\n[green]Valid fits to add ({len(valid_fits)}):[/green]")
        fit_table = Table(box=box.SIMPLE)
        fit_table.add_column("Fit ID", style="dim")
        fit_table.add_column("Fit Name", style="white")
        fit_table.add_column("Ship", style="cyan")
        fit_table.add_column("Existing Target", style="yellow", justify="right")
        for fit in valid_fits:
            existing_target = fit["existing_target"]
            target_display = (
                str(existing_target) if existing_target is not None else "[dim]none[/dim]"
            )
            fit_table.add_row(
                str(fit["fit_id"]), fit["fit_name"], fit["ship_name"], target_display
            )
        console.print(fit_table)

        market_flag = Prompt.ask(
            "\n[bold]Market assignment[/bold]",
            choices=["primary", "deployment", "all"],
            default="all" if market_flag in ("both", "all") else (market_flag or "primary"),
        )
        target_aliases = (
            _configured_market_db_aliases() if market_flag in ("both", "all") else [db_alias]
        )

        if skip_targets:
            console.print(
                "\n[dim]Skipping target prompts (--skip-targets). "
                "Existing targets will be preserved.[/dim]"
            )
            for fit in valid_fits:
                fit_targets[fit["fit_id"]] = (
                    fit["existing_target"] if fit["existing_target"] is not None else target
                )
        else:
            console.print(
                "\n[bold]Set target for each fit[/bold] "
                "(press Enter to keep existing or use default):"
            )
            for fit in valid_fits:
                default_val = (
                    fit["existing_target"] if fit["existing_target"] is not None else target
                )
                fit_targets[fit["fit_id"]] = IntPrompt.ask(
                    f"  {fit['fit_name']} ({fit['ship_name']})", default=default_val
                )

        console.print()
        targets_summary = "\n".join(
            f"  • {fit['fit_name']}: {fit_targets[fit['fit_id']]}" for fit in valid_fits
        )
        console.print(
            Panel(
                f"[bold]Doctrine:[/bold] {doctrine_info['name']} (ID: {doctrine_id})\n"
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
        for fit in valid_fits:
            existing_target = fit["existing_target"]
            fit_targets[fit["fit_id"]] = (
                existing_target if (skip_targets and existing_target is not None) else target
            )

    success_count = 0
    fail_count = 0
    prepared: list[tuple[dict, DoctrineFit, int]] = []
    fit_alias_success: dict[int, int] = {}

    with _uow() as uow:
        # Stage 1: per-fit preparation (fittings DB link + DoctrineFit resolution on UoW conns)
        with uow.read("sde") as sconn:
            for fit_info in valid_fits:
                fit_id = fit_info["fit_id"]
                fit_target = fit_targets.get(fit_id, target)
                try:
                    with uow.tx("fittings") as fconn:
                        ensure_doctrine_link(doctrine_id, fit_id, conn=fconn)
                        doctrine_fit = DoctrineFit.from_connections(
                            fconn, sconn, doctrine_id, fit_id, fit_target
                        )
                    prepared.append((fit_info, doctrine_fit, fit_target))
                except Exception as e:
                    console.print(f"[red]✗ Failed to prepare fit {fit_id}: {e}[/red]")
                    logger.exception(f"Error preparing fit {fit_id} for doctrine {doctrine_id}")
                    fail_count += 1

        # Stage 2 & 3: watchlist prep + provisioning in one transaction per market DB
        fit_alias_success = {df.fit_id: 0 for _, df, _ in prepared}
        fittings_cm = uow.read("fittings") if prepared else nullcontext(None)
        with fittings_cm as fconn:
            for alias in target_aliases:
                current_fit_id: int | None = None
                try:
                    with uow.tx(alias) as conn:
                        for _, doctrine_fit, _ in prepared:
                            current_fit_id = doctrine_fit.fit_id
                            _prepare_watchlist_for_fit(
                                {
                                    "fit_id": doctrine_fit.fit_id,
                                    "ship_type_id": doctrine_fit.ship_type_id,
                                },
                                alias,
                                conn=conn,
                                fittings_conn=fconn,
                            )
                        for _, doctrine_fit, fit_target in prepared:
                            current_fit_id = doctrine_fit.fit_id
                            p = {
                                "fit_id": doctrine_fit.fit_id,
                                "fit_name": doctrine_fit.fit_name,
                                "ship_type_id": doctrine_fit.ship_type_id,
                                "ship_name": doctrine_fit.ship_name,
                                "doctrine_id": doctrine_fit.doctrine_id,
                                "doctrine_name": doctrine_fit.doctrine_name,
                                "target": fit_target,
                            }
                            adopted = _provision_fit_in_market(conn, p, market_flag=market_flag)
                            _report_lead_ship(adopted, doctrine_fit.fit_id, doctrine_fit.doctrine_id)
                    for _, doctrine_fit, _ in prepared:
                        fit_alias_success[doctrine_fit.fit_id] += 1
                except Exception as e:
                    console.print(
                        f"[yellow]Bucket {alias} failed "
                        f"(rolled back at fit {current_fit_id}): {e}[/yellow]"
                    )
                    logger.exception(f"Error provisioning {alias} for doctrine {doctrine_id}")

    for _, doctrine_fit, fit_target in prepared:
        n_ok = fit_alias_success[doctrine_fit.fit_id]
        if n_ok == len(target_aliases):
            db_label = " + ".join(target_aliases)
            console.print(
                f"[green]✓ Added fit {doctrine_fit.fit_id}: {doctrine_fit.fit_name} "
                f"(target: {fit_target}) [{db_label}][/green]"
            )
            success_count += 1
        elif n_ok > 0:
            console.print(
                f"[yellow]⚠ Partial: fit {doctrine_fit.fit_id} added to "
                f"{n_ok}/{len(target_aliases)} markets[/yellow]"
            )
            success_count += 1
        else:
            console.print(f"[red]✗ Failed to add fit {doctrine_fit.fit_id}[/red]")
            fail_count += 1

    console.print()
    if success_count > 0:
        console.print(
            f"[green]Successfully added {success_count} fit(s) to doctrine {doctrine_id}[/green]"
        )
    if fail_count > 0:
        console.print(f"[red]Failed to add {fail_count} fit(s)[/red]")

    return success_count > 0 and fail_count == 0 and uow.ok


def remove_fit_command(
    fit_id: int,
    db_alias: str = "wcmkt",
    keep_doctrine_links: bool = False,
) -> bool:
    """Remove a fit from one market (doctrines, doctrine_map, doctrine_fits, ship_targets)."""
    fit_info = get_fit_info(fit_id)
    if not fit_info:
        console.print(f"[red]Error: Fit {fit_id} not found in fittings database[/red]")
        return False

    doctrine_ids = get_doctrine_ids_for_fit(fit_id)
    doctrine_names = {d["id"]: d["name"] for d in get_available_doctrines()}

    console.print(
        Panel(
            f"[bold]Remove fit from {db_alias}[/bold]\n\n"
            "This removes the fit from this market:\n"
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

    try:
        with _uow() as uow:
            with uow.tx(db_alias) as mconn:
                doctrines_removed = remove_doctrines_for_fit(mconn, fit_id)
                console.print(f"  [dim]doctrines:[/dim] {doctrines_removed} rows removed")

                map_removed = remove_all_doctrine_map_for_fit(mconn, fit_id)
                console.print(f"  [dim]doctrine_map:[/dim] {map_removed} rows removed")

                fits_removed = remove_all_doctrine_fits_for_fit(mconn, fit_id)
                console.print(f"  [dim]doctrine_fits:[/dim] {fits_removed} rows removed")

                target_removed = remove_ship_target(mconn, fit_id)
                console.print(
                    f"  [dim]ship_targets:[/dim] {'removed' if target_removed else 'not found'}"
                )

            if keep_doctrine_links:
                console.print(
                    "  [dim]fittings_doctrine_fittings:[/dim] kept "
                    "(another market still tracks this fit)"
                )
            else:
                try:
                    with uow.tx("fittings", auto_touch=False) as fconn:
                        links_removed = remove_all_doctrine_links_for_fit(fit_id, conn=fconn)
                    if links_removed > 0:
                        uow.mark_touched("fittings")
                    console.print(
                        f"  [dim]fittings_doctrine_fittings:[/dim] {links_removed} rows removed"
                    )
                except Exception as e:
                    console.print(f"[red]✗ Error removing fittings links for fit {fit_id}: {e}[/red]")
                    logger.exception(f"Error removing fittings links for fit_id {fit_id}")

        if not uow.ok:
            return False

        console.print()
        console.print(
            f"[green]✓ Fit {fit_id} ({fit_info['fit_name']}) removed from {db_alias}[/green]"
        )
        return True

    except Exception as e:
        console.print(f"[red]✗ Error removing fit {fit_id}: {e}[/red]")
        logger.exception(f"Error in remove_fit_command for fit_id {fit_id}")
        return False


def update_lead_ship_command(
    doctrine_id: int,
    fit_id: int,
    market_flag: str,
) -> bool:
    """Set or change the lead ship for a doctrine across one or more markets."""
    doctrines = get_available_doctrines()
    doctrine_info = next((d for d in doctrines if d["id"] == doctrine_id), None)
    if not doctrine_info:
        console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
        return False

    fit_info = get_fit_info(fit_id)
    if not fit_info:
        console.print(f"[red]Error: Fit {fit_id} not found[/red]")
        return False

    try:
        target_aliases = _configured_market_db_aliases(market_flag)
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        return False
    if not target_aliases:
        console.print(f"[red]Error: no DB aliases resolved for market '{market_flag}'[/red]")
        return False

    console.print()
    info_table = Table(box=box.SIMPLE, show_header=False)
    info_table.add_column("Field", style="dim")
    info_table.add_column("Value", style="white")
    info_table.add_row("Doctrine", f"{doctrine_info['name']} (ID: {doctrine_id})")
    info_table.add_row("Fit", f"{fit_info['fit_name']} (ID: {fit_id})")
    info_table.add_row("Lead Ship", f"{fit_info['ship_name']} ({fit_info['ship_type_id']})")
    info_table.add_row("Market", f"{market_flag} ({', '.join(target_aliases)})")
    console.print(info_table)
    console.print()

    if not Confirm.ask(
        f"Set lead ship for doctrine {doctrine_id} to {fit_info['ship_name']} "
        f"in {', '.join(target_aliases)}?"
    ):
        console.print("[yellow]Cancelled[/yellow]")
        return False

    successes = 0
    failures = 0
    with _uow() as uow:
        for alias in target_aliases:
            try:
                with uow.tx(alias) as conn:
                    set_lead_ship(
                        conn,
                        doctrine_id=doctrine_id,
                        doctrine_name=doctrine_info["name"],
                        fit_id=fit_id,
                        ship_type_id=fit_info["ship_type_id"],
                    )
                console.print(
                    f"[green]✓ Lead ship for doctrine {doctrine_id} set to "
                    f"{fit_info['ship_name']} (fit {fit_id}) in {alias}[/green]"
                )
                successes += 1
            except _DB_ERRORS as e:
                console.print(f"[yellow]Skipped {alias}: {e}[/yellow]")
                failures += 1
            except Exception as e:
                console.print(f"[red]✗ Error in {alias}: {e}[/red]")
                logger.exception("Error in update_lead_ship_command for %s", alias)
                failures += 1

    return successes > 0 and failures == 0 and uow.ok


def doctrine_remove_fit_command(
    doctrine_id: Optional[int] = None,
    fit_ids: Optional[List[int]] = None,
    interactive: bool = True,
    db_alias: str = "wcmkt",
) -> bool:
    """Unlink fit(s) from a doctrine in the market database (and fittings if unused elsewhere)."""
    doctrines = get_available_doctrines()

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
        if not doctrines:
            console.print("[yellow]No doctrines found.[/yellow]")
            return False
        console.print()
        display_doctrines_table(doctrines)
        console.print()
        if doctrine_id is None:
            doctrine_id = IntPrompt.ask("[bold]Doctrine ID[/bold] to remove fit(s) from")
    else:
        if doctrine_id is None:
            console.print("[red]Error: --doctrine-id is required[/red]")
            return False
        if not fit_ids:
            console.print("[red]Error: --fit-id is required (comma-separated for multiple)[/red]")
            return False

    doctrine_info = next((d for d in doctrines if d["id"] == doctrine_id), None)
    if not doctrine_info:
        console.print(f"[red]Error: Doctrine {doctrine_id} not found[/red]")
        return False

    existing_fit_ids = get_doctrine_fits(doctrine_id)
    if interactive:
        console.print(f"\n[cyan]Selected doctrine:[/cyan] {doctrine_info['name']}")
        if not existing_fit_ids:
            console.print("[yellow]This doctrine has no fits to remove.[/yellow]")
            return False
        console.print(f"\n[dim]Current fits in doctrine ({len(existing_fit_ids)}):[/dim]")
        fit_table = Table(box=box.SIMPLE)
        fit_table.add_column("Fit ID", style="dim")
        fit_table.add_column("Fit Name", style="white")
        fit_table.add_column("Ship", style="cyan")
        for fid in existing_fit_ids:
            info = get_fit_info(fid)
            if info:
                fit_table.add_row(str(info["fit_id"]), info["fit_name"], info["ship_name"])
            else:
                fit_table.add_row(str(fid), "[dim]Unknown[/dim]", "[dim]Unknown[/dim]")
        console.print(fit_table)

        if not fit_ids:
            fit_input = Prompt.ask(
                "\n[bold]Fit ID(s)[/bold] to remove (comma-separated for multiple)"
            )
            if not fit_input:
                console.print("[red]Error: At least one fit ID is required[/red]")
                return False
            fit_ids = [int(f.strip()) for f in fit_input.split(",") if f.strip()]

    valid_fits: list[dict] = []
    not_in_doctrine: list[int] = []
    for fid in fit_ids:
        if fid in existing_fit_ids:
            info = get_fit_info(fid)
            valid_fits.append(
                info
                or {
                    "fit_id": fid,
                    "fit_name": "Unknown",
                    "ship_name": "Unknown",
                    "ship_type_id": 0,
                }
            )
        else:
            not_in_doctrine.append(fid)

    if not_in_doctrine:
        console.print(f"[yellow]Not in this doctrine (skipping):[/yellow] {not_in_doctrine}")
    if not valid_fits:
        console.print("[red]No valid fits to remove[/red]")
        return False

    if interactive:
        console.print(f"\n[yellow]Fits to remove ({len(valid_fits)}):[/yellow]")
        remove_table = Table(box=box.SIMPLE)
        remove_table.add_column("Fit ID", style="dim")
        remove_table.add_column("Fit Name", style="white")
        remove_table.add_column("Ship", style="cyan")
        for fit in valid_fits:
            remove_table.add_row(str(fit["fit_id"]), fit["fit_name"], fit["ship_name"])
        console.print(remove_table)

        console.print()
        console.print(
            Panel(
                f"[bold]Doctrine:[/bold] {doctrine_info['name']} (ID: {doctrine_id})\n"
                f"[bold]Fits to remove:[/bold] {len(valid_fits)}",
                title="[bold yellow]Remove Fits Summary[/bold yellow]",
                border_style="yellow",
            )
        )
        if not Confirm.ask(f"Remove {len(valid_fits)} fit(s) from the doctrine?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    success_count = 0
    fail_count = 0
    other_aliases = [a for a in _configured_market_db_aliases() if a != db_alias]

    with _uow() as uow:
        for fit_info in valid_fits:
            fit_id = fit_info["fit_id"]
            try:
                with uow.tx(db_alias) as mconn:
                    rows_removed = remove_doctrines_for_fit(mconn, fit_id)
                    remove_doctrine_map(mconn, doctrine_id, fit_id)
                    remove_doctrine_fits(mconn, doctrine_id, fit_id)

                still_in_other_market = any(
                    fit_id in get_doctrine_fits_from_market(doctrine_id, db_alias=other_alias)
                    for other_alias in other_aliases
                )
                if not still_in_other_market:
                    with uow.tx("fittings", auto_touch=False) as fconn:
                        link_removed = remove_doctrine_link(doctrine_id, fit_id, conn=fconn)
                    if link_removed:
                        uow.mark_touched("fittings")
                else:
                    logger.info(
                        f"Fit {fit_id} still in doctrine {doctrine_id} on another market; "
                        "keeping fittings link"
                    )

                console.print(
                    f"[green]✓ Removed fit {fit_id}: {fit_info['fit_name']} "
                    f"({rows_removed} doctrine rows)[/green]"
                )
                success_count += 1
            except Exception as e:
                console.print(f"[red]✗ Failed to remove fit {fit_id}: {e}[/red]")
                logger.exception(f"Error removing fit {fit_id} from doctrine {doctrine_id}")
                fail_count += 1

    console.print()
    if success_count > 0:
        console.print(
            f"[green]Successfully removed {success_count} fit(s) from doctrine {doctrine_id}[/green]"
        )
    if fail_count > 0:
        console.print(f"[red]Failed to remove {fail_count} fit(s)[/red]")

    return success_count > 0 and fail_count == 0 and uow.ok


def update_target_command(
    fit_id: int,
    target: int,
    db_alias: str = "wcmkt",
    market_flag: str = "primary",
) -> bool:
    """Update the target quantity for a fit across target markets."""
    target_aliases = sorted(_flag_to_aliases(market_flag))
    if not target_aliases:
        target_aliases = [db_alias]

    stored_flag = "both" if market_flag == "all" else market_flag
    ok = True

    with _uow() as uow:
        for alias in target_aliases:
            try:
                with uow.read(alias) as rconn:
                    existing_target = get_fit_target(rconn, fit_id)
                    if existing_target is None:
                        console.print(
                            f"[red]Fit {fit_id} not present in doctrine_fits for {alias} database[/red]"
                        )
                        ok = False
                        continue
                    row = rconn.execute(
                        text(
                            "SELECT fit_name, ship_type_id, ship_name "
                            "FROM doctrine_fits WHERE fit_id = :fit_id LIMIT 1"
                        ),
                        {"fit_id": fit_id},
                    ).fetchone()
                    if not row:
                        console.print(f"[red]Fit {fit_id} not found in doctrine_fits[/red]")
                        ok = False
                        continue
                    fit_name, ship_id, ship_name = row[0], row[1], row[2]

                with uow.tx(alias) as conn:
                    conn.execute(
                        text(
                            "UPDATE doctrine_fits SET target = :target, market_flag = :market_flag "
                            "WHERE fit_id = :fit_id"
                        ),
                        {"target": target, "market_flag": stored_flag, "fit_id": fit_id},
                    )
                    upsert_ship_target(conn, fit_id, fit_name, ship_id, ship_name, target)

                console.print(
                    f"[green]Updated target for fit {fit_id} from [yellow]{existing_target}[/yellow] "
                    f"to [yellow]{target}[/yellow] for {alias}[/green]"
                )
            except Exception as e:
                console.print(f"[red]Failed to update target for fit {fit_id} to {target}: {e}[/red]")
                logger.exception("Error in update_target_command")
                ok = False

    return ok and uow.ok


def update_friendly_name_command(
    doctrine_id: int,
    friendly_name: str,
    db_alias: str = "wcmkt",
) -> bool:
    """Update ``friendly_name`` for all fits in a doctrine across every configured market."""
    target_aliases = sorted({db_alias} | set(_configured_market_db_aliases()))

    any_success = False
    failures = 0
    with _uow() as uow:
        for alias in target_aliases:
            try:
                with uow.tx(alias, auto_touch=False) as conn:
                    ensure_friendly_name_column(conn, db_label=alias)
                    ok = update_doctrine_friendly_name(conn, doctrine_id, friendly_name)
                if not ok:
                    console.print(
                        f"[yellow]No rows found for doctrine_id {doctrine_id} on {alias}[/yellow]"
                    )
                    continue
                uow.mark_touched(alias)
                console.print(
                    f"[green]Updated friendly_name for doctrine_id {doctrine_id} "
                    f"to '{friendly_name}' ({alias})[/green]"
                )
                any_success = True
            except Exception as exc:
                console.print(f"[red]Failed to update friendly_name on {alias}: {exc}[/red]")
                logger.exception("Error in update_friendly_name_command for %s", alias)
                failures += 1

    if not any_success:
        console.print(
            f"[red]No rows found for doctrine_id {doctrine_id} on any configured market[/red]"
        )
        return False

    return failures == 0 and uow.ok


def populate_friendly_names_command(
    json_path: str = "doctrine_names.json",
    db_alias: str = "wcmkt",
) -> bool:
    """Bulk populate ``friendly_name`` from JSON across every configured market."""
    if not os.path.exists(json_path):
        console.print(f"[red]JSON file not found: {json_path}[/red]")
        return False

    target_aliases = sorted({db_alias} | set(_configured_market_db_aliases()))

    any_success = False
    failures = 0
    with _uow() as uow:
        for alias in target_aliases:
            try:
                with uow.tx(alias, auto_touch=False) as conn:
                    ensure_friendly_name_column(conn, db_label=alias)
                    count = populate_friendly_names_from_json(conn, json_path)
                console.print(f"[green]Updated {count} rows ({alias})[/green]")
                if count <= 0:
                    continue
                uow.mark_touched(alias)
                any_success = True
            except Exception as exc:
                console.print(f"[red]Failed to populate friendly_names on {alias}: {exc}[/red]")
                logger.exception("Error in populate_friendly_names_command for %s", alias)
                failures += 1

    if not any_success:
        console.print("[yellow]No friendly_name rows updated on any configured market[/yellow]")

    return failures == 0 and uow.ok
