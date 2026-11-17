"""One-off: create the market_history (type_id, date) index on every market DB.

The index is declared on ``MarketHistory`` so new databases get it from
``create_all``; this script adds it to existing replicas. Each target is
pulled, indexed through the sync-dialect engine, then pushed so the DDL
reaches Turso through CDC.

Dry-run by default. Pass --apply to write.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.schema import CreateIndex

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.db.models import MarketHistory

# wcmkttest is the [shared.testing] replica the default market routes to in
# development; index it too so its schema does not drift.
TARGETS = ["primary", "deployment", "market3", "wcmkttest"]
INDEX = next(iter(MarketHistory.__table__.indexes))


def has_index(db: DatabaseConfig) -> bool:
    with db.engine.connect() as conn:
        rows = conn.execute(text("PRAGMA index_list(market_history)")).fetchall()
    return any(r.name == INDEX.name for r in rows)


def index_target(alias: str, *, apply: bool) -> bool:
    db = DatabaseConfig(alias)
    print(f"\n=== {alias} -> {db.alias} ({db.path}) ===")
    db.pull()
    if has_index(db):
        print("Index already present.")
        return True
    ddl = str(CreateIndex(INDEX, if_not_exists=True).compile(dialect=db.engine.dialect))
    print(ddl)
    if not apply:
        print("DRY RUN — pass --apply to execute.")
        return True
    with db.engine.begin() as conn:
        conn.execute(text(ddl))
    db.push()
    ok = has_index(db)
    print("OK" if ok else "FAIL: index missing after create")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write. Default is dry-run.")
    parser.add_argument("--only", action="append", metavar="alias",
                        help=f"Restrict to targets from {TARGETS}. Repeatable.")
    args = parser.parse_args()

    targets = [t for t in TARGETS if not args.only or t in args.only]
    failed = []
    for alias in targets:
        try:
            if not index_target(alias, apply=args.apply):
                failed.append(alias)
        except Exception as e:
            print(f"EXCEPTION on {alias}: {e}")
            failed.append(alias)
    if failed:
        print(f"\nFAILED: {failed}")
        return 1
    print("\nAll targets OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
