"""
Backfill group_id / category_id on doctrine rows where these IDs are 0.

Some historical rows in the `doctrines` table have group_id = 0 and
category_id = 0 even though their group_name / category_name strings are
correct. This script looks up the correct IDs in sdelite.db (`sdetypes`
table) and writes them back to the Turso remote for both markets.

Run with: uv run python scripts/backfill_doctrine_group_category_ids.py

Requires TURSO_* credentials for both wcmktprod and wcmktnorth in .env.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from mkts_backend.config.db_config import DatabaseConfig

MARKETS = ("wcmktprod", "wcmktnorth")


def _load_sde_lookup(type_ids: list[int]) -> dict[int, dict]:
    """Fetch group/category metadata for the given type_ids from local SDE."""
    if not type_ids:
        return {}
    sde = DatabaseConfig("sde")
    placeholders = ", ".join(f":t{i}" for i in range(len(type_ids)))
    params = {f"t{i}": tid for i, tid in enumerate(type_ids)}
    query = text(
        f"""
        SELECT typeID, groupID, groupName, categoryID, categoryName
        FROM sdetypes
        WHERE typeID IN ({placeholders})
        """
    )
    with sde.engine.connect() as conn:
        rows = conn.execute(query, params).mappings().all()
    return {
        int(row["typeID"]): {
            "group_id": int(row["groupID"]),
            "group_name": row["groupName"],
            "category_id": int(row["categoryID"]),
            "category_name": row["categoryName"],
        }
        for row in rows
    }


def backfill_market(alias: str) -> tuple[int, list[int]]:
    """Backfill one market's doctrines table on the Turso remote.

    Returns (rows_updated, missing_type_ids).
    """
    db = DatabaseConfig(alias)
    engine = db.remote_engine

    select_bad = text(
        "SELECT DISTINCT type_id FROM doctrines "
        "WHERE group_id = 0 OR category_id = 0"
    )
    count_bad = text(
        "SELECT COUNT(*) FROM doctrines "
        "WHERE group_id = 0 OR category_id = 0"
    )
    update_stmt = text(
        """
        UPDATE doctrines
           SET group_id      = :group_id,
               group_name    = :group_name,
               category_id   = :category_id,
               category_name = :category_name
         WHERE type_id = :type_id
           AND (group_id = 0 OR category_id = 0)
        """
    )

    with engine.connect() as conn:
        before = conn.execute(count_bad).scalar_one()
        type_ids = [int(r[0]) for r in conn.execute(select_bad).all()]
        conn.commit()
        print(f"{alias}: {before} bad rows across {len(type_ids)} distinct type_ids")

        if not type_ids:
            return 0, []

        sde_lookup = _load_sde_lookup(type_ids)
        missing = [tid for tid in type_ids if tid not in sde_lookup]
        if missing:
            print(f"  WARNING: {len(missing)} type_ids not found in SDE: {missing}")

        updatable = [tid for tid in type_ids if tid in sde_lookup]
        rows_updated = 0
        for tid in updatable:
            info = sde_lookup[tid]
            result = conn.execute(update_stmt, {"type_id": tid, **info})
            rows_updated += result.rowcount or 0
        conn.commit()

        after = conn.execute(count_bad).scalar_one()
        print(
            f"  {alias}: updated {rows_updated} rows "
            f"({before} → {after} remaining bad)"
        )
        return rows_updated, missing


def main() -> int:
    total_missing: list[tuple[str, int]] = []
    for alias in MARKETS:
        print(f"\n=== {alias} ===")
        _, missing = backfill_market(alias)
        total_missing.extend((alias, tid) for tid in missing)

    if total_missing:
        print("\nCompleted with warnings — type_ids missing from SDE:")
        for alias, tid in total_missing:
            print(f"  {alias}: {tid}")
        return 1

    print("\nAll markets backfilled cleanly.")
    print("Next step: run `mkts-backend sync --both` to pull updates into local DBs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
