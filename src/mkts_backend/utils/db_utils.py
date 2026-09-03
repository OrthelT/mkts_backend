import pandas as pd
from sqlalchemy import text, select, bindparam
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import Watchlist, UpdateLog
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session


logger = configure_logging(__name__)

sde_db = DatabaseConfig("sde")
wcmkt_db = DatabaseConfig("wcmkt")

def add_missing_items_to_watchlist(missing_items: list[int], remote: bool = False, db_alias: str = "wcmkt") -> bool:
    """
    Add missing items to the watchlist by fetching type information from SDE database.

    Args:
        missing_items: List of type IDs to add to watchlist
        remote: Whether to use remote database (default: False for local)

    Returns:
        True if the watchlist is left in the desired state (items inserted, or
        already present), False on any failure. Callers that need a push must
        do it themselves at the command boundary — this function only writes
        through the local engine.
    """
    if not missing_items:
        logger.warning("No items provided to add to watchlist")
        return False

    logger.info(f"Adding {len(missing_items)} items to watchlist: {missing_items}")

    # Get type information from SDE database
    df = get_type_info(missing_items, remote=remote)

    if df.empty:
        logger.error("No type information found for provided type IDs")
        return False

    # Get current watchlist to check for duplicates
    db = DatabaseConfig(db_alias)
    logger.info(f"Database config: {db.alias}")
    logger.info(f"Remote engine: {remote}")

    engine = db.remote_engine if remote else db.engine

    # Read watchlist from the correct database (local or remote)
    with engine.connect() as conn:
        watchlist = pd.read_sql_table("watchlist", conn)
    logger.info(f"Loaded {len(watchlist)} items from {'remote' if remote else 'local'} watchlist")

    # Filter out items that already exist in watchlist
    existing_type_ids = set(watchlist['type_id'].tolist()) if not watchlist.empty else set()
    new_items = df[~df['type_id'].isin(existing_type_ids)]

    if new_items.empty:
        logger.info("All provided items already exist in watchlist")
        return True

    # Prepare data for insertion
    inv_cols = ['type_id', 'type_name', 'group_id', 'group_name', 'category_id', 'category_name']
    new_items = new_items[inv_cols]

    # Save updated watchlist to CSV for backup
    updated_watchlist = pd.concat([watchlist, new_items], ignore_index=True)
    updated_watchlist.to_csv("data/watchlist_updated.csv", index=False)
    logger.info(f"Saved updated watchlist to data/watchlist_updated.csv")

    # Insert new items into local database (not remote - we don't want to affect production watchlist)
    try:
        db = DatabaseConfig(db_alias)
        engine = db.remote_engine if remote else db.engine

        with engine.connect() as conn:
            for _, row in new_items.iterrows():
                stmt = sqlite_insert(Watchlist).values(
                    type_id=int(row['type_id']),
                    type_name=row['type_name'],
                    group_id=int(row['group_id']),
                    group_name=row['group_name'],
                    category_id=int(row['category_id']),
                    category_name=row['category_name']
                ).on_conflict_do_nothing(index_elements=["type_id"])
                result = conn.execute(stmt)
                if result.rowcount:
                    logger.info(f"Added {row['type_name']} (ID: {row['type_id']}) to watchlist")
                else:
                    logger.info(f"Skipped {row['type_name']} (ID: {row['type_id']}): already in watchlist")
            conn.commit()

        engine.dispose()
        logger.info(f"Successfully added {len(new_items)} new items to watchlist")
        return True

    except Exception as e:
        logger.error(f"Error adding items to watchlist: {e}")
        return False

def get_type_info(type_ids: list[int], remote: bool = False):
    engine = sde_db.remote_engine if remote else sde_db.engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM inv_info WHERE typeID IN :type_ids").bindparams(bindparam('type_ids', expanding=True))
        res = conn.execute(stmt, {"type_ids": type_ids})
        df = pd.DataFrame(res.fetchall())
        df.columns = res.keys()
        df = df.rename(columns={"typeID": "type_id", "typeName": "type_name", "groupID": "group_id", "groupName": "group_name", "categoryID": "category_id", "categoryName": "category_name"})
    return df

def update_watchlist_tables(missing_items: list[int]):
    engine = sde_db.engine
    with engine.connect() as conn:
        from sqlalchemy import bindparam
        stmt = text("SELECT * FROM inv_info WHERE typeID IN :missing").bindparams(bindparam('missing', expanding=True))
        df = pd.read_sql_query(stmt, conn)

    inv_cols = ['typeID', 'typeName', 'groupID', 'groupName', 'categoryID', 'categoryName']
    watchlist_cols = ['type_id', 'type_name', 'group_id', 'group_name', 'category_id', 'category_name']
    df = df[inv_cols]
    df = df.rename(columns=dict(zip(inv_cols, watchlist_cols)))

    engine = wcmkt_db.engine
    with engine.begin() as conn:
        for _, row in df.iterrows():
            stmt = sqlite_insert(Watchlist).values(
                type_id=int(row['type_id']),
                type_name=row['type_name'],
                group_id=int(row['group_id']),
                group_name=row['group_name'],
                category_id=int(row['category_id']),
                category_name=row['category_name']
            ).on_conflict_do_nothing(index_elements=["type_id"])
            result = conn.execute(stmt)
            if result.rowcount:
                logger.info(f"Added {row['type_name']} (ID: {row['type_id']}) to watchlist")
            else:
                logger.info(f"Skipped {row['type_name']} (ID: {row['type_id']}): already in watchlist")

def export_doctrines_to_csv(db_alias: str = "wcmkt", output_file: str = "doctrines_backup.csv"):
    """
    Export doctrines table to CSV for backup purposes.

    Args:
        db_alias: Database alias to export from
        output_file: Output CSV file path
    """
    logger.info(f"Exporting doctrines from {db_alias} to {output_file}")

    try:
        db = DatabaseConfig(db_alias)
        engine = db.remote_engine

        with engine.connect() as conn:
            doctrines_df = pd.read_sql_query("SELECT * FROM doctrines", conn)
            doctrines_df.to_csv(output_file, index=False)
            logger.info(f"Exported {len(doctrines_df)} doctrines records to {output_file}")

        return True

    except Exception as e:
        logger.error(f"Error exporting doctrines: {e}")
        return False
    finally:
        if 'engine' in locals():
            engine.dispose()

def get_most_recent_updates(table_name: str, remote: bool = False):

    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    session = Session(bind=engine)
    with session.begin():
        updates = select(UpdateLog.timestamp).where(UpdateLog.table_name == table_name).order_by(UpdateLog.timestamp.desc())
        result = session.execute(updates).scalar_one()
    session.close()
    engine.dispose()
    return result

def check_updates(remote: bool = False):
    update_status = {
        "stats": {
            "updated": None,
            "needs_update": False,
            "time_since": None
        },
        "history": {
            "updated": None,
            "needs_update": False,
            "time_since": None
        },
        "doctrines": {
            "updated": None,
            "needs_update": False,
            "time_since": None
        },
        "orders": {
            "updated": None,
            "needs_update": False,
            "time_since": None
        }
    }
    logger.info("Checking updates")
    try:
        statsupdate = get_most_recent_updates("marketstats",remote=remote).replace(tzinfo=timezone.utc)
        update_status["stats"]["updated"] = statsupdate
    except Exception as e:
        logger.error(f"Error getting stats update: {e}")

    try:
        historyupdate = get_most_recent_updates("market_history",remote=remote).replace(tzinfo=timezone.utc)
        update_status["history"]["updated"] = historyupdate
    except Exception as e:
        logger.error(f"Error getting history update: {e}")

    try:
        doctrinesupdate = get_most_recent_updates("doctrines",remote=remote).replace(tzinfo=timezone.utc)
        update_status["doctrines"]["updated"] = doctrinesupdate
    except Exception as e:
        logger.error(f"Error getting doctrines update: {e}")

    try:
        ordersupdate = get_most_recent_updates("marketorders",remote=remote).replace(tzinfo=timezone.utc)
        update_status["orders"]["updated"] = ordersupdate
    except Exception as e:
        logger.error(f"Error getting orders update: {e}")

    now = datetime.now(timezone.utc)

    time_since_stats_update = now - update_status["stats"]["updated"]
    time_since_history_update = now - update_status["history"]["updated"]
    time_since_doctrines_update = now - update_status["doctrines"]["updated"]
    time_since_orders_update = now - update_status["orders"]["updated"]

    update_status["stats"]["time_since"] = time_since_stats_update
    update_status["history"]["time_since"] = time_since_history_update
    update_status["doctrines"]["time_since"] = time_since_doctrines_update
    update_status["orders"]["time_since"] = time_since_orders_update

    logger.info(f"Time since stats update: {time_since_stats_update}")
    logger.info(f"Time since history update: {time_since_history_update}")
    logger.info(f"Time since doctrines update: {time_since_doctrines_update}")
    logger.info(f"Time since orders update: {time_since_orders_update}")

    update_status["stats"]["needs_update"] = False
    update_status["history"]["needs_update"] = False
    update_status["doctrines"]["needs_update"] = False
    update_status["orders"]["needs_update"] = False

    if update_status["stats"]["time_since"] > timedelta(hours=1):
        logger.info("Stats update is older than 1 hour")
        logger.info(f"Stats update timestamp: {update_status['stats']['updated']}")
        logger.info(f"Now: {now}")
        update_status["stats"]["needs_update"] = True
    if update_status["history"]["time_since"] > timedelta(hours=1):
        logger.info("History update is older than 1 hour")
        logger.info(f"History update timestamp: {update_status['history']['updated']}")
        logger.info(f"Now: {now}")
        update_status["history"]["needs_update"] = True
    if update_status["doctrines"]["time_since"] > timedelta(hours=1):
        logger.info("Doctrines update is older than 1 hour")
        logger.info(f"Doctrines update timestamp: {update_status['doctrines']['updated']}")
        logger.info(f"Now: {now}")
        update_status["doctrines"]["needs_update"] = True
    if update_status["orders"]["time_since"] > timedelta(hours=1):
        logger.info("Orders update is older than 1 hour")
        logger.info(f"Orders update timestamp: {update_status['orders']['updated']}")
        logger.info(f"Now: {now}")
        update_status["orders"]["needs_update"] = True

    return update_status

def fix_null_doctrine_stats_timestamps (doctrine_stats: pd.DataFrame, timestamp: str) -> pd.DataFrame:
    null_timestamp = doctrine_stats[doctrine_stats.timestamp.isnull()].reset_index(drop=True)
    null_timestamp["timestamp"] = timestamp
    # Filter out rows with null timestamps from original dataframe before concatenating
    doctrine_stats = doctrine_stats[doctrine_stats.timestamp.notnull()]
    doctrine_stats = pd.concat([doctrine_stats, null_timestamp], ignore_index=True)
    return doctrine_stats

def restore_watchlist_from_csv(csv_file: str = "data/watchlist_updated.csv", remote: bool = False):

    cols = ["type_id", "type_name", "group_id", "group_name", "category_id", "category_name"]
    df = pd.read_csv(csv_file)[cols]
    df["type_id"] = df["type_id"].astype(int)
    df["group_id"] = df["group_id"].astype(int)
    df["category_id"] = df["category_id"].astype(int)
    rows = df.to_dict(orient="records")

    if not rows:
        raise ValueError(
            f"refusing to restore watchlist from empty CSV {csv_file}: "
            "would DELETE all rows and insert nothing"
        )

    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM watchlist"))
            for i in range(0, len(rows), 500):
                conn.execute(sqlite_insert(Watchlist).values(rows[i : i + 500]))
    finally:
        engine.dispose()
    logger.info(f"Restored watchlist from {csv_file} to {db.alias}: {len(df)} items")

if __name__ == "__main__":
    pass
