from sqlalchemy import text
from typing import Any, Optional, TYPE_CHECKING
import pandas as pd
from mkts_backend.config.db_config import DatabaseConfig

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


def get_market_history(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    return _read_market_df(
        "SELECT * FROM market_history WHERE type_id = :type_id",
        {"type_id": type_id},
        market_ctx,
    )


def get_market_orders(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    return _read_market_df(
        "SELECT * FROM market_orders WHERE type_id = :type_id",
        {"type_id": type_id},
        market_ctx,
    )


def get_market_stats(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    return _read_market_df(
        "SELECT * FROM marketstats WHERE type_id = :type_id",
        {"type_id": type_id},
        market_ctx,
    )


def get_remote_status(market_ctx: Optional["MarketContext"] = None):
    return _get_db(market_ctx).get_status()


def get_doctrine_stats(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
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
            text("SELECT fitting_id FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        )
        return [row[0] for row in result]
