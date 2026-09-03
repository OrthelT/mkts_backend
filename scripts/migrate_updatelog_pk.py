"""One-off: migrate ``updatelog`` to ``table_name`` PRIMARY KEY on every synced DB.

Replaces the old shape (surrogate ``id`` autoincrement PK, with or without a
``UNIQUE(table_name)`` constraint — the DBs disagreed) with the Base-shaped
table owned by ``UpdateLogMixin``: ``table_name TEXT PRIMARY KEY``. This makes
``ON CONFLICT(table_name)`` valid on every DB and removes the surrogate id
whose churn under delete+insert poisoned the Turso CDC push queue.

Each target is migrated by:
  1. db.pull()  — bring the local replica current with the remote
  2. Read the surviving rows (table_name, MAX(timestamp)) into memory
  3. DROP TABLE updatelog
  4. CREATE TABLE updatelog (mixin schema — final name, no temp table)
  5. Re-insert the rows
  6. db.push() — replicate the change to the Turso remote via the sync connection
  7. Verify PK + row count

All writes go through the sync-dialect engine so they land locally and reach
Turso via push(); never write DDL straight at the remote or the replica's
synced baseline diverges and every machine needs a nuke + re-pull.

Do NOT use the classic create-copy-drop-rename idiom here: turso's CDC replay
executes DDL from the SQL text captured in sqlite_schema records, but rebuilds
row INSERTs from the table's *current local* schema at push time. Rows written
to a temp table that was renamed away by push time generate ``INSERT INTO
updatelog_new ()`` (no columns) and poison the push queue with a SQL_PARSE_ERROR
— and ALTER TABLE ... RENAME emits no CDC record at all, so the rename never
reaches the remote either. Drop→create-final-name→reinsert keeps every row
change pointed at a table whose live schema matches.

Dry-run by default. Pass --apply to actually write.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.schema import CreateTable

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.models import UpdateLog

logger = configure_logging(__name__)

# Aliases resolve through DatabaseConfig routing. wcmkttest is the
# [shared.testing] replica the default market routes to when
# environment="development" — migrate it too or its schema drifts.
TARGETS = ["primary", "deployment", "market3", "buildcost", "wcmkttest"]


def table_shape(db: DatabaseConfig) -> tuple[list[str], list[str]]:
    """Return (all columns, pk columns) of the local updatelog table."""
    with db.engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(updatelog)")).fetchall()
    return [r.name for r in rows], [r.name for r in rows if r.pk]


def migrate_target(alias: str, *, apply: bool) -> bool:
    db = DatabaseConfig(alias)
    print(f"\n=== {alias} -> {db.alias} ({db.path}) ===")
    if not db.turso_url:
        print(f"SKIP: no Turso remote configured for {db.alias}.")
        return False

    db.pull()

    cols, pk = table_shape(db)
    if not cols:
        print("No updatelog table — creating fresh with mixin schema.")
        if apply:
            with db.engine.begin() as conn:
                conn.execute(text(str(
                    CreateTable(UpdateLog.__table__).compile(dialect=db.engine.dialect)
                )))
            db.push()
        return True

    rows = None
    with db.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, MAX(timestamp) AS timestamp FROM updatelog "
                "GROUP BY table_name ORDER BY table_name"
            )
        ).fetchall()
    print(f"Before: cols={cols}, pk={pk}, rows to carry over={len(rows)}")
    for r in rows:
        print(f"  {r.table_name}: {r.timestamp}")

    if pk == ["table_name"] and "id" not in cols:
        print("Already mixin-shaped — nothing to do.")
        return True

    ddl = str(
        CreateTable(UpdateLog.__table__).compile(dialect=db.engine.dialect)
    ).strip().rstrip(";")
    print(f"\nDDL for new table:\n{ddl}")

    if not apply:
        print("\nDRY RUN — no changes made. Pass --apply to execute.")
        return True

    with db.engine.begin() as conn:
        conn.execute(text("DROP TABLE updatelog"))
        conn.execute(text(ddl))
        conn.execute(
            text("INSERT INTO updatelog (table_name, timestamp) VALUES (:t, :ts)"),
            [{"t": r.table_name, "ts": r.timestamp} for r in rows],
        )
        copied = conn.execute(text("SELECT COUNT(*) FROM updatelog")).scalar()
        if copied != len(rows):
            raise RuntimeError(f"Row count mismatch: expected {len(rows)}, copied {copied}")

    db.push()

    cols_after, pk_after = table_shape(db)
    print(f"After:  cols={cols_after}, pk={pk_after}")
    if pk_after != ["table_name"]:
        print(f"FAIL: expected pk ['table_name'], got {pk_after}")
        return False
    print("OK")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually run the migration. Default is dry-run.")
    parser.add_argument("--only", action="append", metavar="alias",
                        help=f"Restrict to one target of {TARGETS}. Repeatable.")
    args = parser.parse_args()

    targets = [t for t in TARGETS if not args.only or t in args.only]
    if not targets:
        print(f"No matching targets for {args.only}. Available: {TARGETS}")
        return 2

    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"Targets: {targets}")

    failed = []
    for alias in targets:
        try:
            if not migrate_target(alias, apply=args.apply):
                failed.append(alias)
        except Exception as e:
            logger.exception(f"Migration failed for {alias}")
            print(f"EXCEPTION on {alias}: {e}")
            failed.append(alias)

    if failed:
        print(f"\nFAILED: {failed}")
        return 1
    print("\nAll targets OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
