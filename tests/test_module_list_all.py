"""Tests for ``fitcheck module --list-all``."""
import csv
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import text

from mkts_backend.cli_tools import fit_check_module


def _seed(db):
    with db.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE doctrines (fit_id INT, type_id INT, type_name TEXT, "
            "category_id INT, fit_qty INT, total_stock INT)"
        ))
        conn.execute(text("CREATE TABLE ship_targets (fit_id INT, ship_target INT)"))
        conn.execute(text("INSERT INTO ship_targets VALUES (1, 10), (2, 10)"))
        conn.execute(text(
            "INSERT INTO doctrines VALUES "
            # Shared module: fits need 20 and 30 from one pool of 25.
            "(1, 100, 'Shared Module', 7, 2, 25), (2, 100, 'Shared Module', 7, 3, 25), "
            # Covered by an equivalent module's stock.
            "(1, 200, 'Faction Module', 7, 1, 4), "
            # Fully stocked.
            "(2, 300, 'Stocked Module', 7, 1, 50)"
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
        {"type_id": 200, "type_name": "Faction Module", "category_id": 7, "needed": 6},
        {"type_id": 100, "type_name": "Shared Module", "category_id": 7, "needed": 5},
    ]


def test_equivalent_stock_reduces_shortfall(tmp_path, fake_db_factory):
    db = fake_db_factory(tmp_path / "mkt.db")
    _seed(db)
    result = _run(db, {200: [{"type_id": 201, "type_name": "Alt", "stock": 6}]})
    assert result == [
        {"type_id": 100, "type_name": "Shared Module", "category_id": 7, "needed": 5},
    ]


def test_list_all_expands_all_markets():
    with patch.object(fit_check_module, "MarketContext") as mc, \
         patch.object(fit_check_module, "_query_low_stock_modules", return_value=[]) as q:
        assert fit_check_module.list_low_stock_command("all")
    called = [c.args[0] for c in mc.from_settings.call_args_list]
    assert "all" not in called
    assert called == fit_check_module.expand_market_alias("all")
    assert q.call_count == len(called)


def _item(type_name, category_id, needed, type_id=1):
    return {"type_id": type_id, "type_name": type_name,
            "category_id": category_id, "needed": needed}


def test_markdown_groups_by_category():
    items = [
        _item("Deimos", 6, 82),
        _item("Hurricane Fleet Issue", 6, 47),
        _item("Hail L", 8, 12000),
        _item("Damage Control II", 7, 5),
        _item("Nanite Repair Paste", 17, 3),
        _item("Unknown", None, 1),
    ]
    md = fit_check_module.format_low_stock_markdown(
        "4-HWWF - WinterCo. Central Station", items)
    assert md == (
        "# 4-HWWF - WinterCo. Central Station\n"
        "\n## Ship\n- Deimos - 82\n- Hurricane Fleet Issue - 47\n"
        "\n## Module\n- Damage Control II - 5\n"
        "\n## Charge\n- Hail L - 12,000\n"
        "\n## Other\n- Nanite Repair Paste - 3\n- Unknown - 1"
    )


def test_markdown_empty_market():
    md = fit_check_module.format_low_stock_markdown("Test", [])
    assert md == "# Test\n\nNo items needed."


def test_csv_export(tmp_path):
    path = fit_check_module.write_low_stock_csv(
        str(tmp_path / "out.csv"), [_item("Deimos", 6, 82, type_id=12023)])
    with open(path, newline="") as f:
        assert list(csv.reader(f)) == [
            ["type_id", "type_name", "category", "needed"],
            ["12023", "Deimos", "Ship", "82"],
        ]


def test_handle_module_passes_output_format():
    with patch.object(fit_check_module, "list_low_stock_command") as cmd:
        fit_check_module.handle_module(["--list-all", "--output=markdown"])
        cmd.assert_called_once_with("primary", "markdown")
        cmd.reset_mock()
        fit_check_module.handle_module(["--list-all"])
        cmd.assert_called_once_with("primary", "multibuy")


def test_handle_module_rejects_bad_output():
    with patch.object(fit_check_module, "list_low_stock_command") as cmd:
        fit_check_module.handle_module(["--list-all", "--output=pdf"])
        fit_check_module.handle_module(["--id=11269", "--output=markdown"])
    cmd.assert_not_called()
