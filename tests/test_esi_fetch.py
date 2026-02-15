"""
Tests for ESI fetching in src/mkts_backend/esi/esi_requests.py.

Covers:
  - fetch_market_orders — paginated order fetching
  - fetch_history — sequential history fetching per type_id
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock


def _make_response(status_code=200, json_data=None, headers=None):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or []
    resp.headers = headers or {}
    resp.raise_for_status.return_value = None
    resp.elapsed = MagicMock()
    resp.elapsed.total_seconds.return_value = 0.1
    return resp


# ===== fetch_market_orders ==================================================

class TestFetchMarketOrders:

    def test_single_page(self, mock_esi_config):
        """One page of orders returns all orders."""
        orders = [
            {"order_id": 1, "type_id": 34, "price": 5.0},
            {"order_id": 2, "type_id": 35, "price": 10.0},
        ]
        resp = _make_response(json_data=orders, headers={"X-Pages": "1"})

        with patch("mkts_backend.esi.esi_requests.requests.get", return_value=resp):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config)

        assert len(result) == 2
        assert result[0]["order_id"] == 1

    def test_pagination(self, mock_esi_config):
        """Multi-page responses should concatenate orders from all pages."""
        page1 = [{"order_id": i, "type_id": 34, "price": 5.0} for i in range(10)]
        page2 = [{"order_id": i + 10, "type_id": 35, "price": 10.0} for i in range(5)]

        resp1 = _make_response(json_data=page1, headers={"X-Pages": "2"})
        resp2 = _make_response(json_data=page2, headers={"X-Pages": "2"})

        with patch("mkts_backend.esi.esi_requests.requests.get", side_effect=[resp1, resp2]):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config)

        assert len(result) == 15

    def test_test_mode_caps_pages(self, mock_esi_config):
        """test_mode=True should cap at 5 pages regardless of X-Pages header."""
        pages_data = [[{"order_id": i}] for i in range(5)]
        responses = [
            _make_response(json_data=data, headers={"X-Pages": "100"})
            for data in pages_data
        ]

        with patch("mkts_backend.esi.esi_requests.requests.get", side_effect=responses):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config, test_mode=True)

        assert len(result) == 5

    def test_empty_page_stops(self, mock_esi_config):
        """An empty data page should stop fetching and return collected orders."""
        page1 = [{"order_id": 1}]
        resp1 = _make_response(json_data=page1, headers={"X-Pages": "3"})
        resp2 = _make_response(json_data=[], headers={"X-Pages": "3"})

        with patch("mkts_backend.esi.esi_requests.requests.get", side_effect=[resp1, resp2]):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config)

        assert len(result) == 1


# ===== fetch_history ========================================================

class TestFetchHistory:

    def test_empty_watchlist_returns_none(self):
        """Empty or None watchlist → returns None."""
        with patch("mkts_backend.esi.esi_requests.ESIConfig") as MockESI:
            mock_esi = MagicMock()
            mock_esi.market_history_url = "https://esi.evetech.net/markets/10000003/history"
            mock_esi.headers.return_value = {"Accept": "application/json", "Authorization": "Bearer x"}
            MockESI.return_value = mock_esi

            from mkts_backend.esi.esi_requests import fetch_history
            assert fetch_history(None) is None
            assert fetch_history(pd.DataFrame()) is None

    def test_basic_fetch(self):
        """Processes watchlist items and returns history with type_name/type_id."""
        watchlist = pd.DataFrame({
            "type_id": [34],
            "type_name": ["Tritanium"],
        })
        history_data = [
            {"date": "2026-02-10", "average": 8.5, "volume": 2000},
            {"date": "2026-02-11", "average": 9.0, "volume": 1800},
        ]
        resp = _make_response(
            json_data=history_data,
            headers={"X-Esi-Error-Limit-Remain": "100"},
        )

        with patch("mkts_backend.esi.esi_requests.ESIConfig") as MockESI:
            mock_esi = MagicMock()
            mock_esi.market_history_url = "https://esi.evetech.net/markets/10000003/history"
            mock_esi.headers.return_value = {"Accept": "application/json", "Authorization": "Bearer test"}
            MockESI.return_value = mock_esi

            with patch("mkts_backend.esi.esi_requests.requests.get", return_value=resp):
                with patch("mkts_backend.esi.esi_requests.time.sleep"):
                    from mkts_backend.esi.esi_requests import fetch_history
                    result = fetch_history(watchlist)

        assert result is not None
        assert len(result) == 2
        # Each record should have type_name and type_id injected
        assert result[0]["type_name"] == "Tritanium"
        assert result[0]["type_id"] == 34
