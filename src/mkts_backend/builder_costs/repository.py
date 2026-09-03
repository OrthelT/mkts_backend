"""Buildcost.db access for the builder-costs flow.

Owns reads and writes against ``buildcost.db`` (and its Turso remote). Writes
land on the local replica and reach Turso via ``DatabaseConfig.push()``,
called at the end of every writer in this module.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.build_cost_models import BuildWatchlist, BuilderCosts, UpdateLog

logger = configure_logging(__name__)

# 6 columns × 500 rows = 3000 params, well under modern SQLite/libsql limits
# and matches the conservative chunk size used elsewhere in this codebase.
_UPSERT_CHUNK_SIZE = 500


def init_buildcost_tables(db: DatabaseConfig) -> None:
    """Idempotently create build_watchlist, builder_costs and updatelog.

    Inspects which of the three tables are actually missing before creating
    anything, and pushes once only if at least one table was created — a
    no-op daily run (schema already present) must not push an unnecessary
    DDL-only transaction. ``checkfirst=True`` is per-table — the existing
    structures/rigs/industry_index tables are untouched. Each create is
    logged on failure so a partial init surfaces *which* table the Turso
    remote rejected, not just a raw stack.
    """
    engine = db.engine
    models = (BuildWatchlist, BuilderCosts, UpdateLog)
    with engine.connect() as conn:
        existing_tables = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }

    created_any = False
    for model in models:
        if model.__tablename__ in existing_tables:
            continue
        try:
            model.__table__.create(engine, checkfirst=True)
            created_any = True
        except SQLAlchemyError:
            logger.error(
                f"buildcost.db schema init failed for table {model.__tablename__!r}"
            )
            raise

    if created_any:
        db.push()
        logger.info(
            "Confirmed buildcost.db schema for build_watchlist, builder_costs, "
            "updatelog (pushed newly created tables)"
        )
    else:
        logger.info(
            "Confirmed buildcost.db schema for build_watchlist, builder_costs, "
            "updatelog (already present, no push needed)"
        )


def read_jita_prices(market_db: DatabaseConfig) -> dict[int, float]:
    """Return ``{type_id: sell_price}`` from the given market DB local mirror."""
    try:
        with market_db.engine.connect() as conn:
            df = pd.read_sql_query(
                text("SELECT type_id, sell_price FROM jita_prices"),
                conn,
            )
    except SQLAlchemyError as exc:
        logger.warning(f"Could not read jita_prices from {market_db.alias}: {exc}")
        return {}

    if df.empty:
        logger.info(
            f"No jita_prices rows in {market_db.alias}; high-value gating disabled"
        )
        return {}

    return {
        int(row.type_id): float(row.sell_price)
        for row in df.itertuples(index=False)
        if pd.notna(row.sell_price)
    }


def read_build_watchlist(db: DatabaseConfig) -> list[dict]:
    """Return all rows from build_watchlist as plain dicts."""
    with db.engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT type_id, type_name, group_name, category_id "
                    "FROM build_watchlist"
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def read_build_watchlist_type_ids(db: DatabaseConfig) -> set[int]:
    """Return just the type_ids in build_watchlist (for set diffs)."""
    with db.engine.connect() as conn:
        rows = conn.execute(text("SELECT type_id FROM build_watchlist")).all()
    return {int(row[0]) for row in rows}


def delete_build_watchlist_rows(db: DatabaseConfig, type_ids: list[int]) -> int:
    """Delete the given type_ids from build_watchlist on the remote.

    Returns the number of rows actually deleted (after the DB resolves
    which type_ids were present).
    """
    if not type_ids:
        return 0

    table = BuildWatchlist.__table__
    engine = db.engine
    deleted = 0
    session = Session(bind=engine)
    try:
        with session.begin():
            for start in range(0, len(type_ids), _UPSERT_CHUNK_SIZE):
                chunk = type_ids[start : start + _UPSERT_CHUNK_SIZE]
                placeholders = ", ".join(f":t_{i}" for i, _ in enumerate(chunk))
                params = {f"t_{i}": tid for i, tid in enumerate(chunk)}
                result = session.execute(
                    text(f"DELETE FROM {table.name} WHERE type_id IN ({placeholders})"),
                    params,
                )
                deleted += result.rowcount or 0
    finally:
        session.close()
    logger.debug("Pushing changes to remote db.")
    db.push()
    logger.info(f"Deleted {deleted} rows from build_watchlist")
    return deleted


def upsert_build_watchlist(db: DatabaseConfig, items: list[dict]) -> int:
    """Upsert build_watchlist rows on the buildcost remote.

    On conflict, refreshes ``type_name``, ``group_name``, ``category_id`` and
    ``last_seen_at``. ``added_at`` is preserved from the original insert so it
    tracks first-seen timestamps.
    """
    if not items:
        return 0

    table = BuildWatchlist.__table__
    engine = db.engine
    session = Session(bind=engine)
    try:
        with session.begin():
            for start in range(0, len(items), _UPSERT_CHUNK_SIZE):
                chunk = items[start : start + _UPSERT_CHUNK_SIZE]
                stmt = sqlite_insert(table).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["type_id"],
                    set_={
                        "type_name": stmt.excluded.type_name,
                        "group_name": stmt.excluded.group_name,
                        "category_id": stmt.excluded.category_id,
                        "last_seen_at": stmt.excluded.last_seen_at,
                    },
                )
                session.execute(stmt)
    finally:
        session.close()
    logger.debug(f"pushing batched changes for {len(items)} to remote")
    db.push()
    logger.info(f"Upserted {len(items)} rows to build_watchlist")
    return len(items)


def delete_orphan_builder_costs(db: DatabaseConfig) -> int:
    """Delete builder_costs rows whose type_id is no longer in build_watchlist.

    build_watchlist is the source of truth for which items the system tracks.
    Rows in builder_costs are only ever upserted, so removing an item from
    build_watchlist (via ``build-watchlist remove``) would otherwise leave its
    cost row behind and the frontend would keep displaying it. Caller must
    ensure build_watchlist is non-empty before invoking — the runner enforces
    this in ``run()`` before reaching the prune step.
    """
    engine = db.engine
    deleted = 0
    session = Session(bind=engine)
    try:
        with session.begin():
            result = session.execute(
                text(
                    "DELETE FROM builder_costs "
                    "WHERE type_id NOT IN (SELECT type_id FROM build_watchlist)"
                )
            )
            deleted = result.rowcount or 0
    finally:
        session.close()
    # Every other writer here pushes its own changes. Without this the prune
    # only reaches Turso when a later caller happens to push — the runner does,
    # but not if its next step raises.
    db.push()
    if deleted:
        logger.info(f"Pruned {deleted} orphan rows from builder_costs")
    return deleted


def upsert_builder_costs(db: DatabaseConfig, records: list[dict]) -> int:
    """Upsert builder_costs rows. On conflict, replaces every non-PK column."""
    if not records:
        return 0

    table = BuilderCosts.__table__
    engine = db.engine
    session = Session(bind=engine)
    try:
        with session.begin():
            for start in range(0, len(records), _UPSERT_CHUNK_SIZE):
                chunk = records[start : start + _UPSERT_CHUNK_SIZE]
                stmt = sqlite_insert(table).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["type_id"],
                    set_={
                        "total_cost_per_unit": stmt.excluded.total_cost_per_unit,
                        "time_per_unit": stmt.excluded.time_per_unit,
                        "me": stmt.excluded.me,
                        "runs": stmt.excluded.runs,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                session.execute(stmt)
    finally:
        session.close()
    logger.debug(f"pushing builder_costs to remote: {db.turso_url}")
    db.push()
    logger.info(f"Upserted {len(records)} rows to builder_costs")
    return len(records)


def log_buildcost_update(db: DatabaseConfig, table_name: str = "buildcost") -> datetime:
    """Stamp the remote ``updatelog`` so the wcmkts_new frontend detects the change.

    Targets buildcost.db's remote engine and the buildcost-bound ``UpdateLog``
    model. Upsert against ``UNIQUE(table_name)`` enforces "one row per
    table_name" at the schema layer; the prior delete-then-insert is replaced
    so concurrent runners can't race the row to zero or two. Returns the
    stamped timestamp so callers can surface it in their result record.
    """
    stamped_at = datetime.now(timezone.utc)
    engine = db.engine
    stmt = sqlite_insert(UpdateLog.__table__).values(
        table_name=table_name, timestamp=stamped_at
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["table_name"],
        set_={"timestamp": stmt.excluded.timestamp},
    )
    with Session(bind=engine) as session, session.begin():
        session.execute(stmt)
    logger.debug("Pushing timestamps to builder_costs updatelog.")
    db.push()
    logger.info(f"Stamped buildcost updatelog for table_name={table_name!r}")
    return stamped_at
