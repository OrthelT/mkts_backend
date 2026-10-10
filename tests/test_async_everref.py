import asyncio
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import create_engine


class TestResolveApiParams:
    def test_t1_items_use_default_me_and_runs(self):
        from mkts_backend.esi.async_everref import _resolve_api_params

        result = _resolve_api_params(
            meta_group_id=1,
            category_id=7,
            group_name="Shield Extender",
            type_name="Large Shield Extender I",
            jita_price=1_500_000,
        )

        assert result == (10, 10)

    def test_high_value_t2_modules_use_conservative_run_count(self):
        from mkts_backend.esi.async_everref import _resolve_api_params

        result = _resolve_api_params(
            meta_group_id=2,
            category_id=7,
            group_name="Shield Extender",
            type_name="Large Shield Extender II",
            jita_price=45_000_000,
        )

        assert result == (4, 5)

    def test_non_manufacturable_and_excluded_items_are_skipped(self):
        from mkts_backend.esi.async_everref import _resolve_api_params

        assert _resolve_api_params(5, 7, "Shield Extender", "Large Shield Extender II", 10) is None
        assert _resolve_api_params(2, 7, "Interdiction Nullifier", "Interdiction Nullifier II", 10) is None
        assert _resolve_api_params(14, 6, "Cruiser", "Vedmak", 10) is None


class TestParseIsoDuration:
    def test_parses_iso_duration_to_seconds(self):
        from mkts_backend.esi.async_everref import _parse_iso_duration

        assert _parse_iso_duration("PT24H20M48.1S") == 87648.1

    def test_invalid_duration_returns_none(self):
        from mkts_backend.esi.async_everref import _parse_iso_duration

        assert _parse_iso_duration("not-a-duration") is None


class TestGetMetaGroups:
    def test_reads_meta_groups_from_sdetypes(self, in_memory_sde_db):
        from mkts_backend.esi.async_everref import _get_meta_groups

        engine = create_engine(f"sqlite:///{in_memory_sde_db}")
        try:
            result = _get_meta_groups([34, 35, 999999], engine)
        finally:
            engine.dispose()

        assert result == {34: 1, 35: 1}

    def test_filters_out_items_with_no_manufacturing_blueprint(self, in_memory_sde_db):
        """type_id 36 (Mexallon) exists in sdetypes but has no industryActivityProducts row.

        Mirrors EverRef HTTP 400 "not produced from a blueprint" for meta-T1 NPC
        drops; filtering at the SDE saves wasted rate-limited requests.
        """
        from mkts_backend.esi.async_everref import _get_meta_groups

        engine = create_engine(f"sqlite:///{in_memory_sde_db}")
        try:
            result = _get_meta_groups([34, 35, 36], engine)
        finally:
            engine.dispose()

        assert 36 not in result
        assert result == {34: 1, 35: 1}


class TestFilterBuildable:
    def test_returns_set_of_buildable_type_ids(self, in_memory_sde_db):
        from mkts_backend.esi.async_everref import filter_buildable

        engine = create_engine(f"sqlite:///{in_memory_sde_db}")
        try:
            result = filter_buildable([34, 35, 36, 999999], engine)
        finally:
            engine.dispose()

        # 34 and 35 are in industryActivityProducts; 36 and 999999 are not.
        assert result == {34, 35}

    def test_empty_input_returns_empty_set(self, in_memory_sde_db):
        from mkts_backend.esi.async_everref import filter_buildable

        engine = create_engine(f"sqlite:///{in_memory_sde_db}")
        try:
            assert filter_buildable([], engine) == set()
        finally:
            engine.dispose()


class TestAsyncFetchBuilderCosts:
    @pytest.mark.asyncio
    async def test_partial_failures_persist_successful_subset(self, in_memory_sde_db):
        """A single failure no longer poisons the whole run.

        The rolling refresh and elevated rate limit make full-run failures
        more impactful, so per-item failures degrade gracefully: successful
        results are returned and persisted, failures are logged and skipped.
        """
        from mkts_backend.esi import async_everref

        engine = create_engine(f"sqlite:///{in_memory_sde_db}")
        watchlist_metadata = {
            34: {
                "category_id": 7,
                "group_name": "Shield Extender",
                "type_name": "Large Shield Extender I",
            },
            35: {
                "category_id": 7,
                "group_name": "Shield Extender",
                "type_name": "Large Shield Extender I",
            },
        }

        with patch.object(
            async_everref,
            "_fetch_one",
            side_effect=[
                {
                    "type_id": 34,
                    "total_cost_per_unit": 123.4,
                    "time_per_unit": 60.0,
                    "me": 10,
                    "runs": 10,
                    "fetched_at": datetime(2026, 4, 17, tzinfo=timezone.utc),
                },
                None,
            ],
        ):
            try:
                summary = await async_everref.async_fetch_builder_costs(
                    [34, 35],
                    {34: 1_000_000, 35: 1_000_000},
                    engine,
                    watchlist_metadata=watchlist_metadata,
                )
            finally:
                engine.dispose()

        assert summary.attempted == 2
        assert summary.succeeded == 1
        assert summary.failed == 1
        assert len(summary.records) == 1
        assert summary.records[0]["type_id"] == 34


class TestRetryDelay:
    def test_numeric_retry_after(self):
        from mkts_backend.esi.async_everref import _retry_delay

        assert _retry_delay("0.25", attempt=0) == 0.25

    def test_http_date_retry_after(self):
        from mkts_backend.esi.async_everref import _retry_delay

        retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)

        assert 28 <= _retry_delay(format_datetime(retry_at, usegmt=True), 0) <= 30

    def test_retry_after_is_capped(self):
        from mkts_backend.esi.async_everref import MAX_RETRY_DELAY, _retry_delay

        far = datetime.now(timezone.utc) + timedelta(days=1)

        assert _retry_delay("3600", attempt=0) == MAX_RETRY_DELAY
        assert _retry_delay(format_datetime(far, usegmt=True), 0) == MAX_RETRY_DELAY

    @pytest.mark.parametrize(
        "retry_after",
        [None, "nan", "inf", "-5", "soon", "Wed, 21 Oct 2015 07:28:00 GMT"],
    )
    def test_missing_or_invalid_retry_after_uses_exponential_backoff(
        self, retry_after
    ):
        from mkts_backend.esi.async_everref import _retry_delay

        assert _retry_delay(retry_after, attempt=0) == 1
        assert _retry_delay(retry_after, attempt=2) == 4


class TestThrottle:
    @pytest.mark.asyncio
    async def test_wait_blocks_until_pause_ends(self):
        from mkts_backend.esi import async_everref

        with patch.object(async_everref, "EVEREF_REQUESTS_PER_MINUTE", 60_000):
            throttle = async_everref._Throttle()
        await throttle.wait()
        assert throttle.pause(0.1)

        start = time.monotonic()
        await throttle.wait()

        assert time.monotonic() - start >= 0.09

    @pytest.mark.asyncio
    async def test_pause_refuses_once_budget_is_spent(self):
        from mkts_backend.esi import async_everref

        with patch.object(async_everref, "RATE_LIMIT_PAUSE_BUDGET", 10.0):
            throttle = async_everref._Throttle()

        assert throttle.pause(8.0)
        # Already paused for longer than this, so it costs no budget.
        assert throttle.pause(5.0)
        assert not throttle.pause(60.0)


class FakeThrottle:
    def __init__(self, allow_pause=True):
        self.allow_pause = allow_pause
        self.pauses = []

    async def wait(self):
        return None

    def pause(self, delay):
        self.pauses.append(delay)
        return self.allow_pause


class FakeClient:
    def __init__(self, responses):
        self.get = AsyncMock(side_effect=responses)


OK_RESPONSE_JSON = {
    "manufacturing": {
        "34": {"total_cost_per_unit": 123.4, "time_per_unit": "PT1M"}
    }
}


async def _fetch(client, throttle):
    from mkts_backend.esi import async_everref

    return await async_everref._fetch_one_inner(
        client, asyncio.Semaphore(1), throttle, type_id=34, me=10, runs=10
    )


class TestFetchOneRateLimitRetry:
    @pytest.mark.asyncio
    async def test_retries_429_using_retry_after(self, caplog):
        client = FakeClient(
            [
                httpx.Response(429, headers={"Retry-After": "0.25"}),
                httpx.Response(200, json=OK_RESPONSE_JSON),
            ]
        )
        throttle = FakeThrottle()

        with caplog.at_level("WARNING"):
            result = await _fetch(client, throttle)

        assert result["type_id"] == 34
        assert client.get.await_count == 2
        assert throttle.pauses == [0.25]
        assert "HTTP 429 for 34; pausing all requests for 0.2s" in caplog.text

    @pytest.mark.asyncio
    async def test_returns_none_after_retries_are_exhausted(self):
        from mkts_backend.esi.async_everref import MAX_RATE_LIMIT_RETRIES

        client = FakeClient(
            [httpx.Response(429) for _ in range(MAX_RATE_LIMIT_RETRIES + 1)]
        )
        throttle = FakeThrottle()

        assert await _fetch(client, throttle) is None
        assert client.get.await_count == MAX_RATE_LIMIT_RETRIES + 1
        assert throttle.pauses == [1, 2, 4]

    @pytest.mark.asyncio
    async def test_does_not_retry_once_pause_budget_is_spent(self):
        client = FakeClient([httpx.Response(429)])

        assert await _fetch(client, FakeThrottle(allow_pause=False)) is None
        assert client.get.await_count == 1
