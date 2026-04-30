import os

from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.settings_service import SettingsService
import requests
import time
import json
import pandas as pd
import millify

logger = configure_logging(__name__)

_USER_AGENT = SettingsService().esi_user_agent

# Check if terminal output (progress prints) should be suppressed.
# Set MKTS_QUIET=1 in CI/GitHub Actions to disable progress output.
QUIET = os.environ.get("MKTS_QUIET", "0") == "1"


def fetch_market_orders(
    esi: ESIConfig,
    order_type: str = "all",
    page_etags: dict[int, str] | None = None,
    test_mode: bool = False,
    _clean_retry: bool = False,
) -> dict:
    """Fetch market orders with per-page etag support.

    Returns:
        {"status": 200, "data": [...], "page_etags": {1: "etag", ...}, "expires": "..."}
        {"status": 304}  — all pages unchanged
        None on fatal error
    """
    logger.info("Fetching market orders")
    page = 1
    # Seed max_pages from cached page count so 304s can iterate all known pages.
    # A 200 response will update max_pages from X-Pages header.
    max_pages = max(page_etags.keys()) if page_etags else 1
    orders = []
    error_count = 0
    request_count = 0
    new_page_etags: dict[int, str] = {}
    expires_value: str | None = None
    got_any_200 = False
    got_any_304 = False

    url = esi.market_orders_url
    headers = esi.headers

    while page <= max_pages:
        request_count += 1
        logger.debug(
            f"NEW REQUEST: request_count: {request_count}, page: {page}, max_pages: {max_pages}"
        )

        querystring = {"page": str(page)}

        # Add per-page etag if available
        page_headers = dict(headers)
        if page_etags and page in page_etags:
            page_headers["If-None-Match"] = page_etags[page]
            logger.debug(f"Page {page} request If-None-Match: {page_etags[page]}")
        else:
            # Remove any stale If-None-Match from base headers
            page_headers.pop("If-None-Match", None)
            logger.debug(f"Page {page} request: no etag (fresh request)")

        logger.debug(f"Page {page} request headers: {page_headers}")
        response = requests.get(url, headers=page_headers, params=querystring, timeout=10)
        logger.debug(
            f"Page {page} response: status={response.status_code}, "
            f"ETag={response.headers.get('ETag')}, "
            f"Expires={response.headers.get('Expires')}"
        )

        if response.status_code == 304:
            logger.debug(f"Page {page} returned 304 Not Modified")
            got_any_304 = True
            if test_mode:
                max_pages = 5
            page += 1
            continue

        response.raise_for_status()

        if response.status_code == 200:
            logger.debug(f"response successful: {response.status_code}")
            got_any_200 = True

            # Capture Expires and ETag headers
            if expires_value is None:
                expires_value = response.headers.get("Expires")
            resp_etag = response.headers.get("ETag")
            if resp_etag:
                new_page_etags[page] = resp_etag

            try:
                data = response.json()
            except requests.exceptions.JSONDecodeError:
                logger.warning(
                    f"Malformed JSON on page {page}, retrying once..."
                )
                time.sleep(1)
                response = requests.get(
                    url, headers=page_headers, params=querystring, timeout=10
                )
                response.raise_for_status()
                data = response.json()
                # Capture etag from retry too
                resp_etag = response.headers.get("ETag")
                if resp_etag:
                    new_page_etags[page] = resp_etag

            if test_mode:
                max_pages = 5
                logger.info(
                    f"test_mode: max_pages set to {max_pages}. current page: {page}/{max_pages}"
                )
            else:
                x_pages = response.headers.get("X-Pages")
                if x_pages:
                    max_pages = int(x_pages)
                # No X-Pages header: keep iterating until empty data stops us
                logger.debug(f"page: {page}, max_pages: {max_pages}")
        else:
            logger.error(f"Error fetching market orders: {response.status_code}")
            error_count += 1
            if error_count > 3:
                logger.error("Too many errors, stopping")
                return None
            else:
                logger.error(f"Retrying... {error_count} attempts")
                time.sleep(5)
                continue

        if data:
            orders.extend(data)
            page += 1
        else:
            logger.debug(
                f"Data retrieved for {page}/{max_pages}. total orders: {len(orders)}"
            )
            break
        logger.debug("-" * 60)

    # If any page returned 200 while others returned 304, page boundaries may have
    # shifted. Re-fetch all pages clean (without etags) to get a consistent dataset.
    if got_any_200 and got_any_304:
        if _clean_retry:
            logger.error("Mixed 200/304 on clean re-fetch; returning available data as-is")
        else:
            logger.info("Mixed 200/304 responses detected — re-fetching all pages clean")
            return fetch_market_orders(
                esi, order_type=order_type, page_etags=None,
                test_mode=test_mode, _clean_retry=True,
            )

    # All pages returned 304 — nothing changed
    if got_any_304 and not got_any_200:
        logger.info("All pages returned 304 Not Modified")
        return {"status": 304}

    logger.info(
        f"market_orders complete: {max_pages} pages. total orders: {len(orders)} orders"
    )
    logger.info("+=" * 40)
    return {
        "status": 200,
        "data": orders,
        "page_etags": new_page_etags,
        "expires": expires_value,
    }


def fetch_history(watchlist: pd.DataFrame) -> list[dict]:
    esi = ESIConfig("primary")
    url = esi.market_history_url
    error_count = 0
    total_time_taken = 0

    logger.info("Fetching history with standard fetch (non-concurrent)")
    if watchlist is None or watchlist.empty:
        logger.error("No watchlist provided or watchlist is empty")
        return None
    else:
        logger.info("Watchlist found")
        if not QUIET:
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
            if not QUIET:
                print(
                    f"\rFetching history for ({request_count}/{watchlist_length})",
                    end="",
                    flush=True,
                )
            t1 = time.perf_counter()
            response = requests.get(
                url, headers=headers, timeout=10, params=querystring
            )
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
                logger.error(
                    f"Error fetching history for {item_name}: {response.status_code}"
                )

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
        logger.info(
            f"time: {time_taken}s, average: {round(total_time_taken / request_count, 2)}s"
        )
        if time_taken < 0.25:
            time.sleep(0.5)
            if not QUIET:
                print(
                    f"sleeping for 0.5 seconds to avoid rate limiting. Time: {time_taken}s"
                )
    if history:
        logger.info(f"Successfully fetched {len(history)} total history records")
        with open("data/market_history.json", "w") as f:
            json.dump(history, f)
        return history
    else:
        logger.error("No history records found")
        return None


def fetch_region_orders(region_id: int, order_type: str = "sell") -> list[dict]:
    orders = []
    max_pages = 1
    page = 1
    error_count = 0
    logger.info(f"Getting orders for region {region_id} with order type {order_type}")
    begin_time = time.time()

    while page <= max_pages:
        status_code = None

        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        }
        base_url = f"https://esi.evetech.net/latest/markets/{region_id}/orders/?datasource=tranquility&order_type={order_type}&page={page}"
        start_time = time.time()
        try:
            response = requests.get(base_url, headers=headers, timeout=10)
            elapsed = millify(response.elapsed.total_seconds(), precision=2)
            status_code = response.status_code
        except requests.exceptions.Timeout as TimeoutError:
            logger.error(f"Timeout: {TimeoutError}")
            elapsed = millify(time.time() - start_time, precision=2)
            logger.error(f"Timeout: {page} of {max_pages} | {elapsed}s")
        except requests.exceptions.ConnectionError as ConnectionError:
            logger.error(f"Connection Error: {ConnectionError}")
            elapsed = millify(time.time() - start_time, precision=2)
            logger.error(f"Connection Error: {page} of {max_pages} | {elapsed}s")
        except requests.exceptions.RequestException as RequestException:
            logger.error(f"Request Error: {RequestException}")
            elapsed = millify(time.time() - start_time, precision=2)
            logger.error(f"Request Error: {page} of {max_pages} | {elapsed}s")

        if status_code and status_code != 200:
            logger.error(
                f"page {page} of {max_pages} | status: {status_code} | {elapsed}s"
            )
            error_count += 1
            if error_count > 5:
                logger.error(f"Error: {status_code}")
                logger.error(f"Error: {status_code}")
                raise Exception(f"Too many errors: {error_count}")
            time.sleep(1)
            continue
        elif status_code == 200:
            logger.info(
                f"page {page} of {max_pages} | status: {status_code} | {elapsed}s"
            )
        else:
            logger.error(f"page {page} of {max_pages} | request failed | {elapsed}s")
            error_count += 1
            if error_count > 5:
                logger.error(f"Too many errors: {error_count}")
                raise Exception(f"Too many errors: {error_count}")
            time.sleep(1)
            continue

        if status_code == 200:
            error_remain = response.headers.get("X-Error-Limit-Remain")
            if error_remain == "0":
                logger.critical(f"Too many errors: {error_count}")
                raise Exception(f"Too many errors: {error_count}")

            if response.headers.get("X-Pages"):
                max_pages = int(response.headers.get("X-Pages"))
            else:
                max_pages = 1

            try:
                order_page = response.json()
            except requests.exceptions.JSONDecodeError:
                logger.warning(
                    f"Malformed JSON on page {page} of {max_pages}, retrying once..."
                )
                time.sleep(1)
                response = requests.get(base_url, headers=headers, timeout=10)
                if response.status_code != 200:
                    error_count += 1
                    continue
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
    logger.info(
        f"{len(orders)} orders fetched in {millify(time.time() - begin_time, precision=2)}s | {millify(len(orders) / (time.time() - begin_time), precision=2)} orders/s"
    )
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
        "User-Agent": _USER_AGENT,
    }

    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"HTTP {response.status_code} for type_id {type_id}")
            return []
    except requests.exceptions.Timeout:
        logger.error(f"Timeout for type_id {type_id}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error for type_id {type_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error for type_id {type_id}: {e}")
        return []


if __name__ == "__main__":
    pass
