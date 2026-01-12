from sqlalchemy import text
from typing import Optional, TYPE_CHECKING
import pandas as pd
from mkts_backend.config.config import DatabaseConfig

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext


def _get_db(market_ctx: Optional["MarketContext"] = None) -> DatabaseConfig:
    """Get database config, optionally using market context."""
    if market_ctx is not None:
        return DatabaseConfig(market_context=market_ctx)
    return DatabaseConfig("wcmkt")


def get_market_history(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        stmt = "SELECT * FROM market_history WHERE type_id = ?"
        result = conn.execute(stmt, (type_id,))
        headers = [col[0] for col in result.description]
    conn.close()
    return pd.DataFrame(result.fetchall(), columns=headers)

def get_market_orders(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        stmt = "SELECT * FROM market_orders WHERE type_id = ?"
        result = conn.execute(stmt, (type_id,))
        headers = [col[0] for col in result.description]
    conn.close()
    return pd.DataFrame(result.fetchall(), columns=headers)

def get_market_stats(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM marketstats WHERE type_id = :type_id")
        df = pd.read_sql_query(stmt, conn, params={"type_id": type_id})
    conn.close()
    return df

def get_remote_status(market_ctx: Optional["MarketContext"] = None):
    db = _get_db(market_ctx)
    status_dict = db.get_status()
    return status_dict

def get_doctrine_stats(type_id: int, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM doctrines WHERE type_id = :type_id")
        df = pd.read_sql_query(stmt, conn, params={"type_id": type_id})
    conn.close()
    return df

def get_table_length(table: str, market_ctx: Optional["MarketContext"] = None) -> int:
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        stmt = text(f"SELECT COUNT(*) FROM {table}")
        result = conn.execute(stmt)
        return result.fetchone()[0]

def get_watchlist_ids(market_ctx: Optional["MarketContext"] = None):
    stmt = text("SELECT DISTINCT type_id FROM watchlist")
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        result = conn.execute(stmt)
        watchlist_ids = [row[0] for row in result]
    conn.close()
    engine.dispose()
    return watchlist_ids

def get_fit_items(fit_id: int) -> list[int]:
    stmt = text("SELECT type_id FROM fittings_fittingitem WHERE fit_id = :fit_id")
    db = DatabaseConfig("fittings")
    engine = db.engine
    with engine.connect() as conn:
        result = conn.execute(stmt, {"fit_id": fit_id})
        fit_items = [row[0] for row in result]
    conn.close()
    engine.dispose()
    return fit_items

def get_fit_ids(doctrine_id: int):
    stmt = text("SELECT fitting_id FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id")
    db = DatabaseConfig("fittings")
    engine = db.engine
    with engine.connect() as conn:
        result = conn.execute(stmt, {"doctrine_id": doctrine_id})
        fit_ids = [row[0] for row in result]
    conn.close()
    engine.dispose()
    return fit_ids

if __name__ == "__main__":
    pass
