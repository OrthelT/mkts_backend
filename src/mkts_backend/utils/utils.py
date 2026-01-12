from __future__ import annotations
from typing import Iterable
import requests

import pandas as pd
import json
import sqlalchemy as sa
from sqlalchemy import text, create_engine
from mkts_backend.config.config import DatabaseConfig
from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.config.logging_config import configure_logging
from sqlalchemy.orm import Session
from datetime import datetime, timezone
logger = configure_logging(__name__)

sde_db = DatabaseConfig("sde")
fittings_db = DatabaseConfig("fittings")
wcmkt_db = DatabaseConfig("wcmkt")

def get_type_names_from_df(df: pd.DataFrame) -> pd.DataFrame:
    verify_db_exists = sde_db.verify_db_exists()
    if not verify_db_exists:
        logger.error("SDE database is not up to date. Exiting...")
        sde_db.sync()
    
    engine = sde_db.engine
    with engine.connect() as conn:
        stmt = text("SELECT typeID, typeName, groupName, categoryName, categoryID FROM inv_info")
        res = conn.execute(stmt)
        df = pd.DataFrame(res.fetchall(), columns=["typeID", "typeName", "groupName", "categoryName", "categoryID"])
        df = df.rename(columns={"typeID": "type_id", "typeName": "type_name", "groupName": "group_name", "categoryName": "category_name", "categoryID": "category_id"})
    engine.dispose()
    return df[["type_id", "type_name", "group_name", "category_name", "category_id"]]

def get_type_name(type_id: int) -> str:
    db = DatabaseConfig("sde")
    engine = db.engine
    with engine.connect() as conn:
        stmt = text("SELECT typeName FROM inv_info WHERE typeID = :type_id")
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
    df = pd.read_csv(watchlist_csv)
    db = wcmkt_db
    engine = db.engine
    with engine.connect() as conn:
        df.to_sql("watchlist", conn, if_exists="replace", index=False)
        conn.commit()
    conn.close()
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

def insert_type_data(data: list[dict]):
    db = DatabaseConfig("sde")
    engine = db.engine
    unprocessed_data = []

    with engine.connect() as conn:
        for row in data:
            try:
                type_id = row["type_id"]
                if type_id is None:
                    logger.warning("Type ID is None, skipping...")
                    continue
                logger.info(f"Inserting type data for {row['type_id']}")

                params = (type_id,)

                query = "SELECT typeName FROM Joined_InvTypes WHERE typeID = ?"
                result = conn.execute(query, params)
                try:
                    type_name = result.fetchone()[0]
                except Exception as e:
                    logger.error(f"Error fetching type name: {e}")
                    unprocessed_data.append(row)
                    continue

                row["type_name"] = str(type_name)
            except Exception as e:
                logger.error(f"Error inserting type data: {e}")
                data.remove(row)
                logger.info(f"Removed row: {row}")
    if unprocessed_data:
        logger.info(f"Unprocessed data: {unprocessed_data}")
        with open("unprocessed_data.json", "w") as f:
            json.dump(unprocessed_data, f)
    return data

def update_ship_target(fit_id: int, ship_target: int):
    old_ship_target = check_ship_target(fit_id)
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine
    with engine.connect() as conn:

        print(f"Current ship target for fit_id {fit_id} is {old_ship_target}, updating to {ship_target}")
        stmt = text("UPDATE ship_targets SET ship_target = :ship_target WHERE fit_id = :fit_id")
        conn.execute(stmt, {"ship_target": ship_target, "fit_id": fit_id})
        conn.commit()
        conn.close()
        engine.dispose()

    new_ship_target = check_ship_target(fit_id)
    print(f"New ship target for fit_id {fit_id} is {new_ship_target}")
    if new_ship_target != old_ship_target:
        logger.info(f"Ship target for fit_id {fit_id} was updated from {old_ship_target} to {new_ship_target}")
        print(f"Ship target for fit_id {fit_id} was updated from {old_ship_target} to {new_ship_target}")
    else:
        logger.info(f"Ship target for fit_id {fit_id} was {old_ship_target}={new_ship_target}, no update needed")
        print(f"Ship target for fit_id {fit_id} was {old_ship_target}={new_ship_target}, no update needed")

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

def add_new_ship_target(fit_id: int, fit_name: str, ship_id: int, ship_name: str, ship_target: int):
    created_at = datetime.now(timezone.utc)
    from mkts_backend.db.models import ShipTargets
    ship_target = ShipTargets(fit_id=fit_id, fit_name=fit_name, ship_id=ship_id, ship_name=ship_name, ship_target=ship_target, created_at=created_at)
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine
    session = Session(bind=engine)
    with session.begin():
        session.add(ship_target)
        session.commit()
        print(f"Ship target {fit_name} added")
    session.close()
    engine.dispose()
    return True

if __name__ == "__main__":
    pass
