"""Unit tests for the add_structure CLI utilities.

Covers enrichment, diffing, and upsert against an in-memory-ish
file-backed SQLite that mirrors the real buildcost.db schema.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from mkts_backend.utils.build_cost_utils import (
    STRUCTURE_COLUMNS,
    STRUCTURE_TYPE_IDS,
    StructureImportError,
    _coerce_tax,
    diff_structures,
    enrich_structure_rows,
    format_diff_for_display,
    load_existing_structures,
    upsert_structures,
)


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def buildcost_engine(tmp_path):
    """Create a file-backed SQLite with the real buildcost schema + seed rows."""
    db_path = tmp_path / "buildcost.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        # Matches production schema (post-migration): no PRIMARY KEY in the
        # table def, but a UNIQUE INDEX on structure_id (ix_structures_structure_id)
        # which is what `INSERT ... ON CONFLICT(structure_id)` binds against.
        conn.execute(text("""
            CREATE TABLE structures(
                structure TEXT,
                rig_1 TEXT, rig_2 TEXT, rig_3 TEXT,
                structure_type TEXT,
                system_id BIGINT,
                structure_id BIGINT,
                structure_type_id BIGINT,
                region_id BIGINT,
                tax FLOAT,
                region TEXT,
                system TEXT
            )
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX ix_structures_structure_id "
            "ON structures(structure_id)"
        ))
        conn.execute(text("""
            CREATE TABLE rigs(
                type_id INTEGER PRIMARY KEY,
                type_name TEXT,
                icon_id INTEGER
            )
        """))
        # Seed: two Vale systems, two known rig names.
        conn.execute(text(
            "INSERT INTO rigs VALUES (37146, 'Standup M-Set Basic Medium Ship "
            "Manufacturing Material Efficiency I', 21729)"
        ))
        conn.execute(text(
            "INSERT INTO rigs VALUES (37147, 'Standup M-Set Basic Medium Ship "
            "Manufacturing Time Efficiency I', 21730)"
        ))
        # Existing structure in Vale (4-HWWF).
        conn.execute(text("""
            INSERT INTO structures
                (structure_id, system, structure, system_id,
                 rig_1, rig_2, rig_3,
                 structure_type, structure_type_id, tax, region, region_id)
            VALUES (1035603743755, '4-HWWF', '4-HWWF - WinterCo. Construction Center',
                    30000240, NULL, NULL, NULL, 'Sotiyo', 35827, 0.0005,
                    'Vale of the Silent', 10000003)
        """))
        # Existing structure in Pure Blind (B-9C24) — seeds the known-systems map.
        conn.execute(text("""
            INSERT INTO structures
                (structure_id, system, structure, system_id,
                 rig_1, rig_2, rig_3,
                 structure_type, structure_type_id, tax, region, region_id)
            VALUES (1046853198075, 'B-9C24', 'B-9C24 - Processing Unit Alpha',
                    30002029, NULL, NULL, NULL, 'Tatara', 35836, 0.001,
                    'Pure Blind', 10000023)
        """))
    return engine


def _minimal_sheet_df(**overrides) -> pd.DataFrame:
    row = {
        "structure_id": 1039999999999,
        "structure": "4-HWWF - Test Azbel",
        "system": "4-HWWF",
        "structure_type": "Azbel",
        "tax": 0.0025,
        "rig_1": "Standup M-Set Basic Medium Ship Manufacturing Material Efficiency I",
        "rig_2": "",
        "rig_3": "",
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ── Enrichment ──────────────────────────────────────────────────


def test_enrich_happy_path(buildcost_engine):
    sheet = _minimal_sheet_df()
    enriched = enrich_structure_rows(sheet, buildcost_engine)

    assert list(enriched.columns) == STRUCTURE_COLUMNS
    row = enriched.iloc[0]
    assert row["structure_type_id"] == STRUCTURE_TYPE_IDS["Azbel"]
    assert row["system_id"] == 30000240
    assert row["region"] == "Vale of the Silent"
    assert row["region_id"] == 10000003
    assert row["rig_2"] is None  # blank normalized to None


def test_enrich_rejects_unknown_structure_type(buildcost_engine):
    sheet = _minimal_sheet_df(structure_type="Fortizar")
    with pytest.raises(StructureImportError, match="Unknown structure_type"):
        enrich_structure_rows(sheet, buildcost_engine)


def test_enrich_rejects_unknown_rig(buildcost_engine):
    sheet = _minimal_sheet_df(rig_1="Made Up Rig That Does Not Exist")
    with pytest.raises(StructureImportError, match="Unknown rig names"):
        enrich_structure_rows(sheet, buildcost_engine)


def test_enrich_rejects_unknown_system(buildcost_engine):
    sheet = _minimal_sheet_df(system="SomeNewSystem")
    with pytest.raises(StructureImportError, match="Unknown systems"):
        enrich_structure_rows(sheet, buildcost_engine)


def test_enrich_accepts_unknown_system_when_sheet_provides_all(buildcost_engine):
    sheet = _minimal_sheet_df(
        system="SomeNewSystem",
        system_id=30010000,
        region="New Region",
        region_id=10000099,
    )
    enriched = enrich_structure_rows(sheet, buildcost_engine)
    assert enriched.iloc[0]["system_id"] == 30010000
    assert enriched.iloc[0]["region"] == "New Region"
    assert enriched.iloc[0]["region_id"] == 10000099


def test_enrich_rejects_missing_required_columns(buildcost_engine):
    sheet = pd.DataFrame([{"structure_id": 1, "structure": "x"}])
    with pytest.raises(StructureImportError, match="missing required columns"):
        enrich_structure_rows(sheet, buildcost_engine)


def test_enrich_rejects_duplicate_structure_ids(buildcost_engine):
    df = pd.concat([_minimal_sheet_df(), _minimal_sheet_df()], ignore_index=True)
    with pytest.raises(StructureImportError, match="Duplicate structure_id"):
        enrich_structure_rows(df, buildcost_engine)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0005, 0.0005),          # native float (UNFORMATTED_VALUE)
        (0, 0.0),                   # native int
        ("0.0005", 0.0005),         # plain numeric string
        ("0.05%", 0.0005),          # percent string → divide by 100
        ("  0.05 %  ", 0.0005),     # whitespace tolerated
        ("1%", 0.01),
    ],
)
def test_coerce_tax_accepts_numeric_and_percent(value, expected):
    assert abs(_coerce_tax(value) - expected) < 1e-12


def test_enrich_accepts_percent_string_tax(buildcost_engine):
    sheet = _minimal_sheet_df(tax="0.25%")
    enriched = enrich_structure_rows(sheet, buildcost_engine)
    assert abs(enriched.iloc[0]["tax"] - 0.0025) < 1e-12


# ── Diff ────────────────────────────────────────────────────────


def test_diff_new_row(buildcost_engine):
    existing = load_existing_structures(buildcost_engine)
    enriched = enrich_structure_rows(_minimal_sheet_df(), buildcost_engine)
    diff = diff_structures(existing, enriched)

    assert len(diff.new_rows) == 1
    assert diff.changed_rows.empty
    assert diff.unchanged_count == 0
    assert len(diff.missing_from_sheet) == 2  # both seeded rows


def test_diff_changed_row(buildcost_engine):
    # Sheet carries the already-present 4-HWWF but with a different tax.
    sheet = pd.DataFrame([{
        "structure_id": 1035603743755,
        "structure": "4-HWWF - WinterCo. Construction Center",
        "system": "4-HWWF",
        "structure_type": "Sotiyo",
        "tax": 0.0010,  # was 0.0005
    }])
    existing = load_existing_structures(buildcost_engine)
    enriched = enrich_structure_rows(sheet, buildcost_engine)
    diff = diff_structures(existing, enriched)

    assert diff.new_rows.empty
    assert len(diff.changed_rows) == 1
    assert diff.unchanged_count == 0
    tax_change = [c for c in diff.change_details if c["column"] == "tax"]
    assert tax_change and tax_change[0]["new"] == 0.0010


def test_diff_unchanged(buildcost_engine):
    # Sheet matches DB exactly.
    sheet = pd.DataFrame([{
        "structure_id": 1035603743755,
        "structure": "4-HWWF - WinterCo. Construction Center",
        "system": "4-HWWF",
        "structure_type": "Sotiyo",
        "tax": 0.0005,
    }])
    existing = load_existing_structures(buildcost_engine)
    enriched = enrich_structure_rows(sheet, buildcost_engine)
    diff = diff_structures(existing, enriched)

    assert diff.new_rows.empty
    assert diff.changed_rows.empty
    assert diff.unchanged_count == 1


def test_format_diff_outputs_every_section(buildcost_engine):
    existing = load_existing_structures(buildcost_engine)
    enriched = enrich_structure_rows(_minimal_sheet_df(), buildcost_engine)
    diff = diff_structures(existing, enriched)

    out = format_diff_for_display(diff)
    assert "NEW rows" in out
    assert "absent from sheet" in out
    assert "4-HWWF - Test Azbel" in out


# ── Upsert ──────────────────────────────────────────────────────


def test_upsert_inserts_new_row(buildcost_engine):
    enriched = enrich_structure_rows(_minimal_sheet_df(), buildcost_engine)
    written = upsert_structures(buildcost_engine, enriched)

    assert written == 1
    with buildcost_engine.connect() as conn:
        row = conn.execute(
            text("SELECT structure, tax, structure_type_id FROM structures WHERE structure_id = :sid"),
            {"sid": 1039999999999},
        ).fetchone()
    assert row is not None
    assert row[0] == "4-HWWF - Test Azbel"
    assert row[1] == 0.0025
    assert row[2] == STRUCTURE_TYPE_IDS["Azbel"]


def test_upsert_updates_existing_row(buildcost_engine):
    sheet = pd.DataFrame([{
        "structure_id": 1035603743755,
        "structure": "4-HWWF - WinterCo. Construction Center",
        "system": "4-HWWF",
        "structure_type": "Sotiyo",
        "tax": 0.0099,
    }])
    enriched = enrich_structure_rows(sheet, buildcost_engine)
    upsert_structures(buildcost_engine, enriched)

    with buildcost_engine.connect() as conn:
        tax = conn.execute(
            text("SELECT tax FROM structures WHERE structure_id = 1035603743755")
        ).scalar()
    assert tax == 0.0099


def test_upsert_is_idempotent(buildcost_engine):
    enriched = enrich_structure_rows(_minimal_sheet_df(), buildcost_engine)
    upsert_structures(buildcost_engine, enriched)
    # Second run should not raise and leave row count identical.
    upsert_structures(buildcost_engine, enriched)
    with buildcost_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM structures")).scalar()
    assert count == 3  # 2 seeded + 1 new


def test_upsert_rolls_back_on_midbatch_failure(tmp_path):
    """A failing row mid-batch must revert earlier rows in the same call."""
    import numpy as np

    db_path = tmp_path / "rollback.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        # CHECK constraint rejects structure_id 666 — we use this to force
        # a failure on the second row while the first has already inserted.
        conn.execute(text("""
            CREATE TABLE structures(
                structure TEXT,
                rig_1 TEXT, rig_2 TEXT, rig_3 TEXT,
                structure_type TEXT,
                system_id BIGINT,
                structure_id BIGINT CHECK (structure_id != 666),
                structure_type_id BIGINT,
                region_id BIGINT,
                tax FLOAT,
                region TEXT,
                system TEXT
            )
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX ix_structures_structure_id ON structures(structure_id)"
        ))

    frame = pd.DataFrame([
        {c: None for c in STRUCTURE_COLUMNS} | {
            "structure_id": 1, "structure": "ok", "system": "s", "system_id": 10,
            "structure_type": "Azbel", "structure_type_id": 35826, "tax": 0.01,
            "region": "r", "region_id": 100,
        },
        {c: None for c in STRUCTURE_COLUMNS} | {
            "structure_id": 666, "structure": "fails", "system": "s", "system_id": 10,
            "structure_type": "Azbel", "structure_type_id": 35826, "tax": 0.01,
            "region": "r", "region_id": 100,
        },
    ])

    with pytest.raises(Exception):
        upsert_structures(engine, frame)

    # Rollback invariant: neither row should be present.
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM structures")).scalar()
    assert count == 0, "first-row insert was not rolled back after second-row failure"


def test_upsert_coerces_nan_to_null(buildcost_engine):
    """NaN values (in optional columns like rig_1..3, region) become SQL NULL."""
    import numpy as np

    # Build a row directly (bypass enrichment) with NaN in rig/region columns.
    row = {c: None for c in STRUCTURE_COLUMNS} | {
        "structure_id": 2000000000001,
        "structure": "NaN-test structure",
        "system": "Jita",
        "system_id": 30000142,
        "rig_1": np.nan,
        "rig_2": np.nan,
        "rig_3": np.nan,
        "structure_type": "Azbel",
        "structure_type_id": 35826,
        "tax": 0.01,
        "region": np.nan,
        "region_id": 10000002,
    }
    frame = pd.DataFrame([row])
    upsert_structures(buildcost_engine, frame)

    with buildcost_engine.connect() as conn:
        result = conn.execute(text(
            "SELECT rig_1, rig_2, rig_3, region FROM structures "
            "WHERE structure_id = 2000000000001"
        )).fetchone()
    assert result == (None, None, None, None), (
        f"NaN should be stored as SQL NULL, got {result!r}"
    )


# ── CLI handler ─────────────────────────────────────────────────


@pytest.fixture
def cli_env(monkeypatch, buildcost_engine, tmp_path):
    """Wire the add_structure handler to use a test DB instead of Turso.

    Monkeypatches DatabaseConfig inside the handler module so both
    `engine` and `remote_engine` point at the same in-test SQLite DB.
    Skips the `_ensure_buildcost_ready` bootstrap path entirely.
    """
    from mkts_backend.cli_tools import add_structure as mod

    class _FakeDB:
        def __init__(self, *_a, **_kw):
            self.engine = buildcost_engine
            self.remote_engine = buildcost_engine
            self.path = str(tmp_path / "buildcost.db")
            self.turso_url = "libsql://fake"
            self.token = "fake-token"

        def sync(self):
            return None

    monkeypatch.setattr(mod, "DatabaseConfig", _FakeDB)
    monkeypatch.setattr(mod, "_ensure_buildcost_ready", lambda db: True)
    return mod


def _csv_for(tmp_path, rows: list[dict]) -> str:
    path = tmp_path / "input.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _sample_row(**overrides) -> dict:
    row = {
        "structure_id": 1040000000001,
        "structure": "4-HWWF - CLI Test Azbel",
        "system": "4-HWWF",
        "structure_type": "Azbel",
        "tax": 0.005,
        "rig_1": "Standup M-Set Basic Medium Ship Manufacturing Material Efficiency I",
        "rig_2": "",
        "rig_3": "",
    }
    row.update(overrides)
    return row


def test_cli_rejects_local_plus_remote_only(cli_env, tmp_path):
    csv = _csv_for(tmp_path, [_sample_row()])
    result = cli_env.add_structure([f"--file={csv}", "--local", "--remote-only", "--yes"])
    assert result is False


def test_cli_dry_run_writes_nothing(cli_env, buildcost_engine, tmp_path):
    csv = _csv_for(tmp_path, [_sample_row()])
    before = _count_structures(buildcost_engine)
    result = cli_env.add_structure([f"--file={csv}", "--dry-run", "--yes"])
    after = _count_structures(buildcost_engine)
    assert result is True
    assert after == before, "--dry-run must not write"


def test_cli_local_only_skips_remote(cli_env, buildcost_engine, tmp_path, monkeypatch):
    """--local must not call upsert against remote_engine."""
    from mkts_backend.cli_tools import add_structure as mod

    called_engines: list[object] = []
    real_upsert = mod.upsert_structures

    def spy_upsert(engine, rows):
        called_engines.append(engine)
        return real_upsert(engine, rows)

    monkeypatch.setattr(mod, "upsert_structures", spy_upsert)

    csv = _csv_for(tmp_path, [_sample_row()])
    result = cli_env.add_structure([f"--file={csv}", "--local", "--yes"])
    assert result is True
    assert len(called_engines) == 1, "--local must produce exactly one upsert call"


def test_cli_partial_success_remote_ok_local_fails(cli_env, buildcost_engine, tmp_path, monkeypatch, capsys):
    """When remote succeeds and local fails, handler returns False AND prints a WARNING."""
    from mkts_backend.cli_tools import add_structure as mod

    # The handler's write phase receives .remote_engine and .engine via the
    # fake DB — enrichment needs a real engine on .engine, and we drive the
    # pass/fail via a stubbed upsert_structures that counts calls.
    def flaky_upsert(engine, rows):
        flaky_upsert.calls += 1  # type: ignore[attr-defined]
        if flaky_upsert.calls == 2:  # second call == local (remote goes first)
            raise RuntimeError("simulated local failure")
        return len(rows)
    flaky_upsert.calls = 0  # type: ignore[attr-defined]

    monkeypatch.setattr(mod, "upsert_structures", flaky_upsert)

    csv = _csv_for(tmp_path, [_sample_row()])
    result = cli_env.add_structure([f"--file={csv}", "--yes"])
    out = capsys.readouterr().out
    assert result is False
    assert "WARNING" in out
    assert "Turso remote was updated but local write failed" in out


def test_cli_missing_sheet_url_errors(cli_env, monkeypatch, capsys):
    """With no --file and no configured sheet_url, handler reports a clean error."""
    from mkts_backend.cli_tools import add_structure as mod
    monkeypatch.setattr(mod, "load_settings", lambda: {"buildcost": {}})

    result = cli_env.add_structure(["--yes"])
    out = capsys.readouterr().out
    assert result is False
    assert "no sheet URL configured" in out


def _count_structures(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM structures")).scalar() or 0
