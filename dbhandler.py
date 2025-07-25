import os
import libsql
import json
import requests
import pandas as pd
from sqlalchemy import inspect, text, create_engine, select, insert, MetaData
from sqlalchemy.orm import Session, sessionmaker, query
from datetime import datetime, timezone
import time
from utils import standby, logger, configure_logging, get_type_name
from dotenv import load_dotenv
from logging_config import configure_logging
from models import Base, Doctrines, RegionHistory
from proj_config import db_path, wcmkt_url, sde_path, sde_url, fittings_path, fittings_path, fittings_url
from dataclasses import dataclass, field
import sqlalchemy as sa

load_dotenv()
logger = configure_logging(__name__)

wcmkt_path = db_path
wcmkt_local_url = wcmkt_url
sde_path = sde_path
sde_local_url = sde_url
fittings_path = fittings_path
fittings_local_url = fittings_url

turso_url = os.getenv("TURSO_URL")
turso_auth_token = os.getenv("TURSO_AUTH_TOKEN")

sde_url = os.getenv("SDE_URL")
sde_token = os.getenv("SDE_AUTH_TOKEN")

@dataclass
class TypeInfo:
    type_id: int
    type_name: str = field(init=False)
    group_name: str = field(init=False)
    category_name: str = field(init=False)
    category_id: int = field(init=False)
    group_id: int = field(init=False)
    volume: int = field(init=False)
    def __post_init__(self):
        self.get_type_info()

    def get_type_info(self):
        stmt = sa.text("SELECT * FROM inv_info WHERE typeID = :type_id")
        engine = sa.create_engine(sde_local_url)
        with engine.connect() as conn:
            result = conn.execute(stmt, {"type_id": self.type_id})
            for row in result:
                self.type_name = row.typeName
                self.group_name = row.groupName
                self.category_name = row.categoryName
                self.category_id = row.categoryID
                self.group_id = row.groupID
                self.volume = row.volume
        engine.dispose()

# SDE connection
def sde_conn():
    conn = libsql.connect(sde_path, sync_url=sde_url, auth_token=sde_token)
    return conn

# WCMKT connection
def wcmkt_conn():
    conn = libsql.connect(wcmkt_path, sync_url=turso_url, auth_token=turso_auth_token)
    return conn

def sde_remote_engine():
    engine = create_engine(sde_url, connect_args={"auth_token": sde_token}, echo=True)
    return engine

def sde_local_engine():
    engine = create_engine(sde_local_url)
    return engine

def get_wcmkt_remote_engine():
    engine = create_engine(
    f"sqlite+{turso_url}?secure=true",
    connect_args={
        "auth_token": turso_auth_token,
    },echo_pool=True, echo=False)
    return engine

def get_wcmkt_local_engine():
    engine = create_engine(wcmkt_local_url, echo_pool=True, echo=False)
    return engine

def insert_type_data(data: list[dict]):
    conn = libsql.connect(sde_path)
    cursor = conn.cursor()
    unprocessed_data = []
    for row in data:
        try:
            type_id = row["type_id"]
            if type_id is None:
                logger.warning("Type ID is None, skipping...")
                continue
            logger.info(f"Inserting type data for {row['type_id']}")

            params = (type_id,)

            query = "SELECT typeName FROM Joined_InvTypes WHERE typeID = ?"
            cursor.execute(query, params)
            try:
                type_name = cursor.fetchone()[0]
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


def get_type_names(df: pd.DataFrame) -> pd.DataFrame:
    type_ids = df["type_id"].unique().tolist()
    logger.info(f"Total unique type IDs: {len(type_ids)}")

    # Process type IDs in chunks of 1000 (ESI limit)
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


def load_data(table: str, df: pd.DataFrame):
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()

    logger.info(f"Loading data into {table}")
    clear_table = f"DELETE FROM {table};"
    cursor.execute(clear_table)

    data = df.to_dict(orient="records")
    total_rows = len(data)

    for i, row in enumerate(data, 1):
        print(f"\rInserting row {i}/{total_rows}", end="", flush=True)
        columns = ", ".join(row.keys())
        # Properly escape values for SQL
        values = ", ".join(
            [
                f"'{str(value).replace("'", "''")}'" if value is not None else "NULL"
                for value in row.values()
            ]
        )
        query = f"INSERT INTO {table} ({columns}) VALUES ({values});"
        try:
            cursor.execute(query)
            conn.commit()
        except Exception as e:
            logger.error(f"Error inserting row: {row}")
            logger.error(f"Error: {e}")
            continue

   # New line after progress display
    logger.info(f"Successfully inserted {total_rows} rows into {table}")
    conn.close()


def update_remote_database(table: str, df: pd.DataFrame):
    remote_engine = get_wcmkt_remote_engine()
    with remote_engine.connect() as conn:
        conn.execute(text(f"DELETE FROM {table};"))
        conn.commit()
        logger.info(f"Deleted {table} table")

        # Process large datasets in chunks to prevent memory issues
        chunk_size = 1000  # Process 1000 records at a time
        total_rows = len(df)

        if total_rows > chunk_size:
            logger.info(f"Processing {total_rows} records in chunks of {chunk_size}")

            # Process first chunk with replace to create table structure
            first_chunk = df.iloc[:chunk_size]
            first_chunk.to_sql(table, conn, if_exists="replace", index=False)
            logger.info(f"Processed chunk 1/{(total_rows // chunk_size) + 1}")

            # Process remaining chunks with append
            for i in range(chunk_size, total_rows, chunk_size):
                chunk = df.iloc[i : i + chunk_size]
                chunk.to_sql(table, conn, if_exists="append", index=False)
                chunk_num = (i // chunk_size) + 1
                total_chunks = (total_rows // chunk_size) + 1
                logger.info(f"Processed chunk {chunk_num}/{total_chunks}")
                print(
                    f"\rProcessing chunk {chunk_num}/{total_chunks}", end="", flush=True
                )
        else:
            # For smaller datasets, process normally
            df.to_sql(table, conn, if_exists="replace", index=False)

        conn.commit()
        logger.info(f"Successfully inserted {total_rows} records into {table}")
        if total_rows > chunk_size:
            print()  # New line after progress display
    conn.close()
    remote_engine.dispose()

def update_remote_database_with_orm_session(table: Base, df: pd.DataFrame):
    df = prepare_data_for_insertion(df, table)
    
    remote_engine = get_wcmkt_remote_engine()
    session = Session(bind=remote_engine)
    print(f"Updating {table} with {len(df)} rows")
    data = df.to_dict(orient="records")
    chunk_size = 1000
    session.query(table).delete()
    session.commit()
    with session.begin():
        for i in range(0, len(data), chunk_size):
            chunk = data[i : i + chunk_size]
            stmt = insert(table).values(chunk)
            session.execute(stmt)
            print(f"Processing chunk {i // chunk_size + 1}, size: {len(chunk)}")

    session.commit()


    print(f"Updated {table} with {len(df)} rows")

    session.close()
    remote_engine.dispose()

    status = get_remote_status()
    print(status)
    
    table_name = table.__tablename__
    print(f"Table {table_name} updated with {len(df)} rows")
    if status[table_name] == len(df):
        print(f"Table {table_name} updated successfully")
        return True
    else:
        print(f"Table {table_name} update failed")
        return False

def read_data(table: str, condition: dict = None) -> pd.DataFrame:
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()
    if condition is None:
        where_clause = ""
    else:
        where_clause = "WHERE " + " AND ".join(
            [f"{k} = {v}" for k, v in condition.items()]
        )
    query = f"SELECT * FROM {table} {where_clause}"
    cursor.execute(query)

    # Extract column names from cursor.description
    headers = [col[0] for col in cursor.description]
    data = cursor.fetchall()
    data_df = pd.DataFrame(data, columns=headers)
    cursor.close()
    conn.close()

    data_df = data_df.infer_objects()
    data_df = data_df.dropna(how="all")  # Only drop rows where all values are NaN
    data_df = data_df.reset_index(drop=True)
    return data_df


def get_valid_columns(table: str) -> list[str]:
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()
    stmt = f"SELECT * FROM {table}"
    cursor.execute(stmt)
    headers = [col[0] for col in cursor.description]
    return headers


def get_valid_columns_df(table: str) -> pd.DataFrame:
    headers = get_valid_columns(table)
    return pd.DataFrame(headers, columns=["column_name"])


def get_table_names() -> list[str]:
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()
    stmt = "SELECT name FROM sqlite_master WHERE type='table'"
    cursor.execute(stmt)
    return [row[0] for row in cursor.fetchall()]


def get_table_schema(table: str) -> pd.DataFrame:
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()
    stmt = f"PRAGMA table_info({table})"
    cursor.execute(stmt)
    return pd.DataFrame(
        cursor.fetchall(),
        columns=["cid", "name", "type", "notnull", "dflt_value", "pk"],
    )

def get_watchlist() -> pd.DataFrame:
    engine = create_engine(wcmkt_local_url)
    with engine.connect() as conn:
        df = pd.read_sql_table("watchlist", conn)
        if len(df) == 0:
            logger.error("No watchlist found")
            update_choice = input("No watchlist found, press Y to update from csv (data/all_watchlist.csv)")
            if update_choice == "Y":
                update_watchlist_data()
                df = pd.read_sql_table("watchlist", conn)
            else:
                logger.error("No watchlist found")
                return None


            if len(df) == 0:
                print("watchlist loading")
                standby(10)
                df = pd.read_sql_table("watchlist", conn)
            if len(df) == 0:
                logger.error("No watchlist found")
                return None
        else:
            print(f"watchlist loaded: {len(df)} items")
            logger.info(f"watchlist loaded: {len(df)} items")
    return df
def get_nakah_watchlist() -> pd.DataFrame:
    engine = create_engine(wcmkt_local_url)
    with engine.connect() as conn:
        df = pd.read_sql_table("nakah_watchlist", conn)
        if len(df) == 0:
            logger.error("No nakah watchlist found")
            return None
        else:
            print(f"nakah watchlist loaded: {len(df)} items")
            logger.info(f"nakah watchlist loaded: {len(df)} items")
    return df

def update_watchlist_data():
    df = pd.read_csv("data/all_watchlist.csv")
    engine = create_engine(wcmkt_local_url)
    with engine.connect() as conn:
        df.to_sql("watchlist", conn, if_exists="replace", index=False)
        conn.commit()
    conn.close()

def update_nakah_watchlist(df):
    engine = create_engine(wcmkt_local_url)
    with engine.connect() as conn:
        df.to_sql("nakah_watchlist", conn, if_exists="replace", index=False)
        conn.commit()
    engine.dispose()
    print("nakah_watchlist updated")

def get_market_orders(type_id: int) -> pd.DataFrame:
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()
    stmt = "SELECT * FROM marketorders WHERE type_id = ?"
    cursor.execute(stmt, (type_id,))
    headers = [col[0] for col in cursor.description]
    return pd.DataFrame(cursor.fetchall(), columns=headers)


def get_market_history(type_id: int) -> pd.DataFrame:
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()
    stmt = "SELECT * FROM market_history WHERE type_id = ?"
    cursor.execute(stmt, (type_id,))
    headers = [col[0] for col in cursor.description]
    return pd.DataFrame(cursor.fetchall(), columns=headers)


def get_table_length(table: str) -> int:
    conn = libsql.connect(wcmkt_path)
    cursor = conn.cursor()
    stmt = f"SELECT COUNT(*) FROM {table}"
    cursor.execute(stmt)
    return cursor.fetchone()[0]


def load_additional_tables():
    df = pd.read_csv("data/doctrine_map.csv")
    targets = pd.read_csv("data/ship_targets.csv")
    engine = create_engine(wcmkt_local_url)
    with engine.connect() as conn:
        df.to_sql("doctrine_map", conn, if_exists="replace", index=False)
        targets.to_sql("ship_targets", conn, if_exists="replace", index=False)
        conn.commit()

def sync_db(db_url="wcmkt2.db", sync_url=turso_url, auth_token=turso_auth_token):
    logger.info("database sync started")
    # Skip sync in development mode or when sync_url/auth_token are not provided
    if not sync_url or not auth_token:
        logger.info(
            "Skipping database sync in development mode or missing sync credentials"
        )
        return

    try:
        sync_start = time.time()
        conn = libsql.connect(db_url, sync_url=sync_url, auth_token=auth_token)
        logger.info("\n")
        logger.info("=" * 80)
        logger.info(f"Database sync started at {sync_start}")
        try:
            conn.sync()
            logger.info(
                f"Database synced in {1000 * (time.time() - sync_start)} milliseconds"
            )
            print(
                f"Database synced in {1000 * (time.time() - sync_start)} milliseconds"
            )
        except Exception as e:
            logger.error(f"Sync failed: {str(e)}")

        last_sync = datetime.now(timezone.utc)
        print(last_sync)
    except Exception as e:
        if "Sync is not supported" in str(e):
            logger.info(
                "Skipping sync: This appears to be a local file database that doesn't support sync"
            )
        else:
            logger.error(f"Sync failed: {str(e)}")

def get_remote_table_list():
    remote_engine = get_wcmkt_remote_engine()
    with remote_engine.connect() as conn:
        tables = conn.execute(text("PRAGMA table_list"))
        return tables.fetchall()
    remote_engine.dispose()

def get_remote_status():
    status_dict = {}
    remote_engine = get_wcmkt_remote_engine()
    with remote_engine.connect() as conn:
        tables = get_remote_table_list()
        for table in tables:
            table_name = table[1]
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            count = count.fetchone()[0]
            status_dict[table_name] = count

    remote_engine.dispose()

    print("Remote Status:")
    print("-" * 20)
    print(status_dict)
    return status_dict

def prepare_data_for_insertion(df, model_class):
    """Convert datetime strings to datetime objects for a model DataFrame"""
    inspector = inspect(model_class)

    for column in inspector.columns:
        column_name = column.key
        if column_name in df.columns:
            # Check if it's a DateTime column
            if hasattr(column.type, '__class__') and 'DateTime' in str(column.type.__class__):
                try:
                    # Convert the entire Series to datetime
                    df[column_name] = pd.to_datetime(df[column_name])
                    # Convert to Python datetime objects for SQLAlchemy
                    df[column_name] = df[column_name].dt.to_pydatetime()
                except Exception as e:
                    print(f"Error converting {column_name}: {e}")
                    # Set to current datetime as fallback for the entire column
                    df[column_name] = datetime.now()
        return df
    
def get_watchlist_ids():
    stmt = text("SELECT DISTINCT type_id FROM watchlist")
    engine = create_engine(wcmkt_local_url)
    with engine.connect() as conn:
        result = conn.execute(stmt)
        watchlist_ids = [row[0] for row in result]
    engine.dispose()
    return watchlist_ids

def get_fit_items(fit_id: int):
    stmt = text("SELECT type_id FROM fittings_fittingitem WHERE fit_id = :fit_id")
    engine = create_engine(fittings_local_url)
    with engine.connect() as conn:
        result = conn.execute(stmt, {"fit_id": fit_id})
        fit_items = [row[0] for row in result]
    engine.dispose()
    return fit_items

def get_fit_ids(doctrine_id: int):
    stmt = text("SELECT fitting_id FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id")
    engine = create_engine(fittings_local_url)
    with engine.connect() as conn:
        result = conn.execute(stmt, {"doctrine_id": doctrine_id})
        fit_ids = [row[0] for row in result]
    engine.dispose()
    return fit_ids

def add_doctrine_type_info_to_watchlist(doctrine_id: int):
    watchlist_ids = get_watchlist_ids()
    fit_ids = get_fit_ids(doctrine_id)
    
    missing_fit_items = []

    for fit_id in fit_ids:
        fit_items = get_fit_items(fit_id)
        for item in fit_items:
            if item not in watchlist_ids:
                missing_fit_items.append(item)

    missing_type_info = []

    for item in missing_fit_items:
        stmt4 = text("SELECT * FROM inv_info WHERE typeID = :item")
        engine = create_engine(sde_local_url)
        with engine.connect() as conn:
            result = conn.execute(stmt4, {"item": item})
            for row in result:
                type_info = TypeInfo(type_id=item)
                missing_type_info.append(type_info)

    for type_info in missing_type_info:
        stmt5 = text("INSERT INTO watchlist (type_id, type_name, group_name, category_name, category_id, group_id) VALUES (:type_id, :type_name, :group_name, :category_name, :category_id, :group_id)")
        engine = create_engine(wcmkt_local_url)
        with engine.connect() as conn:
            conn.execute(stmt5, {"type_id": type_info.type_id, "type_name": type_info.type_name, "group_name": type_info.group_name, "category_name": type_info.category_name, "category_id": type_info.category_id, "group_id": type_info.group_id})
            conn.commit()
        engine.dispose()
        logger.info(f"Added {type_info.type_name} to watchlist")
        print(f"Added {type_info.type_name} to watchlist")

def add_region_history(history: list[dict]):
    timestamp = datetime.now(timezone.utc)
    timestamp = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    engine = create_engine(wcmkt_local_url)
    session = Session(bind=engine)

    with session.begin():
        session.query(RegionHistory).delete()


        for item in history:
            for type_id, history in item.items():
                print(f"Processing type_id: {type_id}, {get_type_name(type_id)}")
                for record in history:
                    date = datetime.strptime(record["date"], "%Y-%m-%d")
                    order = RegionHistory(type_id=type_id, average=record["average"], date=date, highest=record["highest"], lowest=record["lowest"], order_count=record["order_count"], volume=record["volume"], timestamp=datetime.now(timezone.utc))
                    session.add(order)
        session.commit()
        session.close()
        engine.dispose()

def get_region_history()-> pd.DataFrame:
    engine = create_engine(wcmkt_local_url)
    with engine.connect() as conn:
        stmt = text("SELECT * FROM region_history")
        result = conn.execute(stmt)
        df = pd.DataFrame(result.fetchall(), columns=result.keys())
    engine.dispose()
    return df
def get_region_deployment_history(deployment_date: datetime) -> pd.DataFrame:
    """
    Get region history data after a specified deployment date.
    
    Args:
        deployment_date: datetime object representing the deployment date
        
    Returns:
        pandas DataFrame containing region history records after the deployment date
    """
    df = get_region_history()
    
    if df.empty:
        print("No region history data found")
        return df
    
    # Convert the date column to datetime if it's not already
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        
        # Filter records after the deployment date
        filtered_df = df[df['date'] >= deployment_date].copy()
        
        # Sort by date for better readability
        filtered_df = filtered_df.sort_values('date')
        
        print(f"Found {len(filtered_df)} records after {deployment_date.strftime('%Y-%m-%d')}")
        print(f"Date range: {filtered_df['date'].min()} to {filtered_df['date'].max()}")
        
        return filtered_df
    else:
        print("No 'date' column found in region history data")
        return df


if __name__ == "__main__":
    # Test the function with a sample date
    pass