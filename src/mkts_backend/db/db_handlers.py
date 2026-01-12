import pandas as pd
from sqlalchemy import select, insert, func, or_, delete
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from dotenv import load_dotenv
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING
import time
import numpy as np
import os
import json
from mkts_backend.utils.utils import (
    add_timestamp,
    add_autoincrement,
    validate_columns,
    convert_datetime_columns,
    get_type_names_from_df,
)
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import Base, MarketHistory, MarketOrders, UpdateLog
from mkts_backend.config.config import DatabaseConfig
from mkts_backend.db.db_queries import get_table_length, get_remote_status

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

load_dotenv()
logger = configure_logging(__name__)

# Lazy initialization - these will be initialized on first use or via market_ctx
_db = None
_sde_db = None

def _get_db(market_ctx: Optional["MarketContext"] = None) -> DatabaseConfig:
    """Get database config, optionally using market context."""
    if market_ctx is not None:
        return DatabaseConfig(market_context=market_ctx)
    global _db
    if _db is None:
        _db = DatabaseConfig("wcmkt")
    return _db

def _get_sde_db() -> DatabaseConfig:
    """Get SDE database config (shared across all markets)."""
    global _sde_db
    if _sde_db is None:
        _sde_db = DatabaseConfig("sde")
    return _sde_db

def handle_nulls(df: pd.DataFrame, tabname: str) -> pd.DataFrame:
    # CRITICAL SAFETY CHECK: Clean all NaN/inf values before conversion to dict
    # This is a final safety net to prevent SQLAlchemy errors

    # Check for NaN values
    if df.isnull().any().any():
        logger.warning(f"NaN values detected in {tabname} before upsert. Cleaning...")
        logger.warning(f"NaN columns: {df.columns[df.isnull().any()].tolist()}")
        logger.info(df.dtypes)

        # Replace inf with NaN, then fill all NaN with appropriate defaults
        df = df.replace([np.inf, -np.inf], np.nan)

        # Fill NaN in numeric columns with 0
        numeric_cols = df.select_dtypes(include=['number']).columns
        df[numeric_cols] = df[numeric_cols].fillna(0)

        # Fill NaN in string columns with empty string
        string_cols = df.select_dtypes(include=['object']).columns
        df[string_cols] = df[string_cols].fillna('')


        # Fill NaN in datetime columns with current timestamp
        # SQLite DateTime type requires Python datetime objects, not None or strings
        datetime_cols = df.select_dtypes(include=['datetime', 'datetime64']).columns.tolist()

        for col in datetime_cols:
            null_mask = df[col].isna()
            if null_mask.any():
                null_count = null_mask.sum()
                logger.warning(f"Null {col} found in {tabname}: {null_count} rows")
                
                # Log details of rows with null timestamps (for debugging)
                if col == 'timestamp' and null_count <= 10:
                    null_rows = df[null_mask]
                    null_info = []
                    for idx, row in null_rows.iterrows():
                        info = {"type_id": row.get("type_id"), "type_name": row.get("type_name")}
                        if "fit_id" in row:
                            info["fit_id"] = row.get("fit_id")
                        null_info.append(info)
                        logger.info(f"Null timestamp row: {info}")
                    
                    os.makedirs("data", exist_ok=True)
                    with open("data/null_timestamps.json", "w") as f:
                        json.dump(null_info, f, indent=4)
                
                # Fill NaT with current datetime (SQLite requires datetime objects, not None)
                # Use timezone-naive datetime to match pandas datetime64[ns] dtype
                current_time = pd.Timestamp.now('UTC').tz_localize(None)
                df.loc[null_mask, col] = current_time
                logger.info(f"Filled {null_count} null {col} values with current timestamp")

        # Final check
        if df.isnull().any().any():
            logger.error(f"NaN values STILL present after cleaning: {df.isnull().sum()}")
            remaining_nan_cols = df.columns[df.isnull().any()].tolist()
            logger.error(f"Remaining NaN in columns: {remaining_nan_cols}")
            # Last resort: fill remaining NaN values
            for col in remaining_nan_cols:
                if col in datetime_cols:
                    # Fill datetime columns with current timestamp (timezone-naive)
                    current_time = pd.Timestamp.now('UTC').tz_localize(None)
                    df.loc[df[col].isna(), col] = current_time
                else:
                    df[col] = df[col].fillna(0)
    return df

def upsert_database(
    table: Base,
    df: pd.DataFrame,
    market_ctx: Optional["MarketContext"] = None
) -> bool:
    """Upsert data into the database.

    Args:
        table: The table model to update
        df: The DataFrame containing the data to update
        market_ctx: Optional MarketContext for market-specific database

    Returns:
        True if successful, False otherwise
    """
    WIPE_REPLACE_TABLES = ["marketstats", "doctrines"]
    tabname = table.__tablename__
    is_wipe_replace = tabname in WIPE_REPLACE_TABLES
    logger.info(f"Processing table: {tabname}, wipe_replace: {is_wipe_replace}")
    logger.info(f"Upserting {len(df)} rows into {table.__tablename__}")

    df = handle_nulls(df, tabname)

    if df is not None and len(df) > 0:
        data = df.to_dict(orient="records")
    else:
        logger.error(f"No data to upsert into {tabname}")
        return False

    column_count = len(df.columns)
    chunk_size = 1000

    logger.info(
        f"Table {table.__tablename__} has {column_count} columns, using chunk size {chunk_size}"
    )

    db = _get_db(market_ctx)
    logger.info(f"updating: {db.alias} ({db.path})")

    remote_engine = db.remote_engine
    session = Session(bind=remote_engine)

    t = table.__table__
    pk_cols = list(t.primary_key.columns)
    # Handle both single and composite primary keys
    if len(pk_cols) == 1:
        pk_col = pk_cols[0]
    elif len(pk_cols) > 1:
        pk_col = pk_cols  # Use all primary key columns for composite keys
    else:
        raise ValueError("Table must have at least one primary key column.")

    try:
        logger.info(f"Updating {len(data)} rows into {table.__tablename__}")
        with session.begin():

            if is_wipe_replace:
                logger.info(
                    f"Wiping and replacing {len(data)} rows into {table.__tablename__}"
                )
                session.query(table).delete()
                logger.info(f"Wiped data from {table.__tablename__}")

                for idx in range(0, len(data), chunk_size):
                    chunk = data[idx : idx + chunk_size]
                    stmt = insert(t).values(chunk)
                    session.execute(stmt)
                    logger.info(
                        f"  • chunk {idx // chunk_size + 1}, {len(chunk)} rows"
                    )

                count = session.execute(select(func.count()).select_from(t)).scalar_one()
                if count != len(data):
                    raise RuntimeError(
                        f"Row count mismatch: expected {len(data)}, got {count}"
                    )
            else:
                # Delete records not present in incoming data (stale records)
                deleted_count = 0
                if isinstance(pk_col, list):
                    # Composite primary key - build list of tuples
                    incoming_pks = [tuple(row[col.name] for col in pk_col) for row in data]
                    # For composite keys, build a condition for each tuple
                    delete_conditions = []
                    for pk_tuple in incoming_pks:
                        tuple_conditions = [pk_cols[i] == pk_tuple[i] for i in range(len(pk_cols))]
                        delete_conditions.append(tuple_conditions)
                    # Delete where NOT IN incoming PKs (complex for composite, skip for now)
                    logger.warning(f"Stale record deletion not yet implemented for composite primary keys in {tabname}")
                else:
                    # Single primary key - delete records not in incoming data
                    incoming_pks = [row[pk_col.name] for row in data]

                    # Fetch existing PKs and delete stale ones in small chunks to avoid
                    # SQLite/LibSQL variable count limits (Hrana throws SQL_INPUT_ERROR)
                    existing_pks = session.execute(select(pk_col)).scalars().all()
                    stale_pks = list(set(existing_pks) - set(incoming_pks))
                    delete_chunk_size = 500  # keep well under libsql/sqlite var limits

                    if stale_pks:
                        for del_idx in range(0, len(stale_pks), delete_chunk_size):
                            del_chunk = stale_pks[del_idx : del_idx + delete_chunk_size]
                            delete_stmt = delete(t).where(pk_col.in_(del_chunk))
                            delete_result = session.execute(delete_stmt)
                            deleted_count += delete_result.rowcount

                        chunk_batches = (len(stale_pks) + delete_chunk_size - 1) // delete_chunk_size
                        logger.info(
                            f"Deleted {deleted_count} stale records from {tabname} "
                            f"in {chunk_batches} chunks"
                        )

                non_pk_cols = [c for c in t.columns if c not in pk_cols]
                # Exclude timestamp columns from change detection to avoid unnecessary updates
                data_cols = [c for c in non_pk_cols if c.name not in ['timestamp', 'last_update', 'created_at', 'updated_at']]

                total_updated = 0
                total_skipped = 0
                total_inserted = 0

                for idx in range(0, len(data), chunk_size):
                    chunk = data[idx : idx + chunk_size]
                    base = sqlite_insert(t).values(chunk)
                    excluded = base.excluded
                    set_mapping = {c.name: excluded[c.name] for c in non_pk_cols}

                    # Only check for changes in data columns (exclude timestamp fields)
                    if data_cols:
                        changed_pred = or_(*[c.is_distinct_from(excluded[c.name]) for c in data_cols])
                    else:
                        # If no data columns to check, always update (shouldn't happen in practice)
                        changed_pred = True

                    # Handle both single and composite primary keys for conflict resolution
                    if isinstance(pk_col, list):
                        # Composite primary key
                        stmt = base.on_conflict_do_update(
                            index_elements=pk_col, set_=set_mapping, where=changed_pred
                        )
                    else:
                        # Single primary key
                        stmt = base.on_conflict_do_update(
                            index_elements=[pk_col], set_=set_mapping, where=changed_pred
                        )

                    result = session.execute(stmt)
                    # Count affected rows (updated + inserted)
                    chunk_affected = result.rowcount
                    chunk_updated = min(chunk_affected, len(chunk))  # Approximate updates
                    chunk_skipped = len(chunk) - chunk_updated

                    total_updated += chunk_updated
                    total_skipped += chunk_skipped

                    print(f"\r upserting {table.__tablename__}. {round(100*(idx/len(data)),3)}%", end="", flush=True)

                # Calculate insertions: total incoming minus those that already existed
                count_after = session.execute(select(func.count()).select_from(t)).scalar_one()
                count_before = count_after - (deleted_count if not isinstance(pk_col, list) else 0)
                total_inserted = max(0, len(data) - (count_before - (deleted_count if not isinstance(pk_col, list) else 0)))

                if deleted_count > 0:
                    logger.info(f"Upsert summary for {table.__tablename__}: {deleted_count} rows deleted, {total_inserted} rows inserted, {total_updated} rows updated, {total_skipped} rows skipped (no data changes)")
                else:
                    logger.info(f"Upsert summary for {table.__tablename__}: {total_inserted} rows inserted, {total_updated} rows updated, {total_skipped} rows skipped (no data changes)")
            # Calculate distinct incoming records based on primary key type
            if isinstance(pk_col, list):
                # Composite primary key - create tuples of all pk column values
                distinct_incoming = len({tuple(row[col.name] for col in pk_col) for row in data})
                pk_desc = f"composite key ({', '.join(col.name for col in pk_col)})"
            else:
                # Single primary key
                distinct_incoming = len({row[pk_col.name] for row in data})
                pk_desc = f"{pk_col.name}"

            logger.info(f"distinct incoming: {distinct_incoming}")
            count = session.execute(select(func.count()).select_from(t)).scalar_one()
            logger.info(f"count: {count}")
            if count < distinct_incoming:
                logger.error(
                    f"Row count too low: expected at least {distinct_incoming} unique {pk_desc}s, got {count}"
                )
                raise RuntimeError(
                    f"Row count too low: expected at least {distinct_incoming} unique {pk_desc}s, got {count}"
                )

        logger.info(f"Upsert complete: {count} rows present in {table.__tablename__}")

    except SQLAlchemyError as e:
        logger.error("Failed upserting remote DB", exc_info=e)
        raise e
    finally:
        session.close()
        remote_engine.dispose()
    return True

def update_history(
    history_results: list[dict],
    market_ctx: Optional["MarketContext"] = None
):
    """Prepares data for update to the market_history table, then calls upsert_database to update the table.

    Args:
        history_results: List of dicts, each containing history data from the ESI
        market_ctx: Optional MarketContext for market-specific database

    Returns:
        True if successful, False otherwise
    """

    valid_history_columns = MarketHistory.__table__.columns.keys()

    flattened_history = []
    for result in history_results:
        # Handle new format: {"type_id": type_id, "data": [...]}
        if isinstance(result, dict) and "type_id" in result and "data" in result:
            type_id = result["type_id"]
            type_history = result["data"]
        else:
            # Fallback for old format - this shouldn't happen anymore
            logger.warning("Received unexpected history result format")
            continue

        if isinstance(type_history, list):
            for record in type_history:
                record['type_id'] = str(type_id)
                flattened_history.append(record)
        else:
            type_history['type_id'] = str(type_id)
            flattened_history.append(type_history)

    if not flattened_history:
        logger.error("No history data to process")
        return False

    history_df = pd.DataFrame.from_records(flattened_history)
    logger.info(f"Available columns: {list(history_df.columns)}")
    logger.info(f"Expected columns: {list(valid_history_columns)}")

    # Get type names efficiently with bulk lookup
    from sqlalchemy import text

    unique_type_ids = history_df['type_id'].unique()

    sde_db = _get_sde_db()
    engine = sde_db.engine
    with engine.connect() as conn:
        placeholders = ','.join([':type_id_' + str(i) for i in range(len(unique_type_ids))])
        params = {'type_id_' + str(i): int(unique_type_ids[i]) for i in range(len(unique_type_ids))}

        stmt = text(f"SELECT typeID, typeName FROM inv_info WHERE typeID IN ({placeholders})")
        res = conn.execute(stmt, params)
        type_name_map = dict(res.fetchall())
    engine.dispose()

    history_df['type_name'] = history_df['type_id'].map(lambda x: type_name_map.get(int(x), f'Unknown_{x}'))

    missing_columns = set(valid_history_columns) - set(history_df.columns)
    if missing_columns:
        logger.warning(f"Missing required columns: {missing_columns}")
        for col in missing_columns:
            if col in ('timestamp',):
                continue
            else:
                history_df[col] = 0

    history_df = add_timestamp(history_df)
    history_df = validate_columns(history_df, valid_history_columns)
    history_df = convert_datetime_columns(history_df, ['date'])
    history_df.infer_objects()
    history_df.fillna(0)

    try:
        upsert_database(MarketHistory, history_df, market_ctx=market_ctx)
    except Exception as e:
        logger.error(f"history data update failed: {e}")
        return False

    status = get_remote_status(market_ctx=market_ctx)['market_history']
    if status > 0:
        logger.info(f"History updated:{get_table_length('market_history', market_ctx=market_ctx)} items")
        print(f"History updated:{get_table_length('market_history', market_ctx=market_ctx)} items")
    else:
        logger.error("Failed to update market history")
        return False
    return True

def update_market_orders(
    orders: list[dict],
    market_ctx: Optional["MarketContext"] = None
) -> bool:
    """Prepares data for update to the marketorders table, then calls upsert_database to update the table.

    Args:
        orders: List of dicts, each containing order data from the ESI
        market_ctx: Optional MarketContext for market-specific database

    Returns:
        True if successful, False otherwise
    """

    orders_df = pd.DataFrame.from_records(orders)
    type_names = get_type_names_from_df(orders_df)
    orders_df = orders_df.merge(type_names, on="type_id", how="left")

    orders_df = convert_datetime_columns(orders_df, ['issued'])
    orders_df = add_timestamp(orders_df)
    orders_df = orders_df.infer_objects()
    orders_df = orders_df.fillna(0)
    orders_df = add_autoincrement(orders_df)

    valid_columns = MarketOrders.__table__.columns.keys()
    orders_df = validate_columns(orders_df, valid_columns)

    logger.info(f"Orders fetched:{len(orders_df)} items")
    status = upsert_database(MarketOrders, orders_df, market_ctx=market_ctx)
    if status:
        logger.info(f"Orders updated:{get_table_length('marketorders', market_ctx=market_ctx)} items")
        return True
    else:
        logger.error("Failed to update market orders")
        return False

def log_update(
    table_name: str,
    remote: bool = False,
    market_ctx: Optional["MarketContext"] = None
):
    """Log a table update timestamp.

    Args:
        table_name: Name of the table that was updated
        remote: Whether to use remote database
        market_ctx: Optional MarketContext for market-specific database
    """
    db = _get_db(market_ctx)
    engine = db.remote_engine if remote else db.engine

    session = Session(bind=engine)
    with session.begin():
        session.execute(delete(UpdateLog).where(UpdateLog.table_name == table_name))
        session.add(UpdateLog(table_name=table_name, timestamp=datetime.now(timezone.utc)))
        session.commit()
        session.close()

    engine.dispose()
    return True

if __name__ == "__main__":
    pass
