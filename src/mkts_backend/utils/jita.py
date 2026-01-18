"""
Jita price utilities for fetching and working with Jita market prices.

Uses the Fuzzwork Market API for efficient bulk price lookups.
"""

import requests
from typing import Dict, List, Optional

from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)

# Fuzzwork Market API endpoint for aggregated market data
FUZZWORK_API_URL = "https://market.fuzzwork.co.uk/aggregates/"

# The Forge region ID (Jita's region)
JITA_REGION_ID = 10000002


class JitaPrice:
    def __init__(self, type_id: int, price_data: dict):
        self.type_id = type_id
        self.buy_percentile = float(price_data['buy']['percentile'])
        self.buy_median = float(price_data['buy']['median'])
        self.buy_min = float(price_data['buy']['min'])
        self.sell_percentile = float(price_data['sell']['percentile'])
        self.sell_median = float(price_data['sell']['median'])
        self.sell_max = float(price_data['sell']['max'])
        self.sell_min = float(price_data['sell']['min'])
        self.sell_volume = float(price_data['sell']['volume'])
        self.buy_volume = float(price_data['buy']['volume'])
        self.buy_weightedAverage = float(price_data['buy']['weightedAverage'])

    def get_price_data(self) -> dict:
        return {
            'type_id': self.type_id,
            'sell_percentile': self.sell_percentile,
            'buy_percentile': self.buy_percentile
        }


def fetch_jita_prices(type_ids: List[int]) -> Dict[int, Optional[float]]:
    """
    Fetch Jita sell prices for a list of type IDs using Fuzzwork Market API.

    Uses the sell percentile (5th percentile of sell orders) as the reference price,
    which represents a reasonable buy price in Jita.

    Args:
        type_ids: List of type IDs to fetch prices for

    Returns:
        Dict mapping type_id to sell_percentile price (or None if not found)
    """
    if not type_ids:
        return {}

    results = {}

    # Fuzzwork API accepts comma-separated type IDs
    type_ids_str = ",".join(str(tid) for tid in type_ids)

    headers = {
        'User-Agent': 'wcmkts_backend/2.1, orthel.toralen@gmail.com',
        'Accept': 'application/json',
    }

    try:
        params = {
            'region': JITA_REGION_ID,
            'types': type_ids_str,
        }

        response = requests.get(FUZZWORK_API_URL, headers=headers, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()

        for type_id_str, price_data in data.items():
            type_id = int(type_id_str)
            try:
                # Use sell percentile (5th percentile) as the reference Jita price
                sell_percentile = float(price_data['sell']['percentile'])
                # Only use valid prices (non-zero)
                if sell_percentile > 0:
                    results[type_id] = sell_percentile
                else:
                    results[type_id] = None
            except (KeyError, ValueError, TypeError):
                results[type_id] = None

    except requests.exceptions.RequestException as e:
        logger.warning(f"Failed to fetch Jita prices: {e}")
        # Return None for all type_ids on failure
        for type_id in type_ids:
            results[type_id] = None

    # Fill in any missing type_ids with None
    for type_id in type_ids:
        if type_id not in results:
            results[type_id] = None

    return results


def get_overpriced_items(
    market_data: List[Dict],
    threshold: float = 1.2,
) -> List[Dict]:
    """
    Get items whose local market price exceeds the Jita price by a threshold.

    Args:
        market_data: List of item dicts with 'price' and 'jita_price' keys
        threshold: Price ratio threshold (1.2 = 120% of Jita price)

    Returns:
        List of overpriced items with price comparison data
    """
    overpriced = []

    for item in market_data:
        local_price = item.get("price")
        jita_price = item.get("jita_price")

        if local_price and jita_price and jita_price > 0:
            price_ratio = local_price / jita_price
            if price_ratio > threshold:
                overpriced.append({
                    "type_id": item.get("type_id"),
                    "type_name": item.get("type_name"),
                    "local_price": local_price,
                    "jita_price": jita_price,
                    "price_ratio": price_ratio,
                    "percent_above_jita": (price_ratio - 1) * 100,
                })

    # Sort by price ratio (highest first)
    overpriced.sort(key=lambda x: x["price_ratio"], reverse=True)

    return overpriced


if __name__ == "__main__":
    pass
