import pandas as pd
from sqlalchemy import text, insert, select, bindparam
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import Watchlist, UpdateLog
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session


logger = configure_logging(__name__)

sde_db = DatabaseConfig("sde")
wcmkt_db = DatabaseConfig("wcmkt")

def add_missing_items_to_watchlist(missing_items: list[int], remote: bool = False, db_alias: str = "wcmkt"):
    """
    Add missing items to the watchlist by fetching type information from SDE database.

    Args:
        missing_items: List of type IDs to add to watchlist
        remote: Whether to use remote database (default: False for local)

    Returns:
        String message indicating success and items added
    """
    if not missing_items:
        logger.warning("No items provided to add to watchlist")
        return "No items provided to add to watchlist"

    logger.info(f"Adding {len(missing_items)} items to watchlist: {missing_items}")

    # Get type information from SDE database
    df = get_type_info(missing_items, remote=remote)

    if df.empty:
        logger.error("No type information found for provided type IDs")
        return "No type information found for provided type IDs"

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
        return f"All {len(missing_items)} items already exist in watchlist"

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
                stmt = insert(Watchlist).values(
                    type_id=row['type_id'],
                    type_name=row['type_name'],
                    group_id=row['group_id'],
                    group_name=row['group_name'],
                    category_id=row['category_id'],
                    category_name=row['category_name']
                )
                try:
                    conn.execute(stmt)
                    logger.info(f"Added {row['type_name']} (ID: {row['type_id']}) to watchlist")
                except Exception as e:
                    logger.warning(f"Item {row['type_id']} may already exist: {e}")
            conn.commit()

        engine.dispose()
        logger.info(f"Successfully added {len(new_items)} new items to watchlist")
        return f"Added {len(new_items)} items to watchlist: {new_items['type_name'].tolist()}"

    except Exception as e:
        logger.error(f"Error adding items to watchlist: {e}")
        return f"Error adding items to watchlist: {e}"

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
    with engine.connect() as conn:
        for _, row in df.iterrows():
            stmt = insert(Watchlist).values(
                type_id=row['type_id'],
                type_name=row['type_name'],
                group_id=row['group_id'],
                group_name=row['group_name'],
                category_id=row['category_id'],
                category_name=row['category_name']
            )
            try:
                conn.execute(stmt)
                conn.commit()
                logger.info(f"Added {row['type_name']} (ID: {row['type_id']}) to watchlist")
            except Exception as e:
                logger.warning(f"Item {row['type_id']} may already exist in watchlist: {e}")

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

    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    with engine.connect() as conn:
        df = pd.read_csv(csv_file)
        df.to_sql("watchlist", conn, if_exists="replace", index=False)
    conn.close()
    engine.dispose()
    logger.info(f"Restored watchlist from {csv_file} to {db.alias}")

if __name__ == "__main__":
    pass
