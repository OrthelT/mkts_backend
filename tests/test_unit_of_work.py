"""Unit tests for ReplicaUnitOfWork."""

from __future__ import annotations

import subprocess
import sys

import pytest
from sqlalchemy import text

from mkts_backend.db.unit_of_work import ReplicaUnitOfWork


def _init_items_table(db) -> None:
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))


class TestReplicaUnitOfWork:
    def test_commits_and_pushes_each_touched_alias_once_in_sorted_order(
        self, tmp_path, fake_db_factory
    ):
        order: list[str] = []
        dbs = {
            "market_b": fake_db_factory(tmp_path / "b.db", alias="market_b"),
            "market_a": fake_db_factory(tmp_path / "a.db", alias="market_a"),
        }
        for alias, db in dbs.items():
            _init_items_table(db)
            orig_push = db.push
            db.push = lambda a=alias, fn=orig_push: (order.append(a), fn())

        with ReplicaUnitOfWork(db_factory=dbs.__getitem__) as uow:
            with uow.tx("market_b") as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'b1')"))
            with uow.tx("market_a") as conn:
                conn.execute(text("INSERT INTO items VALUES (2, 'a1')"))
            # Second transaction on market_b must still produce only one push.
            with uow.tx("market_b") as conn:
                conn.execute(text("INSERT INTO items VALUES (3, 'b2')"))

        assert uow.ok is True
        assert uow.push_failed is False
        assert order == ["market_a", "market_b"]
        assert dbs["market_a"].pushes == 1
        assert dbs["market_b"].pushes == 1

        with dbs["market_b"].engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM items")).scalar() == 2

    def test_deduplicates_alias_synonyms_by_canonical_alias(
        self, tmp_path, fake_db_factory
    ):
        db = fake_db_factory(tmp_path / "primary.db", alias="wcmktnewkeep")
        _init_items_table(db)

        def factory(alias: str):
            assert alias in ("wcmkt", "wcmktnewkeep")
            return db

        with ReplicaUnitOfWork(db_factory=factory) as uow:
            with uow.tx("wcmkt") as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'via-synonym')"))
            with uow.tx("wcmktnewkeep") as conn:
                conn.execute(text("INSERT INTO items VALUES (2, 'via-canonical')"))

        assert uow.touched == {"wcmktnewkeep"}
        assert uow.pushed == {"wcmktnewkeep"}
        assert db.pushes == 1

    def test_read_does_not_mark_alias_touched_or_push(
        self, tmp_path, fake_db_factory
    ):
        db = fake_db_factory(tmp_path / "ro.db", alias="ro")
        _init_items_table(db)

        with ReplicaUnitOfWork(db_factory=lambda _: db) as uow:
            with uow.read("ro") as conn:
                assert conn.execute(text("SELECT COUNT(*) FROM items")).scalar() == 0

        assert uow.touched == set()
        assert db.pushes == 0

    def test_reentrant_tx_joins_active_transaction_atomically(
        self, tmp_path, fake_db_factory
    ):
        db = fake_db_factory(tmp_path / "reentrant.db", alias="m1")
        _init_items_table(db)

        with ReplicaUnitOfWork(db_factory=lambda _: db) as uow:
            with uow.tx("m1") as outer_conn:
                outer_conn.execute(text("INSERT INTO items VALUES (1, 'outer')"))
                with uow.tx("m1") as inner_conn:
                    assert inner_conn is outer_conn
                    inner_conn.execute(text("INSERT INTO items VALUES (2, 'inner')"))
                with uow.read("m1") as read_conn:
                    assert read_conn is outer_conn
                    assert read_conn.execute(text("SELECT COUNT(*) FROM items")).scalar() == 2

        assert db.pushes == 1
        with db.engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM items")).scalar() == 2

    def test_sole_rolled_back_tx_is_not_pushed_while_sibling_pushes(
        self, tmp_path, fake_db_factory
    ):
        dbs = {
            "good": fake_db_factory(tmp_path / "good.db", alias="good"),
            "bad": fake_db_factory(tmp_path / "bad.db", alias="bad"),
        }
        for db in dbs.values():
            _init_items_table(db)

        with ReplicaUnitOfWork(db_factory=dbs.__getitem__) as uow:
            with uow.tx("good") as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'ok')"))

            with pytest.raises(RuntimeError, match="boom"):
                with uow.tx("bad") as conn:
                    conn.execute(text("INSERT INTO items VALUES (2, 'rolled-back')"))
                    raise RuntimeError("boom")

        assert uow.ok is False
        assert uow.failed == {"bad"}
        assert uow.pushed == {"good"}
        assert dbs["good"].pushes == 1
        assert dbs["bad"].pushes == 0
        with dbs["bad"].engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM items")).scalar() == 0

    def test_earlier_commit_still_pushes_after_later_rollback_on_same_alias(
        self, tmp_path, fake_db_factory
    ):
        """A later rolled-back tx cannot undo an earlier commit in the local
        CDC queue, so the alias must still push while uow.ok stays False."""
        dbs = {
            "good": fake_db_factory(tmp_path / "good.db", alias="good"),
            "bad": fake_db_factory(tmp_path / "bad.db", alias="bad"),
        }
        for db in dbs.values():
            _init_items_table(db)

        with ReplicaUnitOfWork(db_factory=dbs.__getitem__) as uow:
            with uow.tx("good") as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'ok')"))

            with uow.tx("bad") as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'first')"))
            with pytest.raises(RuntimeError, match="boom"):
                with uow.tx("bad") as conn:
                    conn.execute(text("INSERT INTO items VALUES (2, 'rolled-back')"))
                    raise RuntimeError("boom")

        assert uow.ok is False
        assert uow.failed == {"bad"}
        assert uow.pushed == {"good", "bad"}
        assert dbs["good"].pushes == 1
        assert dbs["bad"].pushes == 1
        # The rolled-back row must not exist, while the committed row does.
        with dbs["bad"].engine.connect() as conn:
            rows = conn.execute(text("SELECT id FROM items")).fetchall()
            assert [r[0] for r in rows] == [1]

    def test_auto_touch_false_never_drops_prior_committed_work(
        self, tmp_path, fake_db_factory
    ):
        dbs = {
            "noop": fake_db_factory(tmp_path / "noop.db", alias="noop"),
            "mixed": fake_db_factory(tmp_path / "mixed.db", alias="mixed"),
        }
        for db in dbs.values():
            _init_items_table(db)

        with ReplicaUnitOfWork(db_factory=dbs.__getitem__) as uow:
            with uow.tx("noop", auto_touch=False) as conn:
                conn.execute(text("SELECT 1"))

            with uow.tx("mixed", auto_touch=False) as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'first')"))
            uow.mark_touched("mixed")

            # A subsequent no-op tx does not call mark_touched and must not
            # erase the earlier mark_touched("mixed").
            with uow.tx("mixed", auto_touch=False) as conn:
                conn.execute(text("SELECT 1"))

        assert uow.pushed == {"mixed"}
        assert dbs["noop"].pushes == 0
        assert dbs["mixed"].pushes == 1

    def test_standalone_import_has_no_circular_import(self):
        """Importing unit_of_work or parse_fits before cli_tools must succeed."""
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import mkts_backend.db.unit_of_work; import mkts_backend.utils.parse_fits",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr

    def test_push_failure_does_not_abort_remaining_aliases(
        self, tmp_path, fake_db_factory
    ):
        dbs = {
            "a_fail": fake_db_factory(tmp_path / "a.db", alias="a_fail"),
            "b_ok": fake_db_factory(tmp_path / "b.db", alias="b_ok"),
        }
        for db in dbs.values():
            _init_items_table(db)

        def boom():
            raise RuntimeError("network down")

        dbs["a_fail"].push = boom

        with ReplicaUnitOfWork(db_factory=dbs.__getitem__) as uow:
            with uow.tx("a_fail") as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'a')"))
            with uow.tx("b_ok") as conn:
                conn.execute(text("INSERT INTO items VALUES (2, 'b')"))

        assert uow.push_failed is True
        assert uow.ok is False
        assert uow.failed_pushes == {"a_fail"}
        assert uow.pushed == {"b_ok"}
        assert dbs["b_ok"].pushes == 1

    def test_uncaught_exception_skips_all_pushes_and_disposes_engines(
        self, tmp_path, fake_db_factory
    ):
        db = fake_db_factory(tmp_path / "m.db", alias="m")
        _init_items_table(db)
        disposed = []
        orig_dispose = db.engine.dispose
        db.engine.dispose = lambda: (disposed.append(True), orig_dispose())

        with pytest.raises(ValueError, match="abort"):
            with ReplicaUnitOfWork(db_factory=lambda _: db) as uow:
                with uow.tx("m") as conn:
                    conn.execute(text("INSERT INTO items VALUES (1, 'committed')"))
                raise ValueError("abort")

        assert db.pushes == 0
        assert len(disposed) >= 1

    def test_auto_push_false_skips_pushes(self, tmp_path, fake_db_factory):
        db = fake_db_factory(tmp_path / "m.db", alias="m")
        _init_items_table(db)

        with ReplicaUnitOfWork(auto_push=False, db_factory=lambda _: db) as uow:
            with uow.tx("m") as conn:
                conn.execute(text("INSERT INTO items VALUES (1, 'local-only')"))

        assert uow.touched == {"m"}
        assert db.pushes == 0
