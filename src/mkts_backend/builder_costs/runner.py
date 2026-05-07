"""Single-run orchestration for the builder-costs refresh.

Steps:
    1. Init buildcost.db schema on the remote (idempotent).
    2. Verify local mirrors of buildcost / sde / primary market exist.
    3. Read build_watchlist from the buildcost local mirror.
    4. Read jita_prices from the primary market local mirror.
    5. Fetch costs from EverRef for the buildable set.
    6. Upsert builder_costs to the buildcost remote.

build_watchlist is now an independent table — see
``docs/superpowers/specs/2026-05-03-independent-build-watchlist-design.md``.
The runner no longer rebuilds it from wcmktprod; mutations happen via
``add_watchlist`` (auto-mirror) and ``build-watchlist add|remove|mirror``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mkts_backend.builder_costs.repository import (
    backfill_build_watchlist_metadata,
    delete_builder_cost_rows,
    init_buildcost_tables,
    read_builder_cost_type_ids,
    read_build_watchlist,
    read_jita_prices,
    upsert_builder_costs,
)
from mkts_backend.builder_costs.sde_lookup import lookup_type_metadata
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.esi.async_everref import WatchlistMetadata, run_async_fetch_builder_costs

logger = configure_logging(__name__)


@dataclass
class RunResult:
    success: bool
    fetched: int = 0
    missing: int = 0
    watchlist_size: int = 0


def _sync_local_mirror(db: DatabaseConfig) -> bool:
    """Pull a fresh local mirror when a remote is configured."""
    turso_url = getattr(db, "turso_url", None)
    token = getattr(db, "token", None)

    if not turso_url and not token:
        return True
    if not turso_url or not token:
        logger.error(
            f"Remote sync is partially configured for {db.alias}; both Turso URL "
            "and token are required"
        )
        return False
    try:
        db.sync()
        return True
    except Exception as exc:
        logger.error(f"Failed to sync local mirror for {db.alias}: {exc}")
        return False


def _hydrate_watchlist_metadata(
    buildcost_db: DatabaseConfig,
    sde_db: DatabaseConfig,
    items: list[dict],
) -> list[dict]:
    """Repair missing build_watchlist metadata from SDE for the current run.

    The May 2026 independent build_watchlist rollout can leave existing remote
    rows without the metadata required by the EverRef eligibility filters. When
    that happens, every item is filtered out and builder_costs stop refreshing
    even though the job exits successfully. Hydrate missing metadata here and
    persist the repair to buildcost.db so subsequent runs stay healthy.
    """
    missing_type_ids = [
        int(item["type_id"])
        for item in items
        if item.get("type_name") is None
        or item.get("group_name") is None
        or item.get("category_id") is None
    ]
    if not missing_type_ids:
        return items

    looked_up = lookup_type_metadata(sorted(set(missing_type_ids)), sde_db)
    if not looked_up:
        logger.warning(
            "build_watchlist rows are missing metadata and no SDE metadata "
            "could be recovered"
        )
        return items

    now = datetime.now(timezone.utc)
    repaired_rows: list[dict] = []
    hydrated_items: list[dict] = []
    repaired_count = 0

    for item in items:
        hydrated = dict(item)
        type_id = int(hydrated["type_id"])
        metadata = looked_up.get(type_id)
        if metadata is None:
            hydrated_items.append(hydrated)
            continue

        changed = False
        for key in ("type_name", "group_name", "category_id"):
            if hydrated.get(key) is None and metadata.get(key) is not None:
                hydrated[key] = metadata[key]
                changed = True

        if changed:
            repaired_count += 1
            repaired_rows.append(
                {
                    "type_id": type_id,
                    "type_name": hydrated.get("type_name"),
                    "group_name": hydrated.get("group_name"),
                    "category_id": hydrated.get("category_id"),
                    "added_at": now,
                    "last_seen_at": now,
                }
            )
        hydrated_items.append(hydrated)

    if repaired_rows:
        backfill_build_watchlist_metadata(buildcost_db, repaired_rows)
        logger.info(
            f"Recovered missing metadata for {repaired_count} build_watchlist rows"
        )

    unresolved = sorted(set(missing_type_ids) - set(looked_up))
    if unresolved:
        logger.warning(
            f"Could not recover build_watchlist metadata for {len(unresolved)} rows: "
            f"{unresolved[:10]}"
        )

    return hydrated_items


def run() -> RunResult:
    """Run a single end-to-end refresh of builder_costs in buildcost.db."""
    buildcost_db = DatabaseConfig("buildcost")
    sde_db = DatabaseConfig("sde")
    primary_db = DatabaseConfig("primary")

    init_buildcost_tables(buildcost_db)

    for db in (buildcost_db, sde_db, primary_db):
        if not db.verify_db_exists():
            logger.error(f"Database {db.alias} could not be initialized")
            return RunResult(success=False)

    for db in (buildcost_db, primary_db, sde_db):
        if not _sync_local_mirror(db):
            return RunResult(success=False)

    items = read_build_watchlist(buildcost_db)
    if not items:
        logger.error(
            "build_watchlist is empty; aborting builder cost refresh. "
            "Run 'mkts-backend build-watchlist mirror' to seed from wcmktprod."
        )
        return RunResult(success=False)

    items = _hydrate_watchlist_metadata(buildcost_db, sde_db, items)

    type_ids = [int(item["type_id"]) for item in items]
    watchlist_metadata: dict[int, WatchlistMetadata] = {
        int(item["type_id"]): {
            "type_id": int(item["type_id"]),
            "type_name": item.get("type_name"),
            "group_name": item.get("group_name"),
            "category_id": int(item["category_id"])
            if item.get("category_id") is not None
            else None,
        }
        for item in items
    }

    jita_prices = read_jita_prices(primary_db)

    stale_type_ids = sorted(read_builder_cost_type_ids(buildcost_db) - set(type_ids))
    if stale_type_ids:
        delete_builder_cost_rows(buildcost_db, stale_type_ids)

    summary = run_async_fetch_builder_costs(
        type_ids,
        jita_prices,
        sde_db.engine,
        watchlist_metadata=watchlist_metadata,
    )

    if summary.attempted == 0:
        # Nothing eligible to fetch — every item filtered out by SDE buildable
        # join or the meta-group/category scope filters. Treat as success;
        # the watchlist is just full of non-fetchable items.
        logger.info(
            f"No items eligible for cost fetch "
            f"(unbuildable={summary.filtered_unbuildable}, "
            f"out_of_scope={summary.filtered_out_of_scope}, "
            f"watchlist_size={len(items)})"
        )
        return RunResult(success=True, watchlist_size=len(items))

    if not summary.records:
        logger.error(
            f"EverRef returned no successful results "
            f"({summary.failed}/{summary.attempted} attempted items failed)"
        )
        return RunResult(success=False, watchlist_size=len(items))

    written = upsert_builder_costs(buildcost_db, summary.records)
    missing = summary.attempted - written
    logger.info(
        f"Builder costs refresh complete: fetched={written}, "
        f"missing={missing}, attempted={summary.attempted}, "
        f"filtered_unbuildable={summary.filtered_unbuildable}, "
        f"filtered_out_of_scope={summary.filtered_out_of_scope}, "
        f"watchlist_size={len(items)}"
    )
    return RunResult(
        success=True,
        fetched=written,
        missing=missing,
        watchlist_size=len(items),
    )
