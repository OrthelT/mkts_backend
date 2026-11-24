"""Tests for DoctrineFit metadata lookups and removal of legacy remote-sync helpers."""

import pytest
from sqlalchemy import create_engine, text

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.utils import doctrine_update
from mkts_backend.utils.doctrine_update import DoctrineFit


CREATE_FITTING = """
    CREATE TABLE IF NOT EXISTS fittings_fitting (
        id INTEGER PRIMARY KEY,
        description TEXT,
        name VARCHAR(255),
        ship_type_type_id INTEGER,
        ship_type_id INTEGER,
        created TEXT,
        last_updated TEXT
    )
"""

CREATE_DOCTRINE = """
    CREATE TABLE IF NOT EXISTS fittings_doctrine (
        id INTEGER PRIMARY KEY,
        name VARCHAR(255),
        icon_url VARCHAR(200),
        description TEXT,
        created TEXT,
        last_updated TEXT
    )
"""

CREATE_SDE_TYPES = """
    CREATE TABLE IF NOT EXISTS sdetypes (
        typeID INT PRIMARY KEY,
        typeName TEXT,
        groupID INT,
        groupName TEXT,
        categoryID INT,
        categoryName TEXT,
        volume REAL,
        metaGroupID INT,
        metaGroupName TEXT,
        published BOOL,
        repackagedVolume REAL
    )
"""


class FakeDB:
    """Minimal DatabaseConfig stand-in exposing ``.engine``."""

    def __init__(self, alias, engine):
        self.alias = alias
        self.engine = engine


@pytest.fixture
def fittings_env(tmp_path, monkeypatch):
    fittings = create_engine(f"sqlite:///{tmp_path / 'fittings.db'}")
    sde = create_engine(f"sqlite:///{tmp_path / 'sde.db'}")

    with fittings.begin() as conn:
        conn.execute(text(CREATE_FITTING))
        conn.execute(text(CREATE_DOCTRINE))
        conn.execute(
            text(
                "INSERT INTO fittings_fitting (id, name, ship_type_id) "
                "VALUES (997, 'W2.LR_ONI', 29340)"
            )
        )
        conn.execute(
            text("INSERT INTO fittings_doctrine (id, name) VALUES (42, 'W2 Logi')")
        )

    with sde.begin() as conn:
        conn.execute(text(CREATE_SDE_TYPES))
        conn.execute(
            text("INSERT INTO sdetypes (typeID, typeName) VALUES (29340, 'Oneiros')")
        )

    dbs = {
        "fittings": FakeDB("fittings", fittings),
        "sde": FakeDB("sde", sde),
    }
    monkeypatch.setattr(doctrine_update, "DatabaseConfig", lambda alias: dbs[alias])
    yield dbs
    fittings.dispose()
    sde.dispose()


def test_doctrine_fit_resolves_from_fittings_and_sde(fittings_env):
    fit = DoctrineFit(doctrine_id=42, fit_id=997, target=20)

    assert fit.fit_name == "W2.LR_ONI"
    assert fit.doctrine_name == "W2 Logi"
    assert fit.ship_type_id == 29340
    assert fit.ship_name == "Oneiros"


def test_doctrine_fit_missing_doctrine_raises(fittings_env):
    with pytest.raises(ValueError, match="not found in fittings_doctrine"):
        DoctrineFit(doctrine_id=999, fit_id=997, target=20)


def test_remote_engine_and_sync_friendly_names_removed():
    """Under pyturso, writes go through ``engine`` + ``push()`` — there is no
    separate ``remote_engine`` or ``sync_friendly_names_to_remote``."""
    assert not hasattr(DatabaseConfig, "remote_engine")
    assert not hasattr(doctrine_update, "sync_friendly_names_to_remote")
