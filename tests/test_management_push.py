"""Every management command must push its writes to Turso.

Under pyturso, DatabaseConfig.engine and DatabaseConfig.remote_engine are the
same local engine: a commit through either leaves the write in the local CDC
queue. Only push() sends it. These tests assert the local row AND the push,
because a test that checks only the row passes on a command whose writes never
leave the machine.

Audit mapping table (Phase 4 — "one push() per logical write transaction")
============================================================================
Built from `rg -n "commit\\(|\\.begin\\(|\\.push\\(|remote_engine" src/mkts_backend`
on 2026-08-26 (final-migration worktree). Line numbers drift; re-run the audit
rather than trusting them.

| Public CLI (registry name)      | Final write location(s)                          | Touched aliases                     | Existing internal push()?              | Intended push boundary / status                  |
|----------------------------------|---------------------------------------------------|--------------------------------------|------------------------------------------|----------------------------------------------------|
| `add_watchlist`                  | `db_utils.add_missing_items_to_watchlist` (via `db.engine`, conn.commit()) | one market alias per invocation, e.g. `wcmktnewkeeptest` | none | **Task 7 (this task)**: push once per touched alias in `add_watchlist.py`'s market loop, after `process_add_watchlist()` succeeds |
| `equiv add` / `equiv remove`     | `equiv_handlers.py` (`db.engine.begin()`), then `sync_equiv_to_remote()` deletes+reinserts via `remote_engine` (no push) | every configured market (`_equiv_add_all` / `_equiv_remove_all`) | none (fake local-only "sync") | **Task 8 (this task)**: deleted `sync_equiv_to_remote` and its 3 call sites (`add_equiv_group` x2, `remove_equiv_group` x1); added `DatabaseConfig(market_context=...).push()` after each market's write in all three loops that call `add_equiv_group`/`remove_equiv_group` — `_equiv_add_all` (`--all`/default), `_equiv_remove_all` (`--all`/default), and `_equiv_find`'s `--add` branch (single-market via `--market=` or default-all) — the third loop was not named in the brief but writes through the same handlers and was found by the `rg` audit below |
| `equiv find <id> --add`          | `equiv_handlers.py add_equiv_group` via `equiv_manager._equiv_find`'s `do_add` branch (`equiv_manager.py:270-280`) | target markets from `--market=` or all (same `_get_target_markets` default as add/remove) | none (same fake sync as above, now deleted) | **Task 8 (this task)**: single-market/all-market audit finding — pushes now added at this loop too |
| `fit-update <subcommand>`        | `fit_update.py` + `doctrine_update.py` (many `engine.begin()` / session.commit() sites); also calls `add_missing_items_to_watchlist` at `fit_update.py:1086` (`_prepare_watchlist_for_fit`) | target market alias (`--market`/`--db-alias`) | none | Task 10 (per Task 7 brief cross-reference) — not implemented here; `add_missing_items_to_watchlist`'s push stays at the *command* boundary, not inside the writer, so fit-update's own push (when added) must not double-push |
| `parse` (fit import, `parse_fits.py`) | `parse_fits.py` (conn.commit() at several lines); calls `add_missing_items_to_watchlist` at `parse_fits.py:837` | target market alias | none | Task 9 (per Task 7 brief cross-reference) — not implemented here |
| `add_structure`                  | `build_cost_utils.upsert_structures(local_db.remote_engine, ...)` (`add_structure.py:154`) | `buildcost` | none | Not yet assigned a task brief as of 2026-08-26; flagged gap, out of scope here |
| `build-watchlist add/remove/mirror` | `build_watchlist_cli.py` → `builder_costs/repository.py` (`upsert_build_watchlist`, `upsert_builder_costs`, ...) | `buildcost` | **yes** — `repository.py` calls `db.push()` at the end of every writer (`:125`, `:161`, `:193`, `:226`, `:252`); `build_watchlist_cli.py:121` also pushes directly | Already compliant — no action needed |
| `update-markets` (main pipeline) | `cli.py` (`db.push()` at `:379`, end of run) | all configured markets processed in the run | **yes** | Already compliant (earlier phase) |

This table covers the writers visible in the Task 7 audit; Tasks 8-12 extend
this file and should re-audit before assuming a row above is still accurate.
"""
import importlib

import pandas as pd
from sqlalchemy import text


def _fake_type_info(type_id: int = 34, type_name: str = "Tritanium") -> pd.DataFrame:
    """Stand-in for db_utils.get_type_info's SDE lookup.

    ``get_type_info`` reads through the module-level ``sde_db = DatabaseConfig("sde")``
    bound at db_utils import time — a monkeypatch of the ``DatabaseConfig`` *class*
    inside db_utils does not affect that already-constructed instance, so without
    this patch these tests transitively hit the real local sdelitetest.db (present
    in this worktree but absent on a clean checkout/CI runner). Pattern matches
    tests/test_schema_integrity.py's ``test_add_missing_items_to_watchlist_skips_existing_type_id``.
    """
    return pd.DataFrame([{
        "type_id": type_id, "type_name": type_name, "group_id": 18,
        "group_name": "Mineral", "category_id": 4, "category_name": "Material",
    }])


def _import_add_watchlist_module():
    """Import the add_watchlist submodule itself, not the re-exported function.

    ``mkts_backend.cli_tools.__init__`` does
    ``from mkts_backend.cli_tools.add_watchlist import add_watchlist``, which
    clobbers the ``cli_tools.add_watchlist`` attribute with the function of
    the same name. Both ``from mkts_backend.cli_tools import add_watchlist``
    and ``import mkts_backend.cli_tools.add_watchlist as add_watchlist``
    resolve through that clobbered attribute and hand back the function, not
    the module — so ``monkeypatch.setattr(add_watchlist, "DatabaseConfig", ...)``
    fails with ``AttributeError: <function add_watchlist> has no attribute``.
    ``importlib.import_module`` reads straight from ``sys.modules`` and
    sidesteps the shadowing.
    """
    return importlib.import_module("mkts_backend.cli_tools.add_watchlist")


class TestWatchlistPush:
    def test_add_watchlist_pushes_after_insert(self, tmp_path, monkeypatch, fake_db_factory):
        add_watchlist = _import_add_watchlist_module()

        db = fake_db_factory(tmp_path / "market.db", alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                "type_name TEXT, group_id INTEGER, group_name TEXT, "
                "category_id INTEGER, category_name TEXT)"
            ))
        monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(add_watchlist, "DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids, remote=False: _fake_type_info(),
        )
        mirror_calls: list[list[int]] = []
        monkeypatch.setattr(
            add_watchlist, "_mirror_to_build_watchlist",
            lambda type_ids: mirror_calls.append(type_ids),
        )

        assert add_watchlist.add_watchlist(["--type-id=34"], market_alias="primary")

        with db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM watchlist WHERE type_id = 34")
            ).scalar() == 1
        assert db.pushes == 1, "watchlist insert never reached Turso"
        assert mirror_calls == [[34]], (
            "build_watchlist mirror must run once every market write and push succeeded"
        )

    def test_push_failure_fails_the_command(self, tmp_path, monkeypatch, fake_db_factory, capsys):
        add_watchlist = _import_add_watchlist_module()

        db = fake_db_factory(tmp_path / "market.db", alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            # Full column set so the insert genuinely succeeds inside
            # add_missing_items_to_watchlist and execution actually reaches
            # push() below — a one-column table makes the INSERT itself fail
            # first, leaving `db.push = boom` dead code.
            conn.execute(text(
                "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                "type_name TEXT, group_id INTEGER, group_name TEXT, "
                "category_id INTEGER, category_name TEXT)"
            ))

        def boom():
            raise RuntimeError("turso unreachable")

        db.push = boom
        monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(add_watchlist, "DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids, remote=False: _fake_type_info(),
        )
        mirror_calls: list[list[int]] = []
        monkeypatch.setattr(
            add_watchlist, "_mirror_to_build_watchlist",
            lambda type_ids: mirror_calls.append(type_ids),
        )

        assert add_watchlist.add_watchlist(["--type-id=34"], market_alias="primary") is False

        # The row was written locally (the writer's job) before the push
        # that reports it failed.
        with db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM watchlist WHERE type_id = 34")
            ).scalar() == 1
        assert mirror_calls == [], (
            "mirror must not run when a market write's push failed"
        )
        captured = capsys.readouterr()
        assert "push failed" in captured.out, (
            "push() must actually have been called and raised for this test to be meaningful"
        )


_EQUIV_TABLE_SQL = (
    "CREATE TABLE module_equivalents ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "equiv_group_id INTEGER, type_id INTEGER, type_name TEXT)"
)


def _equiv_dbs_factory(tmp_path, fake_db_factory, dbs):
    """Build a ``DatabaseConfig``-shaped factory keyed by database_alias.

    Both ``equiv_handlers._get_db`` (called with ``market_context=``) and the
    push call in ``equiv_manager``'s market loops (also called with
    ``market_context=``) resolve to the SAME cached fake db per alias, so a
    write made through the handler and the push made through the command
    loop land on one in-memory sqlite file.
    """
    def factory(alias=None, market_context=None):
        key = alias or market_context.database_alias
        if key not in dbs:
            db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
            with db.engine.begin() as conn:
                conn.execute(text(_EQUIV_TABLE_SQL))
            dbs[key] = db
        return dbs[key]
    return factory


class TestEquivPush:
    """``equiv add``/``equiv remove`` write module_equivalents through
    ``equiv_handlers`` and must push once per touched market from the
    command boundary in ``equiv_manager.py`` (Task 8).
    """

    def test_equiv_add_pushes_each_market(self, tmp_path, monkeypatch, fake_db_factory):
        from mkts_backend.cli_tools import equiv_manager
        from mkts_backend.db import equiv_handlers
        from mkts_backend.config.settings_service import get_all_market_contexts

        dbs = {}
        factory = _equiv_dbs_factory(tmp_path, fake_db_factory, dbs)

        monkeypatch.setattr(equiv_handlers, "DatabaseConfig", factory)
        monkeypatch.setattr(equiv_manager, "DatabaseConfig", factory)
        monkeypatch.setattr(equiv_handlers, "resolve_type_name", lambda tid: f"Module {tid}")
        monkeypatch.setattr(equiv_manager, "resolve_type_name", lambda tid: f"Module {tid}")

        assert equiv_manager.equiv_command(["add", "--type-ids=11269,11270"], "primary")

        expected = {c.database_alias for c in get_all_market_contexts().values()}
        assert set(dbs) == expected
        assert all(db.pushes == 1 for db in dbs.values()), {
            k: v.pushes for k, v in dbs.items()
        }

    def test_one_market_failure_does_not_abort_the_rest(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """equiv iterates every configured market; a single unreachable
        remote must not leave the others unwritten."""
        from mkts_backend.cli_tools import equiv_manager
        from mkts_backend.db import equiv_handlers
        from mkts_backend.config.market_context import MarketContext
        from mkts_backend.config.settings_service import get_all_market_contexts

        market_order = MarketContext.list_available()
        first_alias = MarketContext.from_settings(market_order[0]).database_alias

        dbs = {}
        factory = _equiv_dbs_factory(tmp_path, fake_db_factory, dbs)

        # Pre-seed the first market's db with a push that always raises,
        # before the command runs, so the loop hits it on its first market.
        broken = factory(market_context=MarketContext.from_settings(market_order[0]))

        def boom():
            raise RuntimeError("turso unreachable")
        broken.push = boom

        monkeypatch.setattr(equiv_handlers, "DatabaseConfig", factory)
        monkeypatch.setattr(equiv_manager, "DatabaseConfig", factory)
        monkeypatch.setattr(equiv_handlers, "resolve_type_name", lambda tid: f"Module {tid}")
        monkeypatch.setattr(equiv_manager, "resolve_type_name", lambda tid: f"Module {tid}")

        result = equiv_manager.equiv_command(["add", "--type-ids=11269,11270"], "primary")

        assert result is False
        expected = {c.database_alias for c in get_all_market_contexts().values()}
        assert set(dbs) == expected
        for alias, db in dbs.items():
            if alias == first_alias:
                assert db.pushes == 0, "the broken market's push raised; it must not be counted"
            else:
                assert db.pushes == 1, {k: v.pushes for k, v in dbs.items()}

    def test_equiv_find_add_pushes_the_single_target_market(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """``equiv find <id> --add --market=<alias>`` writes exactly one
        market and must push it too — the third call site found by the
        Task 8 audit (`equiv_manager.py`'s `_equiv_find` `do_add` branch).
        """
        from mkts_backend.cli_tools import equiv_manager
        from mkts_backend.db import equiv_handlers
        from mkts_backend.config.market_context import MarketContext

        primary_alias = MarketContext.from_settings("primary").database_alias

        dbs = {}
        factory = _equiv_dbs_factory(tmp_path, fake_db_factory, dbs)

        monkeypatch.setattr(equiv_handlers, "DatabaseConfig", factory)
        monkeypatch.setattr(equiv_manager, "DatabaseConfig", factory)
        monkeypatch.setattr(equiv_handlers, "resolve_type_name", lambda tid: f"Module {tid}")
        monkeypatch.setattr(equiv_manager, "resolve_type_name", lambda tid: f"Module {tid}")
        monkeypatch.setattr(
            equiv_manager, "find_equiv_by_attributes",
            lambda type_id: [
                {"typeID": type_id, "typeName": "Module A", "groupName": "g", "metaGroupName": "m"},
                {"typeID": type_id + 1, "typeName": "Module B", "groupName": "g", "metaGroupName": "m"},
            ],
        )

        assert equiv_manager.equiv_command(
            ["find", "11269", "--add", "--market=primary"], "primary"
        )

        assert set(dbs) == {primary_alias}
        assert dbs[primary_alias].pushes == 1
