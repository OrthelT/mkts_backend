"""``fit-check needed`` subcommand.

Computes per-fit "needed items" tables: type rows whose ``fits_on_mkt``
falls below ``ship_target``. Optionally augments rows with per-character
asset counts and equivalent-module stock. Extracted from ``fit_check.py``
for clarity.
"""

from typing import Dict, List, Optional

from sqlalchemy import text

from mkts_backend.cli_tools.arg_utils import ArgError, ParsedArgs
from mkts_backend.cli_tools.market_args import parse_market_args
from mkts_backend.cli_tools.rich_display import console, create_needed_table
from mkts_backend.config import DatabaseConfig
from mkts_backend.config.market_context import MarketContext


def _query_needed_data(
    market_ctx: Optional[MarketContext] = None,
    ship_filter: Optional[List[str]] = None,
    fit_filter: Optional[List[int]] = None,
    targ_perc_filter: Optional[float] = None,
) -> List[Dict]:
    """Return needed items joined from ``doctrines`` and ``ship_targets``."""
    # Lazy import — get_equiv_stock lives in fit_check.py and importing it at
    # module load time would create a circular dependency through the legacy
    # re-exports.
    from mkts_backend.cli_tools.fit_check import get_equiv_stock

    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    results = []
    with db.engine.connect() as conn:
        query = text("""
            SELECT
                d.fit_id,
                d.ship_id,
                d.ship_name,
                d.type_id,
                d.type_name,
                d.fit_qty,
                t.ship_target AS target,
                t.fit_name,
                d.fits_on_mkt,
                d.total_stock,
                round((1.0 * d.fits_on_mkt) / NULLIF(t.ship_target, 0), 2) AS targ_perc,
                CASE
                    WHEN d.fits_on_mkt < t.ship_target
                        THEN (NULLIF(t.ship_target, 0) - d.fits_on_mkt) * d.fit_qty
                    ELSE 0
                END AS qty_needed
            FROM doctrines AS d
            LEFT JOIN ship_targets AS t
                ON d.fit_id = t.fit_id
            WHERE CASE
                    WHEN d.fits_on_mkt < t.ship_target
                        THEN (NULLIF(t.ship_target, 0) - d.fits_on_mkt) * d.fit_qty
                    ELSE 0
                  END > 0
            ORDER BY d.ship_name, d.fit_id, targ_perc
        """)
        rows = conn.execute(query).fetchall()

        for row in rows:
            item = {
                "fit_id": row.fit_id,
                "ship_id": row.ship_id,
                "ship_name": row.ship_name,
                "type_id": row.type_id,
                "type_name": row.type_name,
                "fit_qty": row.fit_qty or 1,
                "target": row.target,
                "fit_name": row.fit_name or "Unknown",
                "fits_on_mkt": row.fits_on_mkt or 0,
                "total_stock": row.total_stock or 0,
                "targ_perc": row.targ_perc or 0,
                "qty_needed": row.qty_needed or 0,
            }

            if ship_filter and item["ship_name"] not in ship_filter:
                continue
            if fit_filter and item["fit_id"] not in fit_filter:
                continue

            results.append(item)

    all_type_ids = list({item["type_id"] for item in results})
    equiv_stock = get_equiv_stock(all_type_ids, market_ctx)

    for item in results:
        equivs = equiv_stock.get(item["type_id"])
        if not equivs:
            continue

        equiv_total = sum(e["stock"] for e in equivs)
        item["total_stock"] = (item.get("total_stock", 0) or 0) + equiv_total
        target_val = item.get("target", 0) or 0
        fit_qty = item.get("fit_qty", 1) or 1

        item["fits_on_mkt"] = item["total_stock"] / fit_qty if fit_qty > 0 else 0
        item["targ_perc"] = (
            round(item["fits_on_mkt"] / target_val, 2) if target_val > 0 else 0
        )
        item["qty_needed"] = (
            max(0, int((target_val - item["fits_on_mkt"]) * fit_qty))
            if item["fits_on_mkt"] < target_val
            else 0
        )
        item["equiv_items"] = equivs

    if targ_perc_filter is not None:
        results = [item for item in results if item["targ_perc"] < targ_perc_filter]

    results = [item for item in results if item["qty_needed"] > 0]

    return results


def needed_command(
    market_alias: str = "primary",
    ship_filter: Optional[List[str]] = None,
    fit_filter: Optional[List[int]] = None,
    targ_perc_filter: Optional[float] = None,
    show_assets: bool = False,
    force_refresh: bool = False,
) -> bool:
    """Display needed items grouped per-fit."""
    try:
        market_ctx = MarketContext.from_settings(market_alias)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return False

    data = _query_needed_data(
        market_ctx,
        ship_filter=ship_filter,
        fit_filter=fit_filter,
        targ_perc_filter=targ_perc_filter,
    )

    if not data:
        console.print("[yellow]No items needed - all fits are at or above target.[/yellow]")
        return True

    char_assets = None
    if show_assets:
        from mkts_backend.esi.character_assets import fetch_all_character_assets

        all_type_ids = list({item["type_id"] for item in data})
        char_assets = fetch_all_character_assets(
            type_ids=all_type_ids, force_refresh=force_refresh
        )

    grouped: Dict[int, List[Dict]] = {}
    for item in data:
        fid = item["fit_id"]
        grouped.setdefault(fid, []).append(item)

    console.print()
    filter_parts = []
    if ship_filter:
        filter_parts.append(f"ship={','.join(ship_filter)}")
    if fit_filter:
        filter_parts.append(f"fit={','.join(str(f) for f in fit_filter)}")
    if targ_perc_filter is not None:
        filter_parts.append(f"targ_perc<{targ_perc_filter}")
    filter_str = f" ({', '.join(filter_parts)})" if filter_parts else ""

    console.print(
        f"[bold]Needed Items[/bold] - [green]{market_ctx.name}[/green]{filter_str}",
    )
    console.print()

    for fit_id, items in grouped.items():
        ship_id = items[0].get("ship_id")
        ship_name = items[0]["ship_name"]
        fit_name = items[0]["fit_name"]
        target = items[0]["target"] or 0
        min_fits = min((item["fits_on_mkt"] for item in items), default=0)

        table = create_needed_table(
            fit_id=fit_id,
            ship_id=ship_id,
            ship_name=ship_name,
            fit_name=fit_name,
            min_fits=min_fits,
            target=target,
            items=items,
            char_assets=char_assets,
        )
        console.print(table)
        console.print()

    total_fits = len(grouped)
    total_items = len(data)
    console.print(
        f"[dim]{total_fits} fit(s), {total_items} item(s) below target[/dim]"
    )
    return True


def _resolve_ship_filters(
    ship_inputs: List[str],
    market_ctx: Optional[MarketContext] = None,
) -> Optional[List[str]]:
    """Map fuzzy ship-name fragments to exact names via interactive picker."""
    from rich.prompt import Prompt

    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    with db.engine.connect() as conn:
        query = text("SELECT DISTINCT ship_name FROM doctrine_fits ORDER BY ship_name")
        all_ships = [row[0] for row in conn.execute(query).fetchall()]

    resolved: List[str] = []

    for fragment in ship_inputs:
        matches = [s for s in all_ships if fragment.lower() in s.lower()]

        if not matches:
            console.print(f"[yellow]No ships matching '{fragment}'[/yellow]")
            continue
        if len(matches) == 1:
            resolved.append(matches[0])
            continue

        console.print(f"\n[bold]Ships matching '{fragment}':[/bold]")
        console.print("  [dim]0[/dim]) All matches")
        for i, name in enumerate(matches, 1):
            console.print(f"  [dim]{i}[/dim]) {name}")

        choice = Prompt.ask("Select", default="0")
        try:
            idx = int(choice)
        except ValueError:
            console.print("[red]Invalid selection, skipping[/red]")
            continue

        if idx == 0:
            resolved.extend(matches)
        elif 1 <= idx <= len(matches):
            resolved.append(matches[idx - 1])
        else:
            console.print("[red]Invalid selection, skipping[/red]")
            continue

    return resolved if resolved else None


def handle_needed(sub_args: List[str]) -> None:
    """CLI dispatcher for ``fitcheck needed``."""
    p = ParsedArgs(sub_args)
    market_alias = parse_market_args(sub_args)
    show_assets = p.has_flag("assets")
    force_refresh = p.has_flag("refresh")

    ship_filters = p.get_string_list("ship")

    try:
        fit_filters = p.get_int_list("fit", "fit-id", "fit_id", "id")
        targ_perc_filter = p.get_float("target")
    except ArgError as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    resolved_ships = None
    if ship_filters:
        try:
            market_ctx = MarketContext.from_settings(market_alias)
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            return
        resolved_ships = _resolve_ship_filters(ship_filters, market_ctx)
        if resolved_ships is None:
            console.print("[yellow]No matching ships found.[/yellow]")
            return

    needed_command(
        market_alias=market_alias,
        ship_filter=resolved_ships,
        fit_filter=fit_filters or None,
        targ_perc_filter=targ_perc_filter,
        show_assets=show_assets,
        force_refresh=force_refresh,
    )
