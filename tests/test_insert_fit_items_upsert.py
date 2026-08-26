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
