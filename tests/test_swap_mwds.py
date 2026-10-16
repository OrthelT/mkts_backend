"""scripts/swap_mwds.py: plan selection, fit + doctrines writes, and revert."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "swap_mwds", Path(__file__).parent.parent / "scripts" / "swap_mwds.py"
)
swap_mwds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(swap_mwds)

QUAD_500, QUAD_50, QUAD_5 = 35662, 35660, 35658
CG_500, CG_50, CG_5 = 5945, 5975, 5971
II_50 = 12076
AFTERBURNER = 35657

# (item_id, fit_id, flag, quantity, type_id)
ITEMS = [
    (1, 496, "HiSlot2", 1, QUAD_500),   # Apocalypse Navy Issue -> Cold-Gas
    (2, 462, "MedSlot1", 1, CG_50),     # Nighthawk Cold-Gas -> MWD II
    (3, 462, "Cargo", 1, AFTERBURNER),  # afterburner untouched
    (4, 184, "MedSlot2", 1, QUAD_5),    # Flycatcher -> Cold-Gas, merges with cargo
    (5, 184, "Cargo", 1, CG_5),         # already the target
    (6, 397, "MedSlot3", 1, QUAD_50),   # Basilisk is not listed
    (7, 443, "MedSlot0", 1, QUAD_50),   # Loki is listed by fit_id (463, 271) only
    (8, 456, "MedSlot4", 1, QUAD_50),   # Nighthawk fit in no market
]
FIT_SHIPS = {496: "Apocalypse Navy Issue", 462: "Nighthawk", 184: "Flycatcher",
             397: "Basilisk", 443: "Loki"}
HULL_IDS = {"Apocalypse Navy Issue": 17726, "Nighthawk": 22470, "Flycatcher": 22464,
            "Basilisk": 11985, "Loki": 29990}
NAMES = {QUAD_500: "500MN Quad LiF Restrained Microwarpdrive",
         QUAD_50: "50MN Quad LiF Restrained Microwarpdrive",
         QUAD_5: "5MN Quad LiF Restrained Microwarpdrive",
         CG_500: "500MN Cold-Gas Enduring Microwarpdrive",
         CG_50: "50MN Cold-Gas Enduring Microwarpdrive",
         CG_5: "5MN Cold-Gas Enduring Microwarpdrive",
         II_50: "50MN Microwarpdrive II",
         AFTERBURNER: "100MN Y-S8 Compact Afterburner"}


def test_build_plan_selects_listed_market_fits_only():
    plan = swap_mwds.build_plan(FIT_SHIPS, ITEMS)
    assert [(c["item_id"], c["old_type_id"], c["new_type_id"]) for c in plan] == [
        (1, QUAD_500, CG_500),
        (4, QUAD_5, CG_5),
        (2, CG_50, II_50),
    ]


def test_every_target_type_maps_to_its_own_size():
    for size, by_type in swap_mwds.TARGETS.items():
        for new_type in by_type:
            assert swap_mwds.SIZE_OF[new_type] == size


def _make_dbs(tmp_path, fake_db_factory):
    fit = sqlite3.connect(tmp_path / "fit.db")
    fit.execute("CREATE TABLE fittings_fittingitem (id INTEGER PRIMARY KEY, flag TEXT, "
                "quantity INTEGER, type_id INTEGER, fit_id INTEGER, type_fk_id INTEGER)")
    fit.executemany("INSERT INTO fittings_fittingitem VALUES (?, ?, ?, ?, ?, ?)",
                    [(i, flag, q, t, f, t) for i, f, flag, q, t in ITEMS])
    fit.execute("CREATE TABLE fittings_fitting (id INTEGER PRIMARY KEY, ship_type_type_id INTEGER)")
    fit.executemany("INSERT INTO fittings_fitting VALUES (?, ?)",
                    [(f, HULL_IDS[s]) for f, s in FIT_SHIPS.items()] + [(456, HULL_IDS["Nighthawk"])])
    fit.commit()
    fit.close()

    sde = sqlite3.connect(tmp_path / "sde.db")
    sde.execute("CREATE TABLE sdetypes (typeID INTEGER, typeName TEXT, groupID INTEGER, "
                "groupName TEXT, categoryID INTEGER, categoryName TEXT)")
    sde.executemany("INSERT INTO sdetypes VALUES (?, ?, 46, 'Propulsion Module', 7, 'Module')",
                    list(NAMES.items()) + [(t, s) for s, t in HULL_IDS.items()])
    sde.commit()
    sde.close()

    markets = {}
    # mktb lacks fit 496 and holds a stale Basilisk record under fit_id 184.
    for alias, fits in (("mkta", ITEMS), ("mktb", [i for i in ITEMS if i[1] not in (496, 184)]
                                           + [(9, 184, "MedSlot3", 1, QUAD_50)])):
        m = sqlite3.connect(tmp_path / f"{alias}.db")
        m.execute("CREATE TABLE doctrine_fits (fit_id INTEGER, ship_name TEXT)")
        # Stale ship_name for 184, as found in production: the hull must come
        # from fittings_fitting instead.
        m.executemany("INSERT INTO doctrine_fits VALUES (?, ?)",
                      [(f, "Raven" if f == 184 else s) for f, s in FIT_SHIPS.items()])
        # NOT NULL columns as in the wcmktbkg schema.
        m.execute("""CREATE TABLE doctrines (id INTEGER NOT NULL PRIMARY KEY,
            fit_id INTEGER NOT NULL, ship_id INTEGER NOT NULL, ship_name TEXT NOT NULL,
            hulls INTEGER, type_id INTEGER NOT NULL, type_name TEXT, fit_qty INTEGER NOT NULL, fits_on_mkt INTEGER, total_stock INTEGER,
            price FLOAT, avg_vol INTEGER, days FLOAT, group_id INTEGER, group_name TEXT,
            category_id INTEGER, category_name TEXT, timestamp TEXT)""")
        for item_id, fit_id, _, q, t in fits:
            if fit_id in FIT_SHIPS:
                ship = "Basilisk" if item_id == 9 else FIT_SHIPS[fit_id]
                m.execute("INSERT INTO doctrines (fit_id, ship_id, ship_name, hulls, type_id, "
                          "type_name, fit_qty) VALUES (?, ?, ?, 4, ?, ?, ?)",
                          (fit_id, HULL_IDS[ship], ship, t, NAMES[t], q))
        m.execute("CREATE TABLE marketstats (type_id INTEGER, total_volume_remain INTEGER, "
                  "price FLOAT, avg_volume FLOAT, days_remaining FLOAT, last_update TEXT)")
        m.execute("INSERT INTO marketstats VALUES (?, 30, 1000.0, 2.0, 15.0, 'T')", (CG_5,))
        m.commit()
        m.close()
        markets[alias] = fake_db_factory(tmp_path / f"{alias}.db", alias)

    return (fake_db_factory(tmp_path / "fit.db", "fittings"), markets,
            fake_db_factory(tmp_path / "sde.db", "sde"))


def _doctrine_rows(db, fit_id):
    with db.engine.connect() as conn:
        return sorted(tuple(r) for r in conn.exec_driver_sql(
            "SELECT id, type_id, type_name, fit_qty, total_stock, fits_on_mkt "
            f"FROM doctrines WHERE fit_id = {fit_id}"))


def _item_types(db):
    with db.engine.connect() as conn:
        return dict(conn.exec_driver_sql(
            "SELECT id, type_id FROM fittings_fittingitem WHERE type_id = type_fk_id").all())


def test_apply_then_revert_round_trip(tmp_path, fake_db_factory):
    fittings, markets, sde = _make_dbs(tmp_path, fake_db_factory)
    before_items = _item_types(fittings)
    before = {(a, f): _doctrine_rows(db, f) for a, db in markets.items() for f in FIT_SHIPS}

    plan = swap_mwds.build_plan(
        swap_mwds.read_market_fits(markets, fittings, sde), swap_mwds.read_mwd_items(fittings))
    assert [c["item_id"] for c in plan] == [1, 4, 2]
    assert swap_mwds.run_swaps(plan, fittings, markets, sde)

    items = _item_types(fittings)
    assert items[1] == CG_500 and items[2] == II_50 and items[4] == CG_5
    assert {i: items[i] for i in (3, 5, 6, 7, 8)} == {i: before_items[i] for i in (3, 5, 6, 7, 8)}

    # Renamed in place, keeping the row id.
    (row,) = _doctrine_rows(markets["mkta"], 496)
    assert row[0] == before[("mkta", 496)][0][0] and row[1:3] == (CG_500, NAMES[CG_500])
    # Two Cold-Gas items merge into one row with the market's stock figures.
    assert [r[1:] for r in _doctrine_rows(markets["mkta"], 184)] == [
        (CG_5, NAMES[CG_5], 2, 30, 15.0)]
    # A fit with no doctrines rows in a market is skipped, not created, and
    # a stale record for another hull under the same fit_id is left alone.
    assert _doctrine_rows(markets["mktb"], 496) == []
    assert _doctrine_rows(markets["mktb"], 184) == before[("mktb", 184)]
    assert all(db.pushes == 1 for db in (fittings, *markets.values()))

    assert swap_mwds.run_swaps(plan, fittings, markets, sde, reverse=True)

    assert _item_types(fittings) == before_items
    for (alias, fit_id), rows in before.items():
        assert sorted(r[1:4] for r in _doctrine_rows(markets[alias], fit_id)) == \
            sorted(r[1:4] for r in rows), (alias, fit_id)


def test_revert_skips_items_edited_since_apply(tmp_path, fake_db_factory, capsys):
    fittings, markets, sde = _make_dbs(tmp_path, fake_db_factory)
    plan = swap_mwds.build_plan(FIT_SHIPS, ITEMS)
    swap_mwds.run_swaps(plan, fittings, markets, sde)
    with fittings.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE fittings_fittingitem SET type_id = 440 WHERE id = 4")

    assert not swap_mwds.run_swaps(plan, fittings, markets, sde, reverse=True)
    assert "[4]" in capsys.readouterr().out
    assert _item_types(fittings)[1] == QUAD_500


@pytest.mark.parametrize("entry", ["Rokh", 331])
def test_unmatched_targets_reports_missing_entries(entry):
    assert any(m.endswith(f" {entry}") for m in swap_mwds.unmatched_targets(FIT_SHIPS, ITEMS))


def test_failed_push_still_updates_markets(tmp_path, fake_db_factory, capsys):
    fittings, markets, sde = _make_dbs(tmp_path, fake_db_factory)

    def fail():
        raise RuntimeError("network down")
    fittings.push = fail

    assert not swap_mwds.run_swaps(swap_mwds.build_plan(FIT_SHIPS, ITEMS), fittings, markets, sde)
    assert "DatabaseConfig('fittings').push()" in capsys.readouterr().out
    assert _doctrine_rows(markets["mkta"], 496)[0][1] == CG_500
    assert all(db.pushes == 1 for db in markets.values())
