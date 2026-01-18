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
        return f"{value / 1_000_000:,.2f}M{suffix}"
    elif value >= 1_000:
        return f"{value / 1_000:,.2f}K{suffix}"
    else:
        return f"{value:,.2f}{suffix}"


def format_quantity(value: Optional[int]) -> str:
    """Format a quantity with comma separators."""
    if value is None:
        return "0"
    return f"{value:,}"


def format_fits(value: Optional[float]) -> str:
    """Format number of fits with one decimal place."""
    if value is None or value < 0:
        return "N/A"
    return f"{value:,.1f}"


def create_fit_status_table(
    fit_name: str,
    ship_name: str,
    ship_type_id: Optional[int],
    market_data: List[Dict],
    total_fit_cost: float,
    market_name: str = "primary",
    target: Optional[int] = None,
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

        # Calculate qty_needed if target is set
        if target is not None:
            qty_needed = max(0, int((target - fits) * fit_qty)) if fits < target else 0
            qty_needed_str = format_quantity(qty_needed) if qty_needed > 0 else "-"
            qty_needed_style = "red" if qty_needed > 0 else "dim"

            table.add_row(
                str(type_id),
                name_display,
                format_quantity(market_stock),
                str(fit_qty),
                f"[{fits_style}]{format_fits(fits)}[/{fits_style}]",
                f"[{qty_needed_style}]{qty_needed_str}[/{qty_needed_style}]",
                format_isk(price, include_suffix=False),
                format_isk(fit_price, include_suffix=False),
                source_indicator,
                end_section=is_ship,  # Add divider after ship row
            )
        else:
            table.add_row(
                str(type_id),
                name_display,
                format_quantity(market_stock),
                str(fit_qty),
                f"[{fits_style}]{format_fits(fits)}[/{fits_style}]",
                format_isk(price, include_suffix=False),
                format_isk(fit_price, include_suffix=False),
                source_indicator,
                end_section=is_ship,  # Add divider after ship row
            )

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

    # Add total fits available
    if total_fits is not None:
        header_text.append("\n")
        header_text.append("Fits Available: ", style="bold white")
        fits_style = "green" if total_fits >= 10 else ("yellow" if total_fits >= 1 else "red")
        header_text.append(f"{total_fits:.1f}", style=f"bold {fits_style}")

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
    console.print(f"Complete fits possible: [bold yellow]{format_fits(min_fits)}[/bold yellow]")

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
                f"(current: {item['fits']:.1f} fits)",
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
