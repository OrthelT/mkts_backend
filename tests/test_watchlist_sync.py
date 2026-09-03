"""Unit tests for builder_costs.watchlist_sync.

Uses real in-memory SQLite databases for buildcost (build_watchlist + builder_costs)
and reuses the existing in_memory_sde_db fixture for SDE metadata + buildable filter.
"""

from __future__ import annotations


import pytest
from sqlalchemy import text


@pytest.fixture
def buildcost_db(fake_db_factory, tmp_path):
    """Buildcost SQLite DB with build_watchlist + builder_costs tables."""
    from mkts_backend.db.build_cost_models import BuildCostBase

    db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
    BuildCostBase.metadata.create_all(db.engine)
    return db


@pytest.fixture
def sde_db(fake_db_factory, in_memory_sde_db):
    """Wrap the in_memory_sde_db path in the shared fake DatabaseConfig."""
    return fake_db_factory(in_memory_sde_db, alias="sde")


@pytest.fixture
def primary_market_db(fake_db_factory, tmp_path):
    """Primary market DB with a 'watchlist' table containing a few type_ids."""
    db = fake_db_factory(tmp_path / "wcmktprod.db", alias="primary")
    with db.engine.connect() as conn:
        conn.execute(text("CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY)"))
        for tid in (34, 35, 36):  # 34/35 buildable, 36 not buildable
            conn.execute(
                text("INSERT INTO watchlist (type_id) VALUES (:t)"), {"t": tid}
            )
        conn.commit()
    return db


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


class TestDeleteOrphanBuilderCosts:
    """``delete_orphan_builder_costs`` lives in repository.py but is tested
    here because the in-memory ``buildcost_db`` fixture is the only place
    that already wires up both build_watchlist and builder_costs schema.
    """

    @staticmethod
    def _seed_builder_costs(db, type_ids: list[int]) -> None:
        from datetime import datetime, timezone

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from mkts_backend.db.build_cost_models import BuilderCosts

        now = datetime.now(timezone.utc)
        rows = [
            {
                "type_id": tid,
                "total_cost_per_unit": 100.0,
                "time_per_unit": 60.0,
                "me": 10,
                "runs": 1,
                "fetched_at": now,
            }
            for tid in type_ids
        ]
        with db.remote_engine.begin() as conn:
            conn.execute(sqlite_insert(BuilderCosts.__table__).values(rows))

    @staticmethod
    def _read_builder_costs_ids(db) -> set[int]:
        from sqlalchemy import text

        with db.engine.connect() as conn:
            rows = conn.execute(text("SELECT type_id FROM builder_costs")).all()
        return {int(r[0]) for r in rows}

    def test_prunes_orphans_only(self, buildcost_db, sde_db):
        from mkts_backend.builder_costs.repository import (
            delete_orphan_builder_costs,
        )
        from mkts_backend.builder_costs.watchlist_sync import (
            add_to_build_watchlist,
        )

        # build_watchlist: {34, 35}. builder_costs: {34, 35, 99, 19810} — 99
        # and 19810 are orphans (never in watchlist or removed from it).
        add_to_build_watchlist(buildcost_db, sde_db, [34, 35])
        self._seed_builder_costs(buildcost_db, [34, 35, 99, 19810])

        deleted = delete_orphan_builder_costs(buildcost_db)

        assert deleted == 2
        assert self._read_builder_costs_ids(buildcost_db) == {34, 35}

    def test_no_orphans_returns_zero(self, buildcost_db, sde_db):
        from mkts_backend.builder_costs.repository import (
            delete_orphan_builder_costs,
        )
        from mkts_backend.builder_costs.watchlist_sync import (
            add_to_build_watchlist,
        )

        add_to_build_watchlist(buildcost_db, sde_db, [34, 35])
        self._seed_builder_costs(buildcost_db, [34, 35])

        deleted = delete_orphan_builder_costs(buildcost_db)

        assert deleted == 0
        assert self._read_builder_costs_ids(buildcost_db) == {34, 35}
