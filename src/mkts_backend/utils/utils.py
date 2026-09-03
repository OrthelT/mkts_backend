from __future__ import annotations
from typing import Iterable
import requests

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text, create_engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import Watchlist
logger = configure_logging(__name__)

sde_db = DatabaseConfig("sde")
fittings_db = DatabaseConfig("fittings")
wcmkt_db = DatabaseConfig("wcmkt")

def get_type_names_from_df(df: pd.DataFrame) -> pd.DataFrame:
    verify_db_exists = sde_db.verify_db_exists()
    if not verify_db_exists:
        logger.error("SDE database is not up to date. Exiting...")
        sde_db.sync()

    input_type_ids = set(df["type_id"].unique())
    rename_map = {"typeID": "type_id", "typeName": "type_name", "groupName": "group_name", "categoryName": "category_name", "categoryID": "category_id"}
    cols = ["typeID", "typeName", "groupName", "categoryName", "categoryID"]
    out_cols = ["type_id", "type_name", "group_name", "category_name", "category_id"]

    engine = sde_db.engine
    with engine.connect() as conn:
        stmt = text("SELECT typeID, typeName, groupName, categoryName, categoryID FROM sdetypes")
        res = conn.execute(stmt)
        result = pd.DataFrame(res.fetchall(), columns=cols).rename(columns=rename_map)

        missing = input_type_ids - set(result["type_id"])
        if missing:
            placeholders = ','.join([f':id_{i}' for i in range(len(missing))])
            params = {f'id_{i}': int(tid) for i, tid in enumerate(missing)}
            fallback_stmt = text(f"""
                SELECT t.typeID, t.typeName, g.groupName, c.categoryName, c.categoryID
                FROM invTypes t
                LEFT JOIN invGroups g ON t.groupID = g.groupID
                LEFT JOIN invCategories c ON g.categoryID = c.categoryID
                WHERE t.typeID IN ({placeholders})
            """)
            fb_res = conn.execute(fallback_stmt, params)
            fb_df = pd.DataFrame(fb_res.fetchall(), columns=cols).rename(columns=rename_map)
            if not fb_df.empty:
                logger.info(f"Resolved {len(fb_df)} type names from invTypes fallback")
                result = pd.concat([result, fb_df], ignore_index=True)
    engine.dispose()
    return result[out_cols]

def get_type_name(type_id: int) -> str:
    db = DatabaseConfig("sde")
    engine = db.engine
    with engine.connect() as conn:
        stmt = text("SELECT typeName FROM sdetypes WHERE typeID = :type_id")
        res = conn.execute(stmt, {"type_id": type_id})
        type_name = res.fetchone()[0]
    engine.dispose()
    return type_name

def get_type_names_from_esi(df: pd.DataFrame) -> pd.DataFrame:
    type_ids = df["type_id"].unique().tolist()
    logger.info(f"Total unique type IDs: {len(type_ids)}")

    chunk_size = 1000
    all_names = []

    for i in range(0, len(type_ids), chunk_size):
        chunk = type_ids[i : i + chunk_size]
        logger.info(f"Processing chunk {i // chunk_size + 1}, size: {len(chunk)}")

        url = "https://esi.evetech.net/latest/universe/names/?datasource=tranquility"
        headers = {"User-Agent": "mkts-backend", "Accept": "application/json"}
        response = requests.post(url, headers=headers, json=chunk)

        if response.status_code == 200:
            chunk_names = response.json()
            if chunk_names:
                all_names.extend(chunk_names)
            else:
                logger.warning(f"No names found for chunk {i // chunk_size + 1}")
        else:
            logger.error(
                f"Error fetching names for chunk {i // chunk_size + 1}: {response.status_code}"
            )
            logger.error(f"Response: {response.json()}")

    if all_names:
        names_df = pd.DataFrame.from_records(all_names)
        names_df = names_df.drop(columns=["category"])
        names_df = names_df.rename(columns={"name": "type_name", "id": "type_id"})
        df = df.merge(names_df, on="type_id", how="left")
        return df
    else:
        logger.error("No names found for any chunks")
        return None

def get_null_count(df):
    return df.isnull().sum()

def validate_columns(df, valid_columns):
    return df[valid_columns]

def add_timestamp(df):
    df["timestamp"] = pd.Timestamp.now(tz="UTC")
    df["timestamp"] = df["timestamp"].dt.tz_convert(None)
    return df

def add_autoincrement(df):
    df["id"] = df.index + 1
    return df

def convert_datetime_columns(df, datetime_columns):
    for col in datetime_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors='coerce').dt.tz_convert(None)
    return df

def get_fit_items(fit_id: int) -> pd.DataFrame:
    table_list_stmt = "SELECT type_id, quantity FROM fittings_fittingitem WHERE fit_id = (:fit_id)"
    engine = create_engine(fittings_db.url)
    fit_items = []
    with engine.connect() as conn:
        result = conn.execute(text(table_list_stmt), {"fit_id": fit_id})
        table_info = result.fetchall()
        for row in table_info:
            type_id = row.type_id
            fit_qty = row.quantity
            fit_items.append({"type_id": type_id, "fit_qty": fit_qty})
        conn.close
    engine.dispose()

    for row in fit_items:
        type_id = row["type_id"]
        type_name = get_type_name(type_id)
        row["type_name"] = type_name

    df = pd.DataFrame(fit_items)
    return df

def update_watchlist_data(esi: ESIConfig, watchlist_csv: str = "data/watchlist.csv") -> bool:
    cols = ["type_id", "type_name", "group_id", "group_name", "category_id", "category_name"]
    df = pd.read_csv(watchlist_csv)[cols]
    df["type_id"] = df["type_id"].astype(int)
    df["group_id"] = df["group_id"].astype(int)
    df["category_id"] = df["category_id"].astype(int)
    rows = df.to_dict(orient="records")

    if not rows:
        raise ValueError(
            f"refusing to update watchlist from empty CSV {watchlist_csv}: "
            "would DELETE all rows and insert nothing"
        )

    engine = wcmkt_db.engine
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM watchlist"))
        for i in range(0, len(rows), 500):
            conn.execute(sqlite_insert(Watchlist).values(rows[i : i + 500]))
    logger.info(f"Watchlist updated: {len(df)} items")
    return True

def init_databases(aliases: str | list[str] | None = None) -> None:
    if aliases is None:
        aliases = ["sde", "fittings"]
    elif isinstance(aliases, str):
        aliases = [aliases]
    
    for alias in aliases:
        logger.debug(f"connecting to database {alias}")
        try:
            db = DatabaseConfig(alias)
            db.verify_db_exists()
        except Exception as e:
            logger.warning(f"Error initializing database {alias}: {e}")
            continue
        try:
            if db.needs_init():
                logger.info(f"initializing database {alias}")
                db.sync()
            else:
                logger.info(f"Database {alias} verified")
        except Exception as e:
            logger.warning(f"Error initializing database {alias}: {e}")

def check_ship_target(fit_id: int):
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM ship_targets WHERE fit_id = :fit_id")
        res = conn.execute(stmt, {"fit_id": fit_id})
        target = res.fetchone()
        target = target._mapping['ship_target']
    conn.close()
    engine.dispose()
    return target

if __name__ == "__main__":
    pass
