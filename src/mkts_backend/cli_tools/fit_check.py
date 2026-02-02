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
from mkts_backend.utils.eft_parser import (
    parse_eft_file,
    parse_eft_string,
    FitParseResult,
)
from mkts_backend.utils.jita import fetch_jita_prices, get_overpriced_items
from mkts_backend.cli_tools.rich_display import (
    console,
    create_fit_status_table,
    create_module_usage_table,
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
    def hulls(self) -> int:
        """Get the number of ship hulls available (fits for the ship item)."""
        for item in self.market_data:
            if item.get("is_ship", False):
                return int(item.get("fits", 0))
        return 0

    @property
    def missing_for_target(self) -> List[Dict]:
        """Get list of items that are below target with qty_needed."""
        if self.target is None:
            return []
        return [
            {
                "type_name": item["type_name"],
                "qty_needed": max(
                    0, int((self.target - item["fits"]) * item["fit_qty"])
                ),
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
            headers = [
                "type_id",
                "type_name",
                "market_stock",
                "fit_qty",
                "fits",
                "price",
                "fit_cost",
            ]
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
                    qty_needed = (
                        max(0, int(
                            (self.target - item["fits"]) * item["fit_qty"]))
                        if item["fits"] < self.target
                        else 0
                    )
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
            f"Target (**{self.target:,}**); Fits (**{int(self.min_fits)
                                                     }**); Hulls (**{self.hulls}**)",
            "",
        ]
        for item in self.missing_for_target:
            if item["qty_needed"] > 0:
                lines.append(
                    f"- **{item['type_name']
                           }**: {item['qty_needed']:,} needed "
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
            result = conn.execute(
                query, {"ship_type_id": ship_type_id}).fetchone()
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
            fit_price = (
                row.price * row.fit_qty) if row.price and row.fit_qty else 0

            market_data.append(
                {
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
                }
            )

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


def _is_ship(
    type_id: int,
    category_id: Optional[int] = None,
    market_ctx: Optional[MarketContext] = None,
) -> bool:
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
        query = text(
            "SELECT 1 FROM ship_targets WHERE ship_id = :type_id LIMIT 1")
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
                "avg_price": sum(p * v for p, v in zip(prices, volumes)) / total_volume
                if total_volume
                else None,
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
            type_name = item_names.get(
                type_id) or _get_type_name_from_sde(type_id)

        # Calculate fits available
        fits = (market_stock / fit_qty) if fit_qty > 0 else 0

        # Calculate fit cost
        fit_price = (price * fit_qty) if price else 0

        # Get Jita price and calculate Jita fit cost
        jita_price = jita_prices.get(type_id)
        jita_fit_price = (jita_price * fit_qty) if jita_price else 0

        # Determine if this is a ship
        is_ship = _is_ship(type_id, category_id, market_ctx)

        market_data.append(
            {
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
            }
        )

    # Sort: ships first, then by fits available (lowest first to highlight bottlenecks)
    market_data.sort(key=lambda x: (
        not x["is_ship"], x["fits"], x["type_name"]))

    # Calculate totals
    total_fit_cost = sum(item.get("fit_price", 0) for item in market_data)
    total_jita_fit_cost = sum(item.get("jita_fit_price", 0)
                              for item in market_data)
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
        item["jita_fit_price"] = (
            jita_price * item["fit_qty"]) if jita_price else 0

    # Sort: ships first, then by fits available (lowest first to highlight bottlenecks)
    market_data.sort(key=lambda x: (
        not x["is_ship"], x["fits"], x["type_name"]))

    # Calculate totals
    total_fit_cost = sum(item.get("fit_price", 0) for item in market_data)
    total_jita_fit_cost = sum(item.get("jita_fit_price", 0)
                              for item in market_data)
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
        hulls=result.hulls,
    )

    console.print()

    # Print the table
    console.print(table)

    # Print summary
    available_count = sum(
        1 for item in result.market_data if item["fits"] >= 1)
    total_count = len(result.market_data)
    missing_items = [
        item["type_name"] for item in result.market_data if item["fits"] < 1
    ]

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
            console.print(
                "\n[yellow]No target set - export requires --target[/yellow]")
        elif not result.missing_for_target:
            console.print(
                "\n[yellow]No items below target - nothing to export[/yellow]"
            )
        elif output_format == "csv":
            csv_path = result.to_csv(
                f"{result.fit_name.replace(' ', '_')}_missing.csv")
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
        hulls=result.hulls,
    )

    console.print()

    # Print the table
    console.print(table)

    # Print summary
    available_count = sum(
        1 for item in result.market_data if item["fits"] >= 1)
    total_count = len(result.market_data)
    missing_items = [
        item["type_name"] for item in result.market_data if item["fits"] < 1
    ]

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
            console.print(
                "\n[yellow]No target set - export requires --target[/yellow]")
        elif not result.missing_for_target:
            console.print(
                "\n[yellow]No items below target - nothing to export[/yellow]"
            )
        elif output_format == "csv":
            csv_path = result.to_csv(
                f"{result.fit_name.replace(' ', '_')}_missing.csv")
            console.print(f"\n[green]CSV exported to:[/green] {csv_path}")
        elif output_format == "multibuy":
            print_multibuy_export(result.to_multibuy())
        elif output_format == "markdown":
            print_markdown_export(result.to_markdown())

    return result


def _resolve_module_identity(
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
) -> tuple[int, str]:
    """
    Resolve a module to both type_id and type_name via SDE.

    Args:
        type_id: Type ID to look up (mutually exclusive with type_name)
        type_name: Type name to search for (mutually exclusive with type_id)

    Returns:
        Tuple of (type_id, type_name)

    Raises:
        ValueError: If the module cannot be resolved or is ambiguous
    """
    sde_db = DatabaseConfig("sde")

    if type_id is not None:
        with sde_db.engine.connect() as conn:
            query = text(
                "SELECT typeName FROM inv_info WHERE typeID = :type_id")
            result = conn.execute(query, {"type_id": type_id}).fetchone()
            if result:
                return type_id, result[0]
            raise ValueError(f"No item found with typeID={type_id}")

    if type_name is not None:
        with sde_db.engine.connect() as conn:
            # Try exact match first
            query = text(
                "SELECT typeID, typeName FROM inv_info WHERE typeName = :name"
            )
            result = conn.execute(query, {"name": type_name}).fetchone()
            if result:
                return result[0], result[1]

            # Try partial match
            query = text(
                "SELECT typeID, typeName FROM inv_info "
                "WHERE typeName LIKE :pattern ORDER BY typeName LIMIT 10"
            )
            rows = conn.execute(
                query, {"pattern": f"%{type_name}%"}).fetchall()
            if len(rows) == 1:
                return rows[0][0], rows[0][1]
            elif len(rows) > 1:
                names = "\n  ".join(
                    f"{r[0]}: {r[1]}" for r in rows)
                raise ValueError(
                    f"Ambiguous name '{type_name}'. Matches:\n  {names}"
                )
            raise ValueError(f"No item found matching '{type_name}'")

    raise ValueError("Either --id or --name is required")


def _query_module_usage(
    type_id: int,
    market_ctx: Optional[MarketContext] = None,
) -> List[Dict]:
    """
    Query which fits use a given module and their market status.

    Joins doctrines and doctrine_fits on fit_id where type_id matches.

    Args:
        type_id: The type ID of the module to look up
        market_ctx: Market context for database selection

    Returns:
        List of dicts with fit usage and market data
    """
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
            qty_needed = max(0, int(
                (target - fits) * fit_qty)) if fits < target else 0

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
    """
    Display which fits use a module and their market availability.

    Args:
        type_id: Type ID of the module
        type_name: Type name of the module (alternative to type_id)
        market_alias: Market alias or "both" for dual-market display

    Returns:
        True if successful, False otherwise
    """
    # Resolve module identity
    try:
        resolved_id, resolved_name = _resolve_module_identity(
            type_id, type_name)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return False

    show_both = market_alias == "both"

    if show_both:
        # Query both markets
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

        # Merge data: key by fit_id
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

        # Add any deployment-only fits
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

        table = create_module_usage_table(
            resolved_name, resolved_id, merged, show_both=True)
        console.print(table)
        console.print(
            f"\n[dim]Total: {len(merged)} fit(s) using this module[/dim]")

    else:
        # Single market
        try:
            market_ctx = MarketContext.from_settings(market_alias)
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            console.print(f"Available markets: {
                ', '.join(MarketContext.list_available())}")
            return False

        data = _query_module_usage(resolved_id, market_ctx)

        if not data:
            console.print(
                f"[yellow]Module '{resolved_name}' (ID: {resolved_id}) "
                f"is not used in any tracked fits on {market_ctx.name}.[/yellow]"
            )
            return True

        table = create_module_usage_table(
            resolved_name, resolved_id, data, show_both=False)
        console.print(table)
        console.print(
            f"\n[dim]Total: {len(data)} fit(s) using this module "
            f"on {market_ctx.name}[/dim]"
        )

    return True


def _handle_list_fits(sub_args: List[str]) -> None:
    """
    Handle the list-fits subcommand.

    Args:
        sub_args: Remaining arguments after 'list-fits'
    """
    from mkts_backend.cli_tools.fit_update import list_fits_command

    market_alias = "primary"
    for arg in sub_args:
        if arg.startswith("--market="):
            market_alias = arg.split("=", 1)[1]

    try:
        market_ctx = MarketContext.from_settings(market_alias)
        db_alias = market_ctx.database_alias
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    list_fits_command(db_alias=db_alias)


def _handle_module(sub_args: List[str]) -> None:
    """
    Handle the module subcommand.

    Args:
        sub_args: Remaining arguments after 'module'
    """
    type_id = None
    type_name = None
    market_alias = "primary"

    for arg in sub_args:
        if arg.startswith("--id="):
            try:
                type_id = int(arg.split("=", 1)[1])
            except ValueError:
                console.print("[red]Error: --id must be an integer[/red]")
                return
        elif arg.startswith("--name="):
            type_name = arg.split("=", 1)[1]
        elif arg.startswith("--market="):
            market_alias = arg.split("=", 1)[1]

    if type_id is None and type_name is None:
        console.print(
            "[red]Error: --id=<type_id> or --name=<name> is required[/red]")
        console.print("Usage: fitcheck module --id=11269")
        console.print('       fitcheck module --name="Multispectrum Energized Membrane II"')
        return

    module_command(type_id=type_id, type_name=type_name,
                   market_alias=market_alias)


def display_help():
    """Display help for the fitcheck command."""
    print("""
fitcheck - Display market availability for items in an EFT-formatted ship fit

USAGE:
    fitcheck --fit=<id> [options]
    fitcheck --file=<path> [options]
    fitcheck --paste [options]
    fitcheck list-fits [--market=<alias>]
    fitcheck module --id=<type_id> [--market=<alias>]
    fitcheck module --name="<name>" [--market=<alias>]

SUBCOMMANDS:
    list-fits            List all tracked doctrine fits
        --market=<alias>     Market database to query (default: primary)

    module               Show which fits use a given module and market status
        --id=<type_id>       Look up module by type ID
        --name="<name>"      Look up module by name (exact or partial match)
        --market=<alias>     Market to check: primary, deployment, both
                             (default: primary)

DESCRIPTION:
    Analyzes an EFT (Eve Fitting Tool) formatted ship fit and displays market
    availability for each item. Shows how many complete fits can be built from
    current market stock, with color-coded status indicators.

OPTIONS:
    --fit=<id>           Look up fit by ID from doctrine_fits/doctrines tables
                         (uses pre-calculated market data)
    --file=<path>        Path to EFT fit file
    --paste              Read EFT fit from stdin instead of file
    --market=<alias>     Market to check: primary, deployment (default: primary)
    --target=<N>         Override target quantity (default: from doctrine_fits)
    --output=<format>    Export format: csv, multibuy, or markdown
    --no-jita            Hide Jita price comparison columns
    --no-legend          Hide the legend
    --help, -h           Show this help message

EXAMPLES:
    # Check fit by ID (most common usage)
    fitcheck --fit=42

    # Check fit by ID against deployment market
    fitcheck --fit=42 --market=deployment

    # Check fit from EFT file
    fitcheck --file=fits/hurricane_fleet.txt

    # Override target and export multi-buy list
    fitcheck --fit=42 --target=50 --output=multibuy

    # Export markdown for Discord
    fitcheck --fit=42 --output=markdown

    # List all tracked fits
    fitcheck list-fits
    fitcheck list-fits --market=deployment

    # Check module usage across fits
    fitcheck module --id=11269
    fitcheck module --name="Multispectrum Energized Membrane II"
    fitcheck module --id=11269 --market=both
""")


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
        console.print(f"Available markets: {
                      ', '.join(MarketContext.list_available())}")
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
            console.print(
                "[red]Error: Either --file, --paste, or --fit_id must be specified[/red]"
            )
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
        console.print(
            "[yellow]Warning: Some items could not be resolved:[/yellow]")
        for item in parse_result.missing_types[:5]:
            console.print(f"  • {item}", style="yellow")
        if len(parse_result.missing_types) > 5:
            console.print(
                f"  ... and {len(parse_result.missing_types) - 5} more",
                style="dim yellow",
            )
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


def main():
    """
    Standalone CLI entry point for fitcheck command.

    Usage: fitcheck --fit=<id> [options]
    """
    import sys

    args = sys.argv[1:]

    # Handle help
    if not args or "--help" in args or "-h" in args:
        display_help()
        sys.exit(0)

    # Subcommand routing - check before flag parsing
    subcommands = {"list-fits", "module"}
    if args[0] in subcommands:
        sub = args[0]
        sub_args = args[1:]
        if sub == "list-fits":
            _handle_list_fits(sub_args)
        elif sub == "module":
            _handle_module(sub_args)
        sys.exit(0)

    # Parse arguments
    file_path = None
    fit_id = None
    market_alias = "primary"
    target = None
    output_format = None
    show_jita = True
    show_legend = True
    paste_mode = False

    for arg in args:
        if arg.startswith("--fit="):
            try:
                fit_id = int(arg.split("=", 1)[1])
            except ValueError:
                console.print("[red]Error: --fit must be an integer[/red]")
                sys.exit(1)
        elif arg.startswith("--file="):
            file_path = arg.split("=", 1)[1]
        elif arg.startswith("--market="):
            market_alias = arg.split("=", 1)[1]
        elif arg == "--deployment":
            market_alias = "deployment"
        elif arg == "--primary":
            market_alias = "primary"
        elif arg.startswith("--target="):
            try:
                target = int(arg.split("=", 1)[1])
            except ValueError:
                console.print("[red]Error: --target must be an integer[/red]")
                sys.exit(1)
        elif arg.startswith("--output="):
            output_format = arg.split("=", 1)[1].lower()
            if output_format not in ("csv", "multibuy", "markdown"):
                console.print(
                    "[red]Error: --output must be one of: csv, multibuy, markdown[/red]"
                )
                sys.exit(1)
        elif arg == "--no-jita":
            show_jita = False
        elif arg == "--no-legend":
            show_legend = False
        elif arg == "--paste":
            paste_mode = True

    # Validate input
    if not file_path and fit_id is None and not paste_mode:
        console.print(
            "[red]Error: --fit=<id>, --file=<path>, or --paste is required[/red]"
        )
        console.print("Use 'fitcheck --help' for usage information.")
        sys.exit(1)

    # Handle paste mode
    eft_text = None
    if paste_mode:
        from mkts_backend.cli_tools.fit_update import get_multiline_input

        eft_text = get_multiline_input()

    # Run the command
    success = fit_check_command(
        file_path=file_path,
        eft_text=eft_text,
        fit_id=fit_id,
        market_alias=market_alias,
        show_legend=show_legend,
        target=target,
        output_format=output_format,
        show_jita=show_jita,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
