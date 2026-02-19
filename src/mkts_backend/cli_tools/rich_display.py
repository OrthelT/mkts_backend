"""
Rich display utilities for CLI output.

Provides formatted tables and console output for fit market status display.
"""

from typing import Dict, List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box


console = Console()


def format_isk(value: Optional[float], include_suffix: bool = True) -> str:
    """
    Format an ISK value with proper abbreviation.

    Args:
        value: The ISK value to format
        include_suffix: Whether to include " ISK" suffix

    Returns:
        Formatted string like "1.23B ISK" or "456.78M ISK"
    """
    if value is None:
        return "N/A"

    suffix = " ISK" if include_suffix else ""

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:,.2f}B{suffix}"
    elif value >= 1_000_000:
        return f"{value / 1_000_000:,.1f}M{suffix}"
    elif value >= 1_000:
        return f"{value / 1_000:,.1f}K{suffix}"
    else:
        return f"{value:,.0f}{suffix}"


def format_quantity(value: Optional[int]) -> str:
    """Format a quantity with comma separators."""
    if value is None:
        return "0"
    return f"{value:,.0f}"


def format_fits(value: Optional[float]) -> str:
    """Format number of fits with 0 decimal place."""
    if value is None or value < 0:
        return "N/A"
    return f"{value:,.0f}"


def create_fit_status_table(
    fit_name: str,
    ship_name: str,
    ship_type_id: Optional[int],
    market_data: List[Dict],
    total_fit_cost: float,
    market_name: str = "primary",
    target: Optional[int] = None,
    show_jita: bool = True,
) -> Table:
    """
    Create a Rich table displaying fit market status.

    Args:
        fit_name: Name of the fit
        ship_name: Name of the ship
        ship_type_id: Type ID of the ship hull
        market_data: List of item market data dicts
        total_fit_cost: Total cost of the fit
        market_name: Name of the market being queried
        target: Optional target quantity for qty_needed calculation
        show_jita: Whether to show Jita price columns

    Returns:
        A Rich Table object ready for display
    """
    table = Table(
        title=f"[bold cyan]{fit_name}[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title_justify="left",
    )

    # Define columns matching the plan specification
    table.add_column("Type ID", style="dim", justify="right", width=10)
    table.add_column("Item Name", style="white", min_width=30)
    table.add_column("Stock", justify="right", width=10)
    table.add_column("Fit Qty", justify="right", width=8)
    table.add_column("Fits", justify="right", width=8)
    if target is not None:
        table.add_column("Qty Needed", justify="right", width=10)
    table.add_column("Price", justify="right", width=14)
    table.add_column("Fit Cost", justify="right", width=14)
    if show_jita:
        table.add_column("Jita Price", justify="right", width=14)
        table.add_column("Jita Fit", justify="right", width=14)
    table.add_column("Source", justify="center", width=8)

    for item in market_data:
        type_id = item.get("type_id", 0)
        type_name = item.get("type_name", "Unknown")
        market_stock = item.get("market_stock", 0)
        fit_qty = item.get("fit_qty", 1)
        fits = item.get("fits", 0)
        price = item.get("price")
        fit_price = item.get("fit_price", 0)
        is_fallback = item.get("is_fallback", False)
        is_ship = item.get("is_ship", False)
        jita_price = item.get("jita_price")
        jita_fit_price = item.get("jita_fit_price", 0)

        # Color coding based on availability
        if fits >= 10:
            fits_style = "green"
        elif fits >= 1:
            fits_style = "yellow"
        else:
            fits_style = "red"

        # Mark fallback data with asterisk
        source_indicator = "[yellow]*[/yellow]" if is_fallback else "[green]✓[/green]"

        # Style ship row differently (bold cyan name)
        name_display = f"[bold cyan]{type_name}[/bold cyan]" if is_ship else type_name

        # Build the row data
        row_data = [
            str(type_id),
            name_display,
            format_quantity(market_stock),
            str(fit_qty),
            f"[{fits_style}]{format_fits(fits)}[/{fits_style}]",
        ]

        # Add qty_needed if target is set
        if target is not None:
            qty_needed = max(0, int((target - fits) * fit_qty)) if fits < target else 0
            qty_needed_str = format_quantity(qty_needed) if qty_needed > 0 else "-"
            qty_needed_style = "red" if qty_needed > 0 else "dim"
            row_data.append(f"[{qty_needed_style}]{qty_needed_str}[/{qty_needed_style}]")

        # Add price columns
        row_data.append(format_isk(price, include_suffix=False))
        row_data.append(format_isk(fit_price, include_suffix=False))

        # Add Jita columns if enabled
        if show_jita:
            row_data.append(format_isk(jita_price, include_suffix=False))
            row_data.append(format_isk(jita_fit_price, include_suffix=False))

        # Add source indicator
        row_data.append(source_indicator)

        table.add_row(*row_data, end_section=is_ship)

    return table


def print_fit_header(
    fit_name: str,
    ship_name: str,
    ship_type_id: Optional[int],
    market_name: str,
    total_fit_cost: float,
    total_fits: Optional[float] = None,
    target: Optional[int] = None,
    width: Optional[int] = None,
    total_jita_fit_cost: Optional[float] = None,
    hulls: Optional[int] = None,
) -> None:
    """
    Print a formatted header for fit status display.

    Args:
        fit_name: Name of the fit
        ship_name: Name of the ship
        ship_type_id: Type ID of the ship
        market_name: Name of the market
        total_fit_cost: Total cost of the fit
        total_fits: Total complete fits available (minimum of fits column)
        target: Target quantity from doctrine_fits
        width: Optional width to constrain the header panel
        total_jita_fit_cost: Total Jita cost of the fit
        hulls: Number of ship hulls available on market
    """
    header_text = Text()
    header_text.append("Ship: ", style="bold white")
    header_text.append(f"{ship_name}", style="cyan")
    if ship_type_id:
        header_text.append(f" (ID: {ship_type_id})", style="dim")
    header_text.append("\n")
    header_text.append("Market: ", style="bold white")
    header_text.append(f"{market_name}", style="green")
    header_text.append("\n")
    header_text.append("Total Fit Cost: ", style="bold white")
    header_text.append(format_isk(total_fit_cost), style="bold yellow")

    # Add Jita fit cost if available
    if total_jita_fit_cost is not None and total_jita_fit_cost > 0:
        header_text.append("\n")
        header_text.append("Jita Fit Cost: ", style="bold white")
        header_text.append(format_isk(total_jita_fit_cost), style="bold cyan")

    # Add total fits available
    if total_fits is not None:
        header_text.append("\n")
        header_text.append("Fits Available: ", style="bold white")
        fits_style = "green" if total_fits >= 10 else ("yellow" if total_fits >= 1 else "red")
        header_text.append(f"{total_fits:.1f}", style=f"bold {fits_style}")

    # Add hulls available
    if hulls is not None:
        header_text.append("\n")
        header_text.append("Hulls: ", style="bold white")
        hulls_style = "green" if hulls >= 10 else ("yellow" if hulls >= 1 else "red")
        header_text.append(f"{hulls}", style=f"bold {hulls_style}")

    # Add target if known
    if target is not None:
        header_text.append("\n")
        header_text.append("Target: ", style="bold white")
        header_text.append(f"{target}", style="bold magenta")

    panel = Panel(
        header_text,
        title=f"[bold]{fit_name}[/bold]",
        border_style="blue",
        padding=(0, 2),
        width=width,
        expand=False,
    )
    console.print(panel)


def print_fit_summary(
    available_count: int,
    total_count: int,
    min_fits: float,
    missing_items: List[str],
) -> None:
    """
    Print a summary of fit availability.

    Args:
        available_count: Number of items with stock
        total_count: Total number of items in fit
        min_fits: Minimum number of complete fits available
        missing_items: List of items with insufficient stock
    """
    console.print()

    # Availability summary
    if available_count == total_count:
        status_style = "bold green"
        status_msg = "All items available"
    elif available_count > total_count * 0.8:
        status_style = "bold yellow"
        status_msg = "Most items available"
    else:
        status_style = "bold red"
        status_msg = "Low availability"

    console.print(f"[{status_style}]Status: {status_msg}[/{status_style}]")
    console.print(f"Items: [cyan]{available_count}[/cyan]/[white]{total_count}[/white] available")
    console.print(f"Complete fits possible: [bold yellow]{int(format_fits(min_fits))}[/bold yellow]")

    if missing_items:
        console.print()
        console.print("[bold red]Items with insufficient stock:[/bold red]")
        for item in missing_items[:5]:
            console.print(f"  • {item}", style="red")
        if len(missing_items) > 5:
            console.print(f"  ... and {len(missing_items) - 5} more", style="dim red")


def print_legend() -> None:
    """Print a legend explaining the table columns and indicators."""
    legend = """
[bold]Legend:[/bold]
  [green]✓[/green] = Data from watchlist/marketstats
  [yellow]*[/yellow] = Fallback data (marketorders + ESI)
  [green]Fits >= 10[/green] = Good stock
  [yellow]Fits 1-9[/yellow] = Low stock
  [red]Fits < 1[/red] = Insufficient stock
"""
    console.print(legend, style="dim")


def print_missing_for_target(missing_items: List[Dict], target: int) -> None:
    """
    Print a list of items missing to reach the target quantity.

    Args:
        missing_items: List of dicts with type_name and qty_needed
        target: Target quantity
    """
    if not missing_items:
        return

    console.print()
    console.print(f"[bold red]Items below target ({target}):[/bold red]")
    for item in missing_items:
        if item["qty_needed"] > 0:
            console.print(
                f"  • {item['type_name']}: [red]{format_quantity(item['qty_needed'])}[/red] needed "
                f"(current: {item['fits']:.0f} fits)",
                style="white"
            )


def print_multibuy_export(multibuy_text: str) -> None:
    """
    Print the multi-buy export text as plain text for easy copying.

    Args:
        multibuy_text: Multi-buy format text to display
    """
    if not multibuy_text:
        return

    console.print()
    console.print("[bold cyan]Eve Multi-buy / jEveAssets Stockpile Format[/bold cyan]")
    console.print("[dim]Copy and paste into game or tool:[/dim]")
    console.print()
    # Print plain text without any Rich formatting for clean copy-paste
    print(multibuy_text)
    console.print()


def print_markdown_export(markdown_text: str) -> None:
    """
    Print the markdown export text as plain text for easy copying to Discord.

    Args:
        markdown_text: Markdown format text to display
    """
    if not markdown_text:
        return

    console.print()
    console.print("[bold cyan]Discord Markdown Format[/bold cyan]")
    console.print("[dim]Copy and paste into Discord:[/dim]")
    console.print()
    # Print plain text without any Rich formatting for clean copy-paste
    print(markdown_text)
    console.print()


def print_overpriced_items(overpriced_items: List[Dict]) -> None:
    """
    Print a list of items priced above 120% of Jita price.

    Args:
        overpriced_items: List of dicts with type_name, local_price, jita_price, percent_above_jita
    """
    if not overpriced_items:
        return

    console.print()
    console.print("[bold yellow]Items priced above 120% of Jita:[/bold yellow]")
    for item in overpriced_items:
        percent = item.get("percent_above_jita", 0)
        local_price = format_isk(item['local_price'])
        jita_price = format_isk(item['jita_price'])

        console.print(
            f"  • {item['type_name']}: [yellow]{percent:.0f}%[/yellow] above Jita "
            f"({split_suffix_format(local_price,'cyan')} vs {split_suffix_format(jita_price,'cyan')})",
            style="white"
        )

def split_suffix_format(item: str, color: str)->str:
    split_item = item.split(" ")
    value = split_item[0]
    suffix = split_item[1]
    formatted_item = f"[{color}]{value}[/{color}] {suffix}"
    return formatted_item


def create_needed_table(
    fit_id: int,
    ship_name: str,
    fit_name: str,
    min_fits: float,
    target: int,
    items: List[Dict],
    ship_id: Optional[int] = None,
) -> Table:
    """
    Create a Rich sub-table for a single fit's needed items.

    Args:
        fit_id: The fit ID
        ship_name: Ship name for the header
        fit_name: Fit name for the header
        min_fits: Minimum fits on market for this fit
        target: Target quantity for this fit
        items: List of dicts with type_id, type_name, target, fits_on_mkt,
               total_stock, targ_perc, qty_needed
        ship_id: Ship type ID for the header

    Returns:
        A Rich Table object for this fit group
    """
    fits_style = "green" if min_fits >= target else (
        "yellow" if min_fits >= target * 0.5 else "red")

    id_parts = f"fit_id: {fit_id}"
    if ship_id:
        id_parts += f"; type_id: {ship_id}"

    table = Table(
        title=(
            f"[bold cyan]{ship_name}[/bold cyan]"
            f" [dim]({id_parts})[/dim]"
            f"\n[white]{fit_name}[/white]"
            f" ([{fits_style}]{int(min_fits)}[/{fits_style}])"
        ),
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        title_justify="left",
        padding=(0, 1),
    )

    table.add_column("ID", style="dim", justify="right", width=7)
    table.add_column("Item", style="white", no_wrap=False)
    table.add_column("Stock", justify="right", width=7)
    table.add_column("Fits", justify="right", width=6)
    table.add_column("Tgt", justify="right", width=5)
    table.add_column("Tgt%", justify="right", width=5)
    table.add_column("Need", justify="right", width=7)

    for item in items:
        fits = item.get("fits_on_mkt", 0) or 0
        targ_perc = item.get("targ_perc", 0) or 0
        qty_needed = item.get("qty_needed", 0) or 0

        # Color code fits
        if targ_perc >= 1.0:
            perc_style = "green"
        elif targ_perc >= 0.5:
            perc_style = "yellow"
        else:
            perc_style = "red"

        row_data = [
            str(item.get("type_id", "")),
            item.get("type_name", "Unknown"),
            format_quantity(item.get("total_stock", 0)),
            format_fits(fits),
            str(item.get("target", 0)),
            f"[{perc_style}]{targ_perc:.0%}[/{perc_style}]",
            f"[red]{format_quantity(qty_needed)}[/red]" if qty_needed > 0 else "[dim]-[/dim]",
        ]

        table.add_row(*row_data)

    return table


def _fits_style(fits: float, target: Optional[int] = None) -> str:
    """
    Return a Rich style string based on fits available.

    Args:
        fits: Number of fits available
        target: Optional target quantity for comparison

    Returns:
        Rich style string: "green", "yellow", or "red"
    """
    if target is not None and fits >= target:
        return "green"
    if fits >= 10:
        return "green"
    elif fits >= 1:
        return "yellow"
    else:
        return "red"


def create_module_usage_table(
    type_name: str,
    type_id: int,
    market_data: List[Dict],
    show_both: bool = False,
) -> Table:
    """
    Create a Rich table showing which fits use a given module and their market status.

    Args:
        type_name: Name of the module/item
        type_id: Type ID of the module/item
        market_data: List of dicts with fit usage data. Each dict contains:
            - fit_id, fit_name, ship_name, doctrine_name, fit_qty, target
            - For single market: total_stock, fits_on_mkt, qty_needed, price
            - For dual market: p_stock, p_fits, p_need, p_price, d_stock, d_fits, d_need, d_price
        show_both: If True, show columns for both primary and deployment markets

    Returns:
        A Rich Table object ready for display
    """
    table = Table(
        title=f"[bold cyan]{type_name}[/bold cyan] (ID: {type_id})",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title_justify="left",
    )

    table.add_column("Fit ID", style="dim", justify="right", width=8)
    table.add_column("Fit Name", style="white", min_width=25)
    table.add_column("Ship", style="cyan", min_width=18)
    table.add_column("Doctrine", style="green", min_width=15)
    table.add_column("Fit Qty", justify="right", width=8)
    table.add_column("Target", justify="right", width=8)

    if show_both:
        table.add_column("Stock(P)", justify="right", width=10)
        table.add_column("Fits(P)", justify="right", width=9)
        table.add_column("Need(P)", justify="right", width=9)
        table.add_column("Stock(D)", justify="right", width=10)
        table.add_column("Fits(D)", justify="right", width=9)
        table.add_column("Need(D)", justify="right", width=9)
    else:
        table.add_column("Stock", justify="right", width=10)
        table.add_column("Fits", justify="right", width=9)
        table.add_column("Qty Needed", justify="right", width=10)

    table.add_column("Price", justify="right", width=14)

    for row in market_data:
        fit_id = row.get("fit_id", 0)
        fit_name = row.get("fit_name", "Unknown")
        ship_name = row.get("ship_name", "Unknown")
        doctrine_name = row.get("doctrine_name", "")
        fit_qty = row.get("fit_qty", 1)
        target = row.get("target", 0)
        price = row.get("price")

        row_data = [
            str(fit_id),
            fit_name,
            ship_name,
            doctrine_name,
            str(fit_qty),
            str(target),
        ]

        if show_both:
            for prefix in ("p_", "d_"):
                stock = row.get(f"{prefix}stock", 0)
                fits = row.get(f"{prefix}fits", 0)
                need = row.get(f"{prefix}need", 0)

                style = _fits_style(fits, target)
                row_data.append(format_quantity(stock))
                row_data.append(f"[{style}]{format_fits(fits)}[/{style}]")
                need_str = format_quantity(need) if need > 0 else "-"
                need_style = "red" if need > 0 else "dim"
                row_data.append(f"[{need_style}]{need_str}[/{need_style}]")
        else:
            stock = row.get("total_stock", 0)
            fits = row.get("fits_on_mkt", 0)
            need = row.get("qty_needed", 0)

            style = _fits_style(fits, target)
            row_data.append(format_quantity(stock))
            row_data.append(f"[{style}]{format_fits(fits)}[/{style}]")
            need_str = format_quantity(need) if need > 0 else "-"
            need_style = "red" if need > 0 else "dim"
            row_data.append(f"[{need_style}]{need_str}[/{need_style}]")

        row_data.append(format_isk(price, include_suffix=False))

        table.add_row(*row_data)

    return table
