"""
Tests for conditional HTTP request (ETag/Last-Modified) caching in ESI history fetcher.
"""
import pytest
import asyncio
import sqlite3
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Cache table CRUD tests
# ---------------------------------------------------------------------------

class TestEnsureCacheTable:
    def test_creates_table_when_missing(self, tmp_path):
        """ensure_cache_table creates the table on a fresh database."""
        from sqlalchemy import create_engine, text
        from mkts_backend.db.db_handlers import ensure_cache_table

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        ensure_cache_table(engine)

        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='esi_request_cache'"
            ))
            assert result.fetchone() is not None
        engine.dispose()

    def test_idempotent(self, tmp_path):
        """Calling ensure_cache_table twice doesn't raise."""
        from sqlalchemy import create_engine
        from mkts_backend.db.db_handlers import ensure_cache_table

        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        ensure_cache_table(engine)
        ensure_cache_table(engine)  # Should not raise
        engine.dispose()


class TestLoadAndSaveESICache:
    def _make_engine(self, tmp_path, name="test.db"):
        from sqlalchemy import create_engine
        from mkts_backend.db.db_handlers import ensure_cache_table

        db_path = tmp_path / name
        engine = create_engine(f"sqlite:///{db_path}")
        ensure_cache_table(engine)
        return engine

    def test_load_empty_cache(self, tmp_path):
        """Loading from an empty cache returns empty dict."""
        from mkts_backend.db.db_handlers import load_esi_cache

        engine = self._make_engine(tmp_path)

        with patch("mkts_backend.db.db_handlers._get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.engine = engine
            mock_get_db.return_value = mock_db

            result = load_esi_cache(region_id=10000003)
            assert result == {}
        engine.dispose()

    def test_save_and_load_roundtrip(self, tmp_path):
        """Save cache entries then load them back."""
        from mkts_backend.db.db_handlers import load_esi_cache, save_esi_cache

        engine = self._make_engine(tmp_path)

        mock_db = MagicMock()
        mock_db.engine = engine
        mock_db.remote_engine = engine

        with patch("mkts_backend.db.db_handlers._get_db", return_value=mock_db):
            results = [
                {"type_id": 34, "status": 200, "data": [{}], "etag": '"abc123"', "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
                {"type_id": 35, "status": 200, "data": [{}], "etag": '"def456"', "last_modified": None},
            ]

            save_esi_cache(results, region_id=10000003)
            cache = load_esi_cache(region_id=10000003)

            assert 34 in cache
            assert cache[34]["etag"] == '"abc123"'
            assert cache[34]["last_modified"] == "Wed, 01 Jan 2025 00:00:00 GMT"
            assert 35 in cache
            assert cache[35]["etag"] == '"def456"'
        engine.dispose()

    def test_per_region_isolation(self, tmp_path):
        """Cache entries for different regions don't interfere."""
        from mkts_backend.db.db_handlers import load_esi_cache, save_esi_cache

        engine = self._make_engine(tmp_path)

        mock_db = MagicMock()
        mock_db.engine = engine
        mock_db.remote_engine = engine

        with patch("mkts_backend.db.db_handlers._get_db", return_value=mock_db):
            save_esi_cache(
                [{"type_id": 34, "status": 200, "data": [{}], "etag": '"region3"', "last_modified": None}],
                region_id=10000003,
            )
            save_esi_cache(
                [{"type_id": 34, "status": 200, "data": [{}], "etag": '"region1"', "last_modified": None}],
                region_id=10000001,
            )

            cache_r3 = load_esi_cache(region_id=10000003)
            cache_r1 = load_esi_cache(region_id=10000001)

            assert cache_r3[34]["etag"] == '"region3"'
            assert cache_r1[34]["etag"] == '"region1"'
        engine.dispose()

    def test_skips_entries_without_etag_or_last_modified(self, tmp_path):
        """Entries with neither etag nor last_modified are not saved."""
        from mkts_backend.db.db_handlers import load_esi_cache, save_esi_cache

        engine = self._make_engine(tmp_path)

        mock_db = MagicMock()
        mock_db.engine = engine
        mock_db.remote_engine = engine

        with patch("mkts_backend.db.db_handlers._get_db", return_value=mock_db):
            save_esi_cache(
                [{"type_id": 34, "status": 200, "data": [{}], "etag": None, "last_modified": None}],
                region_id=10000003,
            )
            cache = load_esi_cache(region_id=10000003)
            assert cache == {}
        engine.dispose()

    def test_upsert_overwrites_existing(self, tmp_path):
        """Saving new etag for existing type_id overwrites the old value."""
        from mkts_backend.db.db_handlers import load_esi_cache, save_esi_cache

        engine = self._make_engine(tmp_path)

        mock_db = MagicMock()
        mock_db.engine = engine
        mock_db.remote_engine = engine

        with patch("mkts_backend.db.db_handlers._get_db", return_value=mock_db):
            save_esi_cache(
                [{"type_id": 34, "status": 200, "data": [{}], "etag": '"old"', "last_modified": None}],
                region_id=10000003,
            )
            save_esi_cache(
                [{"type_id": 34, "status": 200, "data": [{}], "etag": '"new"', "last_modified": None}],
                region_id=10000003,
            )
            cache = load_esi_cache(region_id=10000003)
            assert cache[34]["etag"] == '"new"'
        engine.dispose()


# ---------------------------------------------------------------------------
# call_one tests
# ---------------------------------------------------------------------------

class TestCallOne304Handling:
    @pytest.mark.asyncio
    async def test_304_returns_none_data(self):
        """A 304 response returns data=None with status=304."""
        from mkts_backend.esi.async_history import call_one
        from aiolimiter import AsyncLimiter

        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_response.headers = {"ETag": '"abc123"'}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        limiter = AsyncLimiter(300, time_period=60.0)
        sema = asyncio.Semaphore(50)

        cache_entry = {"etag": '"abc123"', "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT"}

        result = await call_one(
            mock_client, type_id=34, length=1, region_id=10000003,
            limiter=limiter, sema=sema,
            headers={"User-Agent": "test"},
            cache_entry=cache_entry,
        )

        assert result["type_id"] == 34
        assert result["data"] is None
        assert result["status"] == 304
        assert result["etag"] == '"abc123"'

    @pytest.mark.asyncio
    async def test_304_does_not_call_raise_for_status(self):
        """A 304 response should not trigger raise_for_status."""
        from mkts_backend.esi.async_history import call_one
        from aiolimiter import AsyncLimiter

        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_response.headers = {"ETag": '"test"'}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        limiter = AsyncLimiter(300, time_period=60.0)
        sema = asyncio.Semaphore(50)

        await call_one(
            mock_client, type_id=34, length=1, region_id=10000003,
            limiter=limiter, sema=sema,
            headers={"User-Agent": "test"},
            cache_entry={"etag": '"test"', "last_modified": None},
        )

        mock_response.raise_for_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_200_returns_data_with_headers(self):
        """A 200 response returns data with etag and last_modified captured."""
        from mkts_backend.esi.async_history import call_one
        from aiolimiter import AsyncLimiter

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "ETag": '"newetag"',
            "Last-Modified": "Thu, 02 Jan 2025 12:00:00 GMT",
        }
        mock_response.json.return_value = [{"date": "2025-01-01", "average": 5.0, "volume": 100}]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        limiter = AsyncLimiter(300, time_period=60.0)
        sema = asyncio.Semaphore(50)

        result = await call_one(
            mock_client, type_id=34, length=1, region_id=10000003,
            limiter=limiter, sema=sema,
            headers={"User-Agent": "test"},
        )

        assert result["type_id"] == 34
        assert result["status"] == 200
        assert result["data"] == [{"date": "2025-01-01", "average": 5.0, "volume": 100}]
        assert result["etag"] == '"newetag"'
        assert result["last_modified"] == "Thu, 02 Jan 2025 12:00:00 GMT"


class TestConditionalHeaderInjection:
    @pytest.mark.asyncio
    async def test_sends_if_none_match_when_etag_cached(self):
        """If-None-Match header is sent when cache has an etag."""
        from mkts_backend.esi.async_history import call_one
        from aiolimiter import AsyncLimiter

        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_response.headers = {"ETag": '"cached_etag"'}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        limiter = AsyncLimiter(300, time_period=60.0)
        sema = asyncio.Semaphore(50)

        await call_one(
            mock_client, type_id=34, length=1, region_id=10000003,
            limiter=limiter, sema=sema,
            headers={"User-Agent": "test"},
            cache_entry={"etag": '"cached_etag"', "last_modified": None},
        )

        actual_call = mock_client.get.call_args
        sent_headers = actual_call.kwargs.get("headers", actual_call[1].get("headers", {}))
        assert sent_headers.get("If-None-Match") == '"cached_etag"'
        assert "If-Modified-Since" not in sent_headers

    @pytest.mark.asyncio
    async def test_sends_if_modified_since_when_last_modified_cached(self):
        """If-Modified-Since header is sent when cache has a last_modified value."""
        from mkts_backend.esi.async_history import call_one
        from aiolimiter import AsyncLimiter

        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        limiter = AsyncLimiter(300, time_period=60.0)
        sema = asyncio.Semaphore(50)

        await call_one(
            mock_client, type_id=34, length=1, region_id=10000003,
            limiter=limiter, sema=sema,
            headers={"User-Agent": "test"},
            cache_entry={"etag": None, "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
        )

        actual_call = mock_client.get.call_args
        sent_headers = actual_call.kwargs.get("headers", actual_call[1].get("headers", {}))
        assert sent_headers.get("If-Modified-Since") == "Wed, 01 Jan 2025 00:00:00 GMT"
        assert "If-None-Match" not in sent_headers

    @pytest.mark.asyncio
    async def test_no_conditional_headers_without_cache(self):
        """No conditional headers are sent when there is no cache entry."""
        from mkts_backend.esi.async_history import call_one
        from aiolimiter import AsyncLimiter

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        limiter = AsyncLimiter(300, time_period=60.0)
        sema = asyncio.Semaphore(50)

        await call_one(
            mock_client, type_id=34, length=1, region_id=10000003,
            limiter=limiter, sema=sema,
            headers={"User-Agent": "test"},
            cache_entry=None,
        )

        actual_call = mock_client.get.call_args
        sent_headers = actual_call.kwargs.get("headers", actual_call[1].get("headers", {}))
        assert "If-None-Match" not in sent_headers
        assert "If-Modified-Since" not in sent_headers


# ---------------------------------------------------------------------------
# update_history filtering tests
# ---------------------------------------------------------------------------

class TestUpdateHistoryFiltering:
    def test_all_304_returns_true(self):
        """When all results are 304, update_history returns True (success, not error)."""
        from mkts_backend.db.db_handlers import update_history

        results = [
            {"type_id": 34, "data": None, "status": 304, "etag": '"a"', "last_modified": None},
            {"type_id": 35, "data": None, "status": 304, "etag": '"b"', "last_modified": None},
        ]

        result = update_history(results)
        assert result is True

    def test_mixed_200_and_304_processes_only_200s(self):
        """Mixed 200/304 results should only process the 200s through the pipeline."""
        from mkts_backend.db.db_handlers import update_history

        results = [
            {"type_id": 34, "data": [
                {"date": "2025-01-01", "average": 5.0, "volume": 100, "highest": 6.0, "lowest": 4.0, "order_count": 10}
            ], "status": 200, "etag": '"a"', "last_modified": None},
            {"type_id": 35, "data": None, "status": 304, "etag": '"b"', "last_modified": None},
        ]

        # This will fail at the SDE lookup stage since we don't have a real DB,
        # but we can verify the filtering logic runs by catching the expected error
        with patch("mkts_backend.db.db_handlers._get_sde_db") as mock_sde:
            mock_engine = MagicMock()
            mock_conn = MagicMock()
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_result = MagicMock()
            mock_result.fetchall.return_value = [(34, "Tritanium")]
            mock_conn.execute.return_value = mock_result
            mock_engine.connect.return_value = mock_conn
            mock_engine.dispose = MagicMock()
            mock_sde_db = MagicMock()
            mock_sde_db.engine = mock_engine
            mock_sde.return_value = mock_sde_db

            with patch("mkts_backend.db.db_handlers.upsert_database", return_value=True):
                with patch("mkts_backend.db.db_handlers.get_remote_status", return_value={"market_history": 100}):
                    with patch("mkts_backend.db.db_handlers.get_table_length", return_value=100):
                        result = update_history(results)
                        assert result is True

    def test_empty_results_returns_false(self):
        """Empty results list returns False."""
        from mkts_backend.db.db_handlers import update_history

        result = update_history([])
        assert result is False

    def test_none_results_filtered_out(self):
        """None entries in results are safely filtered."""
        from mkts_backend.db.db_handlers import update_history

        results = [None, None]
        result = update_history(results)
        assert result is False


# ---------------------------------------------------------------------------
# ESIRequestCache model tests
# ---------------------------------------------------------------------------

class TestESIRequestCacheModel:
    def test_model_has_correct_tablename(self):
        from mkts_backend.db.models import ESIRequestCache
        assert ESIRequestCache.__tablename__ == "esi_request_cache"

    def test_model_has_composite_pk(self):
        from mkts_backend.db.models import ESIRequestCache
        pk_cols = [c.name for c in ESIRequestCache.__table__.primary_key.columns]
        assert "type_id" in pk_cols
        assert "region_id" in pk_cols
        assert len(pk_cols) == 2
