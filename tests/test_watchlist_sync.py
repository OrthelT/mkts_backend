"""Unit tests for builder_costs.watchlist_sync.

Uses real in-memory SQLite databases for buildcost (build_watchlist + builder_costs)
and reuses the existing in_memory_sde_db fixture for SDE metadata + buildable filter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture
def buildcost_db(tmp_path):
    """Create a buildcost SQLite DB with build_watchlist + builder_costs tables.

    Returns a DatabaseConfig-like stub whose .engine and .remote_engine both
    point at the same file-backed SQLite — the codepaths we exercise treat
    them as separate engines (writes to remote, reads from local).
    """
    from mkts_backend.db.build_cost_models import BuildCostBase

    db_path = tmp_path / "buildcost.db"
    engine = create_engine(f"sqlite:///{db_path}")
    BuildCostBase.metadata.create_all(engine)

    class _Stub:
        alias = "buildcost"

        def __init__(self, path):
            self._url = f"sqlite:///{path}"

        @property
        def engine(self):
            return create_engine(self._url)

        @property
        def remote_engine(self):
            return create_engine(self._url)

    yield _Stub(db_path)
    engine.dispose()


@pytest.fixture
def sde_db(in_memory_sde_db):
    """Wrap the in_memory_sde_db path in the same DatabaseConfig-like stub."""

    class _Stub:
        alias = "sde"

        def __init__(self, path):
            self._url = f"sqlite:///{path}"

        @property
        def engine(self):
            return create_engine(self._url)

    return _Stub(in_memory_sde_db)


@pytest.fixture
def primary_market_db(tmp_path):
    """Stub primary market DB with a 'watchlist' table containing a few type_ids."""
    db_path = tmp_path / "wcmktprod.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY)"))
        for tid in (34, 35, 36):  # 34/35 buildable, 36 not buildable
            conn.execute(
                text("INSERT INTO watchlist (type_id) VALUES (:t)"), {"t": tid}
            )
        conn.commit()
    engine.dispose()

    class _Stub:
        alias = "primary"

        def __init__(self, path):
            self._url = f"sqlite:///{path}"

        @property
        def engine(self):
            return create_engine(self._url)

    return _Stub(db_path)


def _read_all(db) -> list[dict]:
    with db.engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT type_id, type_name, group_name, category_id "
                    "FROM build_watchlist ORDER BY type_id"
                )
            ).mappings()
        ]


class TestAddToBuildWatchlist:
    def test_buildable_items_added_unbuildable_skipped(self, buildcost_db, sde_db):
        from mkts_backend.builder_costs.watchlist_sync import add_to_build_watchlist

        result = add_to_build_watchlist(buildcost_db, sde_db, [34, 35, 36])

        assert result.added == 2
        assert sorted(result.skipped) == [36]
        assert result.invalid == []

        rows = _read_all(buildcost_db)
        assert {r["type_id"] for r in rows} == {34, 35}
        assert {r["type_name"] for r in rows} == {"Tritanium", "Pyerite"}

    def test_force_bypasses_buildable_filter(self, buildcost_db, sde_db):
        from mkts_backend.builder_costs.watchlist_sync import add_to_build_watchlist

        result = add_to_build_watchlist(
            buildcost_db, sde_db, [34, 36], force=True
        )

        assert result.added == 2
        assert result.skipped == []
        assert result.invalid == []
        assert {r["type_id"] for r in _read_all(buildcost_db)} == {34, 36}

    def test_unknown_type_ids_reported_as_invalid(self, buildcost_db, sde_db):
        from mkts_backend.builder_costs.watchlist_sync import add_to_build_watchlist

        result = add_to_build_watchlist(buildcost_db, sde_db, [34, 999999])

        assert result.added == 1
        assert result.invalid == [999999]
        assert {r["type_id"] for r in _read_all(buildcost_db)} == {34}

    def test_re_adding_existing_item_is_idempotent(self, buildcost_db, sde_db):
        """Primary key + ON CONFLICT DO UPDATE means re-adds don't duplicate."""
        from mkts_backend.builder_costs.watchlist_sync import add_to_build_watchlist

        add_to_build_watchlist(buildcost_db, sde_db, [34])
        add_to_build_watchlist(buildcost_db, sde_db, [34, 35])

        rows = _read_all(buildcost_db)
        assert sorted(r["type_id"] for r in rows) == [34, 35]

    def test_input_dedup_collapses_repeats(self, buildcost_db, sde_db):
        """--type_id=34,34,34 collapses to one row before any DB work."""
        from mkts_backend.builder_costs.watchlist_sync import add_to_build_watchlist

        result = add_to_build_watchlist(buildcost_db, sde_db, [34, 34, 34])

        assert result.added == 1
        assert len(_read_all(buildcost_db)) == 1

    def test_empty_input_no_op(self, buildcost_db, sde_db):
        from mkts_backend.builder_costs.watchlist_sync import add_to_build_watchlist

        result = add_to_build_watchlist(buildcost_db, sde_db, [])

        assert result.added == 0
        assert _read_all(buildcost_db) == []


class TestRemoveFromBuildWatchlist:
    def test_removes_present_items_and_reports_missing(self, buildcost_db, sde_db):
        from mkts_backend.builder_costs.watchlist_sync import (
            add_to_build_watchlist,
            remove_from_build_watchlist,
        )

        add_to_build_watchlist(buildcost_db, sde_db, [34, 35])
        result = remove_from_build_watchlist(buildcost_db, [34, 999999])

        assert result.removed == 1
        assert result.not_present == [999999]
        assert {r["type_id"] for r in _read_all(buildcost_db)} == {35}

    def test_idempotent_when_nothing_present(self, buildcost_db):
        from mkts_backend.builder_costs.watchlist_sync import remove_from_build_watchlist

        result = remove_from_build_watchlist(buildcost_db, [34, 35])

        assert result.removed == 0
        assert sorted(result.not_present) == [34, 35]


class TestSyncFromMarket:
    def test_adds_only_market_items_missing_from_buildcost(
        self, buildcost_db, sde_db, primary_market_db
    ):
        from mkts_backend.builder_costs.watchlist_sync import (
            add_to_build_watchlist,
            sync_from_market,
        )

        # Pre-seed buildcost with type_id 34 only.
        add_to_build_watchlist(buildcost_db, sde_db, [34])

        result = sync_from_market(buildcost_db, sde_db, primary_market_db)

        # Market has 34, 35, 36. 34 already present. 35 added. 36 skipped (no blueprint).
        assert result.market_size == 3
        assert result.already_present == 1
        assert result.added == 1
        assert sorted(result.skipped) == [36]
        assert {r["type_id"] for r in _read_all(buildcost_db)} == {34, 35}

    def test_already_synced_is_no_op(
        self, buildcost_db, sde_db, primary_market_db
    ):
        from mkts_backend.builder_costs.watchlist_sync import (
            add_to_build_watchlist,
            sync_from_market,
        )

        add_to_build_watchlist(buildcost_db, sde_db, [34, 35])
        # 36 is in market but not buildable, so a fully-synced state still has it as missing.
        # First sync writes nothing extra (35 already present, 36 skipped):
        result = sync_from_market(buildcost_db, sde_db, primary_market_db)

        assert result.added == 0
        assert sorted(result.skipped) == [36]


class TestBuilderCostsRunner:
    def test_backfills_missing_watchlist_metadata_before_fetch(
        self, buildcost_db, sde_db, primary_market_db, monkeypatch
    ):
        from mkts_backend.builder_costs import runner

        type_id = 400001
        fresh_time = datetime(2026, 5, 7, tzinfo=timezone.utc)

        with sde_db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sdetypes VALUES ("
                    ":type_id, :type_name, :group_id, :group_name, "
                    ":category_id, :category_name, :volume, :meta_group_id, :meta_group_name"
                    ")"
                ),
                {
                    "type_id": type_id,
                    "type_name": "Large Shield Extender I",
                    "group_id": 38,
                    "group_name": "Shield Extender",
                    "category_id": 7,
                    "category_name": "Module",
                    "volume": 5.0,
                    "meta_group_id": 1,
                    "meta_group_name": "Tech I",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO industryActivityProducts VALUES ("
                    ":blueprint_type_id, :activity_id, :product_type_id, :quantity"
                    ")"
                ),
                {
                    "blueprint_type_id": 900001,
                    "activity_id": 1,
                    "product_type_id": type_id,
                    "quantity": 1,
                },
            )

        buildcost_db.verify_db_exists = lambda: True
        sde_db.verify_db_exists = lambda: True
        primary_market_db.verify_db_exists = lambda: True

        monkeypatch.setattr(
            runner,
            "DatabaseConfig",
            lambda alias: {
                "buildcost": buildcost_db,
                "sde": sde_db,
                "primary": primary_market_db,
            }[alias],
        )
        monkeypatch.setattr(runner, "init_buildcost_tables", lambda db: None)
        monkeypatch.setattr(
            runner,
            "read_build_watchlist",
            lambda db: [
                {
                    "type_id": type_id,
                    "type_name": None,
                    "group_name": None,
                    "category_id": None,
                }
            ],
        )
        monkeypatch.setattr(runner, "read_jita_prices", lambda db: {})

        fetch_mock = AsyncMock(
            return_value={
                "type_id": type_id,
                "total_cost_per_unit": 150.0,
                "time_per_unit": 90.0,
                "me": 10,
                "runs": 10,
                "fetched_at": fresh_time,
            }
        )

        with patch("mkts_backend.esi.async_everref._fetch_one", new=fetch_mock):
            result = runner.run()

        assert result.success is True
        assert result.fetched == 1
        assert fetch_mock.await_count == 1

        with buildcost_db.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT type_id, total_cost_per_unit, fetched_at "
                    "FROM builder_costs WHERE type_id = :type_id"
                ),
                {"type_id": type_id},
            ).mappings().one()

        assert row["type_id"] == type_id
        assert row["total_cost_per_unit"] == 150.0

    def test_prunes_builder_cost_rows_missing_from_build_watchlist(
        self, buildcost_db, sde_db, primary_market_db, monkeypatch
    ):
        from mkts_backend.builder_costs import runner
        from mkts_backend.esi.async_everref import FetchSummary

        stale_time = datetime(2026, 5, 4, tzinfo=timezone.utc)
        fresh_time = datetime(2026, 5, 7, tzinfo=timezone.utc)

        with buildcost_db.remote_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO builder_costs ("
                    "type_id, total_cost_per_unit, time_per_unit, me, runs, fetched_at"
                    ") VALUES ("
                    ":type_id, :total_cost_per_unit, :time_per_unit, :me, :runs, :fetched_at"
                    ")"
                ),
                [
                    {
                        "type_id": 34,
                        "total_cost_per_unit": 100.0,
                        "time_per_unit": 60.0,
                        "me": 10,
                        "runs": 10,
                        "fetched_at": stale_time,
                    },
                    {
                        "type_id": 35,
                        "total_cost_per_unit": 200.0,
                        "time_per_unit": 120.0,
                        "me": 10,
                        "runs": 10,
                        "fetched_at": stale_time,
                    },
                ],
            )

        buildcost_db.verify_db_exists = lambda: True
        sde_db.verify_db_exists = lambda: True
        primary_market_db.verify_db_exists = lambda: True

        monkeypatch.setattr(
            runner,
            "DatabaseConfig",
            lambda alias: {
                "buildcost": buildcost_db,
                "sde": sde_db,
                "primary": primary_market_db,
            }[alias],
        )
        monkeypatch.setattr(runner, "init_buildcost_tables", lambda db: None)
        monkeypatch.setattr(
            runner,
            "read_build_watchlist",
            lambda db: [
                {
                    "type_id": 34,
                    "type_name": "Tritanium",
                    "group_name": "Mineral",
                    "category_id": 4,
                }
            ],
        )
        monkeypatch.setattr(runner, "read_jita_prices", lambda db: {})
        monkeypatch.setattr(
            runner,
            "run_async_fetch_builder_costs",
            lambda *args, **kwargs: FetchSummary(
                records=[
                    {
                        "type_id": 34,
                        "total_cost_per_unit": 150.0,
                        "time_per_unit": 90.0,
                        "me": 10,
                        "runs": 10,
                        "fetched_at": fresh_time,
                    }
                ],
                attempted=1,
            ),
        )

        result = runner.run()

        assert result.success is True

        with buildcost_db.engine.connect() as conn:
            remaining = conn.execute(
                text("SELECT type_id FROM builder_costs ORDER BY type_id")
            ).scalars().all()

        assert remaining == [34]
