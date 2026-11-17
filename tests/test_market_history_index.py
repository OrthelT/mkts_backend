"""market_history needs a type_id-leading index; its (date, type_id) PK can't serve type_id lookups."""
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, text

from mkts_backend.db.models import Base

_spec = importlib.util.spec_from_file_location(
    "add_market_history_index",
    Path(__file__).parent.parent / "scripts" / "add_market_history_index.py",
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

LEGACY_DDL = """
CREATE TABLE market_history (
    date DATETIME NOT NULL, type_id VARCHAR(10) NOT NULL,
    type_name VARCHAR(100) NOT NULL, average FLOAT NOT NULL,
    volume INTEGER NOT NULL, highest FLOAT NOT NULL, lowest FLOAT NOT NULL,
    order_count INTEGER NOT NULL, timestamp DATETIME NOT NULL,
    PRIMARY KEY (date, type_id))
"""


def _type_id_plan(engine) -> str:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "EXPLAIN QUERY PLAN SELECT * FROM market_history WHERE type_id = '34'"
        )).fetchall()
    return " ".join(r[-1] for r in rows)


def test_create_all_indexes_type_id(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(engine)
    assert "ix_market_history_type_id_date" in _type_id_plan(engine)
    engine.dispose()


def test_script_indexes_existing_db_and_pushes(tmp_path, fake_db_factory, monkeypatch):
    db = fake_db_factory(tmp_path / "m.db")
    with db.engine.begin() as conn:
        conn.execute(text(LEGACY_DDL))
    assert "SCAN" in _type_id_plan(db.engine)
    monkeypatch.setattr(script, "DatabaseConfig", lambda alias: db)

    assert script.index_target("primary", apply=False)
    assert db.pushes == 0 and not script.has_index(db)

    assert script.index_target("primary", apply=True)
    assert db.pulls == 2 and db.pushes == 1
    assert "ix_market_history_type_id_date" in _type_id_plan(db.engine)

    # Second run is a no-op.
    assert script.index_target("primary", apply=True)
    assert db.pushes == 1
