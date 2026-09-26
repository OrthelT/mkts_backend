"""Tests for ``fitcheck module --list-all``."""
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text

from mkts_backend.cli_tools import fit_check_module


def _seed(db):
    with db.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE doctrines (fit_id INT, type_id INT, type_name TEXT, "
            "fit_qty INT, total_stock INT)"
        ))
        conn.execute(text("CREATE TABLE ship_targets (fit_id INT, ship_target INT)"))
        conn.execute(text("INSERT INTO ship_targets VALUES (1, 10), (2, 10)"))
        conn.execute(text(
            "INSERT INTO doctrines VALUES "
            # Shared module: fits need 20 and 30 from one pool of 25.
            "(1, 100, 'Shared Module', 2, 25), (2, 100, 'Shared Module', 3, 25), "
            # Covered by an equivalent module's stock.
            "(1, 200, 'Faction Module', 1, 4), "
            # Fully stocked.
            "(2, 300, 'Stocked Module', 1, 50)"
        ))


def _run(db, equivs):
    ctx = SimpleNamespace(database_alias="test", name="Test")
    with patch.object(fit_check_module, "DatabaseConfig", return_value=db), \
         patch("mkts_backend.cli_tools.fit_check.get_equiv_stock", return_value=equivs):
        return fit_check_module._query_low_stock_modules(ctx)


def test_shortfall_is_largest_single_fit_deficit(tmp_path, fake_db_factory):
    db = fake_db_factory(tmp_path / "mkt.db")
    _seed(db)
    result = _run(db, {})
    assert result == [
        {"type_name": "Faction Module", "needed": 6},
        {"type_name": "Shared Module", "needed": 5},
    ]


def test_equivalent_stock_reduces_shortfall(tmp_path, fake_db_factory):
    db = fake_db_factory(tmp_path / "mkt.db")
    _seed(db)
    result = _run(db, {200: [{"type_id": 201, "type_name": "Alt", "stock": 6}]})
    assert result == [{"type_name": "Shared Module", "needed": 5}]


def test_list_all_expands_all_markets():
    with patch.object(fit_check_module, "MarketContext") as mc, \
         patch.object(fit_check_module, "_query_low_stock_modules", return_value=[]) as q:
        assert fit_check_module.list_low_stock_command("all")
    called = [c.args[0] for c in mc.from_settings.call_args_list]
    assert "all" not in called
    assert called == fit_check_module.expand_market_alias("all")
    assert q.call_count == len(called)
