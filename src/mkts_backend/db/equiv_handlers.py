"""
Module Equivalents DB Handlers

CRUD operations for the module_equivalents table, which maps
interchangeable faction modules for aggregated stock calculations.
"""

from typing import Optional, TYPE_CHECKING
from sqlalchemy import text

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.config import DatabaseConfig

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

logger = configure_logging(__name__)


def _get_db(market_ctx: Optional["MarketContext"] = None) -> DatabaseConfig:
    """Get database config, optionally using market context."""
    if market_ctx is not None:
        return DatabaseConfig(market_context=market_ctx)
    return DatabaseConfig("wcmkt")


def _get_sde_db() -> DatabaseConfig:
    """Get SDE database config."""
    return DatabaseConfig("sde")


def resolve_type_name(type_id: int) -> Optional[str]:
    """Look up a type name from the SDE database."""
    sde_db = _get_sde_db()
    query = text("SELECT typeName FROM invTypes WHERE typeID = :type_id")
    with sde_db.engine.connect() as conn:
        result = conn.execute(query, {"type_id": type_id}).fetchone()
        return result[0] if result else None


def list_equiv_groups(market_ctx: Optional["MarketContext"] = None) -> list[dict]:
    """
    List all equivalence groups with their members.

    Returns:
        List of dicts with equiv_group_id, type_id, type_name
    """
    db = _get_db(market_ctx)
    query = text("""
        SELECT equiv_group_id, type_id, type_name
        FROM module_equivalents
        ORDER BY equiv_group_id, type_name
    """)

    with db.engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    groups: dict[int, list[dict]] = {}
    for row in rows:
        gid = row[0]
        if gid not in groups:
            groups[gid] = []
        groups[gid].append({
            "equiv_group_id": gid,
            "type_id": row[1],
            "type_name": row[2],
        })

    return [
        {"equiv_group_id": gid, "members": members}
        for gid, members in groups.items()
    ]


def get_next_equiv_group_id(market_ctx: Optional["MarketContext"] = None) -> int:
    """Get the next available equiv_group_id."""
    db = _get_db(market_ctx)
    query = text("SELECT COALESCE(MAX(equiv_group_id), 0) + 1 FROM module_equivalents")
    with db.engine.connect() as conn:
        result = conn.execute(query).fetchone()
        return result[0]


def add_equiv_group(
    type_ids: list[int],
    market_ctx: Optional["MarketContext"] = None,
) -> int:
    """
    Add a new equivalence group.

    Resolves type names from SDE, inserts rows into module_equivalents.

    Args:
        type_ids: List of EVE type IDs to group as equivalents
        market_ctx: Optional market context

    Returns:
        The new equiv_group_id
    """
    db = _get_db(market_ctx)
    equiv_group_id = get_next_equiv_group_id(market_ctx)

    insert_query = text("""
        INSERT INTO module_equivalents (equiv_group_id, type_id, type_name)
        VALUES (:equiv_group_id, :type_id, :type_name)
    """)

    with db.engine.begin() as conn:
        for type_id in type_ids:
            type_name = resolve_type_name(type_id)
            if type_name is None:
                logger.warning(f"Could not resolve type_id {type_id}, skipping")
                continue

            conn.execute(insert_query, {
                "equiv_group_id": equiv_group_id,
                "type_id": type_id,
                "type_name": type_name,
            })
            logger.info(f"Added {type_name} ({type_id}) to group {equiv_group_id}")

    return equiv_group_id


def remove_equiv_group(
    equiv_group_id: int,
    market_ctx: Optional["MarketContext"] = None,
) -> int:
    """
    Remove an equivalence group.

    Args:
        equiv_group_id: The group ID to remove

    Returns:
        Number of rows deleted
    """
    db = _get_db(market_ctx)
    delete_query = text(
        "DELETE FROM module_equivalents WHERE equiv_group_id = :equiv_group_id"
    )

    with db.engine.begin() as conn:
        result = conn.execute(delete_query, {"equiv_group_id": equiv_group_id})
        count = result.rowcount
        logger.info(f"Removed {count} rows from equiv group {equiv_group_id}")
        return count


def ensure_equiv_table(market_ctx: Optional["MarketContext"] = None) -> bool:
    """
    Ensure the module_equivalents table exists.

    Returns:
        True if table exists or was created
    """
    db = _get_db(market_ctx)
    create_query = text("""
        CREATE TABLE IF NOT EXISTS module_equivalents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equiv_group_id INTEGER NOT NULL,
            type_id INTEGER NOT NULL,
            type_name VARCHAR(255) NOT NULL
        )
    """)

    try:
        with db.engine.begin() as conn:
            conn.execute(create_query)
        return True
    except Exception as e:
        logger.error(f"Failed to create module_equivalents table: {e}")
        return False
