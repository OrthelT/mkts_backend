"""A run must pull every replica it writes, before it writes.

pyturso's ``push()`` asks the *remote* for the last change id it recorded for
this replica's ``client_unique_id`` and then sends only local CDC rows above
that id. A replica restored from a snapshot older than its own last successful
push therefore has every pending change id at or below the remote watermark:
``push()`` finds nothing to send, returns in under a second, and the whole
run's writes are silently lost.

That is exactly what a GitHub Actions cache entry does — the key is immutable,
so every run after the one that created it restores a rolled-back replica.
Reproduced 2026-09-07 against a scratch Turso database: rolling a replica back
to an earlier snapshot and pushing dropped the write with no error, and a
``pull()`` before the write restored correct push behaviour.

The pull is the fix that does not depend on how the cache behaves, so these
tests pin it at the two places a run opens a replica it will later write.
"""
from unittest.mock import MagicMock, patch

import pytest


class TestMarketRunPullsBeforeWriting:
    """``run_market_update`` pulls each market replica before the first write."""

    @pytest.fixture
    def market_dbs(self):
        """Patch DatabaseConfig in cli, recording pull/push per market alias."""
        created = []

        def factory(*args, market_context=None, **kwargs):
            db = MagicMock()
            db.alias = market_context.database_alias if market_context else "unknown"
            db.needs_init.return_value = False
            created.append(db)
            return db

        with patch("mkts_backend.cli.DatabaseConfig", side_effect=factory):
            yield created

    def _run(self, market_dbs, calls):
        """Run the pipeline for one market with every side effect stubbed out."""
        from mkts_backend import cli

        def record(name):
            def _inner(*args, **kwargs):
                calls.append(name)
                return True

            return _inner

        with (
            patch.object(cli, "validate_all", return_value={"is_valid": True, "missing_required": []}),
            patch.object(cli, "init_databases"),
            patch.object(cli, "process_jita_prices", side_effect=record("jita")),
            patch.object(cli, "_run_market_pipeline", side_effect=record("pipeline")),
            patch("os.makedirs"),
        ):
            cli.run_market_update(history=False, market_alias="primary")

    def test_pull_happens_and_precedes_the_first_write(self, market_dbs):
        calls = []
        self._run(market_dbs, calls)

        pulled = [db for db in market_dbs if db.pull.called]
        assert pulled, (
            "run_market_update never pulled the market replica; a replica "
            "restored from a stale snapshot will silently drop every write"
        )
        # The Jita price upsert is the run's first write, and it happens before
        # _run_market_pipeline. A pull after it is too late.
        assert calls and calls[0] == "jita"
        for db in pulled:
            assert db.pull.call_count == 1

    def test_a_replica_needing_init_is_bootstrapped_not_pulled_twice(self, market_dbs):
        """verify_db_exists() already syncs; pulling again would just be waste."""
        from mkts_backend import cli

        def factory(*args, market_context=None, **kwargs):
            db = MagicMock()
            db.alias = market_context.database_alias if market_context else "unknown"
            db.needs_init.return_value = True
            market_dbs.append(db)
            return db

        with patch.object(cli, "DatabaseConfig", side_effect=factory):
            self._run(market_dbs, [])

        for db in market_dbs:
            if db.needs_init.return_value:
                db.verify_db_exists.assert_called_once()
                db.pull.assert_not_called()


class TestSharedDatabasesArePulled:
    """``init_databases`` pulls sde/fittings instead of only asserting they exist.

    Doctrine stats are computed from the shared ``fittings`` replica, and
    ``fittings`` has no scheduled push, so a run reading a day-old copy quietly
    computes stale doctrines.
    """

    def test_existing_shared_replica_is_pulled(self):
        from mkts_backend.utils import utils

        dbs = []

        def factory(alias, *args, **kwargs):
            db = MagicMock()
            db.alias = alias
            db.needs_init.return_value = False
            dbs.append(db)
            return db

        with patch.object(utils, "DatabaseConfig", side_effect=factory):
            utils.init_databases(["sde", "fittings"])

        assert {db.alias for db in dbs} == {"sde", "fittings"}
        for db in dbs:
            db.pull.assert_called_once()


class TestWorkflowCacheKeysAreNotImmutablePerDay:
    """A date-bucketed cache key rolls the replica back on every run but the first.

    ``actions/cache/save`` cannot overwrite an existing key, so with a daily key
    only the day's first run writes an entry and every later run restores that
    same snapshot.
    """

    WORKFLOWS = [
        ".github/workflows/market-data-collection.yml",
        ".github/workflows/builder-costs-collection.yml",
    ]

    @pytest.mark.parametrize("wf", WORKFLOWS)
    def test_db_cache_keys_are_unique_per_run(self, wf):
        from pathlib import Path
        import re

        text = Path(wf).read_text()
        keys = re.findall(r"^\s*key:\s*(\S.*)$", text, re.MULTILINE)
        db_keys = [k for k in keys if "dbs-v" in k]
        assert db_keys, f"{wf} has no database cache key to check"

        stale = [k for k in db_keys if "github.run_id" not in k]
        assert stale == [], (
            f"{wf} reuses a database cache key across runs: {stale}. "
            "actions/cache/save cannot overwrite an existing key, so every run "
            "after the first restores a rolled-back replica whose push() is a "
            "silent no-op. Key on github.run_id and prefix-match with "
            "restore-keys instead."
        )
