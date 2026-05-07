"""Buildcost.db access for the builder-costs flow.

Owns reads and writes against ``buildcost.db`` (and its Turso remote). Writes
target the remote engine; the local mirror is refreshed via
``DatabaseConfig.sync()`` separately.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.build_cost_models import BuildWatchlist, BuilderCosts

logger = configure_logging(__name__)

# 6 columns × 500 rows = 3000 params, well under modern SQLite/libsql limits
# and matches the conservative chunk size used elsewhere in this codebase.
_UPSERT_CHUNK_SIZE = 500


def init_buildcost_tables(db: DatabaseConfig) -> None:
    """Idempotently create build_watchlist and builder_costs on the remote.

    ``checkfirst=True`` is per-table — the existing structures/rigs/industry_index
    tables are untouched.
    """
    engine = db.remote_engine
    BuildWatchlist.__table__.create(engine, checkfirst=True)
    BuilderCosts.__table__.create(engine, checkfirst=True)
    logger.info("Confirmed buildcost.db schema for build_watchlist and builder_costs")


def read_jita_prices(market_db: DatabaseConfig) -> dict[int, float]:
    """Return ``{type_id: sell_price}`` from the given market DB local mirror.

    Empty dict on failure or empty table — Jita prices only gate ME/runs for
    high-value T2 modules; absence degrades gracefully.
    """
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
        logger.info(f"No jita_prices rows in {market_db.alias}; high-value gating disabled")
        return {}

    return {
        int(row.type_id): float(row.sell_price)
        for row in df.itertuples(index=False)
        if pd.notna(row.sell_price)
    }


def read_build_watchlist(db: DatabaseConfig) -> list[dict]:
    """Return all rows from build_watchlist as plain dicts.

    Reads the local mirror; sync the remote first if up-to-date data matters.
    """
    with db.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT type_id, type_name, group_name, category_id "
                "FROM build_watchlist"
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def read_build_watchlist_type_ids(db: DatabaseConfig) -> set[int]:
    """Return just the type_ids in build_watchlist (for set diffs)."""
    with db.engine.connect() as conn:
        rows = conn.execute(text("SELECT type_id FROM build_watchlist")).all()
    return {int(row[0]) for row in rows}


def read_builder_cost_type_ids(db: DatabaseConfig) -> set[int]:
    """Return builder_costs type_ids from the remote table."""
    with db.remote_engine.connect() as conn:
        rows = conn.execute(text("SELECT type_id FROM builder_costs")).all()
    return {int(row[0]) for row in rows}


def delete_build_watchlist_rows(db: DatabaseConfig, type_ids: list[int]) -> int:
    """Delete the given type_ids from build_watchlist on the remote.

    Returns the number of rows actually deleted (after the DB resolves
    which type_ids were present).
    """
    if not type_ids:
        return 0

    table = BuildWatchlist.__table__
    engine = db.remote_engine
    deleted = 0
    session = Session(bind=engine)
    try:
        with session.begin():
            for start in range(0, len(type_ids), _UPSERT_CHUNK_SIZE):
                chunk = type_ids[start : start + _UPSERT_CHUNK_SIZE]
                placeholders = ", ".join(f":t_{i}" for i, _ in enumerate(chunk))
                params = {f"t_{i}": tid for i, tid in enumerate(chunk)}
                result = session.execute(
                    text(
                        f"DELETE FROM {table.name} WHERE type_id IN ({placeholders})"
                    ),
                    params,
                )
                deleted += result.rowcount or 0
    finally:
        session.close()
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
    engine = db.remote_engine
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
    logger.info(f"Upserted {len(items)} rows to build_watchlist")
    return len(items)


def backfill_build_watchlist_metadata(db: DatabaseConfig, items: list[dict]) -> int:
    """Fill missing build_watchlist metadata without changing last_seen_at.

    Existing rows only get ``type_name``, ``group_name`` and ``category_id``
    refreshed. ``added_at`` / ``last_seen_at`` are only used if a row somehow
    needs to be inserted.
    """
    if not items:
        return 0

    table = BuildWatchlist.__table__
    engine = db.remote_engine
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
                    },
                )
                session.execute(stmt)
    finally:
        session.close()
    logger.info(f"Backfilled metadata for {len(items)} build_watchlist rows")
    return len(items)


def upsert_builder_costs(
    db: DatabaseConfig,
    records: Sequence[Mapping[str, object]],
) -> int:
    """Upsert builder_costs rows. On conflict, replaces every non-PK column."""
    if not records:
        return 0

    table = BuilderCosts.__table__
    engine = db.remote_engine
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
    logger.info(f"Upserted {len(records)} rows to builder_costs")
    return len(records)


def delete_builder_cost_rows(db: DatabaseConfig, type_ids: list[int]) -> int:
    """Delete builder_costs rows for the given type_ids on the remote."""
    if not type_ids:
        return 0

    table = BuilderCosts.__table__
    engine = db.remote_engine
    deleted = 0
    session = Session(bind=engine)
    try:
        with session.begin():
            for start in range(0, len(type_ids), _UPSERT_CHUNK_SIZE):
                chunk = type_ids[start : start + _UPSERT_CHUNK_SIZE]
                placeholders = ", ".join(f":t_{i}" for i, _ in enumerate(chunk))
                params = {f"t_{i}": tid for i, tid in enumerate(chunk)}
                result = session.execute(
                    text(
                        f"DELETE FROM {table.name} WHERE type_id IN ({placeholders})"
                    ),
                    params,
                )
                deleted += result.rowcount or 0
    finally:
        session.close()
    logger.info(f"Deleted {deleted} rows from builder_costs")
    return deleted
