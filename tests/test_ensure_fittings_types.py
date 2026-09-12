"""Unit tests for ensure_fittings_types.

``fittings_fittingitem.type_fk_id`` and ``fittings_fitting.ship_type_id`` both
reference ``fittings_type.type_id``. The local turso replica runs with
``PRAGMA foreign_keys`` OFF, so a fit whose modules are absent from
``fittings_type`` writes locally without complaint — but Turso cloud enforces
the constraint when ``push()`` replays the CDC rows, and the orphan INSERTs
fail there. The push reports an error, yet the remote commits every other
statement in the batch and advances the client watermark, so the rows are
never retried: the remote fit silently ends up with fewer items than the
local one (fits 801 and 901, 2026-09-12).

``ensure_fittings_types`` closes the gap by copying any missing type — and the
item group / category it hangs off — from the SDE into ``fittings_type``
before the fit rows are written.

These tests run with FK enforcement ON so they fail the way the remote does.
"""
import pytest
from sqlalchemy import event, text

from mkts_backend.utils import parse_fits


def _fk_on(engine):
    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _build_dbs(fake_db_factory, tmp_path, monkeypatch):
    fittings = fake_db_factory(tmp_path / "fittings.db", alias="fittings")
    sde = fake_db_factory(tmp_path / "sde.db", alias="sde")
    _fk_on(fittings.engine)

    with fittings.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE fittings_itemcategory (category_id INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, published INTEGER NOT NULL)"
        ))
        conn.execute(text(
            "CREATE TABLE fittings_itemgroup (group_id INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, published INTEGER NOT NULL, category_id INTEGER NOT NULL, "
            "FOREIGN KEY (category_id) REFERENCES fittings_itemcategory (category_id))"
        ))
        conn.execute(text(
            "CREATE TABLE fittings_type (type_name TEXT NOT NULL, type_id INTEGER PRIMARY KEY, "
            "published INTEGER NOT NULL, volume REAL, group_id INTEGER, "
            "FOREIGN KEY (group_id) REFERENCES fittings_itemgroup (group_id))"
        ))
        # Pre-existing rows: category 7 (Module) and group 834 (Stealth Bomber)
        # holding the Purifier, as in the real database.
        conn.execute(text("INSERT INTO fittings_itemcategory VALUES (7, 'Module', 1)"))
        conn.execute(text("INSERT INTO fittings_itemcategory VALUES (6, 'Ship', 1)"))
        conn.execute(text("INSERT INTO fittings_itemgroup VALUES (834, 'Stealth Bomber', 1, 6)"))
        conn.execute(text("INSERT INTO fittings_type VALUES ('Purifier', 12038, 1, 28100.0, 834)"))

    with sde.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE sdetypes (typeID INTEGER PRIMARY KEY, typeName TEXT, groupID INTEGER, "
            "groupName TEXT, categoryID INTEGER, categoryName TEXT, volume REAL, "
            "metaGroupID INTEGER, metaGroupName TEXT, published INTEGER, repackagedVolume REAL)"
        ))
        conn.execute(text(
            "INSERT INTO sdetypes VALUES "
            "(12038, 'Purifier', 834, 'Stealth Bomber', 6, 'Ship', 28100.0, 1, 'Tech I', 1, 2500.0), "
            "(8117, 'Prototype ''Arbalest'' Torpedo Launcher', 508, 'Missile Launcher Torpedo', "
            "7, 'Module', 20.0, 1, 'Tech I', 1, NULL), "
            "(31620, 'Small Warhead Calefaction Catalyst I', 779, 'Rig Launcher', "
            "7, 'Module', 5.0, 1, 'Tech I', 1, NULL), "
            "(34, 'Tritanium', 18, 'Mineral', 4, 'Material', 0.01, NULL, NULL, 1, NULL)"
        ))

    dbs = {"fittings": fittings, "sde": sde}
    monkeypatch.setattr(parse_fits, "DatabaseConfig", lambda alias, *a, **k: dbs[alias])
    return fittings, sde


def _types(db):
    with db.engine.connect() as conn:
        return {
            r.type_id: (r.type_name, r.published, r.volume, r.group_id)
            for r in conn.execute(text("SELECT * FROM fittings_type"))
        }


class TestEnsureFittingsTypes:
    def test_missing_types_are_copied_from_sde_with_their_groups(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        fittings, _ = _build_dbs(fake_db_factory, tmp_path, monkeypatch)

        added = parse_fits.ensure_fittings_types([12038, 8117, 8117, 31620])

        assert added == [8117, 31620]
        types = _types(fittings)
        assert types[8117] == ("Prototype 'Arbalest' Torpedo Launcher", 1, 20.0, 508)
        assert types[31620] == ("Small Warhead Calefaction Catalyst I", 1, 5.0, 779)
        # The existing row is untouched, not re-inserted.
        assert types[12038] == ("Purifier", 1, 28100.0, 834)
        with fittings.engine.connect() as conn:
            groups = dict(conn.execute(
                text("SELECT group_id, name FROM fittings_itemgroup")
            ).fetchall())
            # FK enforcement is ON, so the inserts above only succeeded
            # because the groups were created first; check them anyway.
            assert groups == {
                834: "Stealth Bomber",
                508: "Missile Launcher Torpedo",
                779: "Rig Launcher",
            }
            assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    def test_all_present_is_a_no_op(self, tmp_path, monkeypatch, fake_db_factory):
        fittings, _ = _build_dbs(fake_db_factory, tmp_path, monkeypatch)
        before = _types(fittings)

        assert parse_fits.ensure_fittings_types([12038]) == []
        assert parse_fits.ensure_fittings_types([]) == []
        assert _types(fittings) == before

    def test_missing_category_is_created_too(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        fittings, _ = _build_dbs(fake_db_factory, tmp_path, monkeypatch)

        assert parse_fits.ensure_fittings_types([34]) == [34]
        with fittings.engine.connect() as conn:
            assert conn.execute(text(
                "SELECT category_id, name FROM fittings_itemcategory WHERE category_id = 4"
            )).fetchall() == [(4, "Material")]
            assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    def test_type_unknown_to_sde_raises(self, tmp_path, monkeypatch, fake_db_factory):
        fittings, _ = _build_dbs(fake_db_factory, tmp_path, monkeypatch)

        with pytest.raises(ValueError, match="999999"):
            parse_fits.ensure_fittings_types([8117, 999999])
        # Nothing is written when any requested type is unresolvable.
        assert 8117 not in _types(fittings)
