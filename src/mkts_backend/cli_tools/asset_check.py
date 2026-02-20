"""
Asset lookup CLI command.

Resolves a type by ID or partial name, fetches packaged assets for all
configured characters via ESI, and displays a summary table.
"""

from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from mkts_backend.config.config import DatabaseConfig
from mkts_backend.config.character_config import CharacterConfig, load_characters
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.esi.character_assets import fetch_all_character_assets

logger = configure_logging(__name__)


def resolve_type(
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
) -> Tuple[int, str]:
    """
    Resolve a type_id and type_name from either input.

    If type_id is given, looks up the name.
    If type_name is given, tries exact match then partial match.
    For multiple partial matches, presents an interactive picker.

    Returns:
        (type_id, type_name) tuple

    Raises:
        ValueError: If the type cannot be resolved
    """
    sde_db = DatabaseConfig("sde")

    if type_id is not None:
        with sde_db.engine.connect() as conn:
            result = conn.execute(
                text("SELECT typeName FROM sdetypes WHERE typeID = :tid"),
                {"tid": type_id},
            ).fetchone()
            if result:
                return type_id, result[0]
            raise ValueError(f"No item found with typeID={type_id}")

    if type_name is not None:
        with sde_db.engine.connect() as conn:
            # Exact match
            result = conn.execute(
                text("SELECT typeID, typeName FROM sdetypes WHERE typeName = :name"),
                {"name": type_name},
            ).fetchone()
            if result:
                return result[0], result[1]

            # Partial match
            rows = conn.execute(
                text(
                    "SELECT typeID, typeName FROM sdetypes "
                    "WHERE typeName LIKE :pattern ORDER BY typeName LIMIT 20"
                ),
                {"pattern": f"%{type_name}%"},
            ).fetchall()

            if len(rows) == 0:
                raise ValueError(f"No item found matching '{type_name}'")
            if len(rows) == 1:
                return rows[0][0], rows[0][1]

            # Interactive picker for multiple matches
            print(f"\nMultiple matches for '{type_name}':")
            for i, row in enumerate(rows, 1):
                print(f"  {i}. {row[1]} (ID: {row[0]})")

            choice = input("\nSelect number (or 'q' to cancel): ").strip()
            if choice.lower() == "q":
                raise ValueError("Selection cancelled")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(rows):
                    return rows[idx][0], rows[idx][1]
                raise ValueError("Invalid selection")
            except ValueError as e:
                if "Invalid selection" in str(e) or "Selection cancelled" in str(e):
                    raise
                raise ValueError("Invalid input — enter a number")

    raise ValueError("Either --id or --name is required")


def asset_check_command(
    type_id: Optional[int] = None,
    type_name: Optional[str] = None,
    force_refresh: bool = False,
) -> bool:
    """
    Main entry point for the `assets` CLI subcommand.

    Resolves the type, fetches all character assets, and displays a table.

    Returns:
        True on success, False on failure
    """
    from mkts_backend.cli_tools.rich_display import console, create_asset_table

    try:
        resolved_id, resolved_name = resolve_type(type_id=type_id, type_name=type_name)
    except ValueError as e:
        print(f"Error: {e}")
        return False

    console.print(
        f"\nFetching assets for [bold cyan]{resolved_name}[/bold cyan] "
        f"(ID: {resolved_id})...\n"
    )

    # Fetch assets for all characters — no type_ids filter so we get everything,
    # then we filter to just our type
    char_assets = fetch_all_character_assets(
        type_ids=[resolved_id], force_refresh=force_refresh
    )

    # Build display data
    rows = []
    grand_total = 0
    for char, assets_map in char_assets:
        qty = assets_map.get(resolved_id, 0)
        rows.append({"character": char.name, "short_name": char.short_name, "quantity": qty})
        grand_total += qty

    table = create_asset_table(resolved_name, resolved_id, rows, grand_total)
    console.print(table)
    console.print()

    return True
