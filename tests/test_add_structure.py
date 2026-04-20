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
        # Matches production schema: no PRIMARY KEY / UNIQUE constraint on
        # structure_id. Do not change this — the upsert logic must work
        # against the real schema, which is permissive.
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
