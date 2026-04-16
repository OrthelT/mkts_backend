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
        resp = _make_response(json_data=orders, headers={"X-Pages": "1", "ETag": '"abc"', "Expires": "Thu, 01 Jan 2026 00:00:00 GMT"})

        with patch("mkts_backend.esi.esi_requests.requests.get", return_value=resp):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config)

        assert result["status"] == 200
        assert len(result["data"]) == 2
        assert result["data"][0]["order_id"] == 1

    def test_pagination(self, mock_esi_config):
        """Multi-page responses should concatenate orders from all pages."""
        page1 = [{"order_id": i, "type_id": 34, "price": 5.0} for i in range(10)]
        page2 = [{"order_id": i + 10, "type_id": 35, "price": 10.0} for i in range(5)]

        resp1 = _make_response(json_data=page1, headers={"X-Pages": "2", "ETag": '"e1"', "Expires": "Thu, 01 Jan 2026 00:00:00 GMT"})
        resp2 = _make_response(json_data=page2, headers={"X-Pages": "2", "ETag": '"e2"'})

        with patch("mkts_backend.esi.esi_requests.requests.get", side_effect=[resp1, resp2]):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config)

        assert result["status"] == 200
        assert len(result["data"]) == 15

    def test_test_mode_caps_pages(self, mock_esi_config):
        """test_mode=True should cap at 5 pages regardless of X-Pages header."""
        pages_data = [[{"order_id": i}] for i in range(5)]
        responses = [
            _make_response(json_data=data, headers={"X-Pages": "100", "ETag": f'"e{i}"', "Expires": "Thu, 01 Jan 2026 00:00:00 GMT"})
            for i, data in enumerate(pages_data)
        ]

        with patch("mkts_backend.esi.esi_requests.requests.get", side_effect=responses):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config, test_mode=True)

        assert result["status"] == 200
        assert len(result["data"]) == 5

    def test_empty_page_stops(self, mock_esi_config):
        """An empty data page should stop fetching and return collected orders."""
        page1 = [{"order_id": 1}]
        resp1 = _make_response(json_data=page1, headers={"X-Pages": "3", "ETag": '"e1"', "Expires": "Thu, 01 Jan 2026 00:00:00 GMT"})
        resp2 = _make_response(json_data=[], headers={"X-Pages": "3"})

        with patch("mkts_backend.esi.esi_requests.requests.get", side_effect=[resp1, resp2]):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config)

        assert result["status"] == 200
        assert len(result["data"]) == 1

    def test_all_304_returns_not_modified(self, mock_esi_config):
        """When all pages return 304, result should indicate no changes."""
        resp = _make_response(status_code=304, headers={})
        resp.raise_for_status.return_value = None

        with patch("mkts_backend.esi.esi_requests.requests.get", return_value=resp):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(mock_esi_config, page_etags={1: '"old_etag"'})

        assert result["status"] == 304

    def test_all_304_multi_page(self, mock_esi_config):
        """All cached pages returning 304 should probe every page, not just page 1."""
        resp_304 = _make_response(status_code=304, headers={})

        with patch("mkts_backend.esi.esi_requests.requests.get", return_value=resp_304) as mock_get:
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(
                mock_esi_config,
                page_etags={1: '"e1"', 2: '"e2"', 3: '"e3"'},
            )

        assert result["status"] == 304
        # Must have probed all 3 cached pages, not stopped after page 1
        assert mock_get.call_count == 3

    def test_mixed_200_304_triggers_clean_refetch(self, mock_esi_config):
        """When some pages return 304 and others 200, a clean re-fetch fires."""
        # First call: page 1 returns 304, page 2 returns 200 (mixed)
        resp_304 = _make_response(status_code=304, headers={})
        page2_data = [{"order_id": 10, "type_id": 34, "price": 5.0}]
        resp_200_p2 = _make_response(
            json_data=page2_data,
            headers={"X-Pages": "2", "ETag": '"e2_new"', "Expires": "Thu, 01 Jan 2026 00:00:00 GMT"},
        )

        # Clean re-fetch (no etags): both pages return 200
        page1_data = [{"order_id": 1, "type_id": 34, "price": 4.0}]
        resp_clean_p1 = _make_response(
            json_data=page1_data,
            headers={"X-Pages": "2", "ETag": '"e1_clean"', "Expires": "Thu, 01 Jan 2026 00:00:00 GMT"},
        )
        resp_clean_p2 = _make_response(
            json_data=page2_data,
            headers={"X-Pages": "2", "ETag": '"e2_clean"'},
        )

        responses = [resp_304, resp_200_p2, resp_clean_p1, resp_clean_p2]

        with patch("mkts_backend.esi.esi_requests.requests.get", side_effect=responses):
            from mkts_backend.esi.esi_requests import fetch_market_orders
            result = fetch_market_orders(
                mock_esi_config,
                page_etags={1: '"e1"', 2: '"e2"'},
            )

        # Clean re-fetch should return consistent 200 data from both pages
        assert result["status"] == 200
        assert len(result["data"]) == 2
        assert result["data"][0]["order_id"] == 1
        assert result["data"][1]["order_id"] == 10


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
            mock_esi.headers = {"Accept": "application/json", "Authorization": "Bearer test"}
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

    def test_basic_fetch_creates_data_directory(self, tmp_path, monkeypatch):
        """Successful history fetch should create data/ before writing market_history.json."""
        watchlist = pd.DataFrame({
            "type_id": [34],
            "type_name": ["Tritanium"],
        })
        history_data = [
            {"date": "2026-02-10", "average": 8.5, "volume": 2000},
        ]
        resp = _make_response(
            json_data=history_data,
            headers={"X-Esi-Error-Limit-Remain": "100"},
        )

        monkeypatch.chdir(tmp_path)

        with patch("mkts_backend.esi.esi_requests.ESIConfig") as MockESI:
            mock_esi = MagicMock()
            mock_esi.market_history_url = "https://esi.evetech.net/markets/10000003/history"
            mock_esi.headers = {"Accept": "application/json", "Authorization": "Bearer test"}
            MockESI.return_value = mock_esi

            with patch("mkts_backend.esi.esi_requests.requests.get", return_value=resp):
                with patch("mkts_backend.esi.esi_requests.time.sleep"):
                    from mkts_backend.esi.esi_requests import fetch_history
                    result = fetch_history(watchlist)

        assert result is not None
        assert (tmp_path / "data" / "market_history.json").exists()

    def test_basic_fetch_does_not_require_authenticated_headers(self):
        """Public history fetch should use public headers and not touch the authenticated headers property."""
        watchlist = pd.DataFrame({
            "type_id": [34],
            "type_name": ["Tritanium"],
        })
        resp = _make_response(
            json_data=[{"date": "2026-02-10", "average": 8.5, "volume": 2000}],
            headers={"X-Esi-Error-Limit-Remain": "100"},
        )

        class StubESI:
            market_history_url = "https://esi.evetech.net/markets/10000003/history"
            user_agent = "test-agent"
            compatibility_date = "2020-01-01"

            @property
            def headers(self):
                raise AssertionError("authenticated headers should not be used")

        with patch("mkts_backend.esi.esi_requests.ESIConfig", return_value=StubESI()):
            with patch("mkts_backend.esi.esi_requests.requests.get", return_value=resp):
                with patch("mkts_backend.esi.esi_requests.time.sleep"):
                    from mkts_backend.esi.esi_requests import fetch_history
                    result = fetch_history(watchlist)

        assert result is not None
        assert result[0]["type_id"] == 34
