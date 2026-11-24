import os
from sqlalchemy import text, bindparam
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import pandas as pd

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import Watchlist

logger = configure_logging(__name__)

sde_db = DatabaseConfig("sde")
wcmkt_db = DatabaseConfig("wcmkt")


def get_type_info(type_ids: list[int], *, sde_conn=None) -> pd.DataFrame:
    """Fetch SDE type metadata for ``type_ids`` as a watchlist-shaped DataFrame."""
    stmt = text("SELECT * FROM sdetypes WHERE typeID IN :type_ids").bindparams(
        bindparam("type_ids", expanding=True)
    )
    if sde_conn is not None:
        res = sde_conn.execute(stmt, {"type_ids": type_ids})
        rows = res.fetchall()
        keys = res.keys()
    else:
        with sde_db.engine.connect() as conn:
            res = conn.execute(stmt, {"type_ids": type_ids})
            rows = res.fetchall()
            keys = res.keys()

    df = pd.DataFrame(rows, columns=keys)
    return df.rename(
        columns={
            "typeID": "type_id",
            "typeName": "type_name",
            "groupID": "group_id",
            "groupName": "group_name",
            "categoryID": "category_id",
            "categoryName": "category_name",
        }
    )


def add_missing_items_to_watchlist(
    missing_items: list[int],
    db_alias: str = "wcmkt",
    *,
    conn=None,
) -> bool:
    """Add missing items to the watchlist by fetching type information from SDE.

    When ``conn`` is supplied, participates in the caller's transaction without
    committing or disposing. Callers that need a push do so at the command
    boundary (typically via :class:`~mkts_backend.db.unit_of_work.ReplicaUnitOfWork`).

    Returns:
        True if the watchlist is left in the desired state (items inserted, or
        already present), False on any failure.
    """
    if not missing_items:
        logger.warning("No items provided to add to watchlist")
        return False

    logger.info(f"Adding {len(missing_items)} items to watchlist: {missing_items}")

    df = get_type_info(missing_items)
    if df.empty:
        logger.error("No type information found for provided type IDs")
        return False

    inv_cols = ["type_id", "type_name", "group_id", "group_name", "category_id", "category_name"]

    def _insert_into(c) -> bool:
        watchlist = pd.read_sql_table("watchlist", c)
        existing_type_ids = (
            set(watchlist["type_id"].tolist()) if not watchlist.empty else set()
        )
        new_items = df[~df["type_id"].isin(existing_type_ids)]

        if new_items.empty:
            logger.info("All provided items already exist in watchlist")
            return True

        new_items = new_items[inv_cols]

        os.makedirs("data", exist_ok=True)
        updated_watchlist = pd.concat([watchlist, new_items], ignore_index=True)
        updated_watchlist.to_csv("data/watchlist_updated.csv", index=False)
        logger.info("Saved updated watchlist to data/watchlist_updated.csv")

        for _, row in new_items.iterrows():
            stmt = sqlite_insert(Watchlist).values(
                type_id=int(row["type_id"]),
                type_name=row["type_name"],
                group_id=int(row["group_id"]),
                group_name=row["group_name"],
                category_id=int(row["category_id"]),
                category_name=row["category_name"],
            ).on_conflict_do_nothing(index_elements=["type_id"])
            result = c.execute(stmt)
            if result.rowcount:
                logger.info(f"Added {row['type_name']} (ID: {row['type_id']}) to watchlist")
            else:
                logger.info(f"Skipped {row['type_name']} (ID: {row['type_id']}): already in watchlist")

        logger.info(f"Successfully added {len(new_items)} new items to watchlist")
        return True

    if conn is not None:
        return _insert_into(conn)

    db = DatabaseConfig(db_alias)
    engine = db.engine
    try:
        with engine.begin() as own_conn:
            return _insert_into(own_conn)
    except Exception as e:
        logger.error(f"Error adding items to watchlist: {e}")
        return False
    finally:
        engine.dispose()


def fix_null_doctrine_stats_timestamps(
    doctrine_stats: pd.DataFrame, timestamp: str
) -> pd.DataFrame:
    null_timestamp = doctrine_stats[doctrine_stats.timestamp.isnull()].reset_index(drop=True)
    null_timestamp["timestamp"] = timestamp
    doctrine_stats = doctrine_stats[doctrine_stats.timestamp.notnull()]
    return pd.concat([doctrine_stats, null_timestamp], ignore_index=True)


def restore_watchlist_from_csv(csv_file: str = "data/watchlist_updated.csv") -> None:
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
    engine = db.engine
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM watchlist"))
            for i in range(0, len(rows), 500):
                conn.execute(sqlite_insert(Watchlist).values(rows[i : i + 500]))
    finally:
        engine.dispose()
    logger.info(f"Restored watchlist from {csv_file} to {db.alias}: {len(df)} items")
