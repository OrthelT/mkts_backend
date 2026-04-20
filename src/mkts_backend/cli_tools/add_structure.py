"""CLI entry point for importing structure rows into ``buildcost.db``.

Reads rows from a Google Sheet (or a local CSV for testing), derives a few
columns (structure_type_id, and when absent from the sheet, system_id /
region / region_id), prints a diff against the current ``structures``
table, and upserts new + changed rows to the Turso remote and the local
mirror.

Flags
-----
``--sheet-url=URL``    Override the sheet URL from ``settings.toml [buildcost]``.
``--worksheet=NAME``   Worksheet title (defaults to the first sheet).
``--file=PATH``        Read from a local CSV instead of Google Sheets (testing).
``--local``            Only update the local buildcost.db (skip Turso remote).
``--remote-only``      Only update the Turso remote (skip local mirror).
``--dry-run``          Print the diff only, make no writes.
``--yes``              Skip the confirm prompt (for scripted use).
"""

from __future__ import annotations

from mkts_backend.cli_tools.arg_utils import ParsedArgs
from mkts_backend.config.config import DatabaseConfig, load_settings
from mkts_backend.config.gsheets_config import GoogleSheetConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.utils.build_cost_utils import (
    StructureImportError,
    diff_structures,
    enrich_structure_rows,
    format_diff_for_display,
    load_existing_structures,
    read_structures_csv,
    read_structures_sheet,
    upsert_structures,
)

logger = configure_logging(__name__)


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def add_structure(args: list[str], market_alias: str = "primary") -> bool:
    """Top-level handler matching the CommandRegistry signature.

    ``market_alias`` is accepted for signature compatibility and ignored —
    buildcost data is market-agnostic.
    """
    del market_alias  # unused; signature required by registry

    p = ParsedArgs(args)

    sheet_url = p.get_string("sheet-url")
    worksheet_name = p.get_string("worksheet")
    csv_file = p.get_string("file")
    skip_confirm = p.has_flag("yes")
    dry_run = p.has_flag("dry-run")
    local_only = p.has_flag("local")
    remote_only = p.has_flag("remote-only")

    if local_only and remote_only:
        print("Error: --local and --remote-only are mutually exclusive")
        return False

    settings = load_settings()
    buildcost_cfg = settings.get("buildcost", {})
    if sheet_url is None:
        sheet_url = buildcost_cfg.get("sheet_url")
    if worksheet_name is None:
        worksheet_name = buildcost_cfg.get("default_worksheet") or None

    # ── 1. Read the source ─────────────────────────────────────
    try:
        if csv_file:
            source_df = read_structures_csv(csv_file)
            source_label = f"CSV file {csv_file}"
        else:
            if not sheet_url:
                print("Error: no sheet URL configured. Set [buildcost].sheet_url or pass --sheet-url=.")
                return False
            gs_config = GoogleSheetConfig(sheet_url=sheet_url)
            source_df = read_structures_sheet(gs_config, sheet_url=sheet_url, worksheet_name=worksheet_name)
            source_label = f"sheet {sheet_url}" + (f" (worksheet: {worksheet_name})" if worksheet_name else "")
    except Exception as e:
        logger.error(f"Failed to read source: {e}")
        print(f"Error reading source: {e}")
        return False

    if source_df.empty:
        print(f"Source has no rows: {source_label}")
        return False
    print(f"Loaded {len(source_df)} rows from {source_label}")

    # ── 2. Enrich using the local buildcost DB as the canonical lookup ──
    local_db = DatabaseConfig("buildcost")

    # Ensure the local DB is populated. SQLAlchemy's first connection creates
    # an empty 0-byte file, which would make later "no such table: rigs"
    # errors look like code bugs. verify_db_exists triggers sync() when needed.
    if not _ensure_buildcost_ready(local_db):
        return False

    try:
        enriched_df = enrich_structure_rows(source_df, local_db.engine)
    except StructureImportError as e:
        print(f"Error: {e}")
        return False
    except Exception as e:
        logger.error(f"Enrichment failed: {e}")
        print(f"Error during enrichment: {e}")
        return False
    print(f"Enriched {len(enriched_df)} rows")

    # ── 3. Diff against the existing structures table ──────────
    existing_df = load_existing_structures(local_db.engine)
    diff = diff_structures(existing_df, enriched_df)
    print(format_diff_for_display(diff))

    if diff.is_empty:
        print("Nothing to write.")
        return True

    if dry_run:
        print("--dry-run set; no writes performed.")
        return True

    # ── 4. Confirm ─────────────────────────────────────────────
    if not skip_confirm:
        if not _confirm(f"Write {diff.write_count} rows to structures?"):
            print("Aborted by user.")
            return False

    rows_to_write = _concat_new_and_changed(diff)

    # ── 5. Write ───────────────────────────────────────────────
    ok = True

    if not local_only:
        try:
            count = upsert_structures(local_db.remote_engine, rows_to_write)
            print(f"Remote (Turso): wrote {count} rows.")
            logger.info(f"Remote upsert complete: {count} rows; ids={list(rows_to_write['structure_id'])}")
        except Exception as e:
            logger.error(f"Remote upsert failed: {e}")
            print(f"Error writing to Turso remote: {e}")
            ok = False

    if not remote_only:
        try:
            count = upsert_structures(local_db.engine, rows_to_write)
            print(f"Local ({local_db.path}): wrote {count} rows.")
            logger.info(f"Local upsert complete: {count} rows; ids={list(rows_to_write['structure_id'])}")
        except Exception as e:
            logger.error(f"Local upsert failed: {e}")
            print(f"Error writing to local DB: {e}")
            ok = False

    return ok


def _concat_new_and_changed(diff):
    import pandas as pd

    frames = []
    if not diff.new_rows.empty:
        frames.append(diff.new_rows)
    if not diff.changed_rows.empty:
        frames.append(diff.changed_rows)
    return pd.concat(frames, ignore_index=True) if frames else diff.new_rows


def _ensure_buildcost_ready(db: DatabaseConfig) -> bool:
    """Confirm buildcost.db has the expected schema; sync if empty.

    SQLAlchemy's first connect() creates a zero-byte file — which would
    otherwise surface as cryptic "no such table" errors. This checks for the
    ``rigs`` and ``structures`` tables; if either is missing and Turso
    credentials are configured, it runs sync() to populate from remote.
    """
    from pathlib import Path
    from sqlalchemy import text

    def _has_tables(engine) -> bool:
        try:
            with engine.connect() as conn:
                existing = {
                    row[0]
                    for row in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    ).fetchall()
                }
            return {"rigs", "structures"}.issubset(existing)
        except Exception:
            return False

    if _has_tables(db.engine):
        return True

    if not db.turso_url or not db.token:
        db_path = Path(db.path)
        is_empty = db_path.exists() and db_path.stat().st_size == 0
        hint = "remove the empty 0-byte file and " if is_empty else ""
        print(
            f"Error: buildcost.db at '{db.path}' has no schema and Turso credentials "
            f"are not set. {hint}Either set TURSO_BUILDCOST_URL and TURSO_BUILDCOST_TOKEN "
            f"in mkts_backend/.env, or copy a populated buildcost.db into place."
        )
        return False

    print(f"buildcost.db needs initializing; syncing from {db.turso_url} ...")
    # Remove empty stub file so libsql's sync_url flow initializes cleanly.
    from pathlib import Path as _P
    stub = _P(db.path)
    if stub.exists() and stub.stat().st_size == 0:
        stub.unlink()
    info_stub = _P(f"{db.path}-info")
    if info_stub.exists() and info_stub.stat().st_size == 0:
        info_stub.unlink()

    try:
        db.sync()
    except Exception as e:
        logger.error(f"buildcost sync failed: {e}")
        print(f"Error: buildcost sync failed: {e}")
        return False

    # Re-engine after sync (the previous engine pointed at the empty file).
    db._engine = None
    if _has_tables(db.engine):
        print("buildcost.db synced successfully.")
        return True

    print("Error: buildcost.db synced but still missing required tables.")
    return False
