from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.config.logging_config import configure_logging
import requests
import time
import json
import pandas as pd
import millify

logger = configure_logging(__name__)


def fetch_market_orders(esi: ESIConfig, order_type: str = "all", etag: str = None, test_mode: bool = False) -> list[dict]:
    logger.info("Fetching market orders")
    page = 1
    max_pages = 1
    orders = []
    error_count = 0
    request_count = 0

    url = esi.market_orders_url
    headers = esi.headers

    while page <= max_pages:
        request_count += 1
        logger.info(f"NEW REQUEST: request_count: {request_count}, page: {page}, max_pages: {max_pages}")

        # Both primary and deployment use structure markets with same query format
        querystring = {"page": str(page)}
        logger.info(f"querystring: {querystring}")

        response = requests.get(url, headers=headers, params=querystring, timeout=10)
        response.raise_for_status()

        if response.status_code == 200:
            logger.info(f"response successful: {response.status_code}")
            data = response.json()

            if test_mode:
                max_pages = 5
                logger.info(f"test_mode: max_pages set to {max_pages}. current page: {page}/{max_pages}")
            else:
                max_pages = int(response.headers.get("X-Pages"))
                logger.info(f"page: {page}, max_pages: {max_pages}")
        else:
            logger.error(f"Error fetching market orders: {response.status_code}")
            error_count += 1
            if error_count > 3:
                logger.error("Too many errors, stopping")
                return None
            else:
                logger.error(f"Retrying... {error_count} attempts")
                time.sleep(5)
        if data:
            orders.extend(data)
            page += 1
        else:
            logger.info(f"Data retrieved for {page}/{max_pages}. total orders: {len(orders)}")
            return orders
        logger.info("-" * 60)

    logger.info(f"market_orders complete:{page}/{max_pages} pages. total orders: {len(orders)} orders")
    logger.info("+=" * 40)
    return orders


def fetch_history(watchlist: pd.DataFrame) -> list[dict]:
    esi = ESIConfig("primary")
    url = esi.market_history_url
    error_count = 0
    total_time_taken = 0

    logger.info("Fetching history")
    if watchlist is None or watchlist.empty:
        logger.error("No watchlist provided or watchlist is empty")
        return None
    else:
        logger.info("Watchlist found")
        print(f"Watchlist found: {len(watchlist)} items")

    type_ids = watchlist["type_id"].tolist()
    logger.info(f"Fetching history for {len(type_ids)} types")

    headers = esi.headers()
    del headers["Authorization"]

    history = []
    request_count = 0
    watchlist_length = len(type_ids)

    while request_count < watchlist_length:
        type_id = type_ids[request_count]
        item_name = watchlist[watchlist["type_id"] == type_id]["type_name"].values[0]
        logger.info(f"Fetching history for {item_name}: {type_id}")
        querystring = {"type_id": type_id}
        request_count += 1
        try:
            print(f"\rFetching history for ({request_count}/{watchlist_length})", end="", flush=True)
            t1 = time.perf_counter()
            response = requests.get(url, headers=headers, timeout=10, params=querystring)
            response.raise_for_status()

            if response.status_code == 200:
                logger.info(f"response successful: {response.status_code}")
                error_remain = int(response.headers.get("X-Esi-Error-Limit-Remain"))
                if error_remain < 100:
                    logger.info(f"error_remain: {error_remain}")

                data = response.json()
                for record in data:
                    record["type_name"] = item_name
                    record["type_id"] = type_id

                if isinstance(data, list):
                    history.extend(data)
                else:
                    logger.warning(f"Unexpected data format for {item_name}")
            else:
                logger.error(f"Error fetching history for {item_name}: {response.status_code}")

        except Exception as e:
            logger.error(f"Error processing {item_name}: {e}")
            error_count += 1
            if error_count > 10:
                logger.error(f"Too many errors, stopping. Error count: {error_count}")
                return None
            else:
                logger.error(f"Retrying... {error_count} attempts")
                time.sleep(3)
            continue
        t2 = time.perf_counter()
        time_taken = round(t2 - t1, 2)
        total_time_taken += time_taken
        logger.info(f"time: {time_taken}s, average: {round(total_time_taken / request_count, 2)}s")
        if time_taken < 0.25:
            time.sleep(0.5)
            print(f"sleeping for 0.5 seconds to avoid rate limiting. Time: {time_taken}s")
    if history:
        logger.info(f"Successfully fetched {len(history)} total history records")
        with open("data/market_history.json", "w") as f:
            json.dump(history, f)
        return history
    else:
        logger.error("No history records found")
        return None


def fetch_region_orders(region_id: int, order_type: str = 'sell') -> list[dict]:
    orders = []
    max_pages = 1
    page = 1
    error_count = 0
    logger.info(f"Getting orders for region {region_id} with order type {order_type}")
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
            logger.error(f"page {page} of {max_pages} | request failed | {elapsed}s")
            error_count += 1
            if error_count > 5:
                logger.error(f"Too many errors: {error_count}")
                raise Exception(f"Too many errors: {error_count}")
            time.sleep(1)
            continue

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
            continue

        if order_page == []:
            logger.info("No more orders found")
            logger.info("--------------------------------\n\n")
            return orders
        else:
            for order in order_page:
                orders.append(order)

            page += 1
    logger.info(f"{len(orders)} orders fetched in {millify(time.time() - begin_time, precision=2)}s | {millify(len(orders)/(time.time() - begin_time), precision=2)} orders/s")
    logger.info("--------------------------------\n\n")
    return orders


def fetch_region_item_history(region_id: int, type_id: int) -> list[dict]:
    url = f"https://esi.evetech.net/latest/markets/{region_id}/history"
    querystring = {"type_id": type_id}

    headers = {
        "Accept-Language": "en",
        "If-None-Match": "",
        "X-Compatibility-Date": "2020-01-01",
        "X-Tenant": "tranquility",
        "Accept": "application/json",
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

if __name__ == "__main__":
    pass

