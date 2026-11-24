"""Schema integrity checks for tables defined in ``mkts_backend.db.models``.

Guards Base-defined tables (watchlist, marketstats) against PRIMARY KEY
loss caused by ``pandas.DataFrame.to_sql(if_exists='replace')``, which
recreates the table from DataFrame dtype metadata only and silently drops
ON CONFLICT semantics.
"""
from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from mkts_backend.db.models import Base, MarketStats, Watchlist


# ---- helpers ----------------------------------------------------------------


def get_pk_cols(engine, table_name: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return [r.name for r in rows if r.pk]


def assert_table_matches_model(engine, model) -> None:
    """Verify a live table's PK matches the SQLAlchemy model."""
    expected = [c.name for c in model.__table__.primary_key.columns]
    actual = get_pk_cols(engine, model.__tablename__)
    assert actual == expected, (
        f"PK mismatch on {model.__tablename__}: "
        f"model defines {expected}, DB has {actual}"
    )


class _FakeDB:
    """Stand-in for DatabaseConfig exposing ``.engine``."""

    def __init__(self, engine, alias: str = "fake"):
        self.engine = engine
        self.alias = alias
        self.path = ":memory:"


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
def base_engine(tmp_path):
    """A fresh SQLite engine with all Base tables created."""
    engine = create_engine(f"sqlite:///{tmp_path}/integrity.db")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def watchlist_csv(tmp_path):
    """A CSV in the format expected by the watchlist writers."""
    df = pd.DataFrame(
        [
            {"type_id": 1, "type_name": "a", "group_id": 10,
             "group_name": "g", "category_id": 100, "category_name": "c"},
            {"type_id": 2, "type_name": "b", "group_id": 20,
             "group_name": "g", "category_id": 200, "category_name": "c"},
        ]
    )
    path = tmp_path / "watchlist.csv"
    df.to_csv(path, index=False)
    return path


@pytest.fixture
def empty_watchlist_csv(tmp_path):
    """A CSV with only headers — should be refused, not silently truncate."""
    path = tmp_path / "empty.csv"
    pd.DataFrame(
        columns=["type_id", "type_name", "group_id", "group_name",
                 "category_id", "category_name"]
    ).to_csv(path, index=False)
    return path


# ---- 1) Base-built DB matches its models ------------------------------------


@pytest.mark.parametrize("model", [MarketStats, Watchlist])
def test_base_create_all_yields_expected_pk(base_engine, model):
    """create_all(Base) must produce tables whose PKs match the models."""
    assert_table_matches_model(base_engine, model)


# ---- 2) Validator catches the pandas-replace bug ----------------------------


def test_pandas_replace_drops_pk_and_validator_catches_it(tmp_path):
    """``to_sql(if_exists='replace')`` on a Base table loses its PK; the
    validator must fail loudly so any future regression is caught.
    """
    engine = create_engine(f"sqlite:///{tmp_path}/regress.db")
    Base.metadata.create_all(engine)
    assert_table_matches_model(engine, Watchlist)  # sanity

    pd.DataFrame(
        [
            {"type_id": 1, "type_name": "a", "group_id": 10,
             "group_name": "g", "category_id": 100, "category_name": "c"},
        ]
    ).to_sql("watchlist", engine, if_exists="replace", index=False)

    with pytest.raises(AssertionError, match="PK mismatch on watchlist"):
        assert_table_matches_model(engine, Watchlist)
    engine.dispose()


# ---- 3) Watchlist writers preserve PK ---------------------------------------


def test_update_watchlist_data_preserves_pk(base_engine, watchlist_csv, monkeypatch):
    monkeypatch.setattr(
        "mkts_backend.utils.utils.wcmkt_db", _FakeDB(base_engine)
    )
    from mkts_backend.utils.utils import update_watchlist_data

    update_watchlist_data(esi=None, watchlist_csv=str(watchlist_csv))

    assert_table_matches_model(base_engine, Watchlist)
    with base_engine.connect() as conn:
        rows = conn.execute(text("SELECT type_id FROM watchlist ORDER BY type_id")).fetchall()
    assert [r.type_id for r in rows] == [1, 2]


def test_restore_watchlist_from_csv_preserves_pk(
    base_engine, watchlist_csv, monkeypatch
):
    fake = _FakeDB(base_engine)
    monkeypatch.setattr(
        "mkts_backend.utils.db_utils.DatabaseConfig",
        lambda *a, **kw: fake,
    )
    from mkts_backend.utils.db_utils import restore_watchlist_from_csv

    restore_watchlist_from_csv(csv_file=str(watchlist_csv))

    assert_table_matches_model(base_engine, Watchlist)
    with base_engine.connect() as conn:
        rows = conn.execute(text("SELECT type_id FROM watchlist ORDER BY type_id")).fetchall()
    assert [r.type_id for r in rows] == [1, 2]


# ---- 4) Watchlist writers refuse empty input (no DELETE-then-nothing) -------


def test_update_watchlist_data_refuses_empty_csv(
    base_engine, empty_watchlist_csv, monkeypatch
):
    """An empty CSV must NOT silently DELETE the watchlist."""
    monkeypatch.setattr(
        "mkts_backend.utils.utils.wcmkt_db", _FakeDB(base_engine)
    )
    with base_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO watchlist (type_id, type_name, group_id, group_name, category_id, category_name) VALUES (1, 'orig', 10, 'g', 100, 'c')"
        ))
    from mkts_backend.utils.utils import update_watchlist_data

    with pytest.raises(ValueError, match="empty CSV"):
        update_watchlist_data(esi=None, watchlist_csv=str(empty_watchlist_csv))

    with base_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM watchlist")).scalar()
    assert count == 1, "watchlist must be untouched after refusal"


def test_restore_watchlist_from_csv_refuses_empty_csv(
    base_engine, empty_watchlist_csv, monkeypatch
):
    """An empty CSV must NOT silently DELETE the watchlist."""
    fake = _FakeDB(base_engine)
    monkeypatch.setattr(
        "mkts_backend.utils.db_utils.DatabaseConfig",
        lambda *a, **kw: fake,
    )
    with base_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO watchlist (type_id, type_name, group_id, group_name, category_id, category_name) VALUES (1, 'orig', 10, 'g', 100, 'c')"
        ))
    from mkts_backend.utils.db_utils import restore_watchlist_from_csv

    with pytest.raises(ValueError, match="empty CSV"):
        restore_watchlist_from_csv(csv_file=str(empty_watchlist_csv))

    with base_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM watchlist")).scalar()
    assert count == 1, "watchlist must be untouched after refusal"


# ---- 5) ON CONFLICT DO NOTHING semantics ------------------------------------


def test_sqlite_on_conflict_do_nothing_skips_duplicate(base_engine):
    """Pins the SQLite/SQLAlchemy upsert behavior the writers depend on:
    re-inserting an existing PK is a no-op (rowcount 0), not an error.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    with base_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO watchlist (type_id, type_name, group_id, group_name, category_id, category_name) VALUES (1, 'orig', 10, 'g', 100, 'c')"
        ))

    stmt = sqlite_insert(Watchlist.__table__).values(
        type_id=1, type_name="dup", group_id=10,
        group_name="g", category_id=100, category_name="c",
    ).on_conflict_do_nothing(index_elements=["type_id"])

    with base_engine.begin() as conn:
        result = conn.execute(stmt)
    assert result.rowcount == 0

    with base_engine.connect() as conn:
        rows = conn.execute(text("SELECT type_id, type_name FROM watchlist")).fetchall()
    assert len(rows) == 1
    assert rows[0].type_name == "orig"


def test_add_missing_items_to_watchlist_skips_existing_type_id(
    base_engine, monkeypatch
):
    """``add_missing_items_to_watchlist`` must not duplicate existing rows
    when called with a type_id already in the watchlist.
    """
    fake = _FakeDB(base_engine)
    monkeypatch.setattr(
        "mkts_backend.utils.db_utils.DatabaseConfig",
        lambda *a, **kw: fake,
    )
    monkeypatch.setattr(
        "mkts_backend.utils.db_utils.get_type_info",
        lambda type_ids: pd.DataFrame([
            {"type_id": 1, "type_name": "from_sde", "group_id": 10,
             "group_name": "g", "category_id": 100, "category_name": "c"},
            {"type_id": 2, "type_name": "new", "group_id": 20,
             "group_name": "g", "category_id": 200, "category_name": "c"},
        ]),
    )
    with base_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO watchlist (type_id, type_name, group_id, group_name, category_id, category_name) VALUES (1, 'orig', 10, 'g', 100, 'c')"
        ))

    from mkts_backend.utils.db_utils import add_missing_items_to_watchlist

    add_missing_items_to_watchlist([1, 2], db_alias="wcmkt")

    with base_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT type_id, type_name FROM watchlist ORDER BY type_id"
        )).fetchall()
    assert [(r.type_id, r.type_name) for r in rows] == [(1, "orig"), (2, "new")], (
        "row 1 must keep its original name; row 2 must be inserted"
    )


# ---- 6) marketstats writer preserves PK -------------------------------------


def test_upsert_marketstats_preserves_pk(base_engine, monkeypatch):
    """``upsert_database(MarketStats, df)`` must not lose the type_id PK.
    Uses the wipe_replace branch (the bug-prone one) since marketstats is
    listed in [wipe_replace] tables in settings.toml.
    """
    fake = _FakeDB(base_engine, alias="wcmkt")
    monkeypatch.setattr(
        "mkts_backend.db.db_handlers._get_db",
        lambda market_ctx=None: fake,
    )

    df = pd.DataFrame([
        {
            "type_id": 12345, "type_name": "Item",
            "total_volume_remain": 100, "min_price": 1.0,
            "price": 2.0, "avg_price": 1.5, "avg_volume": 50.0,
            "group_id": 1, "group_name": "G",
            "category_id": 2, "category_name": "C",
            "days_remaining": 30.0,
            "last_update": pd.Timestamp.now(tz="UTC").tz_localize(None),
        }
    ])

    from mkts_backend.db.db_handlers import upsert_database

    assert upsert_database(MarketStats, df) is True
    assert_table_matches_model(base_engine, MarketStats)
    with base_engine.connect() as conn:
        ids = conn.execute(text("SELECT type_id FROM marketstats")).fetchall()
    assert [r.type_id for r in ids] == [12345]
