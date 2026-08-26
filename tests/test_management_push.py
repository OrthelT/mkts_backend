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
| `equiv add` / `equiv remove`     | `equiv_handlers.py` (`db.engine.begin()`), then `sync_equiv_to_remote()` deletes+reinserts via `remote_engine` (no push) | every configured market (`_equiv_add_all` / `_equiv_remove_all`) | none (fake local-only "sync") | Task 8: delete `sync_equiv_to_remote`, push per market in `equiv_manager.py` loops |
| `fit-update <subcommand>`        | `fit_update.py` + `doctrine_update.py` (many `engine.begin()` / session.commit() sites); also calls `add_missing_items_to_watchlist` at `fit_update.py:1086` (`_prepare_watchlist_for_fit`) | target market alias (`--market`/`--db-alias`) | none | Task 10 (per Task 7 brief cross-reference) — not implemented here; `add_missing_items_to_watchlist`'s push stays at the *command* boundary, not inside the writer, so fit-update's own push (when added) must not double-push |
| `parse` (fit import, `parse_fits.py`) | `parse_fits.py` (conn.commit() at several lines); calls `add_missing_items_to_watchlist` at `parse_fits.py:837` | target market alias | none | Task 9 (per Task 7 brief cross-reference) — not implemented here |
| `add_structure`                  | `build_cost_utils.upsert_structures(local_db.remote_engine, ...)` (`add_structure.py:154`) | `buildcost` | none | Not yet assigned a task brief as of 2026-08-26; flagged gap, out of scope here |
| `build-watchlist add/remove/mirror` | `build_watchlist_cli.py` → `builder_costs/repository.py` (`upsert_build_watchlist`, `upsert_builder_costs`, ...) | `buildcost` | **yes** — `repository.py` calls `db.push()` at the end of every writer (`:125`, `:161`, `:193`, `:226`, `:252`); `build_watchlist_cli.py:121` also pushes directly | Already compliant — no action needed |
| `update-markets` (main pipeline) | `cli.py` (`db.push()` at `:379`, end of run) | all configured markets processed in the run | **yes** | Already compliant (earlier phase) |

This table covers the writers visible in the Task 7 audit; Tasks 8-12 extend
this file and should re-audit before assuming a row above is still accurate.
"""
import importlib

from sqlalchemy import text


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

        assert add_watchlist.add_watchlist(["--type-id=34"], market_alias="primary")

        with db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM watchlist WHERE type_id = 34")
            ).scalar() == 1
        assert db.pushes == 1, "watchlist insert never reached Turso"

    def test_push_failure_fails_the_command(self, tmp_path, monkeypatch, fake_db_factory):
        add_watchlist = _import_add_watchlist_module()

        db = fake_db_factory(tmp_path / "market.db", alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            conn.execute(text("CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY)"))

        def boom():
            raise RuntimeError("turso unreachable")

        db.push = boom
        monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(add_watchlist, "DatabaseConfig", lambda *a, **k: db)

        assert add_watchlist.add_watchlist(["--type-id=34"], market_alias="primary") is False
