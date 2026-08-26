"""
Tests for DoctrineFit remote lookups.

Regression coverage for ``fit-update add --remote``: the fit is written to the
Turso remote fittings DB, so DoctrineFit must read its metadata from the same
remote engine. Reading the local file raised
"Fit <id> not found in fittings_fitting" for every newly added fit.
"""

import pytest
from sqlalchemy import create_engine, text

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

CREATE_INV_INFO = """
    CREATE TABLE IF NOT EXISTS inv_info (
        typeID INT PRIMARY KEY,
        typeName TEXT,
        groupID INT,
        volume REAL,
        groupName TEXT,
        categoryID INT,
        categoryName TEXT
    )
"""


class FakeDB:
    """Minimal DatabaseConfig stand-in with distinct local/remote engines."""

    def __init__(self, alias, local_engine, remote_engine):
        self.alias = alias
        self.engine = local_engine
        self.remote_engine = remote_engine


@pytest.fixture
def fittings_env(tmp_path, monkeypatch):
    """Remote fittings DB holds fit 997; the local file does not."""
    local = create_engine(f"sqlite:///{tmp_path/'fittings_local.db'}")
    remote = create_engine(f"sqlite:///{tmp_path/'fittings_remote.db'}")
    sde = create_engine(f"sqlite:///{tmp_path/'sde.db'}")

    for eng in (local, remote):
        with eng.begin() as conn:
            conn.execute(text(CREATE_FITTING))
            conn.execute(text(CREATE_DOCTRINE))

    with remote.begin() as conn:
        conn.execute(
            text("INSERT INTO fittings_fitting (id, name, ship_type_id) "
                 "VALUES (997, 'W2.LR_ONI', 29340)")
        )
        conn.execute(
            text("INSERT INTO fittings_doctrine (id, name) "
                 "VALUES (42, 'W2 Logi')")
        )

    with sde.begin() as conn:
        conn.execute(text(CREATE_INV_INFO))
        conn.execute(
            text("INSERT INTO inv_info (typeID, typeName) VALUES (29340, 'Oneiros')")
        )

    dbs = {
        "fittings": FakeDB("fittings", local, remote),
        "sde": FakeDB("sde", sde, sde),
    }
    monkeypatch.setattr(doctrine_update, "DatabaseConfig", lambda alias: dbs[alias])
    return dbs


def test_doctrine_fit_resolves_from_remote(fittings_env):
    fit = DoctrineFit(doctrine_id=42, fit_id=997, target=20, remote=True)

    assert fit.fit_name == "W2.LR_ONI"
    assert fit.doctrine_name == "W2 Logi"
    assert fit.ship_type_id == 29340
    assert fit.ship_name == "Oneiros"


def test_doctrine_fit_local_lookup_still_reads_local(fittings_env):
    """Default (remote=False) must not silently fall back to the remote."""
    with pytest.raises(ValueError, match="not found in fittings_doctrine"):
        DoctrineFit(doctrine_id=42, fit_id=997, target=20)


def test_sync_friendly_names_to_remote_removed():
    """``sync_friendly_names_to_remote`` had the same self-referential
    local-read/remote-write shape as ``sync_equiv_to_remote`` (deleted in
    Task 8) and is deleted here (Task 10): friendly-name propagation now
    comes from calling ``populate_friendly_names_from_json`` /
    ``update_doctrine_friendly_name`` once per configured market and
    pushing each one directly."""
    assert not hasattr(doctrine_update, "sync_friendly_names_to_remote")
