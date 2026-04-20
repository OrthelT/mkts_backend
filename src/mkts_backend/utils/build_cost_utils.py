"""Helpers for importing structures into buildcost.db from a Google Sheet.

The flow (driven by ``mkts_backend.cli_tools.add_structure``):

1. Read the source sheet (or a local CSV for testing) into a DataFrame.
2. Enrich each row with derived columns (``structure_type_id``, and when
   absent from the sheet, ``system_id`` / ``region`` / ``region_id`` by
   looking the system name up against the current ``structures`` table).
3. Diff the enriched rows against the current ``structures`` table and
   render a human-readable summary.
4. Upsert new and changed rows via SQLite's
   ``INSERT ... ON CONFLICT(structure_id) DO UPDATE``.

Rows present in the DB but missing from the sheet are reported as a
warning only — this module never deletes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.build_cost_models import Structure

logger = configure_logging(__name__)


# ── Constants ───────────────────────────────────────────────────

# Map of EVE upwell-structure type names → type_ids.
# Verified against the EVE SDE (sdetypes) as of PR #26 (Apr 2026).
# Cross-check with the SDE before adding new entries.
STRUCTURE_TYPE_IDS: dict[str, int] = {
    "Raitaru": 35825,
    "Azbel": 35826,
    "Sotiyo": 35827,
    "Athanor": 35835,
    "Tatara": 35836,
}

# Columns of the structures table, derived from the ORM model so order stays
# in sync with `build_cost_models.Structure`.
STRUCTURE_COLUMNS: list[str] = [c.name for c in Structure.__table__.columns]

# Columns that must come from the sheet — the rest can be enriched.
REQUIRED_SHEET_COLUMNS: list[str] = [
    "structure_id",
    "structure",
    "system",
    "structure_type",
    "tax",
]

# Columns we recognize on the sheet (required + optional). Passed to gspread
# as ``expected_headers`` so blank/extra columns in row 1 are tolerated.
KNOWN_SHEET_COLUMNS: list[str] = [
    *REQUIRED_SHEET_COLUMNS,
    "rig_1",
    "rig_2",
    "rig_3",
    "system_id",
    "region",
    "region_id",
]


# ── Exceptions ──────────────────────────────────────────────────


class StructureImportError(Exception):
    """Raised when the input sheet cannot be turned into a valid import."""


# ── Data shapes ─────────────────────────────────────────────────


@dataclass
class StructureDiff:
    """Result of comparing incoming sheet rows to the current DB."""

    new_rows: pd.DataFrame
    changed_rows: pd.DataFrame  # incoming rows whose contents differ from DB
    change_details: list[dict]  # {structure_id, column, old, new} per field
    unchanged_count: int
    missing_from_sheet: pd.DataFrame  # rows in DB absent from the sheet (warning only)

    @property
    def write_count(self) -> int:
        return len(self.new_rows) + len(self.changed_rows)

    @property
    def is_empty(self) -> bool:
        return self.write_count == 0


# ── Sheet reading ───────────────────────────────────────────────


def read_structures_sheet(gs_config, sheet_url: str | None, worksheet_name: str | None) -> pd.DataFrame:
    """Read the configured worksheet and return a DataFrame.

    Passes :data:`KNOWN_SHEET_COLUMNS` as ``expected_headers`` so blank or
    extra header cells in row 1 are tolerated. The caller (``enrich_``) then
    validates :data:`REQUIRED_SHEET_COLUMNS` are actually present.
    """
    df = gs_config.get_worksheet_as_dataframe(
        sheet_url=sheet_url,
        worksheet_name=worksheet_name,
        expected_headers=KNOWN_SHEET_COLUMNS,
    )
    logger.info(f"Loaded {len(df)} rows from sheet; columns: {list(df.columns)}")
    return df


def read_structures_csv(path: str) -> pd.DataFrame:
    """Read a structures CSV (for offline testing via --file=)."""
    df = pd.read_csv(path)
    logger.info(f"Loaded {len(df)} rows from CSV {path}; columns: {list(df.columns)}")
    return df


# ── Enrichment ──────────────────────────────────────────────────


def _load_known_systems(engine: Engine) -> dict[str, tuple[int, str, int]]:
    """Return ``{system_name: (system_id, region, region_id)}`` from current structures.

    Used to enrich sheet rows that only carry the system name.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT system, system_id, region, region_id "
                "FROM structures "
                "WHERE system IS NOT NULL AND system_id IS NOT NULL"
            )
        )
        rows = result.fetchall()

    known: dict[str, tuple[int, str, int]] = {}
    for system, system_id, region, region_id in rows:
        if not system:
            continue
        # Prefer the first non-null region/region_id for each system
        existing = known.get(system)
        if existing is None or (region and not existing[1]):
            known[system] = (int(system_id), region or "", int(region_id) if region_id else 0)
    return known


def _load_valid_rig_names(engine: Engine) -> set[str]:
    """Return the set of valid rig names from the ``rigs`` table."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT type_name FROM rigs WHERE type_name IS NOT NULL"))
        return {row[0] for row in result.fetchall()}


def _coerce_tax(value) -> float:
    """Parse a tax value from the sheet into a fractional float.

    Accepted inputs:
      * Native numbers (gspread UNFORMATTED_VALUE): returned as-is.
      * Plain numeric strings: ``"0.0005"`` → ``0.0005``.
      * Percent strings: ``"0.05%"`` → ``0.0005`` (divide by 100).
      * ``"%"`` suffix is stripped and whitespace tolerated.
    """
    if isinstance(value, (int, float)) and not (isinstance(value, float) and pd.isna(value)):
        return float(value)
    if value is None:
        raise ValueError("tax cannot be None")
    s = str(value).strip()
    if not s:
        raise ValueError("tax cannot be blank")
    if s.endswith("%"):
        return float(s[:-1].strip()) / 100.0
    return float(s)


def _normalize_rig_cell(value) -> str | None:
    """Treat blank, '0', and NaN all as 'no rig'."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    s = str(value).strip()
    if not s or s == "0":
        return None
    return s


def _coerce_required_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_SHEET_COLUMNS if c not in df.columns]
    if missing:
        raise StructureImportError(
            f"Sheet is missing required columns: {missing}. "
            f"Required: {REQUIRED_SHEET_COLUMNS}"
        )


def enrich_structure_rows(sheet_df: pd.DataFrame, buildcost_engine: Engine) -> pd.DataFrame:
    """Validate required columns, derive missing ones, normalize types.

    Returns a DataFrame whose columns are exactly ``STRUCTURE_COLUMNS``.
    Raises :class:`StructureImportError` on any validation failure.
    """
    _coerce_required_columns(sheet_df)

    df = sheet_df.copy()

    # Drop entirely-blank rows (sheets often have trailing empty rows).
    df = df[df["structure_id"].astype(str).str.strip() != ""].reset_index(drop=True)

    # structure_id is the primary key — reject duplicates early.
    try:
        df["structure_id"] = df["structure_id"].astype(int)
    except (ValueError, TypeError) as exc:
        raise StructureImportError(f"structure_id must be integer: {exc}") from exc
    dupes = df["structure_id"][df["structure_id"].duplicated()].tolist()
    if dupes:
        raise StructureImportError(f"Duplicate structure_id values in sheet: {dupes}")

    # tax → float. Accepts native numbers (from UNFORMATTED_VALUE),
    # plain numeric strings ("0.0005"), and percent strings ("0.05%").
    # A percent string is divided by 100 to match the DB's fractional form.
    try:
        df["tax"] = df["tax"].map(_coerce_tax).astype(float)
    except (ValueError, TypeError) as exc:
        raise StructureImportError(f"tax must be numeric: {exc}") from exc

    # Normalize rig cells.
    for col in ("rig_1", "rig_2", "rig_3"):
        if col not in df.columns:
            df[col] = None
        df[col] = df[col].map(_normalize_rig_cell)

    # Validate rig names against the rigs table.
    valid_rigs = _load_valid_rig_names(buildcost_engine)
    unknown_rigs: set[str] = set()
    for col in ("rig_1", "rig_2", "rig_3"):
        for value in df[col].dropna().unique():
            if value not in valid_rigs:
                unknown_rigs.add(value)
    if unknown_rigs:
        raise StructureImportError(
            f"Unknown rig names (not in rigs.type_name): {sorted(unknown_rigs)}"
        )

    # structure_type → structure_type_id (always derived from the const map).
    df["structure_type"] = df["structure_type"].astype(str).str.strip()
    unknown_types = sorted(set(df["structure_type"]) - set(STRUCTURE_TYPE_IDS))
    if unknown_types:
        raise StructureImportError(
            f"Unknown structure_type values (add to STRUCTURE_TYPE_IDS): {unknown_types}"
        )
    df["structure_type_id"] = df["structure_type"].map(STRUCTURE_TYPE_IDS).astype(int)

    # System enrichment: fill system_id / region / region_id from known systems
    # when the sheet didn't provide them.
    known_systems = _load_known_systems(buildcost_engine)
    df["system"] = df["system"].astype(str).str.strip()
    for col in ("system_id", "region", "region_id"):
        if col not in df.columns:
            df[col] = None

    unresolved: list[str] = []
    for i, row in df.iterrows():
        system = row["system"]
        sheet_system_id = row.get("system_id")
        sheet_region = row.get("region")
        sheet_region_id = row.get("region_id")

        have_all = _is_truthy(sheet_system_id) and _is_truthy(sheet_region) and _is_truthy(sheet_region_id)
        if have_all:
            df.at[i, "system_id"] = int(sheet_system_id)
            df.at[i, "region"] = str(sheet_region)
            df.at[i, "region_id"] = int(sheet_region_id)
            continue

        known = known_systems.get(system)
        if known is None:
            unresolved.append(system)
            continue
        sid, region, rid = known
        df.at[i, "system_id"] = int(sheet_system_id) if _is_truthy(sheet_system_id) else sid
        df.at[i, "region"] = str(sheet_region) if _is_truthy(sheet_region) else region
        df.at[i, "region_id"] = int(sheet_region_id) if _is_truthy(sheet_region_id) else rid

    if unresolved:
        known_names = sorted(known_systems)
        raise StructureImportError(
            f"Unknown systems (not in structures table): {sorted(set(unresolved))}. "
            f"Either add system_id/region/region_id columns to the sheet for these rows, "
            f"or first add a structure in the system some other way. "
            f"Known systems: {known_names}"
        )

    # Reorder / select to canonical columns.
    df["structure"] = df["structure"].astype(str).str.strip()
    df["system_id"] = df["system_id"].astype(int)
    df["region_id"] = df["region_id"].astype(int)
    return df[STRUCTURE_COLUMNS].reset_index(drop=True)


def _is_truthy(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and pd.isna(v):
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


# ── Diff ────────────────────────────────────────────────────────


def load_existing_structures(engine: Engine) -> pd.DataFrame:
    """Read the full ``structures`` table."""
    with engine.connect() as conn:
        df = pd.read_sql(text(f"SELECT {', '.join(STRUCTURE_COLUMNS)} FROM structures"), conn)
    return df


def _cell_equal(a, b) -> bool:
    """Treat NaN/None/'' as equivalent; otherwise compare after string-norm."""
    a_blank = a is None or (isinstance(a, float) and pd.isna(a)) or (isinstance(a, str) and not a.strip())
    b_blank = b is None or (isinstance(b, float) and pd.isna(b)) or (isinstance(b, str) and not b.strip())
    if a_blank and b_blank:
        return True
    if a_blank or b_blank:
        return False
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return False
    return str(a).strip() == str(b).strip()


def diff_structures(existing: pd.DataFrame, incoming: pd.DataFrame) -> StructureDiff:
    """Compare incoming enriched rows against the current DB.

    Identity is by ``structure_id``. Returns sets of new rows, changed rows
    (with per-field old/new detail), unchanged count, and a warning set of
    DB rows missing from the incoming sheet.
    """
    existing_by_id = {int(row["structure_id"]): row for _, row in existing.iterrows()}
    incoming_by_id = {int(row["structure_id"]): row for _, row in incoming.iterrows()}

    new_ids = sorted(set(incoming_by_id) - set(existing_by_id))
    changed_ids: list[int] = []
    change_details: list[dict] = []
    unchanged = 0

    compare_cols = [c for c in STRUCTURE_COLUMNS if c != "structure_id"]
    for sid in sorted(set(incoming_by_id) & set(existing_by_id)):
        new_row = incoming_by_id[sid]
        old_row = existing_by_id[sid]
        row_changes: list[dict] = []
        for col in compare_cols:
            if not _cell_equal(old_row[col], new_row[col]):
                row_changes.append(
                    {"structure_id": sid, "column": col, "old": old_row[col], "new": new_row[col]}
                )
        if row_changes:
            changed_ids.append(sid)
            change_details.extend(row_changes)
        else:
            unchanged += 1

    missing_ids = sorted(set(existing_by_id) - set(incoming_by_id))

    new_rows = incoming[incoming["structure_id"].isin(new_ids)].reset_index(drop=True)
    changed_rows = incoming[incoming["structure_id"].isin(changed_ids)].reset_index(drop=True)
    missing_rows = existing[existing["structure_id"].isin(missing_ids)].reset_index(drop=True)

    return StructureDiff(
        new_rows=new_rows,
        changed_rows=changed_rows,
        change_details=change_details,
        unchanged_count=unchanged,
        missing_from_sheet=missing_rows,
    )


def format_diff_for_display(diff: StructureDiff) -> str:
    """Build a compact human-readable summary of the diff."""
    lines: list[str] = []
    lines.append("─" * 60)
    lines.append(
        f"Structures diff: "
        f"{len(diff.new_rows)} new, {len(diff.changed_rows)} changed, "
        f"{diff.unchanged_count} unchanged, "
        f"{len(diff.missing_from_sheet)} in DB but missing from sheet"
    )
    lines.append("─" * 60)

    if not diff.new_rows.empty:
        lines.append("NEW rows:")
        for _, r in diff.new_rows.iterrows():
            lines.append(f"  + [{r['structure_id']}] {r['structure']} ({r['structure_type']})")

    if not diff.changed_rows.empty:
        lines.append("CHANGED rows:")
        by_id: dict[int, list[dict]] = {}
        for d in diff.change_details:
            by_id.setdefault(d["structure_id"], []).append(d)
        for sid, changes in by_id.items():
            name_row = diff.changed_rows[diff.changed_rows["structure_id"] == sid].iloc[0]
            lines.append(f"  ~ [{sid}] {name_row['structure']}")
            for ch in changes:
                lines.append(f"      {ch['column']}: {ch['old']!r} → {ch['new']!r}")

    if not diff.missing_from_sheet.empty:
        lines.append("WARNING — present in DB but absent from sheet (will NOT be deleted):")
        for _, r in diff.missing_from_sheet.iterrows():
            lines.append(f"  ? [{r['structure_id']}] {r['structure']}")

    if diff.is_empty and diff.missing_from_sheet.empty:
        lines.append("No changes.")
    lines.append("─" * 60)
    return "\n".join(lines)


# ── Upsert ──────────────────────────────────────────────────────


def upsert_structures(engine: Engine, rows: pd.DataFrame) -> int:
    """Upsert ``rows`` into the ``structures`` table.

    Uses SQLite's native ``INSERT ... ON CONFLICT(structure_id) DO UPDATE``.
    The deployed ``structures`` table has a UNIQUE INDEX on ``structure_id``
    (``ix_structures_structure_id``) which is what ON CONFLICT binds to.

    All writes happen in a single transaction; any exception rolls back
    and is re-raised. NaN values in optional columns (rig_1..rig_3, region)
    are coerced to NULL before binding.
    """
    if rows.empty:
        return 0

    records: list[dict] = rows[STRUCTURE_COLUMNS].to_dict(orient="records")
    update_cols = [c for c in STRUCTURE_COLUMNS if c != "structure_id"]
    update_set = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    insert_cols = ", ".join(STRUCTURE_COLUMNS)
    insert_params = ", ".join(f":{c}" for c in STRUCTURE_COLUMNS)

    upsert_sql = text(
        f"INSERT INTO structures ({insert_cols}) VALUES ({insert_params}) "
        f"ON CONFLICT(structure_id) DO UPDATE SET {update_set}"
    )

    with engine.begin() as conn:
        for record in records:
            clean = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in record.items()}
            conn.execute(upsert_sql, clean)
    return len(records)


# ── Convenience for tests/callers ───────────────────────────────


def iter_structure_ids(df: pd.DataFrame) -> Iterable[int]:
    return (int(x) for x in df["structure_id"].tolist())
