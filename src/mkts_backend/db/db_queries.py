from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from typing import Any, Optional, TYPE_CHECKING
import pandas as pd
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import UpdateLog

logger = configure_logging(__name__)

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext


def _get_db(market_ctx: Optional["MarketContext"] = None) -> DatabaseConfig:
    """Get database config, optionally using market context."""
    if market_ctx is not None:
        return DatabaseConfig(market_context=market_ctx)
    return DatabaseConfig("wcmkt")


def _read_market_df(
    sql: str,
    params: dict[str, Any],
    market_ctx: Optional["MarketContext"] = None,
) -> pd.DataFrame:
    """Run a parameterized SELECT against the market DB and return a DataFrame."""
    db = _get_db(market_ctx)
    with db.engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params)


def get_market_history(
    type_id: int, market_ctx: Optional["MarketContext"] = None
) -> pd.DataFrame:
    return _read_market_df(
        "SELECT * FROM market_history WHERE type_id = :type_id",
        {"type_id": type_id},
        market_ctx,
    )


def get_market_orders(
    type_id: int, market_ctx: Optional["MarketContext"] = None
) -> pd.DataFrame:
    return _read_market_df(
        "SELECT * FROM market_orders WHERE type_id = :type_id",
        {"type_id": type_id},
        market_ctx,
    )


def get_market_stats(
    type_id: int, market_ctx: Optional["MarketContext"] = None
) -> pd.DataFrame:
    return _read_market_df(
        "SELECT * FROM marketstats WHERE type_id = :type_id",
        {"type_id": type_id},
        market_ctx,
    )


def get_doctrine_stats(
    type_id: int, market_ctx: Optional["MarketContext"] = None
) -> pd.DataFrame:
    return _read_market_df(
        "SELECT * FROM doctrines WHERE type_id = :type_id",
        {"type_id": type_id},
        market_ctx,
    )


def get_table_length(table: str, market_ctx: Optional["MarketContext"] = None) -> int:
    db = _get_db(market_ctx)
    with db.engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def get_watchlist_ids(market_ctx: Optional["MarketContext"] = None) -> list[int]:
    db = _get_db(market_ctx)
    with db.engine.connect() as conn:
        result = conn.execute(text("SELECT DISTINCT type_id FROM watchlist"))
        return [row[0] for row in result]


def get_fit_items(fit_id: int) -> list[int]:
    db = DatabaseConfig("fittings")
    with db.engine.connect() as conn:
        result = conn.execute(
            text("SELECT type_id FROM fittings_fittingitem WHERE fit_id = :fit_id"),
            {"fit_id": fit_id},
        )
        return [row[0] for row in result]


def get_fit_ids(doctrine_id: int) -> list[int]:
    db = DatabaseConfig("fittings")
    with db.engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT fitting_id FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id"
            ),
            {"doctrine_id": doctrine_id},
        )
        return [row[0] for row in result]


def get_update_age(
    table_name: str, market_ctx: Optional["MarketContext"] = None
) -> Optional[timedelta]:
    """Return how long ago ``table_name`` was last written, or None if unknown.

    ``updatelog`` is one row per table (``table_name`` is the primary key), so
    this reads at most one row. Returns None when the row is absent or the
    table cannot be read — callers treat None as "no cache, fetch".

    ``log_update`` binds a tz-aware UTC datetime, but SQLite's DateTime column
    stores no offset and hands it back naive. Attach UTC before subtracting;
    subtracting a naive value from an aware one raises TypeError.
    """
    db = _get_db(market_ctx)
    try:
        with db.engine.connect() as conn:
            written = conn.execute(
                select(UpdateLog.timestamp).where(UpdateLog.table_name == table_name)
            ).scalar()
    except SQLAlchemyError as exc:
        logger.warning(f"Could not read updatelog for {table_name}: {exc}")
        return None

    if written is None:
        return None

    if written.tzinfo is None:
        written = written.replace(tzinfo=timezone.utc)

    return datetime.now(timezone.utc) - written


def read_jita_prices(
    market_ctx: Optional["MarketContext"] = None,
    type_ids: Optional[list[int]] = None,
) -> dict[int, float]:
    """Return ``{type_id: sell_price}`` from the market DB's ``jita_prices``.

    ``type_ids`` narrows the read to those items; None reads the whole table.
    Rows with a NULL sell_price are dropped. Returns an empty dict if the table
    is missing or unreadable, so callers can fall back to a live fetch.

    Read-only by design. ``jita_prices`` is a wipe-and-replace table, so any
    partial write against it would delete the rows it does not carry.
    """
    if type_ids is not None and not type_ids:
        return {}

    db = _get_db(market_ctx)
    sql = "SELECT type_id, sell_price FROM jita_prices"
    params: dict[str, Any] = {}
    if type_ids is not None:
        placeholders = ", ".join(f":t{i}" for i in range(len(type_ids)))
        sql += f" WHERE type_id IN ({placeholders})"
        params = {f"t{i}": tid for i, tid in enumerate(type_ids)}

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
    except SQLAlchemyError as exc:
        logger.warning(f"Could not read jita_prices from {db.alias}: {exc}")
        return {}

    return {
        int(type_id): float(sell_price)
        for type_id, sell_price in rows
        if sell_price is not None
    }
