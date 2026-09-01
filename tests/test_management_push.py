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
| `fit-update add` / `update-fit` (shared `fittings` writes via `update_fit_workflow`) | `parse_fits.py`'s `update_fit_workflow` (writes `fittings` + one market replica per call); also calls `add_missing_items_to_watchlist` | shared `fittings` + the resolved target market alias, once per CLI invocation | none | **Task 9 (this task)**: `update_fit_workflow` never pushes itself — it takes an optional `touched_aliases: set` accumulator and adds `"fittings"` and its resolved `target_alias` on success (never on a dry run). Every outer caller (`interactive_add_fit`, `fit_update_command`'s `add` and `update` subcommands in `fit_update.py`, and `command_registry.py`'s `update-fit` handler) owns one set for its whole invocation, threads it through every workflow call, and pushes each distinct alias once at the end (skipped entirely on `--dry-run`; a push failure fails the command). Standalone `create_doctrine_command` pushes `"fittings"` once after `create_doctrine()` succeeds. A source-scan test (`TestUpdateFitWorkflowCallSiteCoverage`) asserts every `update_fit_workflow(` call site passes `touched_aliases=`. Two dead unreachable wrappers, `update_existing_fit`/`update_fit` in `parse_fits.py` (no callers anywhere in `src`/`tests`), were deleted rather than plumbed, per the "minimize codebase size" standing preference. |
| `fit-update update-target` / `update-friendly-name` / `populate-friendly-names` / `remove` / `doctrine-remove-fit` | `fit_update.py` (many `engine.begin()` / session.commit() sites); also calls `add_missing_items_to_watchlist` at `fit_update.py:1086` (`_prepare_watchlist_for_fit`) | target market alias (`--market`/`--db-alias`) | none | Task 10 — not implemented here; `add_missing_items_to_watchlist`'s push stays at the *command* boundary, not inside the writer, so fit-update's own push (when added) must not double-push |
| `add_structure`                  | `build_cost_utils.upsert_structures(local_db.engine, ...)` | `buildcost` | **yes** — **Task 12 (this task)**: deleted the duplicate `remote_engine` call and the now-dead `--local`/`--remote-only` flags/branching; writes once via `local_db.engine`, then `local_db.push()` | Already compliant as of Task 12 |
| `build-watchlist add/remove/mirror` | `build_watchlist_cli.py` → `builder_costs/repository.py` (`upsert_build_watchlist`, `upsert_builder_costs`, ...) | `buildcost` | **yes** — `repository.py` calls `db.push()` at the end of every writer (`:125`, `:161`, `:193`, `:226`, `:252`); `build_watchlist_cli.py:121` also pushes directly | Already compliant — no action needed |
| `update-markets` (main pipeline) | `cli.py` (`db.push()` at `:379`, end of run) | all configured markets processed in the run | **yes** | Already compliant (earlier phase) |

This table covers the writers visible in the Task 7 audit; Tasks 8-12 extend
this file and should re-audit before assuming a row above is still accurate.
"""
import importlib
import json

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

    def test_equiv_find_add_returns_false_when_push_fails(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Mirrors ``TestWatchlistPush::test_push_failure_fails_the_command``:
        the Phase-4 rule is a push failure must fail the command, and
        ``equiv find --add`` is a write path like the other two loops."""
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

        # Pre-seed the db with a push that always raises.
        broken = factory(market_context=MarketContext.from_settings("primary"))

        def boom():
            raise RuntimeError("turso unreachable")
        broken.push = boom

        result = equiv_manager.equiv_command(
            ["find", "11269", "--add", "--market=primary"], "primary"
        )

        assert result is False
        assert set(dbs) == {primary_alias}


# ---------------------------------------------------------------------------
# TestFittingsPush (Task 9) — update_fit_workflow's touched_aliases design
# ---------------------------------------------------------------------------
#
# update_fit_workflow writes the shared `fittings` replica plus one market
# replica per call, and is called once per market by every outer command. It
# must NOT push itself (see module docstring's Task 9 row); instead it takes
# an optional `touched_aliases: set` accumulator and adds "fittings" and its
# resolved `target_alias` to it on success (never on a dry run). Every outer
# caller owns one accumulator for the whole CLI invocation and pushes each
# distinct alias once at the end.
#
# EFT text below is copied verbatim from
# tests/test_eft_parser.py::TestEFTParserString.test_parse_simple_fit (not
# importable as a fixture there — it's a local string inside that test
# method), per the brief's "use an existing EFT fixture" instruction.

_FITTINGS_EFT_TYPE_MAP = {
    "Hurricane Fleet Issue": 33157,
    "Damage Control II": 2048,
    "Gyrostabilizer II": 519,
    "Large Shield Extender II": 3841,
    "720mm Howitzer Artillery II": 2961,
    "Valkyrie II": 2446,
    "Nanite Repair Paste": 28668,
}

_FITTINGS_EFT_TEXT = """[Hurricane Fleet Issue, Test Fit]
Damage Control II
Gyrostabilizer II

Large Shield Extender II

720mm Howitzer Artillery II


Valkyrie II x5

Nanite Repair Paste x100
"""


def _create_fittings_schema(conn) -> None:
    conn.execute(text(
        "CREATE TABLE fittings_doctrine (id INTEGER PRIMARY KEY, name TEXT, "
        "icon_url TEXT, description TEXT, created TEXT, last_updated TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE watch_doctrines (id INTEGER PRIMARY KEY, name TEXT, "
        "icon_url TEXT, description TEXT, created TEXT, last_updated TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE fittings_fitting (id INTEGER PRIMARY KEY, description TEXT, "
        "name TEXT, ship_type_type_id INTEGER, ship_type_id INTEGER, "
        "created TEXT, last_updated TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE fittings_fittingitem (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "flag TEXT, quantity INTEGER, type_id INTEGER, fit_id INTEGER, type_fk_id INTEGER)"
    ))
    conn.execute(text(
        "CREATE TABLE fittings_doctrine_fittings (id INTEGER PRIMARY KEY, "
        "doctrine_id INTEGER, fitting_id INTEGER)"
    ))


def _create_fittings_market_schema(conn) -> None:
    conn.execute(text(
        "CREATE TABLE doctrine_fits (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "doctrine_name TEXT, fit_name TEXT, ship_type_id INTEGER, doctrine_id INTEGER, "
        "fit_id INTEGER, ship_name TEXT, target INTEGER, market_flag TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE doctrine_map (id INTEGER PRIMARY KEY, doctrine_id INTEGER, "
        "fitting_id INTEGER)"
    ))
    conn.execute(text(
        "CREATE TABLE ship_targets (fit_id INTEGER PRIMARY KEY, fit_name TEXT, "
        "ship_id INTEGER, ship_name TEXT, ship_target INTEGER, created_at TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE doctrines (id INTEGER PRIMARY KEY AUTOINCREMENT, fit_id INTEGER, "
        "ship_id INTEGER, ship_name TEXT, type_id INTEGER, type_name TEXT, "
        "fit_qty INTEGER, hulls INTEGER, fits_on_mkt REAL, total_stock INTEGER, "
        "price REAL, avg_vol REAL, days REAL, group_id INTEGER, group_name TEXT, "
        "category_id INTEGER, category_name TEXT, timestamp TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE marketstats (type_id INTEGER PRIMARY KEY, price REAL, "
        "avg_price REAL, avg_volume REAL, days_remaining REAL, total_volume_remain INTEGER)"
    ))
    conn.execute(text(
        "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, type_name TEXT, "
        "group_id INTEGER, group_name TEXT, category_id INTEGER, category_name TEXT)"
    ))


def _create_fittings_sde_schema(conn) -> None:
    conn.execute(text(
        "CREATE TABLE inv_info (typeID INTEGER PRIMARY KEY, typeName TEXT, "
        "groupID INTEGER, groupName TEXT, categoryID INTEGER, categoryName TEXT, "
        "volume REAL)"
    ))
    for type_name, type_id in _FITTINGS_EFT_TYPE_MAP.items():
        conn.execute(
            text(
                "INSERT INTO inv_info VALUES "
                "(:id, :name, 18, 'Group', 6, 'Category', 1.0)"
            ),
            {"id": type_id, "name": type_name},
        )


def _fittings_workflow_dbs_factory(tmp_path, fake_db_factory, dbs):
    """Build a ``DatabaseConfig``-shaped factory keyed by database_alias.

    Every module under test (``parse_fits``, ``doctrine_update``,
    ``get_type_info``, ``db_utils``, ``fit_update``) resolves a *fresh*
    ``DatabaseConfig(alias)`` on every call rather than caching one, so a
    single alias-keyed cache here is shared correctly across every writer in
    the workflow, exactly like ``_equiv_dbs_factory`` above.
    """
    def factory(alias=None, market_context=None):
        key = alias or market_context.database_alias
        if key not in dbs:
            db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
            with db.engine.begin() as conn:
                if key == "fittings":
                    _create_fittings_schema(conn)
                elif key == "sde":
                    _create_fittings_sde_schema(conn)
                else:
                    _create_fittings_market_schema(conn)
            dbs[key] = db
        return dbs[key]
    return factory


def _patch_fittings_workflow_dbs(monkeypatch, factory) -> None:
    """Point every module that resolves ``DatabaseConfig`` fresh at ``factory``.

    Each module below did ``from ... import DatabaseConfig`` at its own
    module top, so a class patch anywhere else does not reach an
    already-bound name in another module's namespace — each must be patched
    individually, same reasoning as ``TestEquivPush``'s
    equiv_handlers/equiv_manager double-patch.
    """
    monkeypatch.setattr("mkts_backend.utils.parse_fits.DatabaseConfig", factory)
    monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)
    monkeypatch.setattr("mkts_backend.utils.get_type_info.DatabaseConfig", factory)
    monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", factory)
    monkeypatch.setattr("mkts_backend.cli_tools.fit_update.DatabaseConfig", factory)
    monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", factory)


def _fake_multi_type_info(type_ids) -> pd.DataFrame:
    """Stand-in for db_utils.get_type_info's SDE lookup, multi-row version.

    Same rationale as ``_fake_type_info`` above: ``get_type_info`` reads
    through the module-level ``sde_db`` bound at ``db_utils`` import time,
    which a ``DatabaseConfig`` class patch does not reach.
    """
    return pd.DataFrame([
        {
            "type_id": tid, "type_name": f"Type {tid}", "group_id": 18,
            "group_name": "Group", "category_id": 6, "category_name": "Category",
        }
        for tid in type_ids
    ])


def _write_fittings_eft_fixture(tmp_path, suffix: str = ""):
    fit_file = tmp_path / f"fit{suffix}.txt"
    fit_file.write_text(_FITTINGS_EFT_TEXT)
    return fit_file


def _write_fittings_meta_fixture(tmp_path, fit_id: int, doctrine_id: int, suffix: str = ""):
    meta_file = tmp_path / f"meta{suffix}.json"
    meta_file.write_text(json.dumps({
        "fit_id": fit_id,
        "name": "Test HFI",
        "description": "test fit",
        "doctrine_id": doctrine_id,
        "target": 10,
    }))
    return meta_file


class TestFittingsPush:
    """Drives ``fit_update.fit_update_command(subcommand="add", ...)`` — the
    highest-level PUBLIC, non-interactive entry point onto
    ``update_fit_workflow``. ``interactive_add_fit`` (``interactive=True``)
    prompts via ``rich.prompt`` and cannot be driven headlessly without
    extensively faking stdin, so per the brief's fallback instruction we use
    the non-interactive ``add`` subcommand instead and record that choice
    here.
    """

    def test_update_fit_workflow_pushes_fittings_db(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """update_fit_workflow writes the fittings replica and the market
        replica; both must push exactly once for a single-market add."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = _fittings_workflow_dbs_factory(tmp_path, fake_db_factory, dbs)
        _patch_fittings_workflow_dbs(monkeypatch, factory)
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids, remote=False: _fake_multi_type_info(type_ids),
        )

        fit_file = _write_fittings_eft_fixture(tmp_path)
        meta_file = _write_fittings_meta_fixture(tmp_path, fit_id=99001, doctrine_id=501)

        assert fit_update.fit_update_command(
            subcommand="add",
            file_path=str(fit_file),
            meta_file=str(meta_file),
            market_flag="primary",
            target_alias="wcmktnewkeeptest",
            interactive=False,
        )

        assert dbs["fittings"].pushes == 1, "fittings replica never reached Turso"
        assert dbs["wcmktnewkeeptest"].pushes == 1, "market replica never reached Turso"

        with dbs["fittings"].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM fittings_fitting WHERE id = 99001")
            ).scalar() == 1
        with dbs["wcmktnewkeeptest"].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM doctrine_fits WHERE fit_id = 99001")
            ).scalar() == 1

    def test_multi_market_update_pushes_fittings_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """The workflow runs once per configured market, but fittings is
        shared; the outer CLI invocation must push it only once, while each
        market alias it touched pushes exactly once too."""
        from mkts_backend.cli_tools import fit_update
        from mkts_backend.config.market_context import MarketContext

        expected_markets = {
            MarketContext.from_settings(m).database_alias
            for m in MarketContext.list_available()
        }
        assert len(expected_markets) > 1, "test requires more than one configured market"

        dbs = {}
        factory = _fittings_workflow_dbs_factory(tmp_path, fake_db_factory, dbs)
        _patch_fittings_workflow_dbs(monkeypatch, factory)
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids, remote=False: _fake_multi_type_info(type_ids),
        )

        fit_file = _write_fittings_eft_fixture(tmp_path, suffix="_multi")
        meta_file = _write_fittings_meta_fixture(
            tmp_path, fit_id=99002, doctrine_id=502, suffix="_multi"
        )

        assert fit_update.fit_update_command(
            subcommand="add",
            file_path=str(fit_file),
            meta_file=str(meta_file),
            market_flag="all",
            interactive=False,
        )

        assert expected_markets <= set(dbs)
        assert dbs["fittings"].pushes == 1, "fittings must push exactly once per invocation"
        for alias in expected_markets:
            assert dbs[alias].pushes == 1, {a: d.pushes for a, d in dbs.items()}


class TestCreateDoctrineCommandPush:
    """``create_doctrine_command`` calls ``create_doctrine`` exactly once
    (unlike ``update_fit_workflow``, which is invoked per-market) so it owns
    its own push directly rather than needing an accumulator."""

    def test_create_doctrine_command_pushes_fittings_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = _fittings_workflow_dbs_factory(tmp_path, fake_db_factory, dbs)
        _patch_fittings_workflow_dbs(monkeypatch, factory)

        assert fit_update.create_doctrine_command(
            name="Test Doctrine",
            description="desc",
            doctrine_id=777,
            remote=False,
            interactive=False,
        )

        assert dbs["fittings"].pushes == 1
        with dbs["fittings"].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM fittings_doctrine WHERE id = 777")
            ).scalar() == 1


class TestDoctrinePush:
    """Task 10 — ``update-target``, ``update-friendly-name``,
    ``populate-friendly-names``, ``remove``, and ``doctrine-remove-fit`` must
    each push their touched aliases; the friendly-name commands used to write
    ``db_alias`` twice under a local/remote split that no longer exists.
    """

    def _target_dbs_factory(self, tmp_path, fake_db_factory, dbs):
        """Alias-keyed factory with a doctrine_fits + ship_targets market schema.

        Used for the update-target tests, which only touch a single market
        database (no fittings/sde involved).
        """
        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE TABLE doctrine_fits (fit_id INTEGER PRIMARY KEY, "
                        "fit_name TEXT, ship_type_id INTEGER, ship_name TEXT, "
                        "target INTEGER, market_flag TEXT)"
                    ))
                    conn.execute(text(
                        "CREATE TABLE ship_targets (fit_id INTEGER PRIMARY KEY, "
                        "fit_name TEXT, ship_id INTEGER, ship_name TEXT, "
                        "ship_target INTEGER, created_at TEXT)"
                    ))
                dbs[key] = db
            return dbs[key]
        return factory

    def test_update_target_pushes(self, tmp_path, monkeypatch, fake_db_factory):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._target_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)

        db = factory(alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO doctrine_fits (fit_id, fit_name, ship_type_id, "
                "ship_name, target, market_flag) VALUES "
                "(39, 'Test Fit', 587, 'Rifter', 10, 'primary')"
            ))

        assert fit_update._update_target_single(
            fit_id=39, target=20, remote=False, market_flag="primary",
            db_alias="wcmktnewkeeptest",
        )

        with db.engine.connect() as conn:
            target_row = conn.execute(
                text("SELECT target FROM doctrine_fits WHERE fit_id = 39")
            ).fetchone()
            assert target_row[0] == 20
            ship_target_row = conn.execute(
                text("SELECT ship_target FROM ship_targets WHERE fit_id = 39")
            ).fetchone()
            assert ship_target_row[0] == 20
        assert db.pushes == 1, "update-target never reached Turso"

    def test_update_target_push_failure_fails_the_command(
        self, tmp_path, monkeypatch, fake_db_factory, capsys
    ):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._target_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)

        db = factory(alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO doctrine_fits (fit_id, fit_name, ship_type_id, "
                "ship_name, target, market_flag) VALUES "
                "(39, 'Test Fit', 587, 'Rifter', 10, 'primary')"
            ))

        def boom():
            raise RuntimeError("turso unreachable")
        db.push = boom

        result = fit_update._update_target_single(
            fit_id=39, target=20, remote=False, market_flag="primary",
            db_alias="wcmktnewkeeptest",
        )

        assert result is False
        # The local write is the writer's job and happens before the push
        # that reports failure.
        with db.engine.connect() as conn:
            target_row = conn.execute(
                text("SELECT target FROM doctrine_fits WHERE fit_id = 39")
            ).fetchone()
            assert target_row[0] == 20
        captured = capsys.readouterr()
        assert "turso unreachable" in captured.out, (
            "push() must actually have been called and raised for this "
            "test to be meaningful"
        )

    def test_update_friendly_name_writes_once_and_pushes_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """The command used to write the same replica twice under a
        local/remote split that no longer exists."""
        from mkts_backend.cli_tools import fit_update
        from mkts_backend.config.market_context import MarketContext

        expected_markets = {
            MarketContext.from_settings(m).database_alias
            for m in MarketContext.list_available()
        }
        primary_alias = MarketContext.from_settings("primary").database_alias
        assert primary_alias in expected_markets

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
                        "INSERT INTO doctrine_fits (fit_id, doctrine_id) VALUES (1, 501)"
                    ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)

        update_calls: list[str] = []
        original_update = fit_update.update_doctrine_friendly_name

        def counted_update(doctrine_id, friendly_name, db_alias="wcmkt", remote=False):
            update_calls.append(db_alias)
            return original_update(
                doctrine_id, friendly_name, db_alias=db_alias, remote=remote
            )

        monkeypatch.setattr(fit_update, "update_doctrine_friendly_name", counted_update)

        assert fit_update.update_friendly_name_command(
            doctrine_id=501, friendly_name="Test Doctrine", db_alias=primary_alias,
        )

        assert set(dbs) == expected_markets
        # Each alias updated exactly once — the old code updated db_alias
        # twice (once as "local", once as "remote" whenever db_alias also
        # appeared in the configured markets).
        assert sorted(update_calls) == sorted(expected_markets), update_calls
        for alias in expected_markets:
            assert dbs[alias].pushes == 1, {a: d.pushes for a, d in dbs.items()}
            with dbs[alias].engine.connect() as conn:
                name = conn.execute(
                    text("SELECT friendly_name FROM doctrine_fits WHERE fit_id = 1")
                ).scalar()
                assert name == "Test Doctrine"

    def test_populate_friendly_names_pushes_each_configured_market(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """``sync_friendly_names_to_remote`` is gone; propagation now comes
        from calling ``populate_friendly_names_from_json`` once per
        configured market and pushing each one that actually updated rows."""
        import json as json_module
        from mkts_backend.cli_tools import fit_update
        from mkts_backend.config.market_context import MarketContext

        expected_markets = {
            MarketContext.from_settings(m).database_alias
            for m in MarketContext.list_available()
        }
        primary_alias = MarketContext.from_settings("primary").database_alias

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
                        "INSERT INTO doctrine_fits (fit_id, doctrine_id) VALUES (7, 900)"
                    ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)

        json_path = tmp_path / "doctrine_names.json"
        json_path.write_text(json_module.dumps(
            [{"fit_id": 7, "doctrine_id": 900, "friendly_name": "Hurricane"}]
        ))

        assert fit_update.populate_friendly_names_command(
            json_path=str(json_path), db_alias=primary_alias,
        )

        assert set(dbs) == expected_markets
        for alias in expected_markets:
            assert dbs[alias].pushes == 1, {a: d.pushes for a, d in dbs.items()}
            with dbs[alias].engine.connect() as conn:
                name = conn.execute(
                    text("SELECT friendly_name FROM doctrine_fits WHERE fit_id = 7")
                ).scalar()
                assert name == "Hurricane"

    def _doctrine_remove_dbs_factory(self, tmp_path, fake_db_factory, dbs):
        """Alias-keyed factory for ``remove_fit_command``'s three DB roles."""
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
                            "CREATE TABLE sdetypes (typeID INTEGER PRIMARY KEY, "
                            "typeName TEXT)"
                        ))
                    else:
                        conn.execute(text(
                            "CREATE TABLE doctrines (fit_id INTEGER)"
                        ))
                        conn.execute(text(
                            "CREATE TABLE doctrine_map (fitting_id INTEGER, "
                            "doctrine_id INTEGER)"
                        ))
                        conn.execute(text(
                            "CREATE TABLE doctrine_fits (fit_id INTEGER, "
                            "doctrine_id INTEGER)"
                        ))
                        conn.execute(text(
                            "CREATE TABLE ship_targets (fit_id INTEGER PRIMARY KEY)"
                        ))
                dbs[key] = db
            return dbs[key]
        return factory

    def test_remove_fit_pushes_market_and_fittings(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._doctrine_remove_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.parse_fits.DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.cli_tools.fit_update.Confirm.ask", lambda *a, **k: True)

        market_alias = "wcmktnewkeeptest"
        fittings_db = factory(alias="fittings")
        with fittings_db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fittings_fitting (id, name, description, ship_type_id) "
                "VALUES (501, 'Test Fit', 'desc', 587)"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine (id, name, description) "
                "VALUES (10, 'Test Doctrine', '')"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine_fittings (id, doctrine_id, fitting_id) "
                "VALUES (1, 10, 501)"
            ))

        sde_db = factory(alias="sde")
        with sde_db.engine.begin() as conn:
            conn.execute(text("INSERT INTO sdetypes (typeID, typeName) VALUES (587, 'Rifter')"))

        market_db = factory(alias=market_alias)
        with market_db.engine.begin() as conn:
            conn.execute(text("INSERT INTO doctrines (fit_id) VALUES (501)"))
            conn.execute(text(
                "INSERT INTO doctrine_map (fitting_id, doctrine_id) VALUES (501, 10)"
            ))
            conn.execute(text("INSERT INTO doctrine_fits (fit_id) VALUES (501)"))
            conn.execute(text("INSERT INTO ship_targets (fit_id) VALUES (501)"))

        assert fit_update.remove_fit_command(
            fit_id=501, remote=False, db_alias=market_alias,
        )

        assert dbs[market_alias].pushes == 1, "market replica never reached Turso"
        assert dbs["fittings"].pushes == 1, "fittings replica never reached Turso"

        with dbs[market_alias].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM doctrines WHERE fit_id = 501")
            ).scalar() == 0
        with dbs["fittings"].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM fittings_doctrine_fittings WHERE fitting_id = 501")
            ).scalar() == 0

    def test_remove_fit_push_failure_fails_the_command(
        self, tmp_path, monkeypatch, fake_db_factory, capsys
    ):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._doctrine_remove_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.parse_fits.DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.cli_tools.fit_update.Confirm.ask", lambda *a, **k: True)

        market_alias = "wcmktnewkeeptest"
        fittings_db = factory(alias="fittings")
        with fittings_db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fittings_fitting (id, name, description, ship_type_id) "
                "VALUES (502, 'Test Fit 2', 'desc', 587)"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine (id, name, description) "
                "VALUES (11, 'Test Doctrine 2', '')"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine_fittings (id, doctrine_id, fitting_id) "
                "VALUES (2, 11, 502)"
            ))

        sde_db = factory(alias="sde")
        with sde_db.engine.begin() as conn:
            conn.execute(text("INSERT INTO sdetypes (typeID, typeName) VALUES (587, 'Rifter')"))

        market_db = factory(alias=market_alias)
        with market_db.engine.begin() as conn:
            conn.execute(text("INSERT INTO doctrines (fit_id) VALUES (502)"))
            conn.execute(text(
                "INSERT INTO doctrine_map (fitting_id, doctrine_id) VALUES (502, 11)"
            ))
            conn.execute(text("INSERT INTO doctrine_fits (fit_id) VALUES (502)"))
            conn.execute(text("INSERT INTO ship_targets (fit_id) VALUES (502)"))

        def boom():
            raise RuntimeError("turso unreachable")
        market_db.push = boom

        result = fit_update.remove_fit_command(
            fit_id=502, remote=False, db_alias=market_alias,
        )

        assert result is False
        # The market-side deletes ran (the writer's job) before the failed push.
        with market_db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM doctrines WHERE fit_id = 502")
            ).scalar() == 0
        # fittings must not be pushed either — the command fails on the
        # market push before it ever reaches the fittings push.
        assert dbs["fittings"].pushes == 0
        captured = capsys.readouterr()
        assert "turso unreachable" in captured.out

    def test_doctrine_remove_fit_pushes_market_and_fittings(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._doctrine_remove_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.doctrine_update.DatabaseConfig", factory)
        monkeypatch.setattr("mkts_backend.utils.parse_fits.DatabaseConfig", factory)

        market_alias = "wcmktnewkeeptest"
        fittings_db = factory(alias="fittings")
        with fittings_db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO fittings_fitting (id, name, description, ship_type_id) "
                "VALUES (503, 'Test Fit 3', 'desc', 587)"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine (id, name, description) "
                "VALUES (12, 'Test Doctrine 3', '')"
            ))
            conn.execute(text(
                "INSERT INTO fittings_doctrine_fittings (id, doctrine_id, fitting_id) "
                "VALUES (3, 12, 503)"
            ))

        market_db = factory(alias=market_alias)
        with market_db.engine.begin() as conn:
            conn.execute(text("INSERT INTO doctrine_map (fitting_id, doctrine_id) VALUES (503, 12)"))
            conn.execute(text(
                "INSERT INTO doctrine_fits (fit_id, doctrine_id) VALUES (503, 12)"
            ))

        assert fit_update.doctrine_remove_fit_command(
            doctrine_id=12, fit_ids=[503], remote=False, interactive=False,
            db_alias=market_alias,
        )

        assert dbs[market_alias].pushes == 1, "market replica never reached Turso"
        assert dbs["fittings"].pushes == 1, "fittings replica never reached Turso"
        with dbs["fittings"].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM fittings_doctrine_fittings WHERE fitting_id = 503")
            ).scalar() == 0


class TestUpdateFitWorkflowCallSiteCoverage:
    """Guard against a new caller silently omitting the accumulator.

    This is a source scan, not a runtime check: whether a call site passes
    ``touched_aliases=`` is a static property of the call site, not something
    a single execution path exercises.
    """

    def test_every_call_site_passes_touched_aliases(self):
        import re
        from pathlib import Path

        src_root = Path(__file__).resolve().parent.parent / "src" / "mkts_backend"
        call_sites: list[tuple[str, int, str]] = []
        for path in src_root.rglob("*.py"):
            source = path.read_text()
            for match in re.finditer(r"\bupdate_fit_workflow\(", source):
                line_start = source.rfind("\n", 0, match.start()) + 1
                line_end = source.find("\n", match.start())
                line = source[line_start: line_end if line_end != -1 else len(source)]
                if line.lstrip().startswith("def "):
                    continue  # the function definition itself, not a call
                depth = 0
                end = match.end() - 1
                for idx in range(match.end() - 1, len(source)):
                    if source[idx] == "(":
                        depth += 1
                    elif source[idx] == ")":
                        depth -= 1
                        if depth == 0:
                            end = idx
                            break
                call_text = source[match.start(): end + 1]
                line_no = source.count("\n", 0, match.start()) + 1
                call_sites.append((str(path.relative_to(src_root.parent.parent)), line_no, call_text))

        assert call_sites, "expected at least one update_fit_workflow(...) call site"
        missing = [
            f"{path}:{line_no}" for path, line_no, call_text in call_sites
            if "touched_aliases" not in call_text
        ]
        assert not missing, f"update_fit_workflow call sites missing touched_aliases=: {missing}"


# ---------------------------------------------------------------------------
# TestMultiAliasPush (Task 11) — _execute_market_plan's two write loops
# ---------------------------------------------------------------------------
#
# _execute_market_plan (fit_update.py) buckets writes by (is_remote, alias)
# and applies each bucket in its own `engine.begin()` transaction (Phase 3),
# then runs a second orphan-cleanup pass over every configured alias for any
# fit that was fully removed (Phase 4) — which can add writes to an alias
# Phase 3 already processed. Both phases must push each distinct touched
# alias exactly once, after BOTH phases finish, skipping any alias whose
# bucket raised (rolled back, so nothing genuinely new landed from that
# bucket — see the docstring inside _execute_market_plan for why a rolled-
# back bucket's alias is excluded rather than force-pushed).
#
# ``_apply_step`` and the orphan-cleanup helpers (``_check_fit_orphaned``,
# ``remove_doctrines_for_fit``, ``remove_ship_target``) are monkeypatched to
# write a bare marker row through the caller's transaction rather than
# exercising the full provisioning machinery (upsert_doctrine_fits,
# upsert_lead_ship, etc.) — that machinery is already covered by
# TestFittingsPush/TestDoctrinePush above and by test_fit_update_assign.py.
# This keeps the fixture hermetic while still exercising a real
# engine.begin() transaction per bucket and a real (counted) push() per
# alias, which is what this task is actually about.

_MARKER_TABLE_SQL = (
    "CREATE TABLE marker (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "alias TEXT, fit_id INTEGER)"
)


class TestMultiAliasPush:
    def _plan_dbs_factory(self, tmp_path, fake_db_factory, dbs):
        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    conn.execute(text(_MARKER_TABLE_SQL))
                dbs[key] = db
            return dbs[key]
        return factory

    def _patch_flag_and_aliases(self, monkeypatch, fit_update, flag_map, configured):
        """Replace the two settings-derived helpers _execute_market_plan uses
        to turn a plan into (is_remote, alias) buckets, so a test plan's
        market_flag/new_flag strings map to whatever aliases it wants
        regardless of the real settings.toml market configuration."""
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases",
            lambda market_flag=None: list(configured),
        )
        monkeypatch.setattr(
            fit_update, "_flag_to_aliases",
            lambda flag: set(flag_map.get(flag, ())),
        )

    def test_assign_market_pushes_each_touched_alias_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Two markets touched, two pushes, one each — not one push for the
        first alias repeated, and not a push per step."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._plan_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        self._patch_flag_and_aliases(
            monkeypatch, fit_update,
            flag_map={"flagA": {"aliasA"}, "flagB": {"aliasB"}},
            configured=["aliasA", "aliasB"],
        )
        monkeypatch.setattr(fit_update, "_prepare_watchlist_for_fit", lambda *a, **k: None)

        applied: list[tuple[str, int]] = []

        def fake_apply_step(conn, step_type, p, arg, alias=""):
            conn.execute(
                text("INSERT INTO marker (alias, fit_id) VALUES (:a, :f)"),
                {"a": alias, "f": p["fit_id"]},
            )
            applied.append((alias, p["fit_id"]))
            return True

        monkeypatch.setattr(fit_update, "_apply_step", fake_apply_step)

        plans = [
            {"fit_id": 1, "doctrine_id": 10, "action": "update",
             "market_flag": "flagA", "new_flag": "flagA", "doctrine_name": "D"},
            {"fit_id": 2, "doctrine_id": 10, "action": "update",
             "market_flag": "flagB", "new_flag": "flagB", "doctrine_name": "D"},
        ]

        result = fit_update._execute_market_plan(plans, remote=False, db_alias="aliasA")

        assert result["push_failed"] is False
        assert result["bucket_failures"] == 0
        assert set(dbs) == {"aliasA", "aliasB"}
        assert dbs["aliasA"].pushes == 1, {a: d.pushes for a, d in dbs.items()}
        assert dbs["aliasB"].pushes == 1, {a: d.pushes for a, d in dbs.items()}
        assert sorted(applied) == [("aliasA", 1), ("aliasB", 2)]

    def test_alias_whose_bucket_raised_is_not_pushed(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """A failed bucket must not push a half-applied plan."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._plan_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        self._patch_flag_and_aliases(
            monkeypatch, fit_update,
            flag_map={"flagA": {"aliasA"}, "flagB": {"aliasB"}},
            configured=["aliasA", "aliasB"],
        )
        monkeypatch.setattr(fit_update, "_prepare_watchlist_for_fit", lambda *a, **k: None)

        def fake_apply_step(conn, step_type, p, arg, alias=""):
            if alias == "aliasB":
                # One of the four exception types _execute_market_plan's
                # bucket-level except actually catches (see the source);
                # anything else would be swallowed as a mere step failure
                # and would NOT roll back or fail the bucket.
                raise ConnectionError("simulated turso outage")
            conn.execute(
                text("INSERT INTO marker (alias, fit_id) VALUES (:a, :f)"),
                {"a": alias, "f": p["fit_id"]},
            )
            return True

        monkeypatch.setattr(fit_update, "_apply_step", fake_apply_step)

        plans = [
            {"fit_id": 1, "doctrine_id": 10, "action": "update",
             "market_flag": "flagA", "new_flag": "flagA", "doctrine_name": "D"},
            {"fit_id": 2, "doctrine_id": 10, "action": "update",
             "market_flag": "flagB", "new_flag": "flagB", "doctrine_name": "D"},
        ]

        result = fit_update._execute_market_plan(plans, remote=False, db_alias="aliasA")

        assert result["bucket_failures"] == 1
        assert set(dbs) == {"aliasA", "aliasB"}
        assert dbs["aliasA"].pushes == 1, "the healthy alias must still be pushed"
        assert dbs["aliasB"].pushes == 0, "a bucket that raised must not be pushed"
        with dbs["aliasB"].engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM marker")).scalar() == 0, (
                "the raised bucket's own write must have rolled back too"
            )

    def test_orphan_cleanup_writes_are_included_in_the_push(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """The second loop at fit_update.py:1415 adds writes after the first
        loop; a push placed inside the first loop would miss them."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._plan_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        self._patch_flag_and_aliases(
            monkeypatch, fit_update,
            flag_map={},
            configured=["aliasA", "aliasB"],
        )
        monkeypatch.setattr(fit_update, "_prepare_watchlist_for_fit", lambda *a, **k: None)

        def fake_apply_step(conn, step_type, p, arg, alias=""):
            assert step_type == "remove_row"
            conn.execute(
                text("INSERT INTO marker (alias, fit_id) VALUES (:a, :f)"),
                {"a": alias, "f": p["fit_id"]},
            )
            return True

        monkeypatch.setattr(fit_update, "_apply_step", fake_apply_step)
        monkeypatch.setattr(fit_update, "_check_fit_orphaned", lambda fid, conn=None: True)

        def fake_remove_doctrines_for_fit(fid, conn=None, **kwargs):
            conn.execute(
                text("INSERT INTO marker (alias, fit_id) VALUES ('orphan-cleanup', :f)"),
                {"f": fid},
            )
            return 1

        monkeypatch.setattr(fit_update, "remove_doctrines_for_fit", fake_remove_doctrines_for_fit)
        monkeypatch.setattr(fit_update, "remove_ship_target", lambda fid, conn=None, **k: 0)

        # A single "remove" plan: Phase 3 only ever writes the canonical
        # alias (aliasA). aliasB is untouched until Phase 4's orphan-cleanup
        # pass, which iterates every configured alias.
        plans = [
            {"fit_id": 3, "doctrine_id": 20, "action": "remove", "doctrine_name": "D"},
        ]

        result = fit_update._execute_market_plan(plans, remote=False, db_alias="aliasA")

        assert result["bucket_failures"] == 0
        assert result["push_failed"] is False
        assert set(dbs) == {"aliasA", "aliasB"}
        # aliasA is written by BOTH loops but must push exactly once.
        assert dbs["aliasA"].pushes == 1, {a: d.pushes for a, d in dbs.items()}
        # aliasB is written ONLY by the orphan-cleanup pass; it must still
        # be covered by the single post-loop push, not missed because the
        # first loop never touched it.
        assert dbs["aliasB"].pushes == 1, {a: d.pushes for a, d in dbs.items()}
        with dbs["aliasA"].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM marker WHERE fit_id = 3")
            ).scalar() == 2, "expected one row from Phase 3 and one from Phase 4 cleanup"
        with dbs["aliasB"].engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM marker WHERE fit_id = 3")
            ).scalar() == 1, "aliasB is only ever touched by the orphan-cleanup pass"

    def test_push_failure_does_not_abort_pushing_the_remaining_aliases(
        self, tmp_path, monkeypatch, fake_db_factory, capsys
    ):
        """A push failure on one alias must still be attempted for every
        other touched alias (matches the brief's pseudocode, which loops
        over ALL of sorted(touched - failed) rather than stopping at the
        first push exception), and must mark the result as failed."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._plan_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        self._patch_flag_and_aliases(
            monkeypatch, fit_update,
            flag_map={"flagA": {"aliasA"}, "flagB": {"aliasB"}},
            configured=["aliasA", "aliasB"],
        )
        monkeypatch.setattr(fit_update, "_prepare_watchlist_for_fit", lambda *a, **k: None)

        def fake_apply_step(conn, step_type, p, arg, alias=""):
            conn.execute(
                text("INSERT INTO marker (alias, fit_id) VALUES (:a, :f)"),
                {"a": alias, "f": p["fit_id"]},
            )
            return True

        monkeypatch.setattr(fit_update, "_apply_step", fake_apply_step)

        plans = [
            {"fit_id": 1, "doctrine_id": 10, "action": "update",
             "market_flag": "flagA", "new_flag": "flagA", "doctrine_name": "D"},
            {"fit_id": 2, "doctrine_id": 10, "action": "update",
             "market_flag": "flagB", "new_flag": "flagB", "doctrine_name": "D"},
        ]

        # aliasA sorts before aliasB, so this proves the push loop keeps
        # going past the first failure rather than returning early.
        broken = factory(alias="aliasA")

        def boom():
            raise RuntimeError("turso unreachable")
        broken.push = boom

        result = fit_update._execute_market_plan(plans, remote=False, db_alias="aliasA")

        assert result["push_failed"] is True
        assert result["bucket_failures"] == 0
        assert dbs["aliasB"].pushes == 1, (
            "a push failure on one alias must not skip pushing the rest"
        )
        captured = capsys.readouterr()
        assert "Push failed for aliasA" in captured.out

    def test_step_failure_does_not_block_the_alias_push_but_is_visible_in_counters(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Controller ruling (item 3): a per-step non-fatal exception is
        swallowed by _apply_step's inner try/except as a step failure and
        does NOT roll back the bucket (see the `continue` at fit_update.py's
        Phase 3 inner handler) — the bucket's other, already-applied work is
        genuine local truth, so it is still pushed; the CDC queue converges.
        But the failure must remain visible via counters["step_failures"] so
        a consumer can still fail the command (see
        TestAssignMarketDispatcherPushGate's step-failure tests below)."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        factory = self._plan_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        self._patch_flag_and_aliases(
            monkeypatch, fit_update,
            flag_map={"flagA": {"aliasA"}},
            configured=["aliasA"],
        )
        monkeypatch.setattr(fit_update, "_prepare_watchlist_for_fit", lambda *a, **k: None)

        def fake_apply_step(conn, step_type, p, arg, alias=""):
            # A generic (non-DB) exception — hits the inner "step failure"
            # except, not the outer bucket-level except.
            raise RuntimeError("non-fatal step error")

        monkeypatch.setattr(fit_update, "_apply_step", fake_apply_step)

        plans = [
            {"fit_id": 1, "doctrine_id": 10, "action": "update",
             "market_flag": "flagA", "new_flag": "flagA", "doctrine_name": "D"},
        ]

        result = fit_update._execute_market_plan(plans, remote=False, db_alias="aliasA")

        assert result["step_failures"] == 1
        assert result["bucket_failures"] == 0
        assert result["push_failed"] is False
        assert dbs["aliasA"].pushes == 1, (
            "the bucket did not roll back, so its alias must still be pushed"
        )


class TestDoctrineAddFitPush:
    """``doctrine_add_fit_command``'s Stage 3 loop (one transaction per
    target market alias) had no push at all before Task 11. DoctrineFit's
    real constructor does DB lookups against the fittings DB, so it is
    monkeypatched to a plain stand-in here to keep the fixture hermetic —
    Stage 3's provisioning itself (_provision_fit_in_market) is already
    covered by TestFittingsPush/TestDoctrinePush elsewhere in this file.
    """

    def _bare_dbs_factory(self, tmp_path, fake_db_factory, dbs):
        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                dbs[key] = fake_db_factory(tmp_path / f"{key}.db", alias=key)
            return dbs[key]
        return factory

    def _patch_common(self, monkeypatch, fit_update, dbs, tmp_path, fake_db_factory):
        factory = self._bare_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases",
            lambda *a, **k: ["aliasA", "aliasB"],
        )
        monkeypatch.setattr(fit_update, "get_doctrine_fits_from_market", lambda *a, **k: [])
        monkeypatch.setattr(
            fit_update, "get_available_doctrines",
            lambda remote=False: [{"id": 10, "name": "Test Doctrine"}],
        )
        monkeypatch.setattr(
            fit_update, "get_fit_info",
            lambda fid, remote=False: {
                "fit_id": fid, "fit_name": "Test Fit", "ship_name": "Rifter",
                "ship_type_id": 587,
            },
        )
        monkeypatch.setattr(fit_update, "get_fit_target", lambda *a, **k: None)
        monkeypatch.setattr(fit_update, "ensure_doctrine_link", lambda *a, **k: None)
        monkeypatch.setattr(fit_update, "_prepare_watchlist_for_fit", lambda *a, **k: None)

        class _FakeDoctrineFit:
            def __init__(self, doctrine_id, fit_id, target, remote=False):
                self.doctrine_id = doctrine_id
                self.fit_id = fit_id
                self.target = target
                self.doctrine_name = "Test Doctrine"
                self.fit_name = "Test Fit"
                self.ship_type_id = 587
                self.ship_name = "Rifter"

        monkeypatch.setattr(fit_update, "DoctrineFit", _FakeDoctrineFit)

    def test_pushes_each_touched_alias_once(self, tmp_path, monkeypatch, fake_db_factory):
        """Two target aliases, two pushes, one each."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        self._patch_common(monkeypatch, fit_update, dbs, tmp_path, fake_db_factory)
        monkeypatch.setattr(fit_update, "_provision_fit_in_market", lambda conn, p, market_flag: False)

        result = fit_update.doctrine_add_fit_command(
            doctrine_id=10, fit_ids=[1], target=100, market_flag="all",
            remote=False, interactive=False, db_alias="aliasA",
        )

        assert result is True
        assert set(dbs) == {"aliasA", "aliasB"}
        assert dbs["aliasA"].pushes == 1, {a: d.pushes for a, d in dbs.items()}
        assert dbs["aliasB"].pushes == 1, {a: d.pushes for a, d in dbs.items()}

    def test_alias_whose_bucket_raised_is_not_pushed_and_command_fails(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """A raised bucket must not be pushed, and — per the fix for item 1
        — a doctrine_add_fit_command run with any failed bucket must return
        False even though the other alias's partial success still counts
        toward success_count under this command's pre-existing partial-
        success semantics."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        self._patch_common(monkeypatch, fit_update, dbs, tmp_path, fake_db_factory)

        calls: list[int] = []

        def fake_provision(conn, p, market_flag):
            calls.append(p["fit_id"])
            if len(calls) == 2:
                # aliasA (first in the deterministic ["aliasA", "aliasB"]
                # order) succeeds; aliasB's bucket raises.
                raise ConnectionError("simulated turso outage")
            return False

        monkeypatch.setattr(fit_update, "_provision_fit_in_market", fake_provision)

        result = fit_update.doctrine_add_fit_command(
            doctrine_id=10, fit_ids=[1], target=100, market_flag="all",
            remote=False, interactive=False, db_alias="aliasA",
        )

        assert result is False
        assert set(dbs) == {"aliasA", "aliasB"}
        assert dbs["aliasA"].pushes == 1, "the healthy alias must still be pushed"
        assert dbs["aliasB"].pushes == 0, "a bucket that raised must not be pushed"


class TestUpdateLeadShipPush:
    """``update_lead_ship_command``'s per-alias loop had no push at all
    before Task 11. Unlike ``_execute_market_plan``, its bucket-level
    exception handling has TWO except clauses — the specific DB-error tuple
    and a generic ``except Exception`` catch-all — both of which must add
    the alias to ``failed`` and both are exercised below.
    """

    def _bare_dbs_factory(self, tmp_path, fake_db_factory, dbs):
        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                dbs[key] = fake_db_factory(tmp_path / f"{key}.db", alias=key)
            return dbs[key]
        return factory

    def _patch_common(self, monkeypatch, fit_update, dbs, tmp_path, fake_db_factory):
        factory = self._bare_dbs_factory(tmp_path, fake_db_factory, dbs)
        monkeypatch.setattr(fit_update, "DatabaseConfig", factory)
        monkeypatch.setattr(
            fit_update, "_configured_market_db_aliases",
            lambda *a, **k: ["aliasA", "aliasB"],
        )
        monkeypatch.setattr(
            fit_update, "get_available_doctrines",
            lambda remote=False: [{"id": 10, "name": "Test Doctrine"}],
        )
        monkeypatch.setattr(
            fit_update, "get_fit_info",
            lambda fid, remote=False: {
                "fit_id": fid, "fit_name": "Test Fit", "ship_name": "Rifter",
                "ship_type_id": 587,
            },
        )
        monkeypatch.setattr("mkts_backend.cli_tools.fit_update.Confirm.ask", lambda *a, **k: True)

    def test_pushes_each_touched_alias_once(self, tmp_path, monkeypatch, fake_db_factory):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        self._patch_common(monkeypatch, fit_update, dbs, tmp_path, fake_db_factory)
        monkeypatch.setattr(fit_update, "set_lead_ship", lambda **k: None)

        result = fit_update.update_lead_ship_command(
            doctrine_id=10, fit_id=1, market_flag="all", remote=False,
        )

        assert result is True
        assert set(dbs) == {"aliasA", "aliasB"}
        assert dbs["aliasA"].pushes == 1, {a: d.pushes for a, d in dbs.items()}
        assert dbs["aliasB"].pushes == 1, {a: d.pushes for a, d in dbs.items()}

    def test_alias_whose_bucket_raised_a_db_error_is_not_pushed(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        self._patch_common(monkeypatch, fit_update, dbs, tmp_path, fake_db_factory)

        calls: list[dict] = []

        def fake_set_lead_ship(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise ConnectionError("simulated turso outage")

        monkeypatch.setattr(fit_update, "set_lead_ship", fake_set_lead_ship)

        result = fit_update.update_lead_ship_command(
            doctrine_id=10, fit_id=1, market_flag="all", remote=False,
        )

        assert result is False
        assert dbs["aliasA"].pushes == 1, "the healthy alias must still be pushed"
        assert dbs["aliasB"].pushes == 0, "a bucket that raised must not be pushed"

    def test_alias_whose_bucket_raised_a_generic_exception_is_not_pushed(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Covers the second (generic ``except Exception``) branch, which
        ``ConnectionError`` above does not exercise."""
        from mkts_backend.cli_tools import fit_update

        dbs = {}
        self._patch_common(monkeypatch, fit_update, dbs, tmp_path, fake_db_factory)

        calls: list[dict] = []

        def fake_set_lead_ship(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("unexpected error")

        monkeypatch.setattr(fit_update, "set_lead_ship", fake_set_lead_ship)

        result = fit_update.update_lead_ship_command(
            doctrine_id=10, fit_id=1, market_flag="all", remote=False,
        )

        assert result is False
        assert dbs["aliasA"].pushes == 1, "the healthy alias must still be pushed"
        assert dbs["aliasB"].pushes == 0, "a bucket that raised must not be pushed"


class TestAssignMarketDispatcherPushGate:
    """The ``assign-market``/``unassign-market`` CLI dispatch branches in
    ``fit_update_command`` must treat a push failure, a bucket failure, or a
    non-fatal step failure reported by ``_execute_market_plan`` (via
    ``assign_market_command``/``unassign_market_command``) as command
    failure — ``updated > 0`` alone is not enough, since a later bucket, its
    push, or an individual step within an otherwise-successful bucket can
    still have failed. Exercised here by mocking the command functions
    directly so the dispatcher's own boolean composition is what's under
    test, independent of the plan-execution internals covered by
    ``TestMultiAliasPush`` above.
    """

    def _base_result(self, **overrides):
        result = {
            "updated": 1, "deleted": 0, "skipped": 0, "step_failures": 0,
            "push_failed": False, "bucket_failures": 0,
        }
        result.update(overrides)
        return result

    def test_push_failure_fails_the_dispatched_command(self, monkeypatch):
        from mkts_backend.cli_tools import fit_update

        monkeypatch.setattr(
            fit_update, "assign_market_command",
            lambda *a, **k: self._base_result(push_failed=True),
        )
        assert fit_update.fit_update_command(
            subcommand="assign-market", fit_id=1, market_flag="primary",
        ) is False

    def test_bucket_failure_fails_the_dispatched_command(self, monkeypatch):
        from mkts_backend.cli_tools import fit_update

        monkeypatch.setattr(
            fit_update, "assign_market_command",
            lambda *a, **k: self._base_result(bucket_failures=1),
        )
        assert fit_update.fit_update_command(
            subcommand="assign-market", fit_id=1, market_flag="primary",
        ) is False

    def test_step_failures_fail_the_dispatched_assign_command(self, monkeypatch):
        """Controller ruling (item 3): a non-fatal per-step failure still
        pushes the alias (see TestMultiAliasPush's step-failure test), but
        the command result must be False, not just a printed warning."""
        from mkts_backend.cli_tools import fit_update

        monkeypatch.setattr(
            fit_update, "assign_market_command",
            lambda *a, **k: self._base_result(step_failures=1),
        )
        assert fit_update.fit_update_command(
            subcommand="assign-market", fit_id=1, market_flag="primary",
        ) is False

    def test_step_failures_fail_the_dispatched_unassign_command(self, monkeypatch):
        from mkts_backend.cli_tools import fit_update

        monkeypatch.setattr(
            fit_update, "unassign_market_command",
            lambda *a, **k: self._base_result(step_failures=1),
        )
        assert fit_update.fit_update_command(
            subcommand="unassign-market", fit_id=1, market_flag="primary",
        ) is False

    def test_clean_result_still_succeeds(self, monkeypatch):
        from mkts_backend.cli_tools import fit_update

        monkeypatch.setattr(
            fit_update, "assign_market_command",
            lambda *a, **k: self._base_result(),
        )
        assert fit_update.fit_update_command(
            subcommand="assign-market", fit_id=1, market_flag="primary",
        ) is True


# ---------------------------------------------------------------------------
# TestStructureAndBuildcostSeams (Task 12) — the last three writers flagged
# by the Task 7 audit table above: add_structure's remote_engine/engine
# double-write (the audit row for `add_structure` said "Not yet assigned a
# task brief... flagged gap, out of scope here" — this task closes it), the
# watchlist mirror's wrong-direction sync() pull, and init_buildcost_tables's
# missing push.
# ---------------------------------------------------------------------------

def _seed_buildcost_structures_schema(conn) -> None:
    """Minimal buildcost.db schema for add_structure's enrichment + upsert."""
    conn.execute(text("""
        CREATE TABLE structures(
            structure TEXT, rig_1 TEXT, rig_2 TEXT, rig_3 TEXT,
            structure_type TEXT, system_id BIGINT, structure_id BIGINT,
            structure_type_id BIGINT, region_id BIGINT, tax FLOAT,
            region TEXT, system TEXT
        )
    """))
    conn.execute(text(
        "CREATE UNIQUE INDEX ix_structures_structure_id ON structures(structure_id)"
    ))
    conn.execute(text(
        "CREATE TABLE rigs(type_id INTEGER PRIMARY KEY, type_name TEXT, icon_id INTEGER)"
    ))
    conn.execute(text(
        "INSERT INTO rigs VALUES (37146, 'Standup M-Set Basic Medium Ship "
        "Manufacturing Material Efficiency I', 21729)"
    ))


class TestStructureAndBuildcostSeams:
    def test_add_structure_writes_once_and_pushes(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """upsert_structures used to run twice against the same replica —
        once via db.remote_engine, once via db.engine — which are identical
        under pyturso, so the row landed twice and push() never ran."""
        from mkts_backend.cli_tools import add_structure as mod

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        with db.engine.begin() as conn:
            _seed_buildcost_structures_schema(conn)

        monkeypatch.setattr(mod, "DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(mod, "_ensure_buildcost_ready", lambda db: True)

        calls: list[object] = []
        real_upsert = mod.upsert_structures

        def spy_upsert(engine, rows):
            calls.append(engine)
            return real_upsert(engine, rows)

        monkeypatch.setattr(mod, "upsert_structures", spy_upsert)

        csv_path = tmp_path / "structures.csv"
        pd.DataFrame([{
            "structure_id": 1040000000001,
            "structure": "4-HWWF - Test Azbel",
            "system": "4-HWWF",
            "structure_type": "Azbel",
            "tax": 0.005,
            "rig_1": "Standup M-Set Basic Medium Ship Manufacturing Material Efficiency I",
            "rig_2": "", "rig_3": "",
            "system_id": 30000240,
            "region": "Vale of the Silent",
            "region_id": 10000003,
        }]).to_csv(csv_path, index=False)

        result = mod.add_structure([f"--file={csv_path}", "--yes"])

        assert result is True
        assert len(calls) == 1, "upsert_structures must run exactly once"
        assert calls[0] is db.engine
        assert db.pushes == 1, "the surviving write must reach Turso via push()"
        with db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM structures WHERE structure_id = 1040000000001")
            ).scalar() == 1

    def test_mirror_does_not_pull_after_writing(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """add_watchlist.py:136 called sync() — a pull — where a push was
        meant; the pull overwrote freshly pushed local state. Assert BOTH
        pulls==0 and syncs==0: FakeDatabaseConfig.sync() increments `syncs`,
        not `pulls`, so asserting pulls alone can't fail before the fix."""
        add_watchlist = _import_add_watchlist_module()

        buildcost_db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        with buildcost_db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE build_watchlist (type_id INTEGER PRIMARY KEY, "
                "type_name TEXT, group_name TEXT, category_id INTEGER, "
                "added_at TEXT, last_seen_at TEXT)"
            ))

        sde_db = fake_db_factory(tmp_path / "sde.db", alias="sde")
        with sde_db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE sdetypes (typeID INTEGER PRIMARY KEY, typeName TEXT, "
                "groupID INTEGER, groupName TEXT, categoryID INTEGER, categoryName TEXT)"
            ))
            conn.execute(text(
                "INSERT INTO sdetypes VALUES (34, 'Tritanium', 18, 'Mineral', 4, 'Material')"
            ))

        dbs = {"buildcost": buildcost_db, "sde": sde_db}

        def factory(alias=None, market_context=None):
            return dbs[alias]

        # _mirror_to_build_watchlist does a LOCAL `from
        # mkts_backend.config.db_config import DatabaseConfig` inside its own
        # try block, shadowing add_watchlist's module-level import — patching
        # add_watchlist.DatabaseConfig would not reach it.
        monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", factory)
        monkeypatch.setattr(
            "mkts_backend.builder_costs.watchlist_sync.filter_buildable",
            lambda type_ids, engine: set(type_ids),
        )

        add_watchlist._mirror_to_build_watchlist([34])

        assert buildcost_db.pulls == 0, "sync() pulls, undoing the push the writer just did"
        assert buildcost_db.syncs == 0, "no pull-direction call belongs after a successful mirror write"
        assert buildcost_db.pushes == 1, "upsert_build_watchlist's own push must still run"
        with buildcost_db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM build_watchlist WHERE type_id = 34")
            ).scalar() == 1

    def test_mirror_failure_does_not_fail_the_market_write(
        self, tmp_path, monkeypatch, fake_db_factory, capsys
    ):
        """buildcost is optional: a broken mirror must not fail the command
        whose market-side write already succeeded."""
        add_watchlist = _import_add_watchlist_module()

        market_db = fake_db_factory(tmp_path / "market.db", alias="wcmktnewkeeptest")
        with market_db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                "type_name TEXT, group_id INTEGER, group_name TEXT, "
                "category_id INTEGER, category_name TEXT)"
            ))
        monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", lambda *a, **k: market_db)
        monkeypatch.setattr(add_watchlist, "DatabaseConfig", lambda *a, **k: market_db)
        monkeypatch.setattr(
            "mkts_backend.utils.db_utils.get_type_info",
            lambda type_ids, remote=False: _fake_type_info(),
        )

        def boom(*a, **k):
            raise RuntimeError("buildcost unreachable")

        # Exercise the real _mirror_to_build_watchlist (not stubbed out, as
        # in TestWatchlistPush above) so its own try/except is what's tested.
        monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", boom)

        result = add_watchlist.add_watchlist(["--type-id=34"], market_alias="primary")

        assert result is True, "buildcost mirror failure must not fail the market write"
        captured = capsys.readouterr()
        assert "build_watchlist mirror failed" in captured.out
        with market_db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM watchlist WHERE type_id = 34")
            ).scalar() == 1

    def test_init_buildcost_tables_pushes_schema(self, tmp_path, fake_db_factory):
        from mkts_backend.builder_costs import repository

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        repository.init_buildcost_tables(db)

        assert db.pushes == 1
        with db.engine.connect() as conn:
            tables = {
                row[0] for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
        assert {"build_watchlist", "builder_costs", "updatelog"} <= tables

    def test_existing_buildcost_schema_does_not_push(self, tmp_path, fake_db_factory):
        from mkts_backend.builder_costs import repository

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        repository.init_buildcost_tables(db)
        assert db.pushes == 1
        db.pushes = 0

        repository.init_buildcost_tables(db)

        assert db.pushes == 0, "a no-op schema init (nothing missing) must not push"
