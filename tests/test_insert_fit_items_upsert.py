"""Unit tests for insert_fit_items_to_db's upsert-in-place rewrite.

Task 9's live push verification (fit-update update --fit-id=494 against
fits/hfi.txt) found that the original delete-then-reinsert implementation —
which toggled ``PRAGMA foreign_keys = OFF`` around the whole transaction —
pushed successfully to the LOCAL replica but failed on Turso with
``FOREIGN KEY constraint failed`` on ``fittings_fittingitem``. ``PRAGMA
foreign_keys`` is per-connection session state; it is not something
pyturso's CDC log can replay, so the remote replayed the same INSERTs under
its own (enforced) foreign key checking.

These tests exercise the fixed function under FK enforcement turned ON for
every connection the function opens (via a SQLAlchemy ``connect`` event
listener), simulating the remote's strict behavior, and prove:
  1. Rows whose natural key (flag, type_id) is unchanged across two calls
     keep their AUTOINCREMENT rowid — no delete+reinsert churn.
  2. A quantity change updates the existing row in place (same id).
  3. An item removed from the fit disappears (clear_existing=True).
  4. A new item appears with a fresh id.
  5. clear_existing=False never deletes anything, only adds/updates.
"""
from sqlalchemy import event, text

from mkts_backend.utils import parse_fits


def _build_fittings_engine(fake_db_factory, tmp_path, fit_id: int, type_ids):
    """Real schema (with the actual FK constraints), FK enforcement ON for
    every connection the engine opens — including ones opened internally by
    ``insert_fit_items_to_db`` via ``_get_engine``."""
    db = fake_db_factory(tmp_path / "fittings.db", alias="fittings")

    @event.listens_for(db.engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with db.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE fittings_type (type_id INTEGER PRIMARY KEY, type_name TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE fittings_fitting (id INTEGER PRIMARY KEY, description TEXT, "
            "name TEXT, ship_type_type_id INTEGER, ship_type_id INTEGER, "
            "created TEXT, last_updated TEXT, "
            "FOREIGN KEY (ship_type_id) REFERENCES fittings_type (type_id))"
        ))
        conn.execute(text(
            "CREATE TABLE fittings_fittingitem ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, flag TEXT NOT NULL, "
            "quantity INTEGER NOT NULL, type_id INTEGER NOT NULL, "
            "fit_id INTEGER NOT NULL, type_fk_id INTEGER NOT NULL, "
            "FOREIGN KEY (fit_id) REFERENCES fittings_fitting (id), "
            "FOREIGN KEY (type_fk_id) REFERENCES fittings_type (type_id))"
        ))
        for tid in type_ids:
            conn.execute(
                text("INSERT INTO fittings_type VALUES (:tid, :name)"),
                {"tid": tid, "name": f"Type {tid}"},
            )
        conn.execute(
            text(
                "INSERT INTO fittings_fitting (id, description, name, "
                "ship_type_type_id, ship_type_id, created, last_updated) "
                "VALUES (:id, 'd', 'n', :ship, :ship, 'c', 'u')"
            ),
            {"id": fit_id, "ship": type_ids[0]},
        )
    return db


def _rows_by_key(db, fit_id):
    with db.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, flag, type_id, quantity, type_fk_id "
                "FROM fittings_fittingitem WHERE fit_id = :fit_id"
            ),
            {"fit_id": fit_id},
        ).fetchall()
    return {(r.flag, r.type_id): r for r in rows}


class TestInsertFitItemsUpsert:
    def test_unchanged_rows_keep_stable_rowids_under_fk_enforcement(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        fit_id = 494
        type_ids = [2048, 3841, 21640, 99999]
        db = _build_fittings_engine(fake_db_factory, tmp_path, fit_id, type_ids)
        monkeypatch.setattr(parse_fits, "DatabaseConfig", lambda *a, **k: db)

        first_items = [
            {"flag": "LoSlot0", "quantity": 1, "type_id": 2048, "type_fk_id": 2048},
            {"flag": "MedSlot0", "quantity": 1, "type_id": 3841, "type_fk_id": 3841},
            {"flag": "Cargo", "quantity": 100, "type_id": 21640, "type_fk_id": 21640},
        ]
        parse_fits.insert_fit_items_to_db(first_items, fit_id=fit_id, clear_existing=True)

        first_rows = _rows_by_key(db, fit_id)
        assert set(first_rows) == {("LoSlot0", 2048), ("MedSlot0", 3841), ("Cargo", 21640)}
        lo_id_before = first_rows[("LoSlot0", 2048)].id
        cargo_id_before = first_rows[("Cargo", 21640)].id

        # Second call: LoSlot0/2048 unchanged, Cargo/21640's quantity changes,
        # MedSlot0/3841 is removed (no longer in the fit), a new item appears.
        second_items = [
            {"flag": "LoSlot0", "quantity": 1, "type_id": 2048, "type_fk_id": 2048},
            {"flag": "Cargo", "quantity": 250, "type_id": 21640, "type_fk_id": 21640},
            {"flag": "Cargo", "quantity": 5, "type_id": 99999, "type_fk_id": 99999},
        ]
        parse_fits.insert_fit_items_to_db(second_items, fit_id=fit_id, clear_existing=True)

        second_rows = _rows_by_key(db, fit_id)
        assert set(second_rows) == {("LoSlot0", 2048), ("Cargo", 21640), ("Cargo", 99999)}

        # Unchanged row: same rowid, same content.
        assert second_rows[("LoSlot0", 2048)].id == lo_id_before

        # Updated row: same rowid, new quantity — proves it was UPDATEd in
        # place, not deleted and reinserted.
        assert second_rows[("Cargo", 21640)].id == cargo_id_before
        assert second_rows[("Cargo", 21640)].quantity == 250

        # Removed row: MedSlot0/3841 is gone.
        assert ("MedSlot0", 3841) not in second_rows

        # New row: got a fresh id, distinct from anything reused.
        new_id = second_rows[("Cargo", 99999)].id
        assert new_id not in (lo_id_before, cargo_id_before)

    def test_clear_existing_false_never_deletes(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        fit_id = 495
        type_ids = [2048, 3841]
        db = _build_fittings_engine(fake_db_factory, tmp_path, fit_id, type_ids)
        monkeypatch.setattr(parse_fits, "DatabaseConfig", lambda *a, **k: db)

        parse_fits.insert_fit_items_to_db(
            [{"flag": "LoSlot0", "quantity": 1, "type_id": 2048, "type_fk_id": 2048}],
            fit_id=fit_id, clear_existing=True,
        )
        first_rows = _rows_by_key(db, fit_id)
        lo_id = first_rows[("LoSlot0", 2048)].id

        # A call with a completely different item set and clear_existing=False
        # must add the new item without touching the old one.
        parse_fits.insert_fit_items_to_db(
            [{"flag": "MedSlot0", "quantity": 1, "type_id": 3841, "type_fk_id": 3841}],
            fit_id=fit_id, clear_existing=False,
        )
        rows = _rows_by_key(db, fit_id)
        assert set(rows) == {("LoSlot0", 2048), ("MedSlot0", 3841)}
        assert rows[("LoSlot0", 2048)].id == lo_id

    def test_reinsert_identical_items_is_a_no_op_on_rowids(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Re-running the exact same fit content twice (the common case —
        re-parsing an unchanged EFT export) must not churn any rowid at
        all."""
        fit_id = 496
        type_ids = [2048, 3841, 519]
        db = _build_fittings_engine(fake_db_factory, tmp_path, fit_id, type_ids)
        monkeypatch.setattr(parse_fits, "DatabaseConfig", lambda *a, **k: db)

        items = [
            {"flag": "LoSlot0", "quantity": 1, "type_id": 2048, "type_fk_id": 2048},
            {"flag": "LoSlot1", "quantity": 1, "type_id": 519, "type_fk_id": 519},
            {"flag": "MedSlot0", "quantity": 1, "type_id": 3841, "type_fk_id": 3841},
        ]
        parse_fits.insert_fit_items_to_db(items, fit_id=fit_id, clear_existing=True)
        ids_before = _rows_by_key(db, fit_id)

        parse_fits.insert_fit_items_to_db(items, fit_id=fit_id, clear_existing=True)
        ids_after = _rows_by_key(db, fit_id)

        assert {k: v.id for k, v in ids_before.items()} == {
            k: v.id for k, v in ids_after.items()
        }
        assert len(ids_after) == 3

    def test_duplicate_key_rows_are_treated_as_a_multiset(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Reproduces the real wcfittingtest.db shape found by a live audit:
        multiple rows sharing one (fit_id, flag, type_id) key (e.g. fit 404's
        two Cargo/15463 rows with quantities 1 and 11; fit 492's five
        HighSlot0/14278 rows) — the table has no UNIQUE constraint and the
        EFT parser legitimately emits repeated Cargo/DroneBay lines for the
        same item. The upsert must consume existing same-key rows one at a
        time as matching incoming lines are processed — never collapse them
        into a single UPDATE while leaving the rest neither updated nor
        deleted — and rows it keeps must retain their rowid."""
        fit_id = 404
        type_id = 15463
        db = _build_fittings_engine(fake_db_factory, tmp_path, fit_id, [type_id])
        monkeypatch.setattr(parse_fits, "DatabaseConfig", lambda *a, **k: db)

        def _rows():
            with db.engine.connect() as conn:
                return conn.execute(
                    text(
                        "SELECT id, quantity FROM fittings_fittingitem "
                        "WHERE fit_id = :f ORDER BY id"
                    ),
                    {"f": fit_id},
                ).fetchall()

        # Seed 3 pre-existing rows sharing the same (Cargo, type_id) key —
        # the real duplicate shape, generalized to three occurrences.
        first_items = [
            {"flag": "Cargo", "quantity": 1, "type_id": type_id, "type_fk_id": type_id},
            {"flag": "Cargo", "quantity": 11, "type_id": type_id, "type_fk_id": type_id},
            {"flag": "Cargo", "quantity": 99, "type_id": type_id, "type_fk_id": type_id},
        ]
        parse_fits.insert_fit_items_to_db(first_items, fit_id=fit_id, clear_existing=True)
        seeded = _rows()
        assert len(seeded) == 3
        id1, id2, id3 = (r.id for r in seeded)

        # New EFT source has only 2 occurrences of the same key (count
        # shrank by one).
        second_items = [
            {"flag": "Cargo", "quantity": 50, "type_id": type_id, "type_fk_id": type_id},
            {"flag": "Cargo", "quantity": 60, "type_id": type_id, "type_fk_id": type_id},
        ]
        parse_fits.insert_fit_items_to_db(second_items, fit_id=fit_id, clear_existing=True)
        rows = _rows()

        # Final rows exactly match the new EFT source: 2 rows. The earliest
        # two existing ids were reused (updated in place, oldest-first);
        # the third (now-surplus) id was deleted, not left dangling.
        assert [r.id for r in rows] == [id1, id2]
        assert [r.quantity for r in rows] == [50, 60]

        with db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM fittings_fittingitem WHERE id = :i"),
                {"i": id3},
            ).scalar() == 0

        # Count grows back to 3 — the two surviving ids stay stable, and a
        # fresh row is inserted for the extra occurrence.
        third_items = [
            {"flag": "Cargo", "quantity": 50, "type_id": type_id, "type_fk_id": type_id},
            {"flag": "Cargo", "quantity": 60, "type_id": type_id, "type_fk_id": type_id},
            {"flag": "Cargo", "quantity": 70, "type_id": type_id, "type_fk_id": type_id},
        ]
        parse_fits.insert_fit_items_to_db(third_items, fit_id=fit_id, clear_existing=True)
        rows = _rows()

        assert len(rows) == 3
        assert [r.id for r in rows[:2]] == [id1, id2]
        assert [r.quantity for r in rows] == [50, 60, 70]
        assert rows[2].id not in (id1, id2, id3)

    def test_list_form_item_does_not_redirect_the_scoped_fit_id(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """A list/tuple-form item's own embedded fit_id must never leak
        into the SELECT/DELETE that scopes the upsert to the caller's
        fit_id parameter. Before the fix, the normalization loop's tuple
        unpacking (``flag, quantity, type_id, fit_id, type_fk_id = item``)
        rebound the very ``fit_id`` name the SELECT/DELETE used, so a
        list-form item carrying a different fit_id could silently redirect
        which fit's rows were read and deleted."""
        target_fit_id = 700
        other_fit_id = 800
        type_ids = [2048, 519, 3841]
        db = _build_fittings_engine(fake_db_factory, tmp_path, target_fit_id, type_ids)
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO fittings_fitting (id, description, name, "
                    "ship_type_type_id, ship_type_id, created, last_updated) "
                    "VALUES (:id, 'd', 'n', :ship, :ship, 'c', 'u')"
                ),
                {"id": other_fit_id, "ship": type_ids[0]},
            )
        monkeypatch.setattr(parse_fits, "DatabaseConfig", lambda *a, **k: db)

        # Seed one existing row under each of two distinct fits.
        parse_fits.insert_fit_items_to_db(
            [{"flag": "LoSlot0", "quantity": 1, "type_id": 2048, "type_fk_id": 2048}],
            fit_id=target_fit_id, clear_existing=True,
        )
        parse_fits.insert_fit_items_to_db(
            [{"flag": "LoSlot0", "quantity": 1, "type_id": 3841, "type_fk_id": 3841}],
            fit_id=other_fit_id, clear_existing=True,
        )

        # A list-form item embedding a DIFFERENT fit_id (other_fit_id),
        # passed into a call scoped to target_fit_id.
        list_item = ["MedSlot0", 1, 519, other_fit_id, 519]
        parse_fits.insert_fit_items_to_db(
            [list_item], fit_id=target_fit_id, clear_existing=True,
        )

        with db.engine.connect() as conn:
            other_rows = conn.execute(
                text("SELECT type_id FROM fittings_fittingitem WHERE fit_id = :f"),
                {"f": other_fit_id},
            ).fetchall()
            target_rows = conn.execute(
                text("SELECT type_id FROM fittings_fittingitem WHERE fit_id = :f"),
                {"f": target_fit_id},
            ).fetchall()

        # other_fit_id's pre-existing row (3841) must survive untouched by a
        # call scoped to target_fit_id -- before the fix, unpacking the
        # list-form item would have rebound the SELECT/DELETE's fit_id to
        # other_fit_id, deleting this row instead. (The list-item's own
        # INSERT legitimately lands under its embedded fit_id, 800 -- that
        # per-item routing is preserved legacy behavior, not the bug.)
        assert 3841 in [r.type_id for r in other_rows]

        # target_fit_id's own pre-existing row (2048) WAS correctly removed
        # by this call, since none of its incoming items reasserted that
        # key -- proving the DELETE really was scoped to target_fit_id, not
        # silently redirected into a no-op on the wrong fit.
        assert target_rows == []
