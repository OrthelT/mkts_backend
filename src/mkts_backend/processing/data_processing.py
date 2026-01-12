import pandas as pd
from typing import Optional, TYPE_CHECKING
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.utils.db_utils import fix_null_doctrine_stats_timestamps
from mkts_backend.db.models import MarketStats, MarketHistory
from mkts_backend.config.config import DatabaseConfig
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from mkts_backend.db.db_queries import get_remote_status

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

logger = configure_logging(__name__)

# Lazy database initialization
_wcmkt_db = None

def _get_db(market_ctx: Optional["MarketContext"] = None) -> DatabaseConfig:
    """Get database config, optionally using market context."""
    if market_ctx is not None:
        return DatabaseConfig(market_context=market_ctx)
    global _wcmkt_db
    if _wcmkt_db is None:
        _wcmkt_db = DatabaseConfig("wcmkt")
    return _wcmkt_db


def calculate_5_percentile_price(market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    query = """
    SELECT
    type_id,
    price
    FROM marketorders
    WHERE is_buy_order = 0
    """
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        df = pd.read_sql_query(query, conn)
    conn.close()
    logger.info(f"5 percentile price queried: {df.shape[0]} items")
    engine.dispose()
    df = df.groupby("type_id")["price"].quantile(0.05).reset_index()
    df.price = df.price.apply(lambda x: round(x, 2))
    df.columns = ["type_id", "5_perc_price"]
    return df

def calculate_market_stats(market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    query = """
    SELECT
    w.type_id,
    w.type_name,
    w.group_name,
    w.category_name,
    w.category_id,
    w.group_id,
    o.min_price,
    o.total_volume_remain,
    h.avg_price,
    h.avg_volume,
    ROUND(CASE
    WHEN h.avg_volume > 0 THEN o.total_volume_remain / h.avg_volume
    WHEN h.avg_volume IS NULL OR h.avg_volume = 0 THEN 30
    ELSE 0
    END, 2) as days_remaining

    FROM watchlist w

    LEFT JOIN (
    SELECT
        type_id,
        MIN(price) as min_price,
        SUM(volume_remain) as total_volume_remain
    FROM marketorders
        WHERE is_buy_order = 0
        GROUP BY type_id
    ) AS o
    ON w.type_id = o.type_id
    LEFT JOIN (
    SELECT
        type_id,
        AVG(average) as avg_price,
        AVG(volume) as avg_volume
    FROM market_history
    WHERE date >= DATE('now', '-30 day') AND average > 0 AND volume > 0
    GROUP BY type_id
    ) AS h ON w.type_id = h.type_id
    """
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        df = pd.read_sql_query(query, conn)
        logger.info(f"Market stats queried: {df.shape[0]} items")
    engine.dispose()

    logger.info("Calculating 5 percentile price")
    df2 = calculate_5_percentile_price(market_ctx)
    logger.info("Merging 5 percentile price with market stats")
    df = df.merge(df2, on="type_id", how="left")
    df = df.rename(columns={"5_perc_price": "price"})


    df = fill_nulls_from_history(df, market_ctx)


    df["last_update"] = pd.Timestamp.now(tz="UTC")

    # Round numeric columns
    df["days_remaining"] = df["days_remaining"].apply(lambda x: round(x, 1))
    df["avg_price"] = df["avg_price"].apply(lambda x: round(x, 2) if pd.notnull(x) and x > 0 else 0)
    df["avg_volume"] = df["avg_volume"].apply(lambda x: round(x, 1) if pd.notnull(x) and x > 0 else 0)
    df["total_volume_remain"] = df["total_volume_remain"].fillna(0).astype(int)
    df["days_remaining"] = df["days_remaining"].fillna(0)

    # Ensure we have all required database columns
    db_cols = MarketStats.__table__.columns.keys()
    df = df[db_cols]

    logger.info(f"Market stats calculated: {df.shape[0]} items")
    return df

def fill_nulls_from_history(stats: pd.DataFrame, market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    """
    Fill nulls from market history data.
    """
    logger.info("Filling nulls from history")

    # Check if there are any null values to fill
    if stats.isnull().sum().sum() == 0:
        logger.info("No null values found, returning original stats")
        return stats
    else:
        logger.info(f"stats has nulls: {stats.isnull().sum().sum()}")

    stats['days_remaining'] = stats['days_remaining'].fillna(0)
    stats['total_volume_remain'] = stats['total_volume_remain'].fillna(0)

    logger.info("Getting nulls")
    nulls = stats[stats.isnull().any(axis=1)]
    nulls_type_ids = nulls.type_id.unique().tolist()
    logger.info(f"nulls: {len(nulls)} items")
    logger.info(f"nulls_type_ids: {nulls_type_ids}")

    if not nulls_type_ids:
        logger.info("No type_ids with nulls found")
        return stats

    logger.info("Querying history")
    db = _get_db(market_ctx)
    engine = db.engine
    session = Session(engine)
    try:
        with session.begin():
            stmt = select(
                MarketHistory.type_id,
                func.avg(MarketHistory.average).label("avg_price"),
                func.avg(MarketHistory.volume).label("avg_volume")
            ).where(
                MarketHistory.type_id.in_(nulls_type_ids)
            ).where(
                MarketHistory.average > 0
            ).where(
                MarketHistory.volume > 0
            ).group_by(MarketHistory.type_id)

            res = session.execute(stmt)
            history_data = res.fetchall()
            logger.info(f"Found {len(history_data)} history records")

            if history_data:

                # Convert to DataFrame
                history_df = pd.DataFrame(history_data, columns=res.keys())
                history_df = history_df.set_index('type_id')
                history_df.index = history_df.index.astype(int)
                logger.info(f"history_df shape: {history_df.shape}")

                # Fill null values using merge for safer indexing
                for type_id in nulls_type_ids:
                    if type_id in history_df.index:
                        # Fill price-related nulls with historical average price
                        try:
                            if pd.isnull(stats.loc[stats.type_id == type_id, 'avg_price']).any():
                                stats.loc[stats.type_id == type_id, 'avg_price'] = history_df.loc[type_id, 'avg_price']
                            if pd.isnull(stats.loc[stats.type_id == type_id, 'min_price']).any():
                                stats.loc[stats.type_id == type_id, 'min_price'] = history_df.loc[type_id, 'avg_price']
                            if pd.isnull(stats.loc[stats.type_id == type_id, 'price']).any():
                                stats.loc[stats.type_id == type_id, 'price'] = history_df.loc[type_id, 'avg_price']
                        except Exception as e:
                            logger.error(f"Error filling nulls for type_id {type_id}: {e}")
                        # Fill volume-related nulls
                        try:
                            if pd.isnull(stats.loc[stats.type_id == type_id, 'avg_volume']).any():
                                stats.loc[stats.type_id == type_id, 'avg_volume'] = history_df.loc[type_id, 'avg_volume']
                        except Exception as e:
                            logger.error(f"Error filling nulls for type_id {type_id}: {e}")

                    else:
                        logger.info(f"No history data found for type_id {type_id}")
            else:
                logger.info("No history data found for null type_ids")

    except Exception as e:
        logger.error(f"Error filling nulls from history: {e}")
    finally:
        session.close()
        engine.dispose()
    if stats.isnull().sum().sum() > 0:
        stats = stats.fillna(0)

    if stats.isnull().sum().sum() == 0:
        logger.info("No nulls found after filling")
    else:
        logger.error(f"stats has nulls after filling: {stats.isnull().sum().sum()}")
    return stats

def calculate_doctrine_stats(market_ctx: Optional["MarketContext"] = None) -> pd.DataFrame:
    doctrine_query = """
    SELECT
    *
    FROM doctrines
    """
    stats_query = """
    SELECT
    *
    FROM marketstats
    """
    db = _get_db(market_ctx)
    engine = db.engine
    with engine.connect() as conn:
        doctrine_stats = pd.read_sql_query(doctrine_query, conn)
        market_stats = pd.read_sql_query(stats_query, conn)
    doctrine_stats = doctrine_stats.drop(columns=[
        "hulls", "fits_on_mkt", "total_stock", "avg_vol", "days", "timestamp"
    ])
    doctrine_stats["hulls"] = doctrine_stats["ship_id"].map(
        market_stats.set_index("type_id")["total_volume_remain"]
    )
    doctrine_stats["total_stock"] = doctrine_stats["type_id"].map(
        market_stats.set_index("type_id")["total_volume_remain"]
    )
    doctrine_stats["price"] = doctrine_stats["type_id"].map(
        market_stats.set_index("type_id")["price"]
    )
    doctrine_stats["avg_vol"] = doctrine_stats["type_id"].map(
        market_stats.set_index("type_id")["avg_volume"]
    )
    doctrine_stats["days"] = doctrine_stats["type_id"].map(
        market_stats.set_index("type_id")["days_remaining"]
    )
    doctrine_stats["timestamp"] = doctrine_stats["type_id"].map(
        market_stats.set_index("type_id")["last_update"]
    )
    # Calculate fits_on_mkt with safe division
    doctrine_stats["fits_on_mkt"] = doctrine_stats.apply(
        lambda row: round(row["total_stock"] / row["fit_qty"], 1) if row["fit_qty"] > 0 else 0,
        axis=1
    )

    doctrine_stats = doctrine_stats.infer_objects()

    # Aggressive NaN and inf cleaning for all numeric columns
    numeric_cols = doctrine_stats.select_dtypes(include=['number']).columns.tolist()
    for col in numeric_cols:
        # Replace inf first, then NaN
        doctrine_stats[col] = doctrine_stats[col].replace([float('inf'), float('-inf')], float('nan'))
        doctrine_stats[col] = doctrine_stats[col].fillna(0)

    # Convert ALL integer columns to int with explicit type safety
    # Include both nullable and non-nullable Integer columns from the model
    int_cols = ['id', 'fit_id', 'ship_id', 'hulls', 'type_id', 'fit_qty',
                'total_stock', 'group_id', 'category_id']
    for col in int_cols:
        if col in doctrine_stats.columns:
            # Ensure clean conversion: coerce any non-numeric, replace NaN, convert to int
            doctrine_stats[col] = pd.to_numeric(doctrine_stats[col], errors='coerce').fillna(0).astype(int)

    # Final safety check: ensure no NaN values remain in ANY column
    if doctrine_stats.isnull().any().any():
        logger.warning(f"WARNING: NaN values still present after cleaning: {doctrine_stats.isnull().sum()}")
        # Replace any remaining NaN with appropriate defaults
        doctrine_stats = doctrine_stats.fillna({'ship_name': '', 'type_name': '',
                                                  'group_name': '', 'category_name': ''})
        # Fill any remaining numeric NaN with 0
        doctrine_stats = doctrine_stats.fillna(0)

        if doctrine_stats.timestamp.isnull().any():
            doctrine_stats = fix_null_doctrine_stats_timestamps(doctrine_stats, pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"))

    doctrine_stats = doctrine_stats.reset_index(drop=True)
    final_check = doctrine_stats.isnull().any().any()

    if final_check:
        logger.error("WARNING: NaN values still present after cleaning:")
        logger.error(f"NaN columns: {doctrine_stats.columns[doctrine_stats.isnull().any()].tolist()}")
        return doctrine_stats

    return doctrine_stats

if __name__ == "__main__":
    pass