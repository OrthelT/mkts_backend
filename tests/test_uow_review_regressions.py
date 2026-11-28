"""Regression tests for review findings 2-10 on the UoW refactor.

Finding 1 (circular import) and ReplicaUnitOfWork primitive behavior for
findings 2 and 5 are covered in ``tests/test_unit_of_work.py``.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pandas as pd
from sqlalchemy import text

from mkts_backend.cli_tools import equiv_manager, fit_update
from mkts_backend.cli_tools.fit_manager import _plan_succeeded
from mkts_backend.config.market_context import MarketContext
from mkts_backend.db import equiv_handlers
from mkts_backend.utils.doctrine_update import DoctrineFit, upsert_doctrine_fits


def _import_add_watchlist_module():
    return importlib.import_module("mkts_backend.cli_tools.add_watchlist")


def _seed_doctrine_remove_dbs(tmp_path, fake_db_factory, dbs):
    def factory(alias=None, market_context=None):
        key = alias or market_context.database_alias
        if key not in dbs:
            db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
            with db.engine.begin() as conn:
                if key == "fittings":
                    conn.execute(text(
                        "CREATE TABLE fittings_fitting (id INTEGER PRIMARY KEY, "
                        "description TEXT, name TEXT, ship_type_id INTEGER)"
                    ))
                    conn.execute(text(
                        "CREATE TABLE fittings_doctrine (id INTEGER PRIMARY KEY, "
                        "name TEXT, description TEXT)"
                    ))
                    conn.execute(text(
                        "CREATE TABLE fittings_doctrine_fittings (id INTEGER "
                        "PRIMARY KEY, doctrine_id INTEGER, fitting_id INTEGER)"
                    ))
                elif key == "sde":
                    conn.execute(text(
                        "CREATE TABLE sdetypes (typeID INTEGER PRIMARY KEY, typeName TEXT)"
                    ))
                else:
                    conn.execute(text("CREATE TABLE doctrines (fit_id INTEGER)"))
                    conn.execute(text(
                        "CREATE TABLE doctrine_map (fitting_id INTEGER, doctrine_id INTEGER)"
                    ))
                    conn.execute(text(
                        "CREATE TABLE doctrine_fits (fit_id INTEGER, doctrine_id INTEGER)"
                    ))
                    conn.execute(text("CREATE TABLE ship_targets (fit_id INTEGER PRIMARY KEY)"))
            dbs[key] = db
        return dbs[key]

    return factory


class TestFinding2And5DoctrineRemoveFit:
    def test_later_fit_rollback_still_pushes_earlier_commit_and_returns_false(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Finding 2: fit 1 commits, fit 2 raises inside uow.tx(db_alias).
        Fit 1's committed delete must still push, and the command must return False."""
        dbs = {}
        factory = _seed_doctrine_remove_dbs(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.parse_fits.DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases", lambda market_flag=None: ["mkt1"]
        )

        fittings_db = factory("fittings")
        with fittings_db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fittings_doctrine (id, name, description) VALUES (12, 'D', '')"
            ))
            for fid in (501, 502):
                conn.execute(text(
                    "INSERT INTO fittings_fitting (id, name, description, ship_type_id) "
                    f"VALUES ({fid}, 'Fit {fid}', '', 587)"
                ))
                conn.execute(text(
                    f"INSERT INTO fittings_doctrine_fittings (id, doctrine_id, fitting_id) "
                    f"VALUES ({fid}, 12, {fid})"
                ))

        mkt_db = factory("mkt1")
        with mkt_db.engine.begin() as conn:
            for fid in (501, 502):
                conn.execute(text(f"INSERT INTO doctrine_map VALUES ({fid}, 12)"))
                conn.execute(text(f"INSERT INTO doctrine_fits VALUES ({fid}, 12)"))

        real_remove = fit_update.remove_doctrine_fits

        def fail_on_second(conn, doctrine_id, fit_id):
            if fit_id == 502:
                raise RuntimeError("simulated db error on fit 2")
            return real_remove(conn, doctrine_id, fit_id)

        monkeypatch.setattr(fit_update, "remove_doctrine_fits", fail_on_second)

        result = fit_update.doctrine_remove_fit_command(
            doctrine_id=12, fit_ids=[501, 502], interactive=False, db_alias="mkt1"
        )

        assert result is False
        assert dbs["mkt1"].pushes == 1, "fit 1's committed market delete must still be pushed"
        assert dbs["fittings"].pushes == 1, "fit 1's committed fittings unlink must still be pushed"
        with dbs["mkt1"].engine.connect() as conn:
            remaining = [
                r[0] for r in conn.execute(text("SELECT fit_id FROM doctrine_fits")).fetchall()
            ]
        assert remaining == [502], "fit 1 committed, fit 2 rolled back"

    def test_duplicate_fit_ids_do_not_drop_fittings_push(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Finding 5: --fit-id=503,503 deletes the link on pass 1, then returns
        False on pass 2; fittings must still be pushed once."""
        dbs = {}
        factory = _seed_doctrine_remove_dbs(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.parse_fits.DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases", lambda market_flag=None: ["mkt1"]
        )

        fittings_db = factory("fittings")
        with fittings_db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fittings_doctrine (id, name, description) VALUES (12, 'D', '')"
            ))
            conn.execute(text(
                "INSERT INTO fittings_fitting (id, name, description, ship_type_id) "
                "VALUES (503, 'Fit 503', '', 587)"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine_fittings (id, doctrine_id, fitting_id) "
                "VALUES (1, 12, 503)"
            ))

        mkt_db = factory("mkt1")
        with mkt_db.engine.begin() as conn:
            conn.execute(text("INSERT INTO doctrine_map VALUES (503, 12)"))
            conn.execute(text("INSERT INTO doctrine_fits VALUES (503, 12)"))

        result = fit_update.doctrine_remove_fit_command(
            doctrine_id=12, fit_ids=[503, 503], interactive=False, db_alias="mkt1"
        )

        assert result is True
        assert dbs["fittings"].pushes == 1, "pass 2 returning False must not erase pass 1's commit"
        assert dbs["mkt1"].pushes == 1


class TestFinding3LaterMarketExceptionDoesNotSkipEarlierPushes:
    def _setup_equiv(self, tmp_path, monkeypatch, fake_db_factory, dbs):
        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE TABLE module_equivalents ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "equiv_group_id INTEGER, type_id INTEGER, type_name TEXT)"
                    ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", factory)
        monkeypatch.setattr(equiv_handlers, "DatabaseConfig", factory)
        monkeypatch.setattr(equiv_handlers, "resolve_type_name", lambda tid: f"Mod {tid}")
        monkeypatch.setattr(equiv_manager, "resolve_type_name", lambda tid: f"Mod {tid}")
        return factory

    def test_equiv_add_pushes_earlier_markets_when_later_market_raises(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        markets = MarketContext.list_available()
        assert len(markets) >= 2
        first_alias = MarketContext.from_settings(markets[0]).database_alias
        second_alias = MarketContext.from_settings(markets[1]).database_alias

        dbs = {}
        self._setup_equiv(tmp_path, monkeypatch, fake_db_factory, dbs)
        real_add = equiv_manager.add_equiv_group

        def fail_second(type_ids, market_ctx, conn=None):
            if market_ctx.database_alias == second_alias:
                raise RuntimeError("market 2 write exploded")
            return real_add(type_ids, market_ctx, conn=conn)

        monkeypatch.setattr(equiv_manager, "add_equiv_group", fail_second)

        result = equiv_manager.equiv_command(["add", "--type-ids=100,101"], "primary")

        assert result is False
        assert dbs[first_alias].pushes == 1
        assert dbs[second_alias].pushes == 0

    def test_equiv_remove_pushes_earlier_markets_when_later_market_raises(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        markets = MarketContext.list_available()
        assert len(markets) >= 2
        first_alias = MarketContext.from_settings(markets[0]).database_alias
        second_alias = MarketContext.from_settings(markets[1]).database_alias

        dbs = {}
        self._setup_equiv(tmp_path, monkeypatch, fake_db_factory, dbs)
        real_remove = equiv_manager.remove_equiv_group

        def fail_second(group_id, market_ctx, conn=None):
            if market_ctx.database_alias == second_alias:
                raise RuntimeError("market 2 remove exploded")
            return real_remove(group_id, market_ctx, conn=conn)

        monkeypatch.setattr(equiv_manager, "remove_equiv_group", fail_second)

        result = equiv_manager.equiv_command(["remove", "--id=1"], "primary")

        assert result is False
        assert dbs[first_alias].pushes == 1
        assert dbs[second_alias].pushes == 0

    def test_equiv_find_add_pushes_earlier_markets_when_later_market_raises(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        markets = MarketContext.list_available()
        assert len(markets) >= 2
        first_alias = MarketContext.from_settings(markets[0]).database_alias
        second_alias = MarketContext.from_settings(markets[1]).database_alias

        dbs = {}
        self._setup_equiv(tmp_path, monkeypatch, fake_db_factory, dbs)
        monkeypatch.setattr(
            equiv_manager,
            "find_equiv_by_attributes",
            lambda tid: [
                {"typeID": tid, "typeName": "A", "groupName": "g", "metaGroupName": "m"},
                {"typeID": tid + 1, "typeName": "B", "groupName": "g", "metaGroupName": "m"},
            ],
        )
        real_add = equiv_manager.add_equiv_group

        def fail_second(type_ids, market_ctx, conn=None):
            if market_ctx.database_alias == second_alias:
                raise RuntimeError("market 2 find --add exploded")
            return real_add(type_ids, market_ctx, conn=conn)

        monkeypatch.setattr(equiv_manager, "add_equiv_group", fail_second)

        result = equiv_manager.equiv_command(["find", "100", "--add"], "primary")

        assert result is False
        assert dbs[first_alias].pushes == 1
        assert dbs[second_alias].pushes == 0

    def test_friendly_name_commands_push_earlier_markets_on_later_exception(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs = {}

        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE TABLE doctrine_fits (fit_id INTEGER PRIMARY KEY, "
                        "doctrine_id INTEGER, friendly_name TEXT)"
                    ))
                    conn.execute(text(
                        "INSERT INTO doctrine_fits (fit_id, doctrine_id) VALUES (1, 50)"
                    ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases", lambda market_flag=None: ["mkt_a", "mkt_b"]
        )

        # update_friendly_name_command: mkt_a succeeds, mkt_b raises
        real_update = fit_update.update_doctrine_friendly_name
        calls = []

        def fail_b_update(conn, doctrine_id, friendly_name):
            calls.append(doctrine_id)
            if len(calls) == 2:
                raise RuntimeError("mkt_b broke")
            return real_update(conn, doctrine_id, friendly_name)

        monkeypatch.setattr(fit_update, "update_doctrine_friendly_name", fail_b_update)

        res1 = fit_update.update_friendly_name_command(50, "Hurricanes", db_alias="mkt_a")
        assert res1 is False
        assert dbs["mkt_a"].pushes == 1
        assert dbs["mkt_b"].pushes == 0

        # populate_friendly_names_command: mkt_a succeeds, mkt_b raises
        dbs["mkt_a"].pushes = 0
        json_path = tmp_path / "names.json"
        json_path.write_text(json.dumps([{"fit_id": 1, "doctrine_id": 50, "friendly_name": "HFI"}]))
        real_pop = fit_update.populate_friendly_names_from_json
        pop_calls = []

        def fail_b_pop(conn, path):
            pop_calls.append(path)
            if len(pop_calls) == 2:
                raise RuntimeError("mkt_b populate broke")
            return real_pop(conn, path)

        monkeypatch.setattr(fit_update, "populate_friendly_names_from_json", fail_b_pop)

        res2 = fit_update.populate_friendly_names_command(str(json_path), db_alias="mkt_a")
        assert res2 is False
        assert dbs["mkt_a"].pushes == 1
        assert dbs["mkt_b"].pushes == 0


class TestFinding4WatchlistPartialWriteRollback:
    def test_failed_second_insert_rolls_back_first_row(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Finding 4: when row 2 fails inside add_missing_items_to_watchlist,
        uow.tx() must roll back row 1 instead of committing a partial write."""
        add_watchlist = _import_add_watchlist_module()

        db = fake_db_factory(tmp_path / "market.db", alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                "type_name TEXT NOT NULL, group_id INTEGER, group_name TEXT, "
                "category_id INTEGER, category_name TEXT)"
            ))

        monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", lambda *a, **k: db)
        # Row 1 is valid; Row 2 has NULL type_name -> violates NOT NULL mid-loop.
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids: pd.DataFrame([
                {
                    "type_id": 34, "type_name": "Tritanium", "group_id": 18,
                    "group_name": "Mineral", "category_id": 4, "category_name": "Material",
                },
                {
                    "type_id": 35, "type_name": None, "group_id": 18,
                    "group_name": "Mineral", "category_id": 4, "category_name": "Material",
                },
            ]),
        )
        monkeypatch.setattr(add_watchlist, "_mirror_to_build_watchlist", lambda type_ids: None)

        result = add_watchlist.add_watchlist(["--type-id=34,35"], market_alias="primary")

        assert result is False
        assert db.pushes == 0
        with db.engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM watchlist")).scalar() == 0


class TestFinding6PromptCreateDoctrineExistingName:
    def test_existing_doctrine_id_returns_stored_name_not_user_input(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        fittings_db = fake_db_factory(tmp_path / "fittings.db", alias="fittings")
        with fittings_db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE fittings_doctrine (id INTEGER PRIMARY KEY, name TEXT, "
                "icon_url TEXT, description TEXT, created TEXT, last_updated TEXT)"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine (id, name, icon_url, description, created, last_updated) "
                "VALUES (42, 'Canonical Doctrine Name', '', '', '', '')"
            ))

        monkeypatch.setattr(fit_update, "DatabaseConfig", lambda *a, **k: fittings_db)
        monkeypatch.setattr(fit_update, "get_next_doctrine_id", lambda: 43)
        monkeypatch.setattr(fit_update.IntPrompt, "ask", lambda *a, **k: 42)
        answers = iter(["Wrong Typed Name", "some desc"])
        monkeypatch.setattr(fit_update.Prompt, "ask", lambda *a, **k: next(answers))

        doc_id, doc_name, create_args = fit_update._prompt_new_doctrine()

        assert doc_id == 42
        assert doc_name == "Canonical Doctrine Name"
        assert create_args is None
        assert fittings_db.pushes == 0


class TestFinding7DoctrineFitDisposesEngines:
    def test_post_init_disposes_standalone_engines(self, tmp_path, monkeypatch, fake_db_factory):
        fittings_db = fake_db_factory(tmp_path / "fittings.db", alias="fittings")
        sde_db = fake_db_factory(tmp_path / "sde.db", alias="sde")
        with fittings_db.engine.begin() as conn:
            conn.execute(text("CREATE TABLE fittings_doctrine (id INTEGER PRIMARY KEY, name TEXT)"))
            conn.execute(text(
                "CREATE TABLE fittings_fitting (id INTEGER PRIMARY KEY, name TEXT, ship_type_id INTEGER)"
            ))
            conn.execute(text("INSERT INTO fittings_doctrine VALUES (10, 'Alpha')"))
            conn.execute(text("INSERT INTO fittings_fitting VALUES (100, 'Rifter Fit', 587)"))
        with sde_db.engine.begin() as conn:
            conn.execute(text("CREATE TABLE sdetypes (typeID INTEGER PRIMARY KEY, typeName TEXT)"))
            conn.execute(text("INSERT INTO sdetypes VALUES (587, 'Rifter')"))

        disposed: list[str] = []
        for name, db in (("fittings", fittings_db), ("sde", sde_db)):
            orig_dispose = db.engine.dispose
            db.engine.dispose = lambda n=name, fn=orig_dispose: (disposed.append(n), fn())

        dbs = {"fittings": fittings_db, "sde": sde_db}
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", dbs.__getitem__)

        df = DoctrineFit(doctrine_id=10, fit_id=100, target=50)

        assert df.doctrine_name == "Alpha"
        assert df.ship_name == "Rifter"
        assert set(disposed) == {"fittings", "sde"}


class TestFinding8PreserveMarketFlagOnUpdate:
    def test_upsert_doctrine_fits_preserves_existing_flag_when_omitted(
        self, tmp_path, fake_db_factory
    ):
        db = fake_db_factory(tmp_path / "m.db", alias="m")
        with db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE doctrine_fits (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "doctrine_name TEXT, fit_name TEXT, ship_type_id INTEGER, doctrine_id INTEGER, "
                "fit_id INTEGER, ship_name TEXT, target INTEGER, market_flag TEXT)"
            ))
            df = DoctrineFit.from_resolved(
                doctrine_id=10, fit_id=313, target=100,
                doctrine_name="Doc", fit_name="Old Fit", ship_type_id=587, ship_name="Rifter",
            )
            # Initial insert with explicit deployment flag
            upsert_doctrine_fits(conn, df, market_flag="deployment")
            # Subsequent update omitting market_flag (as fit-manager update does)
            df2 = DoctrineFit.from_resolved(
                doctrine_id=10, fit_id=313, target=200,
                doctrine_name="Doc", fit_name="New Fit", ship_type_id=587, ship_name="Rifter",
            )
            upsert_doctrine_fits(conn, df2)

            row = conn.execute(
                text("SELECT fit_name, target, market_flag FROM doctrine_fits WHERE fit_id = 313")
            ).fetchone()
            assert row == ("New Fit", 200, "deployment")

            # New row without market_flag still defaults to 'primary'
            df_new = DoctrineFit.from_resolved(
                doctrine_id=10, fit_id=314, target=50,
                doctrine_name="Doc", fit_name="Default Fit", ship_type_id=587, ship_name="Rifter",
            )
            upsert_doctrine_fits(conn, df_new)
            row_new = conn.execute(
                text("SELECT market_flag FROM doctrine_fits WHERE fit_id = 314")
            ).fetchone()
            assert row_new == ("primary",)


class TestFinding9HealWorkCounted:
    def test_heal_if_needed_work_counts_as_updated_and_succeeds(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs = {}

        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                dbs[key] = fake_db_factory(tmp_path / f"{key}.db", alias=key)
            return dbs[key]

        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases", lambda market_flag=None: ["aliasA"]
        )
        monkeypatch.setattr(fit_update, "_flag_to_aliases", lambda flag: {"aliasA"})
        monkeypatch.setattr(fit_update, "_prepare_watchlist_for_fit", lambda *a, **k: None)
        monkeypatch.setattr(fit_update, "_apply_step", lambda conn, step_type, p, arg, alias="": True)

        # _plan_market_action emits action="skip" when market_flag == target_market
        plan = fit_update._plan_market_action(
            {
                "doctrine_id": 10, "fit_id": 1, "market_flag": "primary",
                "doctrine_name": "D", "fit_name": "F", "ship_name": "Rifter",
                "ship_type_id": 587, "target": 10,
            },
            target_market="primary",
            mode="assign",
        )
        assert plan["action"] == "skip"

        result = fit_update._execute_market_plan([plan], db_alias="aliasA")

        assert result["updated"] == 1
        assert result["skipped"] == 0
        assert _plan_succeeded(result, "updated") is True
        assert dbs["aliasA"].pushes == 1


class TestFinding10WatchlistPrepInsideUow:
    def test_execute_market_plan_runs_watchlist_prep_on_uow_connections_and_rolls_back_on_bucket_failure(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs = {}

        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    if key == "fittings":
                        conn.execute(text(
                            "CREATE TABLE fittings_fittingitem (fit_id INTEGER, type_id INTEGER)"
                        ))
                        conn.execute(text("INSERT INTO fittings_fittingitem VALUES (1, 2048)"))
                    else:
                        conn.execute(text(
                            "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                            "type_name TEXT, group_id INTEGER, group_name TEXT, "
                            "category_id INTEGER, category_name TEXT)"
                        ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases", lambda market_flag=None: ["aliasA", "aliasB"]
        )
        monkeypatch.setattr(
            fit_update, "_flag_to_aliases",
            lambda flag: {"aliasA"} if flag == "A" else {"aliasB"},
        )
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids: pd.DataFrame([
                {
                    "type_id": tid, "type_name": f"Type {tid}", "group_id": 18,
                    "group_name": "G", "category_id": 6, "category_name": "C",
                }
                for tid in type_ids
            ]),
        )

        # aliasA succeeds; aliasB raises a DB error after watchlist prep ran inside uow.tx(aliasB)
        def fake_apply_step(conn, step_type, p, arg, alias=""):
            if alias == "aliasB":
                raise ConnectionError("bucket failed after watchlist prep")
            return True

        monkeypatch.setattr(fit_update, "_apply_step", fake_apply_step)

        plans = [
            {
                "fit_id": 1, "doctrine_id": 10, "action": "update",
                "market_flag": "A", "new_flag": "A", "doctrine_name": "D",
                "ship_type_id": 587,
            },
            {
                "fit_id": 1, "doctrine_id": 10, "action": "update",
                "market_flag": "B", "new_flag": "B", "doctrine_name": "D",
                "ship_type_id": 587,
            },
        ]

        result = fit_update._execute_market_plan(plans, db_alias="aliasA")

        assert result["bucket_failures"] == 1
        assert dbs["aliasA"].pushes == 1
        assert dbs["aliasB"].pushes == 0
        # aliasA committed watchlist rows and pushed them
        with dbs["aliasA"].engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM watchlist")).scalar() == 2
        # aliasB rolled back watchlist rows atomically with the bucket (no stranded write)
        with dbs["aliasB"].engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM watchlist")).scalar() == 0

    def test_doctrine_add_fit_runs_watchlist_prep_inside_market_tx(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs = {}

        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    if key == "fittings":
                        conn.execute(text(
                            "CREATE TABLE fittings_fittingitem (fit_id INTEGER, type_id INTEGER)"
                        ))
                        conn.execute(text("INSERT INTO fittings_fittingitem VALUES (1, 2048)"))
                    elif key != "sde":
                        conn.execute(text(
                            "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                            "type_name TEXT, group_id INTEGER, group_name TEXT, "
                            "category_id INTEGER, category_name TEXT)"
                        ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases", lambda *a, **k: ["aliasA"]
        )
        monkeypatch.setattr(fit_update, "get_doctrine_fits_from_market", lambda *a, **k: [])
        monkeypatch.setattr(
            fit_update, "get_available_doctrines", lambda: [{"id": 10, "name": "D"}]
        )
        monkeypatch.setattr(
            fit_update, "get_fit_info",
            lambda fid: {"fit_id": fid, "fit_name": "F", "ship_name": "Rifter", "ship_type_id": 587},
        )
        monkeypatch.setattr(fit_update, "_lookup_fit_target", lambda *a, **k: None)
        monkeypatch.setattr(fit_update, "ensure_doctrine_link", lambda *a, **k: None)
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids: pd.DataFrame([
                {
                    "type_id": tid, "type_name": f"Type {tid}", "group_id": 18,
                    "group_name": "G", "category_id": 6, "category_name": "C",
                }
                for tid in type_ids
            ]),
        )

        class _FakeDF:
            @classmethod
            def from_connections(cls, fconn, sconn, doctrine_id, fit_id, target):
                return DoctrineFit.from_resolved(
                    doctrine_id=doctrine_id, fit_id=fit_id, target=target,
                    doctrine_name="D", fit_name="F", ship_type_id=587, ship_name="Rifter",
                )

        monkeypatch.setattr(fit_update, "DoctrineFit", _FakeDF)
        # Provisioning fails with DB error -> watchlist insert must roll back with it
        monkeypatch.setattr(
            fit_update, "_provision_fit_in_market",
            lambda conn, p, market_flag: (_ for _ in ()).throw(ConnectionError("outage")),
        )

        result = fit_update.doctrine_add_fit_command(
            doctrine_id=10, fit_ids=[1], target=100, market_flag="primary",
            interactive=False, db_alias="aliasA",
        )

        assert result is False
        assert dbs["aliasA"].pushes == 0
        with dbs["aliasA"].engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM watchlist")).scalar() == 0


class TestDoctrineAddFitNonDbError:
    def test_non_db_error_in_watchlist_prep_fails_the_market_and_still_pushes_fittings(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs = {}

        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    if key == "fittings":
                        conn.execute(text(
                            "CREATE TABLE fittings_fittingitem (fit_id INTEGER, type_id INTEGER)"
                        ))
                        conn.execute(text("INSERT INTO fittings_fittingitem VALUES (1, 2048)"))
                    elif key != "sde":
                        conn.execute(text(
                            "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                            "type_name TEXT, group_id INTEGER, group_name TEXT, "
                            "category_id INTEGER, category_name TEXT)"
                        ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases", lambda *a, **k: ["aliasA"]
        )
        monkeypatch.setattr(fit_update, "get_doctrine_fits_from_market", lambda *a, **k: [])
        monkeypatch.setattr(
            fit_update, "get_available_doctrines", lambda: [{"id": 10, "name": "D"}]
        )
        monkeypatch.setattr(
            fit_update, "get_fit_info",
            lambda fid: {"fit_id": fid, "fit_name": "F", "ship_name": "Rifter", "ship_type_id": 587},
        )
        monkeypatch.setattr(fit_update, "_lookup_fit_target", lambda *a, **k: None)
        monkeypatch.setattr(fit_update, "ensure_doctrine_link", lambda *a, **k: None)
        # A NaN group_id makes int() raise ValueError inside the watchlist insert.
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids: pd.DataFrame([
                {
                    "type_id": tid, "type_name": f"Type {tid}", "group_id": float("nan"),
                    "group_name": "G", "category_id": 6, "category_name": "C",
                }
                for tid in type_ids
            ]),
        )

        class _FakeDF:
            @classmethod
            def from_connections(cls, fconn, sconn, doctrine_id, fit_id, target):
                return DoctrineFit.from_resolved(
                    doctrine_id=doctrine_id, fit_id=fit_id, target=target,
                    doctrine_name="D", fit_name="F", ship_type_id=587, ship_name="Rifter",
                )

        monkeypatch.setattr(fit_update, "DoctrineFit", _FakeDF)

        result = fit_update.doctrine_add_fit_command(
            doctrine_id=10, fit_ids=[1], target=100, market_flag="primary",
            interactive=False, db_alias="aliasA",
        )

        assert result is False
        assert dbs["fittings"].pushes == 1, "the committed fittings link must be pushed"
        assert dbs["aliasA"].pushes == 0, "the rolled-back market must not be pushed"


class TestInteractiveAddDefersDoctrineCreation:
    """``fit-manager add --interactive`` with a new doctrine writes nothing
    before the dry-run check and the final confirmation."""

    def _setup(self, tmp_path, monkeypatch, fake_db_factory, *, proceed=True):
        dbs = {}

        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                if key == "fittings":
                    with db.engine.begin() as conn:
                        conn.execute(text(
                            "CREATE TABLE fittings_doctrine (id INTEGER PRIMARY KEY, name TEXT, "
                            "icon_url TEXT, description TEXT, created TEXT, last_updated TEXT)"
                        ))
                dbs[key] = db
            return dbs[key]

        answers = {
            "Fit description": "fit desc",
            "Choose action": "new",
            "New doctrine ID": 77,
            "New doctrine name": "Typed Doctrine",
            "]Description[": "doctrine desc",
            "Target quantity": 100,
            "Fit ID": 500,
            "Market assignment": "primary",
            "Proceed with adding": proceed,
        }

        def ask(prompt, *args, **kwargs):
            for key, value in answers.items():
                if key in prompt:
                    return value
            raise AssertionError(f"unexpected prompt: {prompt}")

        for prompt_cls in (fit_update.Prompt, fit_update.IntPrompt, fit_update.Confirm):
            monkeypatch.setattr(prompt_cls, "ask", ask)

        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(fit_update, "get_next_doctrine_id", lambda: 77)
        monkeypatch.setattr(
            fit_update, "get_available_doctrines", lambda: [{"id": 1, "name": "Existing"}]
        )
        monkeypatch.setattr(
            fit_update, "parse_eft_file",
            lambda path: SimpleNamespace(
                ship_name="Rifter", fit_name="F", items=[], has_missing_types=False,
                missing_types=[], ship_type_id=587,
            ),
        )
        monkeypatch.setattr(fit_update, "update_fit_workflow", lambda **kwargs: None)
        monkeypatch.setattr(fit_update, "update_fit_market_flag", lambda *a, **k: None)
        monkeypatch.setattr(fit_update, "upsert_lead_ship", lambda *a, **k: False)
        return dbs, factory

    def _doctrine_rows(self, factory):
        with factory("fittings").engine.connect() as conn:
            return conn.execute(text("SELECT id, name FROM fittings_doctrine")).fetchall()

    def test_dry_run_does_not_create_or_push_the_new_doctrine(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs, factory = self._setup(tmp_path, monkeypatch, fake_db_factory)

        result = fit_update.interactive_add_fit(
            fit_file="fit.txt", dry_run=True, target_alias="aliasA", market_flag="primary"
        )

        assert result is True
        assert self._doctrine_rows(factory) == []
        assert dbs["fittings"].pushes == 0

    def test_declining_the_confirmation_does_not_create_the_new_doctrine(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs, factory = self._setup(tmp_path, monkeypatch, fake_db_factory, proceed=False)

        result = fit_update.interactive_add_fit(
            fit_file="fit.txt", target_alias="aliasA", market_flag="primary"
        )

        assert result is False
        assert self._doctrine_rows(factory) == []
        assert dbs["fittings"].pushes == 0

    def test_confirmed_add_creates_the_doctrine_with_the_typed_name_and_pushes_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        dbs, factory = self._setup(tmp_path, monkeypatch, fake_db_factory)

        result = fit_update.interactive_add_fit(
            fit_file="fit.txt", target_alias="aliasA", market_flag="primary"
        )

        assert result is True
        assert [tuple(r) for r in self._doctrine_rows(factory)] == [(77, "Typed Doctrine")]
        assert dbs["fittings"].pushes == 1
