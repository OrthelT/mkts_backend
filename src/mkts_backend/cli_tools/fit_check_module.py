"""``fit-check module`` subcommand.

Resolves a module by ID or name and shows which doctrine fits use it,
along with per-market stock counts. Extracted from ``fit_check.py`` for
clarity — this feature has no shared helpers with the main fit-status
display.
"""

from typing import Dict, List, Optional

from sqlalchemy import text

from mkts_backend.cli_tools.arg_utils import ArgError, ParsedArgs
from mkts_backend.cli_tools.market_args import parse_market_args
from mkts_backend.cli_tools.rich_display import console, create_module_usage_table
from mkts_backend.config import DatabaseConfig
from mkts_backend.config.market_context import MarketContext


def _resolve_module_identity(
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
) -> tuple[int, str]:
    """Resolve a module to ``(type_id, type_name)`` via SDE."""
    sde_db = DatabaseConfig("sde")

    if type_id is not None:
        with sde_db.engine.connect() as conn:
            query = text("SELECT typeName FROM sdetypes WHERE typeID = :type_id")
            result = conn.execute(query, {"type_id": type_id}).fetchone()
            if result:
                return type_id, result[0]
            raise ValueError(f"No item found with typeID={type_id}")

    if type_name is not None:
        with sde_db.engine.connect() as conn:
            query = text("SELECT typeID, typeName FROM sdetypes WHERE typeName = :name")
            result = conn.execute(query, {"name": type_name}).fetchone()
            if result:
                return result[0], result[1]

            query = text(
                "SELECT typeID, typeName FROM sdetypes "
                "WHERE typeName LIKE :pattern ORDER BY typeName LIMIT 10"
            )
            rows = conn.execute(query, {"pattern": f"%{type_name}%"}).fetchall()
            if len(rows) == 1:
                return rows[0][0], rows[0][1]
            if len(rows) > 1:
                names = "\n  ".join(f"{r[0]}: {r[1]}" for r in rows)
                raise ValueError(f"Ambiguous name '{type_name}'. Matches:\n  {names}")
            raise ValueError(f"No item found matching '{type_name}'")

    raise ValueError("Either --id or --name is required")


def _query_module_usage(
    type_id: int,
    market_ctx: Optional[MarketContext] = None,
) -> List[Dict]:
    """Return per-fit usage and market stock for the given module."""
    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    results = []
    with db.engine.connect() as conn:
        query = text("""
            SELECT
                d.fit_id,
                df.fit_name,
                df.ship_name,
                df.doctrine_name,
                d.fit_qty,
                df.target,
                d.total_stock,
                d.fits_on_mkt,
                d.price
            FROM doctrines d
            JOIN doctrine_fits df ON d.fit_id = df.fit_id
            WHERE d.type_id = :type_id
            ORDER BY df.doctrine_name, df.fit_name
        """)
        rows = conn.execute(query, {"type_id": type_id}).fetchall()

        for row in rows:
            fits = row.fits_on_mkt or 0
            target = row.target or 0
            fit_qty = row.fit_qty or 1
            qty_needed = (
                max(0, int((target - fits) * fit_qty)) if fits < target else 0
            )

            results.append({
                "fit_id": row.fit_id,
                "fit_name": row.fit_name,
                "ship_name": row.ship_name,
                "doctrine_name": row.doctrine_name,
                "fit_qty": fit_qty,
                "target": target,
                "total_stock": row.total_stock or 0,
                "fits_on_mkt": fits,
                "qty_needed": qty_needed,
                "price": row.price,
            })

    return results


def module_command(
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    market_alias: str = "primary",
) -> bool:
    """Display which fits use a module and their market availability."""
    try:
        resolved_id, resolved_name = _resolve_module_identity(type_id, type_name)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return False

    show_both = market_alias == "both"

    if show_both:
        try:
            primary_ctx = MarketContext.from_settings("primary")
            deploy_ctx = MarketContext.from_settings("deployment")
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            return False

        primary_data = _query_module_usage(resolved_id, primary_ctx)
        deploy_data = _query_module_usage(resolved_id, deploy_ctx)

        if not primary_data and not deploy_data:
            console.print(
                f"[yellow]Module '{resolved_name}' (ID: {resolved_id}) "
                f"is not used in any tracked fits.[/yellow]"
            )
            return True

        deploy_by_fit = {r["fit_id"]: r for r in deploy_data}
        merged = []
        seen_fit_ids = set()

        for row in primary_data:
            fid = row["fit_id"]
            seen_fit_ids.add(fid)
            d_row = deploy_by_fit.get(fid, {})
            merged.append({
                "fit_id": fid,
                "fit_name": row["fit_name"],
                "ship_name": row["ship_name"],
                "doctrine_name": row["doctrine_name"],
                "fit_qty": row["fit_qty"],
                "target": row["target"],
                "price": row["price"],
                "p_stock": row["total_stock"],
                "p_fits": row["fits_on_mkt"],
                "p_need": row["qty_needed"],
                "d_stock": d_row.get("total_stock", 0),
                "d_fits": d_row.get("fits_on_mkt", 0),
                "d_need": d_row.get("qty_needed", 0),
            })

        for fid, d_row in deploy_by_fit.items():
            if fid not in seen_fit_ids:
                merged.append({
                    "fit_id": fid,
                    "fit_name": d_row["fit_name"],
                    "ship_name": d_row["ship_name"],
                    "doctrine_name": d_row["doctrine_name"],
                    "fit_qty": d_row["fit_qty"],
                    "target": d_row["target"],
                    "price": d_row["price"],
                    "p_stock": 0,
                    "p_fits": 0,
                    "p_need": 0,
                    "d_stock": d_row["total_stock"],
                    "d_fits": d_row["fits_on_mkt"],
                    "d_need": d_row["qty_needed"],
                })

        table = create_module_usage_table(resolved_name, resolved_id, merged, show_both=True)
        console.print(table)
        console.print(f"\n[dim]Total: {len(merged)} fit(s) using this module[/dim]")
        return True

    try:
        market_ctx = MarketContext.from_settings(market_alias)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print(f"Available markets: {', '.join(MarketContext.list_available())}")
        return False

    data = _query_module_usage(resolved_id, market_ctx)

    if not data:
        console.print(
            f"[yellow]Module '{resolved_name}' (ID: {resolved_id}) "
            f"is not used in any tracked fits on {market_ctx.name}.[/yellow]"
        )
        return True

    table = create_module_usage_table(resolved_name, resolved_id, data, show_both=False)
    console.print(table)
    console.print(
        f"\n[dim]Total: {len(data)} fit(s) using this module "
        f"on {market_ctx.name}[/dim]"
    )
    return True


def handle_module(sub_args: List[str]) -> None:
    """CLI dispatcher for ``fitcheck module``."""
    p = ParsedArgs(sub_args)
    market_alias = parse_market_args(sub_args)

    try:
        type_id = p.get_int("id")
    except ArgError as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    type_name = p.get_string("name")

    if type_id is None and type_name is None:
        console.print("[red]Error: --id=<type_id> or --name=<name> is required[/red]")
        console.print("Usage: fitcheck module --id=11269")
        console.print('       fitcheck module --name="Multispectrum Energized Membrane II"')
        return

    module_command(type_id=type_id, type_name=type_name, market_alias=market_alias)
