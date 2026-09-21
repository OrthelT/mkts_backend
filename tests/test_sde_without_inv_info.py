"""Exercise migrated readers after physically dropping the legacy table.

Optionally set MKTS_TEST_SDE_SNAPSHOT to a disposable ordinary SQLite snapshot
for real-data coverage. Tests copy it again and never open the configured replica.
"""
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock

import pytest
from sqlalchemy import create_engine, text

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.utils import db_utils, eft_parser, get_type_info, parse_fits
from mkts_backend.builder_costs import sde_lookup


@pytest.fixture
def sde_without_legacy(tmp_path, monkeypatch):
    path = tmp_path / "sde.db"
    snapshot = os.getenv("MKTS_TEST_SDE_SNAPSHOT")
    if snapshot:
        shutil.copyfile(Path(snapshot), path)
    with sqlite3.connect(path) as conn:
        if not snapshot:
            conn.executescript("""
                CREATE TABLE inv_info (typeID INTEGER);
                CREATE TABLE sdetypes (
                    typeID INTEGER PRIMARY KEY, typeName TEXT, groupID INTEGER,
                    groupName TEXT, categoryID INTEGER, categoryName TEXT, volume REAL
                );
                INSERT INTO sdetypes VALUES
                    (587, 'Rifter', 25, 'Frigate', 6, 'Ship', 27289),
                    (2048, 'Damage Control II', 60, 'Damage Control', 7, 'Module', 5),
                    (34, 'Tritanium', 18, 'Mineral', 4, 'Material', 0.01);
            """)
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='inv_info'").fetchone()[0] == 1
        conn.execute('DROP TABLE inv_info')
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='inv_info'").fetchone()[0] == 0
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    # Any accidental access to a configured replica is a hard failure.
    monkeypatch.setattr(DatabaseConfig, 'engine', PropertyMock(side_effect=AssertionError('Live replica access')))
    monkeypatch.setattr(DatabaseConfig, 'remote_engine', PropertyMock(side_effect=AssertionError('Live replica access')))
    for method in ('push', 'pull', 'sync', 'verify_db_exists'):
        monkeypatch.setattr(DatabaseConfig, method, lambda *a, **kw: pytest.fail('Live replica operation'))
    engine = create_engine(f'sqlite:///{path}')
    fake = SimpleNamespace(engine=engine, remote_engine=engine)
    monkeypatch.setattr(get_type_info, 'DatabaseConfig', lambda *a, **kw: fake)
    monkeypatch.setattr(db_utils, 'sde_db', fake)
    monkeypatch.setattr(eft_parser, '_sde_db', fake)
    yield fake
    engine.dispose()


def test_type_info_by_id_name_and_list(sde_without_legacy):
    assert get_type_info.TypeInfo(587).type_name == 'Rifter'
    assert get_type_info.TypeInfo('Rifter').type_id == 587
    assert [t.type_name for t in get_type_info.get_type_from_list([34, 2048])] == ['Tritanium', 'Damage Control II']
    with pytest.raises(ValueError):
        get_type_info.TypeInfo('Deliberately nonexistent test type')


@pytest.mark.parametrize('remote', [False, True])
def test_watchlist_metadata_lookup(sde_without_legacy, remote):
    result = db_utils.get_type_info([587, 2048], remote=remote)
    assert set(result.type_name) == {'Rifter', 'Damage Control II'}
    assert set(result.category_id) == {6, 7}


def test_both_eft_parsers(sde_without_legacy, tmp_path):
    fit = '[Rifter, Removal regression]\nDamage Control II\nNonexistent regression module'
    result = eft_parser.parse_eft_string(fit, sde_engine=sde_without_legacy.engine)
    assert result.ship_type_id == 587
    assert [item['type_id'] for item in result.items] == [2048]
    assert result.missing_types == ['Nonexistent regression module']
    path = tmp_path / 'fit.txt'
    path.write_text(fit)
    parsed = parse_fits.parse_eft_fit_file(str(path), 42, sde_without_legacy.engine)
    assert [item['type_id'] for item in parsed.items] == [2048]
    assert parsed.missing_types == result.missing_types


def test_builder_metadata_and_name_resolution(sde_without_legacy):
    metadata = sde_lookup.lookup_type_metadata([587, 2048], sde_without_legacy)
    assert metadata[587]['type_name'] == 'Rifter'
    assert metadata[2048]['category_id'] == 7
    ids, missing = sde_lookup.lookup_type_ids_by_name(['rifter', 'Missing regression type'], sde_without_legacy)
    assert ids == [587]
    assert missing == ['Missing regression type']
    with sde_without_legacy.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name='inv_info'")).scalar_one() == 0
