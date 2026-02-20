"""
Local SQLite cache for ESI character asset data.

Stores aggregated {type_id: quantity} per character to avoid redundant
ESI fetches within the cache window. ESI's asset endpoint has a ~1 hour
cache, so we use a matching TTL.
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import text

from mkts_backend.config.config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)

CACHE_TTL_SECONDS = 3600  # 1 hour, matches ESI cache window


def _ensure_table(engine) -> None:
    """Create the cache table if it doesn't exist."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS character_asset_cache (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                char_id   INTEGER NOT NULL,
                type_id   INTEGER NOT NULL,
                quantity  INTEGER NOT NULL,
                cached_at TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_asset_cache_char_id
            ON character_asset_cache (char_id)
        """))
        conn.commit()


def _get_engine():
    """Get the primary market database engine."""
    db = DatabaseConfig("primary")
    return db.engine


def read_cache(char_id: int) -> Optional[Dict[int, int]]:
    """
    Read cached assets for a character if fresh.

    Args:
        char_id: ESI character ID

    Returns:
        Dict mapping type_id to quantity if cache is fresh, None otherwise
    """
    engine = _get_engine()
    _ensure_table(engine)

    with engine.connect() as conn:
        # Check the most recent cached_at for this character
        row = conn.execute(
            text("""
                SELECT cached_at FROM character_asset_cache
                WHERE char_id = :char_id
                LIMIT 1
            """),
            {"char_id": char_id},
        ).fetchone()

        if not row:
            return None

        cached_at = datetime.fromisoformat(row[0])
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()

        if age > CACHE_TTL_SECONDS:
            logger.debug(f"Cache expired for char_id={char_id} (age={age:.0f}s)")
            return None

        # Cache is fresh — read all rows
        rows = conn.execute(
            text("""
                SELECT type_id, quantity FROM character_asset_cache
                WHERE char_id = :char_id
            """),
            {"char_id": char_id},
        ).fetchall()

        assets = {r[0]: r[1] for r in rows}
        logger.debug(
            f"Cache hit for char_id={char_id}: {len(assets)} types, age={age:.0f}s"
        )
        return assets


def write_cache(char_id: int, assets: Dict[int, int]) -> None:
    """
    Write aggregated assets to the cache, replacing any existing data.

    Args:
        char_id: ESI character ID
        assets: Dict mapping type_id to total packaged quantity
    """
    engine = _get_engine()
    _ensure_table(engine)

    now = datetime.now(timezone.utc).isoformat()

    with engine.connect() as conn:
        # Atomic replace: delete old rows, insert new
        conn.execute(
            text("DELETE FROM character_asset_cache WHERE char_id = :char_id"),
            {"char_id": char_id},
        )

        for type_id, quantity in assets.items():
            conn.execute(
                text("""
                    INSERT INTO character_asset_cache
                        (char_id, type_id, quantity, cached_at)
                    VALUES (:char_id, :type_id, :quantity, :cached_at)
                """),
                {
                    "char_id": char_id,
                    "type_id": type_id,
                    "quantity": quantity,
                    "cached_at": now,
                },
            )
            logger.debug(f"write_cache: char_id: {char_id} | {type_id} | {quantity}")
        conn.commit()

    logger.info(f"Cached {len(assets)} asset types for char_id={char_id}")


def invalidate_cache(char_id: Optional[int] = None) -> None:
    """
    Clear cached asset data.

    Args:
        char_id: If provided, clear only this character's cache.
                 If None, clear all cached assets.
    """
    engine = _get_engine()
    _ensure_table(engine)

    with engine.connect() as conn:
        if char_id is not None:
            conn.execute(
                text("DELETE FROM character_asset_cache WHERE char_id = :char_id"),
                {"char_id": char_id},
            )
        else:
            conn.execute(text("DELETE FROM character_asset_cache"))
        conn.commit()

    target = f"char_id={char_id}" if char_id else "all characters"
    logger.info(f"Invalidated asset cache for {target}")
