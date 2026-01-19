"""
Fit Check CLI command.

Displays market availability for items in an EFT-formatted ship fit.
Uses Rich tables for beautiful console output.
"""

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import text

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config import DatabaseConfig
from mkts_backend.config.market_context import MarketContext
from mkts_backend.utils.eft_parser import parse_eft_file, parse_eft_string, FitParseResult
from mkts_backend.utils.jita import fetch_jita_prices, get_overpriced_items
from mkts_backend.cli_tools.rich_display import (
    console,
    create_fit_status_table,
    print_fit_header,
    print_fit_summary,
    print_legend,
    print_missing_for_target,
    print_multibuy_export,
    print_markdown_export,
    print_overpriced_items,
)

logger = configure_logging(__name__)


@dataclass
class DoctrineFitInfo:
    """Metadata for a fit from the doctrine_fits table."""
    fit_id: int
    fit_name: str
    ship_type_id: int
    ship_name: str
    target: int
    doctrine_name: str
    doctrine_id: int
    market_flag: Optional[str] = None


@dataclass
class FitCheckResult:
    """Result of a fit check operation with market data and export utilities."""
    fit_name: str
    ship_name: str
    ship_type_id: Optional[int]
    market_data: List[Dict]
    total_fit_cost: float
    min_fits: float
    target: Optional[int]
    market_name: str
    total_jita_fit_cost: float = 0.0

    @property
    def missing_for_target(self) -> List[Dict]:
        """Get list of items that are below target with qty_needed."""
        if self.target is None:
            return []
        return [
            {
                "type_name": item["type_name"],
                "qty_needed": max(0, int((self.target - item["fits"]) * item["fit_qty"])),
                "fits": item["fits"],
            }
            for item in self.market_data
            if item["fits"] < self.target
        ]

    @property
    def overpriced_items(self) -> List[Dict]:
        """Get list of items priced above 120% of Jita price."""
        return get_overpriced_items(self.market_data, threshold=1.2)

    def to_csv(self, file_path: str) -> str:
        """Export fit status table to CSV file."""
        path = Path(file_path)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            # Header
            headers = ["type_id", "type_name", "market_stock", "fit_qty", "fits", "price", "fit_cost"]
            if self.target is not None:
                headers.append("qty_needed")
            writer.writerow(headers)
            # Data rows
            for item in self.market_data:
                row = [
                    item.get("type_id", ""),
                    item.get("type_name", ""),
                    item.get("market_stock", 0),
                    item.get("fit_qty", 1),
                    f"{item.get('fits', 0):.1f}",
                    f"{item.get('price', 0):.2f}" if item.get("price") else "",
                    f"{item.get('fit_price', 0):.2f}",
                ]
                if self.target is not None:
                    qty_needed = max(0, int((self.target - item["fits"]) * item["fit_qty"])) if item["fits"] < self.target else 0
                    row.append(qty_needed)
                writer.writerow(row)
        return str(path.absolute())

    def to_multibuy(self) -> str:
        """Generate Eve Multi-buy/jEveAssets stockpile format for items below target."""
        if self.target is None:
            return ""
        lines = []
        for item in self.missing_for_target:
            if item["qty_needed"] > 0:
                lines.append(f"{item['type_name']} {item['qty_needed']}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Generate Discord-friendly markdown format for items below target."""
        if self.target is None:
            return ""
        lines = [
            f"# {self.fit_name}",
            f"Target (**{self.target:,}**); Fits (**{int(self.min_fits)}**)",
            "",
        ]
        for item in self.missing_for_target:
            if item["qty_needed"] > 0:
                lines.append(
                    f"- **{item['type_name']}**: {item['qty_needed']:,} needed "
                    f"(current: {item['fits']:.1f} fits)"
                )
        return "\n".join(lines)


def _get_target_for_fit(
    fit_name: str,
    ship_type_id: Optional[int] = None,
    market_ctx: Optional[MarketContext] = None,
) -> Optional[int]:
    """
    Look up target quantity from doctrine_fits table.

    Args:
        fit_name: Name of the fit to look up
        ship_type_id: Ship type ID (used as fallback lookup)
        market_ctx: Market context for database selection

    Returns:
        Target quantity if found, None otherwise
    """
    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    with db.engine.connect() as conn:
        # Try exact fit name match first
        query = text("""
            SELECT target FROM doctrine_fits
            WHERE fit_name = :fit_name
            LIMIT 1
        """)
        result = conn.execute(query, {"fit_name": fit_name}).fetchone()
        if result:
            return result[0]

        # Try ship_type_id match as fallback
        if ship_type_id:
            query = text("""
                SELECT target FROM doctrine_fits
                WHERE ship_type_id = :ship_type_id
                LIMIT 1
            """)
            result = conn.execute(query, {"ship_type_id": ship_type_id}).fetchone()
            if result:
                return result[0]

    return None


def _get_doctrine_fit_info(
    fit_id: int,
    market_ctx: Optional[MarketContext] = None,
) -> Optional[DoctrineFitInfo]:
    """
    Look up fit metadata from doctrine_fits table by fit_id.

    Args:
        fit_id: The fit ID to look up
        market_ctx: Market context for database selection

    Returns:
        DoctrineFitInfo if found, None otherwise
    """
    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    with db.engine.connect() as conn:
        query = text("""
            SELECT fit_id, fit_name, ship_type_id, ship_name, target,
                   doctrine_name, doctrine_id
            FROM doctrine_fits
            WHERE fit_id = :fit_id
            LIMIT 1
        """)
        result = conn.execute(query, {"fit_id": fit_id}).fetchone()
        if result:
            return DoctrineFitInfo(
                fit_id=result.fit_id,
                fit_name=result.fit_name,
                ship_type_id=result.ship_type_id,
                ship_name=result.ship_name,
                target=result.target,
                doctrine_name=result.doctrine_name,
                doctrine_id=result.doctrine_id,
            )

    return None


def _get_doctrines_market_data(
    fit_id: int,
    market_ctx: Optional[MarketContext] = None,
) -> List[Dict]:
    """
    Fetch market data from doctrines table for a given fit_id.

    Args:
        fit_id: The fit ID to query
        market_ctx: Market context for database selection

    Returns:
        List of market data dicts compatible with display functions
    """
    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    market_data = []
    with db.engine.connect() as conn:
        query = text("""
            SELECT fit_id, ship_id, ship_name, hulls, type_id, type_name,
                   fit_qty, fits_on_mkt, total_stock, price, avg_vol, days,
                   group_id, group_name, category_id, category_name
            FROM doctrines
            WHERE fit_id = :fit_id
            ORDER BY category_id ASC, fits_on_mkt ASC
        """)
        rows = conn.execute(query, {"fit_id": fit_id}).fetchall()

        for row in rows:
            is_ship = row.category_id == 6 if row.category_id else False
            fit_price = (row.price * row.fit_qty) if row.price and row.fit_qty else 0

            market_data.append({
                "type_id": row.type_id,
                "type_name": row.type_name or "Unknown",
                "market_stock": row.total_stock or 0,
                "fit_qty": row.fit_qty or 1,
                "fits": row.fits_on_mkt or 0,
                "price": row.price,
                "fit_price": fit_price,
                "avg_price": None,  # Not stored in doctrines table
                "is_fallback": False,  # Pre-calculated data
                "is_ship": is_ship,
                "category_id": row.category_id,
                "jita_price": None,  # Will be populated later
                "jita_fit_price": 0,
            })

    return market_data


def _get_marketstats_data(
    type_ids: List[int],
    market_ctx: Optional[MarketContext] = None,
) -> Dict[int, Dict]:
    """
    Query marketstats table for items on the watchlist.

    Args:
        type_ids: List of type IDs to query
        market_ctx: Market context for database selection

    Returns:
        Dict mapping type_id to market stats data
    """
    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    results = {}
    with db.engine.connect() as conn:
        for type_id in type_ids:
            query = text("""
                SELECT type_id, type_name, price, min_price, total_volume_remain,
                       avg_price, avg_volume, days_remaining, last_update, category_id
                FROM marketstats
                WHERE type_id = :type_id
            """)
            row = conn.execute(query, {"type_id": type_id}).fetchone()
            if row:
                results[type_id] = dict(row._mapping)

    return results


def _is_ship(type_id: int, category_id: Optional[int] = None, market_ctx: Optional[MarketContext] = None) -> bool:
    """
    Determine if a type_id is a ship.

    Checks in order:
    1. If category_id is provided and equals 6 (Ship category)
    2. If type_id exists in ship_targets table
    3. Lookup categoryID in sde.db inv_info table

    Args:
        type_id: The type ID to check
        category_id: Optional category_id from marketstats
        market_ctx: Market context for database selection

    Returns:
        True if the item is a ship, False otherwise
    """
    # Check 1: Direct category_id check
    if category_id == 6:
        return True

    # Check 2: Look up in ship_targets table
    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)
    with db.engine.connect() as conn:
        query = text("SELECT 1 FROM ship_targets WHERE ship_id = :type_id LIMIT 1")
        result = conn.execute(query, {"type_id": type_id}).fetchone()
        if result:
            return True

    # Check 3: Look up in SDE inv_info table
    sde_db = DatabaseConfig("sde")
    with sde_db.engine.connect() as conn:
        query = text("SELECT categoryID FROM inv_info WHERE typeID = :type_id")
        result = conn.execute(query, {"type_id": type_id}).fetchone()
        if result and result[0] == 6:
            return True

    return False


def _get_fallback_data(
    type_id: int,
    market_ctx: Optional[MarketContext] = None,
) -> Optional[Dict]:
    """
    Get market data from marketorders for items not on watchlist.

    Calculates 5th percentile price from current sell orders.

    Args:
        type_id: Type ID to query
        market_ctx: Market context for database selection

    Returns:
        Dict with calculated price data, or None if no orders found
    """
    db_alias = market_ctx.database_alias if market_ctx else "wcmkt"
    db = DatabaseConfig(db_alias)

    with db.engine.connect() as conn:
        # Get sell orders sorted by price (lowest first)
        query = text("""
            SELECT price, volume_remain
            FROM marketorders
            WHERE type_id = :type_id AND is_buy_order = 0
            ORDER BY price ASC
        """)
        rows = conn.execute(query, {"type_id": type_id}).fetchall()

        if not rows:
            return None

        # Calculate total volume and 5th percentile price
        prices = []
        volumes = []
        for row in rows:
            prices.append(row.price)
            volumes.append(row.volume_remain)

        total_volume = sum(volumes)

        # Calculate 5th percentile price (price where 5% of volume is below)
        if total_volume > 0:
            target_volume = total_volume * 0.05
            cumulative = 0
            percentile_5_price = prices[0]

            for price, volume in zip(prices, volumes):
                cumulative += volume
                if cumulative >= target_volume:
                    percentile_5_price = price
                    break

            return {
                "type_id": type_id,
                "price": percentile_5_price,
                "min_price": prices[0] if prices else None,
                "total_volume_remain": total_volume,
                "avg_price": sum(p * v for p, v in zip(prices, volumes)) / total_volume if total_volume else None,
                "is_fallback": True,
            }

    return None


def _get_type_name_from_sde(type_id: int) -> str:
    """Get type name from SDE database."""
    sde_db = DatabaseConfig("sde")
    with sde_db.engine.connect() as conn:
        query = text("SELECT typeName FROM inv_info WHERE typeID = :type_id")
        result = conn.execute(query, {"type_id": type_id}).fetchone()
        return result[0] if result else f"Unknown (ID: {type_id})"


def get_fit_market_status(
    parse_result: FitParseResult,
    market_ctx: Optional[MarketContext] = None,
    target: Optional[int] = None,
) -> FitCheckResult:
    """
    Get market status for all items in a parsed fit.

    Args:
        parse_result: Parsed EFT fit result
        market_ctx: Market context for database selection
        target: Optional target quantity override. If None, looks up from doctrine_fits.

    Returns:
        FitCheckResult with market data and export utilities
    """
    market_name = market_ctx.name if market_ctx else "primary"

    # Look up target from doctrine_fits if not provided
    if target is None:
        target = _get_target_for_fit(
            parse_result.fit_name,
            parse_result.ship_type_id,
            market_ctx,
        )
    # Aggregate items by type_id
    item_quantities = defaultdict(int)
    item_names = {}
    for item in parse_result.items:
        type_id = item["type_id"]
        item_quantities[type_id] += item["quantity"]
        if type_id not in item_names:
            item_names[type_id] = item.get("type_name", "")

    # Add ship hull
    if parse_result.ship_type_id:
        if parse_result.ship_type_id not in item_quantities:
            item_quantities[parse_result.ship_type_id] = 1
            item_names[parse_result.ship_type_id] = parse_result.ship_name

    type_ids = list(item_quantities.keys())

    # Get marketstats data
    marketstats_data = _get_marketstats_data(type_ids, market_ctx)

    # Fetch Jita prices for all items
    jita_prices = fetch_jita_prices(type_ids)

    # Build result list
    market_data = []
    for type_id, fit_qty in item_quantities.items():
        category_id = None
        if type_id in marketstats_data:
            stats = marketstats_data[type_id]
            market_stock = stats.get("total_volume_remain", 0) or 0
            price = stats.get("price")
            avg_price = stats.get("avg_price")
            is_fallback = False
            type_name = stats.get("type_name", item_names.get(type_id, ""))
            category_id = stats.get("category_id")
        else:
            # Try fallback from marketorders
            fallback = _get_fallback_data(type_id, market_ctx)
            if fallback:
                market_stock = fallback.get("total_volume_remain", 0) or 0
                price = fallback.get("price")
                avg_price = fallback.get("avg_price")
                is_fallback = True
            else:
                market_stock = 0
                price = None
                avg_price = None
                is_fallback = True

            # Get type name from SDE if not in item_names
            type_name = item_names.get(type_id) or _get_type_name_from_sde(type_id)

        # Calculate fits available
        fits = (market_stock / fit_qty) if fit_qty > 0 else 0

        # Calculate fit cost
        fit_price = (price * fit_qty) if price else 0

        # Get Jita price and calculate Jita fit cost
        jita_price = jita_prices.get(type_id)
        jita_fit_price = (jita_price * fit_qty) if jita_price else 0

        # Determine if this is a ship
        is_ship = _is_ship(type_id, category_id, market_ctx)

        market_data.append({
            "type_id": type_id,
            "type_name": type_name,
            "market_stock": market_stock,
            "fit_qty": fit_qty,
            "fits": fits,
            "price": price,
            "fit_price": fit_price,
            "avg_price": avg_price,
            "is_fallback": is_fallback,
            "is_ship": is_ship,
            "jita_price": jita_price,
            "jita_fit_price": jita_fit_price,
        })

    # Sort: ships first, then by fits available (lowest first to highlight bottlenecks)
    market_data.sort(key=lambda x: (not x["is_ship"], x["fits"], x["type_name"]))

    # Calculate totals
    total_fit_cost = sum(item.get("fit_price", 0) for item in market_data)
    total_jita_fit_cost = sum(item.get("jita_fit_price", 0) for item in market_data)
    min_fits = min((item["fits"] for item in market_data), default=0)

    return FitCheckResult(
        fit_name=parse_result.fit_name,
        ship_name=parse_result.ship_name,
        ship_type_id=parse_result.ship_type_id,
        market_data=market_data,
        total_fit_cost=total_fit_cost,
        min_fits=min_fits,
        target=target,
        market_name=market_name,
        total_jita_fit_cost=total_jita_fit_cost,
    )


def get_fit_market_status_by_id(
    fit_id: int,
    market_ctx: Optional[MarketContext] = None,
    target: Optional[int] = None,
) -> Optional[FitCheckResult]:
    """
    Get market status for a fit using pre-calculated data from the doctrines table.

    This function retrieves cached market data that was calculated during the
    main backend data collection workflow, rather than querying live market data.

    Args:
        fit_id: The fit_id to look up in doctrine_fits/doctrines tables
        market_ctx: Market context for database selection
        target: Optional target quantity override. If None, uses value from doctrine_fits.

    Returns:
        FitCheckResult with market data from doctrines table, or None if fit not found
    """
    market_name = market_ctx.name if market_ctx else "primary"

    # Get fit metadata from doctrine_fits
    fit_info = _get_doctrine_fit_info(fit_id, market_ctx)
    if not fit_info:
        return None

    # Use provided target or fall back to doctrine_fits target
    effective_target = target if target is not None else fit_info.target

    # Get market data from doctrines table
    market_data = _get_doctrines_market_data(fit_id, market_ctx)
    if not market_data:
        return None

    # Fetch Jita prices for comparison
    type_ids = [item["type_id"] for item in market_data]
    jita_prices = fetch_jita_prices(type_ids)

    # Populate Jita prices in market data
    for item in market_data:
        type_id = item["type_id"]
        jita_price = jita_prices.get(type_id)
        item["jita_price"] = jita_price
        item["jita_fit_price"] = (jita_price * item["fit_qty"]) if jita_price else 0

    # Sort: ships first, then by fits available (lowest first to highlight bottlenecks)
    market_data.sort(key=lambda x: (not x["is_ship"], x["fits"], x["type_name"]))

    # Calculate totals
    total_fit_cost = sum(item.get("fit_price", 0) for item in market_data)
    total_jita_fit_cost = sum(item.get("jita_fit_price", 0) for item in market_data)
    min_fits = min((item["fits"] for item in market_data), default=0)

    return FitCheckResult(
        fit_name=fit_info.fit_name,
        ship_name=fit_info.ship_name,
        ship_type_id=fit_info.ship_type_id,
        market_data=market_data,
        total_fit_cost=total_fit_cost,
        min_fits=min_fits,
        target=effective_target,
        market_name=market_name,
        total_jita_fit_cost=total_jita_fit_cost,
    )


def display_fit_status_by_id(
    fit_id: int,
    market_ctx: Optional[MarketContext] = None,
    show_legend: bool = True,
    target: Optional[int] = None,
    output_format: Optional[str] = None,
    show_jita: bool = True,
) -> Optional[FitCheckResult]:
    """
    Display market status for a fit by fit_id using pre-calculated doctrines data.

    Args:
        fit_id: The fit_id to look up
        market_ctx: Market context for database selection
        show_legend: Whether to show the legend
        target: Optional target quantity override
        output_format: Export format - 'csv', 'multibuy', or 'markdown' (optional)
        show_jita: Whether to show Jita price comparison columns

    Returns:
        FitCheckResult object with market data, or None if fit not found
    """
    # Get market data from doctrines table
    result = get_fit_market_status_by_id(fit_id, market_ctx, target)

    if not result:
        console.print(f"[red]Error: No fit found with fit_id={fit_id}[/red]")
        return None

    # Create table first to measure its width
    table = create_fit_status_table(
        fit_name=result.fit_name,
        ship_name=result.ship_name,
        ship_type_id=result.ship_type_id,
        market_data=result.market_data,
        total_fit_cost=result.total_fit_cost,
        market_name=result.market_name,
        target=result.target,
        show_jita=show_jita,
    )

    # Measure table width for header alignment
    table_width = console.measure(table).maximum

    # Print header with matching width
    print_fit_header(
        fit_name=result.fit_name,
        ship_name=result.ship_name,
        ship_type_id=result.ship_type_id,
        market_name=result.market_name,
        total_fit_cost=result.total_fit_cost,
        total_fits=result.min_fits,
        target=result.target,
        width=table_width,
        total_jita_fit_cost=result.total_jita_fit_cost if show_jita else None,
    )

    console.print()

    # Print the table
    console.print(table)

    # Print summary
    available_count = sum(1 for item in result.market_data if item["fits"] >= 1)
    total_count = len(result.market_data)
    missing_items = [item["type_name"] for item in result.market_data if item["fits"] < 1]

    print_fit_summary(
        available_count=available_count,
        total_count=total_count,
        min_fits=result.min_fits,
        missing_items=missing_items,
    )

    # Print missing modules for target
    if result.target is not None and result.missing_for_target:
        print_missing_for_target(result.missing_for_target, result.target)

    # Print items priced above 120% of Jita
    if show_jita and result.overpriced_items:
        print_overpriced_items(result.overpriced_items)

    if show_legend:
        print_legend()

    # Handle output format exports
    if output_format:
        if result.target is None:
            console.print("\n[yellow]No target set - export requires --target[/yellow]")
        elif not result.missing_for_target:
            console.print("\n[yellow]No items below target - nothing to export[/yellow]")
        elif output_format == "csv":
            csv_path = result.to_csv(f"{result.fit_name.replace(' ', '_')}_missing.csv")
            console.print(f"\n[green]CSV exported to:[/green] {csv_path}")
        elif output_format == "multibuy":
            print_multibuy_export(result.to_multibuy())
        elif output_format == "markdown":
            print_markdown_export(result.to_markdown())

    return result


def display_fit_status(
    parse_result: FitParseResult,
    market_ctx: Optional[MarketContext] = None,
    show_legend: bool = True,
    target: Optional[int] = None,
    output_format: Optional[str] = None,
    show_jita: bool = True,
) -> FitCheckResult:
    """
    Display market status for a parsed fit using Rich formatting.

    Args:
        parse_result: Parsed EFT fit result
        market_ctx: Market context for database selection
        show_legend: Whether to show the legend
        target: Optional target quantity override
        output_format: Export format - 'csv', 'multibuy', or 'markdown' (optional)
        show_jita: Whether to show Jita price comparison columns

    Returns:
        FitCheckResult object with market data
    """
    # Get market data with target lookup
    result = get_fit_market_status(parse_result, market_ctx, target)

    # Create table first to measure its width
    table = create_fit_status_table(
        fit_name=result.fit_name,
        ship_name=result.ship_name,
        ship_type_id=result.ship_type_id,
        market_data=result.market_data,
        total_fit_cost=result.total_fit_cost,
        market_name=result.market_name,
        target=result.target,
        show_jita=show_jita,
    )

    # Measure table width for header alignment
    table_width = console.measure(table).maximum

    # Print header with matching width
    print_fit_header(
        fit_name=result.fit_name,
        ship_name=result.ship_name,
        ship_type_id=result.ship_type_id,
        market_name=result.market_name,
        total_fit_cost=result.total_fit_cost,
        total_fits=result.min_fits,
        target=result.target,
        width=table_width,
        total_jita_fit_cost=result.total_jita_fit_cost if show_jita else None,
    )

    console.print()

    # Print the table
    console.print(table)

    # Print summary
    available_count = sum(1 for item in result.market_data if item["fits"] >= 1)
    total_count = len(result.market_data)
    missing_items = [item["type_name"] for item in result.market_data if item["fits"] < 1]

    print_fit_summary(
        available_count=available_count,
        total_count=total_count,
        min_fits=result.min_fits,
        missing_items=missing_items,
    )

    # Print missing modules for target
    if result.target is not None and result.missing_for_target:
        print_missing_for_target(result.missing_for_target, result.target)

    # Print items priced above 120% of Jita
    if show_jita and result.overpriced_items:
        print_overpriced_items(result.overpriced_items)

    if show_legend:
        print_legend()

    # Handle output format exports
    if output_format:
        if result.target is None:
            console.print("\n[yellow]No target set - export requires --target[/yellow]")
        elif not result.missing_for_target:
            console.print("\n[yellow]No items below target - nothing to export[/yellow]")
        elif output_format == "csv":
            csv_path = result.to_csv(f"{result.fit_name.replace(' ', '_')}_missing.csv")
            console.print(f"\n[green]CSV exported to:[/green] {csv_path}")
        elif output_format == "multibuy":
            print_multibuy_export(result.to_multibuy())
        elif output_format == "markdown":
            print_markdown_export(result.to_markdown())

    return result


def fit_check_command(
    file_path: Optional[str] = None,
    eft_text: Optional[str] = None,
    fit_id: Optional[int] = None,
    market_alias: str = "primary",
    show_legend: bool = True,
    target: Optional[int] = None,
    output_format: Optional[str] = None,
    show_jita: bool = True,
) -> bool:
    """
    Execute the fit-check command.

    Args:
        file_path: Path to EFT fit file
        eft_text: Raw EFT text (alternative to file)
        fit_id: Fit ID to look up from doctrine_fits/doctrines tables
        market_alias: Market alias (primary, deployment)
        show_legend: Whether to show the legend
        target: Optional target quantity override
        output_format: Export format - 'csv', 'multibuy', or 'markdown' (optional)
        show_jita: Whether to show Jita price comparison columns

    Returns:
        True if successful, False otherwise
    """
    try:
        # Get market context
        market_ctx = MarketContext.from_settings(market_alias)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print(f"Available markets: {', '.join(MarketContext.list_available())}")
        return False

    # Handle fit_id mode - use pre-calculated data from doctrines table
    if fit_id is not None:
        result = display_fit_status_by_id(
            fit_id,
            market_ctx,
            show_legend=show_legend,
            target=target,
            output_format=output_format,
            show_jita=show_jita,
        )
        return result is not None

    # Parse fit from file or text
    try:
        if file_path:
            parse_result = parse_eft_file(file_path)
        elif eft_text:
            parse_result = parse_eft_string(eft_text)
        else:
            console.print("[red]Error: Either --file, --paste, or --fit_id must be specified[/red]")
            return False
    except FileNotFoundError:
        console.print(f"[red]Error: File not found: {file_path}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Error parsing fit: {e}[/red]")
        logger.exception("Error parsing EFT fit")
        return False

    # Check for missing types
    if parse_result.has_missing_types:
        console.print("[yellow]Warning: Some items could not be resolved:[/yellow]")
        for item in parse_result.missing_types[:5]:
            console.print(f"  • {item}", style="yellow")
        if len(parse_result.missing_types) > 5:
            console.print(f"  ... and {len(parse_result.missing_types) - 5} more", style="dim yellow")
        console.print()

    # Display status
    display_fit_status(
        parse_result,
        market_ctx,
        show_legend=show_legend,
        target=target,
        output_format=output_format,
        show_jita=show_jita,
    )

    return True
