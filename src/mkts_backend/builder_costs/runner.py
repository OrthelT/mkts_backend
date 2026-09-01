"""Single-run orchestration for the builder-costs refresh.

Steps:
    1. Verify local replicas of buildcost / sde / primary market exist.
    2. Init buildcost.db schema (idempotent; pushes only if it created a table).
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

from sqlalchemy.exc import SQLAlchemyError

from mkts_backend.builder_costs.repository import (
    delete_orphan_builder_costs,
    init_buildcost_tables,
    log_buildcost_update,
    read_build_watchlist,
    read_jita_prices,
    upsert_builder_costs,
)
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.esi.async_everref import run_async_fetch_builder_costs

logger = configure_logging(__name__)


@dataclass
class RunResult:
    success: bool
    fetched: int = 0
    missing: int = 0
    watchlist_size: int = 0
    log_stamped: bool = False
    pruned: int = 0


def run() -> RunResult:
    """Run a single end-to-end refresh of builder_costs in buildcost.db."""
    buildcost_db = DatabaseConfig("buildcost")
    sde_db = DatabaseConfig("sde")
    primary_db = DatabaseConfig("primary")

    for db in (buildcost_db, sde_db, primary_db):
        if not db.verify_db_exists():
            logger.error(f"Database {db.alias} could not be initialized")
            return RunResult(success=False)

    init_buildcost_tables(buildcost_db)

    items = read_build_watchlist(buildcost_db)
    if not items:
        logger.error(
            "build_watchlist is empty; aborting builder cost refresh. "
            "Run 'mkts-backend build-watchlist mirror' to seed from wcmktprod."
        )
        return RunResult(success=False)

    type_ids = [int(item["type_id"]) for item in items]
    watchlist_metadata = {
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

    # Prune builder_costs rows whose type_id is no longer in build_watchlist
    # (e.g. removed via `build-watchlist remove`). Without this pass the
    # upsert-only writer leaves orphans behind forever and the frontend keeps
    # displaying them. Done before the updatelog stamp so the stamp reflects
    # a fully reconciled state.
    pruned = 0
    try:
        pruned = delete_orphan_builder_costs(buildcost_db)
    except SQLAlchemyError as exc:
        logger.error(
            f"orphan prune failed after upserting {written} rows; "
            f"stale builder_costs rows may persist. error={exc}"
        )

    # The frontend probe depends on this stamp being current after every
    # successful refresh. If it fails *after* the upsert committed, the data
    # is fresh but the frontend will see the old timestamp and skip syncing —
    # report success=False so the cron exit code surfaces the mismatch.
    log_stamped = False
    try:
        log_buildcost_update(buildcost_db)
        log_stamped = True
    except SQLAlchemyError as exc:
        logger.error(
            f"buildcost updatelog stamp failed after upserting {written} rows; "
            f"frontend will not detect the refresh. error={exc}"
        )

    missing = summary.attempted - written
    logger.info(
        f"Builder costs refresh complete: fetched={written}, "
        f"missing={missing}, attempted={summary.attempted}, "
        f"filtered_unbuildable={summary.filtered_unbuildable}, "
        f"filtered_out_of_scope={summary.filtered_out_of_scope}, "
        f"watchlist_size={len(items)}, pruned={pruned}, "
        f"log_stamped={log_stamped}"
    )
    return RunResult(
        success=log_stamped,
        fetched=written,
        missing=missing,
        watchlist_size=len(items),
        log_stamped=log_stamped,
        pruned=pruned,
    )
