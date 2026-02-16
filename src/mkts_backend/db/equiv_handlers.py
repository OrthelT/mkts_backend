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
    query = text("SELECT typeName FROM sdetypes WHERE typeID = :type_id")
    with sde_db.engine.connect() as conn:
        result = conn.execute(query, {"type_id": type_id}).fetchone()
        return result[0] if result else None


def resolve_type_id(name: str) -> list[tuple[int, str]]:
    """
    Look up type IDs by name from the SDE database.

    Tries exact match first, then partial LIKE match.
    Excludes blueprints (categoryID 9).
    Returns list of (typeID, typeName) tuples, max 20 results.
    """
    sde_db = _get_sde_db()

    # Exact match first
    exact_query = text("""
        SELECT typeID, typeName FROM sdetypes
        WHERE typeName = :name AND categoryID != 9
        LIMIT 20
    """)
    with sde_db.engine.connect() as conn:
        rows = conn.execute(exact_query, {"name": name}).fetchall()
        if rows:
            return [(r[0], r[1]) for r in rows]

    # Partial match
    like_query = text("""
        SELECT typeID, typeName FROM sdetypes
        WHERE typeName LIKE :pattern AND categoryID != 9
        ORDER BY typeName
        LIMIT 20
    """)
    with sde_db.engine.connect() as conn:
        rows = conn.execute(like_query, {"pattern": f"%{name}%"}).fetchall()
        return [(r[0], r[1]) for r in rows]


def find_equiv_by_attributes(type_id: int) -> list[dict]:
    """
    Find modules with identical dogma attributes (attribute fingerprinting).

    Uses GROUP_CONCAT of attributeID:valueInt pairs as a fingerprint to find
    all types sharing the same attribute set as the given type_id.

    Returns list of dicts with typeID, typeName, groupName, metaGroupName.
    """
    sde_db = _get_sde_db()
    query = text("""
        WITH type_fingerprints AS (
            SELECT typeID,
                   GROUP_CONCAT(attributeID || ':' || valueInt, ',') as fingerprint
            FROM dgmTypeAttributes
            WHERE valueInt IS NOT NULL
            GROUP BY typeID
        )
        SELECT tf.typeID, s.typeName, s.groupName, s.metaGroupName
        FROM type_fingerprints tf
        JOIN sdetypes s ON tf.typeID = s.typeID
        WHERE tf.fingerprint = (
            SELECT GROUP_CONCAT(attributeID || ':' || valueInt, ',') as fingerprint
            FROM dgmTypeAttributes
            WHERE typeID = :type_id AND valueInt IS NOT NULL
        )
        ORDER BY s.metaGroupName, s.typeName
    """)
    with sde_db.engine.connect() as conn:
        rows = conn.execute(query, {"type_id": type_id}).fetchall()

    return [
        {
            "typeID": r[0],
            "typeName": r[1],
            "groupName": r[2],
            "metaGroupName": r[3],
        }
        for r in rows
    ]


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


def find_overlapping_group(
    type_ids: list[int],
    market_ctx: Optional["MarketContext"] = None,
) -> Optional[int]:
    """
    Check if any existing group contains any of the given type_ids.

    Returns the equiv_group_id if any type_id is already in a group,
    or None if none of them are grouped yet.
    """
    db = _get_db(market_ctx)
    # Build a parameterized IN clause
    placeholders = ", ".join(f":tid_{i}" for i in range(len(type_ids)))
    query = text(f"""
        SELECT DISTINCT equiv_group_id
        FROM module_equivalents
        WHERE type_id IN ({placeholders})
    """)
    params = {f"tid_{i}": tid for i, tid in enumerate(type_ids)}

    with db.engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()

    if rows:
        return rows[0][0]
    return None


def add_equiv_group(
    type_ids: list[int],
    market_ctx: Optional["MarketContext"] = None,
) -> int | None:
    """
    Add a new equivalence group.

    Resolves type names from SDE, inserts rows into module_equivalents.
    Returns None if any of the type_ids already belong to an existing group.

    Args:
        type_ids: List of EVE type IDs to group as equivalents
        market_ctx: Optional market context

    Returns:
        The new equiv_group_id, or None if blocked by overlap
    """
    # Guard against duplicates
    existing_gid = find_overlapping_group(type_ids, market_ctx)
    if existing_gid is not None:
        logger.warning(
            f"Type IDs overlap with existing group {existing_gid}, skipping"
        )
        return None

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

    sync_equiv_to_remote(market_ctx)
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

    sync_equiv_to_remote(market_ctx)
    return count


def sync_equiv_to_remote(market_ctx: Optional["MarketContext"] = None) -> bool:
    """
    Push local module_equivalents table to Turso remote.

    Replaces the entire remote table with local data since libsql sync()
    is pull-only (cloud → local).
    """
    db = _get_db(market_ctx)
    try:
        remote = db.remote_engine
    except (KeyError, Exception) as e:
        logger.warning(f"No remote engine for {db.alias}, skipping remote sync: {e}")
        return False

    # Ensure local table exists before reading
    ensure_equiv_table(market_ctx)

    # Read all local rows
    with db.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT equiv_group_id, type_id, type_name FROM module_equivalents"
        )).fetchall()

    # Ensure table exists on remote, then replace contents
    create_query = text("""
        CREATE TABLE IF NOT EXISTS module_equivalents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equiv_group_id INTEGER NOT NULL,
            type_id INTEGER NOT NULL,
            type_name VARCHAR(255) NOT NULL
        )
    """)
    with remote.begin() as conn:
        conn.execute(create_query)
        conn.execute(text("DELETE FROM module_equivalents"))
        if rows:
            values = ",".join(
                f"({r[0]},{r[1]},'{r[2].replace(chr(39), chr(39)+chr(39))}')"
                for r in rows
            )
            conn.execute(text(
                f"INSERT INTO module_equivalents (equiv_group_id, type_id, type_name) VALUES {values}"
            ))

    logger.info(f"Synced {len(rows)} equiv rows to remote ({db.alias})")
    return True


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
