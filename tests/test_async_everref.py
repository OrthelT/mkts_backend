from datetime import datetime, timezone
from unittest.mock import patch

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


class TestAsyncFetchBuilderCosts:
    @pytest.mark.asyncio
    async def test_partial_failures_abort_the_batch(self, in_memory_sde_db):
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
                results = await async_everref.async_fetch_builder_costs(
                    [34, 35],
                    {34: 1_000_000, 35: 1_000_000},
                    engine,
                    watchlist_metadata=watchlist_metadata,
                )
            finally:
                engine.dispose()

        assert results == []