import requests
import os
import json
import time
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from proj_config import wcmkt_url, db_path, sys_id, reg_id, user_agent
from datetime import datetime, timezone
from logging_config import configure_logging
from models import RegionOrders, Base
import pandas as pd
logger = configure_logging(__name__)
from utils import get_type_names, get_type_name
from dbhandler import add_region_history, get_watchlist_ids, get_nakah_watchlist
from millify import millify
from models import RegionHistory

def fetch_region_orders(region_id: int, order_type: str = 'sell') -> list[dict]:
    """
    Get all orders for a given region and order type
    Args:
        region_id: int
        order_type: str (sell, buy, all)
    Returns:
        list of order dicts
    """
    orders = []
    max_pages = 1
    page = 1
    error_count = 0
    logger.info(f"Getting orders for region {region_id} with order type {order_type}")
    status_codes = {}
    begin_time = time.time()
    
    while page <= max_pages:
        status_code = None
        
        headers = {
            'User-Agent': 'wcmkts_backend/1.0, orthel.toralen@gmail.com, (https://github.com/OrthelT/wcmkts_backend)',
            'Accept': 'application/json',
        }
        base_url = f"https://esi.evetech.net/latest/markets/{region_id}/orders/?datasource=tranquility&order_type={order_type}&page={page}"
        start_time = time.time()
        try:
            response = requests.get(base_url, headers=headers, timeout=10)
            elapsed = millify(response.elapsed.total_seconds(), precision=2)
            status_code = response.status_code
        except requests.exceptions.Timeout as TimeoutError:
            print(TimeoutError)
            elapsed = millify(time.time() - start_time, precision=2)
            logger.error(f"Timeout: {page} of {max_pages} | {elapsed}s")
        except requests.exceptions.ConnectionError as ConnectionError:
            print(ConnectionError)
            elapsed = millify(time.time() - start_time, precision=2)
            logger.error(f"Connection Error: {page} of {max_pages} | {elapsed}s")
        except requests.exceptions.RequestException as RequestException:
            print(RequestException)
            elapsed = millify(time.time() - start_time, precision=2)
            logger.error(f"Request Error: {page} of {max_pages} | {elapsed}s")

        if status_code and status_code != 200:
            logger.error(f"page {page} of {max_pages} | status: {status_code} | {elapsed}s")
            error_count += 1
            if error_count > 5:
                print("error", status_code)
                logger.error(f"Error: {status_code}")
                raise Exception(f"Too many errors: {error_count}")
            time.sleep(1)
            continue
        elif status_code == 200:
            logger.info(f"page {page} of {max_pages} | status: {status_code} | {elapsed}s")
        else:
            # Handle case where response failed (timeout, connection error, etc.)
            logger.error(f"page {page} of {max_pages} | request failed | {elapsed}s")
            error_count += 1
            if error_count > 5:
                logger.error(f"Too many errors: {error_count}")
                raise Exception(f"Too many errors: {error_count}")
            time.sleep(1)
            continue

        
        # Only process response if we have a valid status code
        if status_code == 200:
            error_remain = response.headers.get('X-Error-Limit-Remain')
            if error_remain == '0':
                logger.critical(f"Too many errors: {error_count}")
                raise Exception(f"Too many errors: {error_count}")
        
            if response.headers.get('X-Pages'):
                max_pages = int(response.headers.get('X-Pages'))
            else:
                max_pages = 1
            
            order_page = response.json()
        else:
            # Skip processing this page due to error
            continue


        if order_page == []:
            logger.info(f"No more orders found")
            logger.info("--------------------------------\n\n")
            return orders
        else:
            for order in order_page:
                orders.append(order)

            page += 1
    logger.info(f"{len(orders)} orders fetched in {millify(time.time() - begin_time, precision=2)}s | {millify(len(orders)/(time.time() - begin_time), precision=2)} orders/s")
    logger.info("--------------------------------\n\n")
    return orders

def get_region_orders_from_db(region_id: int) -> pd.DataFrame:
    """
    Get all orders for a given region and order type
    Args:
        region_id: int
        order_type: str (sell, buy, all)
    Returns:
        pandas DataFrame
    """
    stmt = select(RegionOrders)

    engine = create_engine(wcmkt_url)
    session = Session(bind=engine)
    result = session.scalars(stmt)
    orders_data = []
    for order in result:
        orders_data.append({
            'order_id': order.order_id,
            'duration': order.duration,
            'is_buy_order': order.is_buy_order,
            'issued': order.issued,
            'location_id': order.location_id,
            'min_volume': order.min_volume,
            'price': order.price,
            'range': order.range,
            'system_id': order.system_id,
            'type_id': order.type_id,
            'volume_remain': order.volume_remain,
            'volume_total': order.volume_total
        })
    
    session.close()
    return pd.DataFrame(orders_data)

def update_region_orders(region_id: int, order_type: str = 'sell') -> pd.DataFrame:
    """
    Fetch region orders from the database
    Args:
        region_id: int
        order_type: str (sell, buy, all)
    Returns:    
        pandas DataFrame
    """
    orders = fetch_region_orders(region_id, order_type)
    engine = create_engine(wcmkt_url)
    session = Session(bind=engine)
    
    # Clear existing orders
    session.query(RegionOrders).delete()
    session.commit()
    session.expunge_all()  # Clear all objects from identity map
    session.close()
    time.sleep(1)
    session = Session(bind=engine)  # Create a fresh session
    
    # Convert API response dicts to RegionOrders model instances
    for order_data in orders:
        # Convert the API response to match our model fields
        region_order = RegionOrders(
            order_id=order_data['order_id'],
            duration=order_data['duration'],
            is_buy_order=order_data['is_buy_order'],
            issued=datetime.fromisoformat(order_data['issued'].replace('Z', '+00:00')),
            location_id=order_data['location_id'],
            min_volume=order_data['min_volume'],
            price=order_data['price'],
            range=order_data['range'],
            system_id=order_data['system_id'],
            type_id=order_data['type_id'],
            volume_remain=order_data['volume_remain'],
            volume_total=order_data['volume_total']
        )
        session.add(region_order)
    
    session.commit()
    session.close()
    
    return pd.DataFrame(orders)

def get_system_orders_from_db(system_id: int) -> pd.DataFrame:
    """
    Get all orders for a given system
    Args:
        system_id: int
    Returns:
        pandas DataFrame    
    """
    stmt = select(RegionOrders).where(RegionOrders.system_id == system_id)
    engine = create_engine(wcmkt_url)
    session = Session(bind=engine)
    result = session.scalars(stmt)
    
    # Convert SQLAlchemy objects to dictionaries for DataFrame
    orders_data = []
    for order in result:
        orders_data.append({
            'order_id': order.order_id,
            'duration': order.duration,
            'is_buy_order': order.is_buy_order,
            'issued': order.issued,
            'location_id': order.location_id,
            'min_volume': order.min_volume,
            'price': order.price,
            'range': order.range,
            'system_id': order.system_id,
            'type_id': order.type_id,
            'volume_remain': order.volume_remain,
            'volume_total': order.volume_total
        })
    
    session.close()
    return pd.DataFrame(orders_data)

def process_system_orders(system_id: int) -> pd.DataFrame:
    df = get_system_orders_from_db(system_id)
    df = df[df['is_buy_order'] == False]
    df2 = df.copy()
    nakah = 60014068
    nakah_df = df[df.location_id == nakah].reset_index(drop=True)
    nakah_df = nakah_df[["price","type_id","volume_remain"]]
    nakah_df = nakah_df.groupby("type_id").agg({"price": "mean", "volume_remain": "sum"}).reset_index()
    nakah_ids = nakah_df["type_id"].unique().tolist()
    type_names = get_type_names(nakah_ids)
    nakah_df = nakah_df.merge(type_names, on="type_id", how="left")
    nakah_df = nakah_df[["type_id", "type_name", "group_name", "category_name", "price", "volume_remain"]]
    nakah_df['timestamp'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    nakah_df.to_csv("nakah_stats.csv", index=False)
    return nakah_df


def calculate_total_market_value(market_data: pd.DataFrame) -> float:
    """
    Calculate the total market value from process_system_orders output
    
    Args:
        market_data: DataFrame from process_system_orders containing price and volume_remain columns
        
    Returns:
        Total market value as float
    """
    if market_data is None or market_data.empty:
        logger.warning("No market data provided for value calculation")
        return 0.0
    
    # Filter out Blueprint and Skill categories
    filtered_data = market_data[
        (~market_data['category_name'].isin(['Blueprint', 'Skill']))
    ].copy()
    
    if filtered_data.empty:
        logger.warning("No market data after filtering out Blueprint and Skill categories")
        print("No market data after filtering out Blueprint and Skill categories")
        return 0.0
    # Calculate total value for each item (price * volume_remain)
    filtered_data['total_value'] = filtered_data['price'] * filtered_data['volume_remain']
    # Sum all individual totals to get overall market value
    total_market_value = filtered_data['total_value'].sum()
    logger.info(f"Total market value calculated: {millify(total_market_value, precision=2)} ISK")
    print(f"Total market value calculated: {millify(total_market_value, precision=2)} ISK")
    return total_market_value


def get_system_market_value(system_id: int) -> float:
    """
    Convenience function to get total market value for a system
    
    Args:
        system_id: System ID to calculate market value for
        
    Returns:
        Total market value as float
    """
    market_data = process_system_orders(system_id)
    return calculate_total_market_value(market_data)


def calculate_total_ship_count(market_data: pd.DataFrame) -> int:
    """
    Calculate the total number of ships on the market
    
    Args:
        market_data: DataFrame from process_system_orders containing category_name and volume_remain columns
        
    Returns:
        Total ship count as int
    """
    if market_data is None or market_data.empty:
        logger.warning("No market data provided for ship count calculation")
        print("No market data provided for ship count calculation")
        return 0
    
    # Filter for ships only and sum volume_remain
    ships_data = market_data[market_data['category_name'] == 'Ship']
    total_ship_count = ships_data['volume_remain'].sum()
    
    logger.info(f"Total ships on market: {total_ship_count:,}")
    print(f"Total ships on market: {total_ship_count:,}")
    return int(total_ship_count)


def get_system_ship_count(system_id: int) -> int:
    """
    Convenience function to get total ship count for a system
    
    Args:
        system_id: System ID to calculate ship count for
        
    Returns:
        Total ship count as int
    """
    market_data = process_system_orders(system_id)
    return calculate_total_ship_count(market_data)

def fetch_region_item_history(region_id: int, type_id: int) -> list[dict]:
    url = f"https://esi.evetech.net/latest/markets/{region_id}/history"

    querystring = {"type_id":type_id}

    headers = {
        "Accept-Language": "en",
        "If-None-Match": "",
        "X-Compatibility-Date": "2020-01-01",
        "X-Tenant": "tranquility",
        "Accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"    HTTP {response.status_code} for type_id {type_id}")
            return []
            
    except requests.exceptions.Timeout:
        print(f"    Timeout for type_id {type_id}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"    Request error for type_id {type_id}: {e}")
        return []
    except Exception as e:
        print(f"    Unexpected error for type_id {type_id}: {e}")
        return []

def fetch_region_history(region_id: int, type_ids: list[int]):
    history = []
    total_items = len(type_ids)
    
    print(f"Starting fetch_region_history for {total_items} items in region {region_id}")
    print("=" * 60)
    
    for i, type_id in enumerate(type_ids, 1):
        print(f"Processing item {i}/{total_items} (type_id: {type_id})", end="", flush=True)
        
        try:
            start_time = time.time()
            item_history = fetch_region_item_history(region_id, type_id)
            elapsed_time = time.time() - start_time
            
            if item_history and len(item_history) > 0:
                print(f" ✓ {len(item_history)} records in {elapsed_time:.2f}s")
            else:
                print(f" ⚠ No data in {elapsed_time:.2f}s")
            
            history.append({type_id: item_history})
            
        except Exception as e:
            print(f" ❌ Error: {e}")
            # Still add the item to history with empty data
            history.append({type_id: []})
    
    print("=" * 60)
    print(f"Completed fetch_region_history: {len(history)} items processed")
    
    # Summary
    items_with_data = sum(1 for item in history if list(item.values())[0])
    items_without_data = len(history) - items_with_data
    total_records = sum(len(list(item.values())[0]) for item in history)
    
    print(f"Summary: {items_with_data} items with data, {items_without_data} items without data")
    print(f"Total history records: {total_records}")
    
    return history


if __name__ == "__main__":
    pass
    