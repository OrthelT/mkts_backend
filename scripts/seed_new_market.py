"""Seed a new market's Turso DB with reference/config tables.

Copies the market-independent "reference" tables (watchlist, doctrines, fits,
etc.) from a source market's LOCAL database into a destination market's local
pyturso replica, then pushes the result to that market's Turso remote.
Market-data tables (marketorders, market_history, marketstats, jita_prices,
esi_request_cache, update_log) are intentionally NOT copied — those are
populated by the normal collection run for the new market.

Use this to bootstrap a freshly-created market so the frontend has doctrines,
watchlist, and targets to display before the first data-collection run.

Once up front (only when applying): ensure schema — create any missing target
tables from Base (checkfirst leaves existing tables alone).

Flow per table:
  1. Read source rows (model column set) from <source> local DB.
  2. CSV backup of current destination rows (if any).
  3. In one transaction on the destination's local replica: DELETE all
     destination rows, INSERT the source rows (ids preserved so cross-table
     references stay consistent), then verify destination row count ==
     source row count — a mismatch rolls the whole transaction back.

Market-derived columns (stock, price, timestamps — see MARKET_DERIVED_RESET)
are reset on insert so the new market starts at zero availability instead of
reporting the source market's numbers until its first collection run.

Writes land on the destination's local replica, table by table. Once every
requested table has been processed, the whole run pushes to Turso a single
time (skipped on a dry run, or when nothing was actually written). A push
failure is reported and fails the command, even though the local writes it's
reporting on already committed.

Dry-run by default. Pass --apply to actually write.

Examples:
  # Default: copy wcmktnewkeep -> wcmktbkg (dry-run, shows the plan)
  uv run python scripts/seed_new_market.py

  # Actually write
  uv run python scripts/seed_new_market.py --apply

  # A different source/destination, only two tables
  uv run python scripts/seed_new_market.py --source wcmktnewkeep --dest wcmktbkg \\
      --only watchlist --only doctrines --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import (
    Base,
    Doctrines,
    DoctrineFitItems,
    DoctrineMap,
    LeadShips,
    ModuleEquivalents,
    ShipTargets,
    Watchlist,
)

logger = configure_logging(__name__)

BACKUP_DIR = Path("data/migration_backups")

# Reference/config tables to seed into a new market, in dependency-friendly
# order. Add a model here to include another reference table in the bootstrap.
# Market-data tables are deliberately excluded (see module docstring).
REFERENCE_MODELS = [
    Watchlist,
    Doctrines,
    DoctrineFitItems,
    DoctrineMap,
    LeadShips,
    ShipTargets,
    ModuleEquivalents,
]

# Columns whose values are snapshots of the SOURCE market's state, recalculated
# per-market by process_doctrine_stats() on every collection run. Copying them
# verbatim would make a fresh market report the source market's availability,
# so they are reset on insert (zero stock, no price/history) instead.
MARKET_DERIVED_RESET: dict[str, dict] = {
    Doctrines.__tablename__: {
        "hulls": 0,
        "fits_on_mkt": 0.0,
        "total_stock": 0,
        "price": None,
        "avg_vol": None,
        "days": None,
        "timestamp": None,
    },
}


def table_exists(engine, table: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).fetchone()
    return row is not None


def read_source_rows(src: DatabaseConfig, table: str, cols: list[str]) -> list[dict]:
    """Read the model's columns from the source LOCAL db as a list of dicts."""
    col_list = ", ".join(cols)
    with src.engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {col_list} FROM {table}")).fetchall()
    return [dict(r._mapping) for r in rows]


def backup_table(dest: DatabaseConfig, table: str) -> Path | None:
    """CSV-dump the destination table's current rows. Returns None if empty."""
    with dest.remote_engine.connect() as conn:
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    if df.empty:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = BACKUP_DIR / f"{dest.alias}_{table}_{ts}.csv"
    df.to_csv(out, index=False)
    logger.info(f"Backed up {len(df)} rows from {dest.alias}.{table} -> {out}")
    return out


def dest_count(dest: DatabaseConfig, table: str) -> int | None:
    """Destination row count, or None if the table does not exist yet."""
    if not table_exists(dest.remote_engine, table):
        return None
    with dest.remote_engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def seed_table(
    src: DatabaseConfig, dest: DatabaseConfig, model, *, apply: bool,
    allow_empty_source: bool = False,
) -> bool:
    table = model.__tablename__
    cols = list(model.__table__.columns.keys())

    print(f"\n=== {table} ({src.alias} -> {dest.alias}) ===")
    rows = read_source_rows(src, table, cols)
    before = dest_count(dest, table)
    before_str = "missing" if before is None else str(before)
    print(f"Source rows: {len(rows)} | Destination rows: {before_str}")

    # An empty source table against a populated destination almost always means
    # the wrong --source or a stale/never-synced local db — wiping the
    # destination and inserting nothing would destroy live data and still
    # report success (0 == 0 passes verification).
    if not rows and (before or 0) > 0 and not allow_empty_source:
        print(f"REFUSED: source {src.alias}.{table} is empty but destination has "
              f"{before} rows. Wrong --source or stale local db? "
              f"Pass --allow-empty-source to wipe anyway.")
        return False

    reset = MARKET_DERIVED_RESET.get(table)
    if reset:
        rows = [{**r, **reset} for r in rows]
        print(f"Resetting market-derived columns: {sorted(reset)}")

    if not apply:
        print(f"Plan: ensure schema, backup {before_str} dest rows, "
              f"wipe + insert {len(rows)} rows.")
        print("DRY RUN — no changes made. Pass --apply to execute.")
        return True

    backup = backup_table(dest, table)
    if backup:
        print(f"Backup written: {backup}")

    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    insert_sql = text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})")

    # Wipe + insert + verify as one transaction; a mismatch rolls everything back.
    with dest.remote_engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table}"))
        if rows:
            conn.execute(insert_sql, rows)
        copied = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        if copied != len(rows):
            raise RuntimeError(
                f"Row count mismatch after copy: source={len(rows)}, dest={copied}"
            )

    print(f"After: {copied} rows. OK")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Required, not defaulted: this script wipes and re-seeds reference tables
    # on the destination, so it must never guess which databases it is pointed
    # at. Aliases come from settings.toml [markets.*] database_alias.
    parser.add_argument(
        "--source", required=True,
        help="Source market DB alias to copy from (LOCAL db).",
    )
    parser.add_argument(
        "--dest", required=True,
        help="Destination market DB alias to seed (local replica; pushed to "
             "Turso once after --apply).",
    )
    parser.add_argument(
        "--only", action="append", metavar="table",
        help="Restrict to specific reference tables. Repeatable. "
             f"Choices: {[m.__tablename__ for m in REFERENCE_MODELS]}",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually run the migration. Default is dry-run.",
    )
    parser.add_argument(
        "--allow-empty-source", action="store_true",
        help="Permit wiping a populated destination table even when the source "
             "table is empty. Without this flag such tables are refused.",
    )
    args = parser.parse_args()

    models = REFERENCE_MODELS
    if args.only:
        wanted = set(args.only)
        models = [m for m in REFERENCE_MODELS if m.__tablename__ in wanted]
        unknown = wanted - {m.__tablename__ for m in REFERENCE_MODELS}
        if unknown:
            print(f"Unknown table(s): {sorted(unknown)}. Available: "
                  f"{[m.__tablename__ for m in REFERENCE_MODELS]}")
            return 2

    src = DatabaseConfig(args.source)
    dest = DatabaseConfig(args.dest)

    # A missing/empty source file would otherwise surface as one confusing
    # "no such table" error per table (SQLite creates an empty db on connect).
    src_path = Path(src.path)
    if not src_path.exists() or src_path.stat().st_size == 0:
        print(f"Source database missing or empty: {src.path}\n"
              f"Sync it first: uv run mkts-backend sync --market=<market alias>")
        return 2

    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"Source (local): {src.alias} ({src.path})")
    print(f"Destination (local, pushed to Turso after apply): {dest.alias} ({dest.path})")
    print(f"Tables: {[m.__tablename__ for m in models]}")

    # Ensure destination schema once, up front (only when applying). checkfirst
    # leaves existing tables untouched and creates only what's missing.
    if args.apply:
        Base.metadata.create_all(
            dest.remote_engine,
            tables=[m.__table__ for m in models],
            checkfirst=True,
        )

    failed = []
    wrote_any = False
    for model in models:
        try:
            if seed_table(src, dest, model, apply=args.apply,
                          allow_empty_source=args.allow_empty_source):
                if args.apply:
                    wrote_any = True
            else:
                failed.append(model.__tablename__)
        except Exception as e:
            logger.exception(f"Seeding failed for {model.__tablename__}")
            print(f"EXCEPTION on {model.__tablename__}: {e}")
            failed.append(model.__tablename__)

    # Push once for the whole run, not once per table — a per-table push
    # multiplies round trips for no benefit and leaves more partial states.
    # Skip entirely on a dry run, and when every table was refused or failed
    # before writing anything. A run with failures still pushes whatever
    # committed (local truth; the queue converges) but must still fail below.
    if wrote_any:
        try:
            dest.push()
            print(f"Pushed {dest.alias} to Turso.")
        except Exception as e:
            logger.exception("Push to Turso failed")
            print(f"PUSH FAILED for {dest.alias}: {e}")
            failed.append("push")

    if failed:
        print(f"\nFAILED: {failed}")
        return 1
    print("\nAll tables OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
