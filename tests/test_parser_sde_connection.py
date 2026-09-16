"""Parser lookups must use the caller's SDE, not the configured replica."""
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import pytest
from sqlalchemy import create_engine, text

from mkts_backend.utils import eft_parser, parse_fits


@pytest.fixture
def sde_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE sdetypes (typeID INTEGER, typeName TEXT)"))
        conn.execute(text("INSERT INTO sdetypes VALUES (1, 'Test Ship'), (2, 'Test Module')"))
    # Any attempt to open the configured replica is a test failure.
    with patch(
        "mkts_backend.config.db_config.DatabaseConfig.engine",
        new_callable=PropertyMock,
        side_effect=AssertionError("Unexpected default SDE access"),
    ):
        yield engine
    engine.dispose()


@pytest.mark.parametrize("lookup", [
    eft_parser.lookup_type_id,
    eft_parser.resolve_ship_type_id,
    parse_fits._lookup_type_id,
    parse_fits._resolve_ship_type_id,
])
def test_lookup_uses_supplied_connection(sde_engine, lookup):
    with sde_engine.connect() as conn:
        assert lookup("Test Ship", conn) == 1
        assert lookup("Unknown", conn) is None
        # The caller still owns a usable connection.
        assert conn.execute(text("SELECT 1")).scalar_one() == 1


def test_lookup_without_connection_uses_default_engine(sde_engine, monkeypatch):
    monkeypatch.setattr(eft_parser, "_sde_db", SimpleNamespace(engine=sde_engine))
    assert eft_parser.lookup_type_id("Test Ship") == 1
    assert eft_parser.lookup_type_id("Unknown") is None


def test_string_parser_uses_supplied_engine(sde_engine):
    result = eft_parser.parse_eft_string(
        "[Test Ship, Test Fit]\nTest Module\nUnknown", sde_engine=sde_engine
    )
    assert result.ship_type_id == 1
    assert [item["type_id"] for item in result.items] == [2]
    assert result.missing_types == ["Unknown"]


def test_file_parser_uses_supplied_engine(sde_engine, tmp_path):
    fit_file = tmp_path / "fit.txt"
    fit_file.write_text("[Test Ship, Test Fit]\nTest Module\nUnknown", encoding="utf-8")
    result = parse_fits.parse_eft_fit_file(str(fit_file), 42, sde_engine)
    assert [item["type_id"] for item in result.items] == [2]
    assert result.missing_types == ["Unknown"]
