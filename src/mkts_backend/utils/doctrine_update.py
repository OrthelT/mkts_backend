"""Persistence helpers for market-side doctrine and fit tracking tables.

All write/read helpers here operate on a caller-owned SQLAlchemy ``conn``
(typically supplied by :class:`~mkts_backend.db.unit_of_work.ReplicaUnitOfWork`).
They never open engines, commit transactions, or push replicas themselves.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field

from sqlalchemy import text

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.utils.get_type_info import TypeInfo

logger = configure_logging(__name__)


@dataclass
class DoctrineFit:
    doctrine_id: int
    fit_id: int
    target: int
    doctrine_name: str = field(init=False)
    fit_name: str = field(init=False)
    ship_type_id: int = field(init=False)
    ship_name: str = field(init=False)

    def __post_init__(self) -> None:
        fittings_engine = DatabaseConfig("fittings").engine
        sde_engine = DatabaseConfig("sde").engine
        with fittings_engine.connect() as fconn, sde_engine.connect() as sconn:
            resolved = self.from_connections(
                fconn, sconn, self.doctrine_id, self.fit_id, self.target
            )
        self.doctrine_name = resolved.doctrine_name
        self.fit_name = resolved.fit_name
        self.ship_type_id = resolved.ship_type_id
        self.ship_name = resolved.ship_name

    @classmethod
    def from_resolved(
        cls,
        *,
        doctrine_id: int,
        fit_id: int,
        target: int,
        doctrine_name: str,
        fit_name: str,
        ship_type_id: int,
        ship_name: str,
    ) -> DoctrineFit:
        """Create a DoctrineFit with pre-resolved values, bypassing DB lookups."""
        obj = object.__new__(cls)
        obj.doctrine_id = doctrine_id
        obj.fit_id = fit_id
        obj.target = target
        obj.doctrine_name = doctrine_name
        obj.fit_name = fit_name
        obj.ship_type_id = ship_type_id
        obj.ship_name = ship_name
        return obj

    @classmethod
    def from_connections(
        cls,
        fittings_conn,
        sde_conn,
        doctrine_id: int,
        fit_id: int,
        target: int,
    ) -> DoctrineFit:
        """Resolve a DoctrineFit using caller-supplied fittings and SDE connections."""
        drow = fittings_conn.execute(
            text("SELECT name FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()
        if drow is None:
            raise ValueError(f"Doctrine {doctrine_id} not found in fittings_doctrine")
        doctrine_name = drow[0].strip()

        frow = fittings_conn.execute(
            text("SELECT name, ship_type_id FROM fittings_fitting WHERE id = :fit_id"),
            {"fit_id": fit_id},
        ).fetchone()
        if frow is None:
            raise ValueError(f"Fit {fit_id} not found in fittings_fitting")
        fit_name = frow[0].strip()
        ship_type_id = frow[1]

        srow = sde_conn.execute(
            text("SELECT typeName FROM sdetypes WHERE typeID = :type_id"),
            {"type_id": ship_type_id},
        ).fetchone()
        if srow is None:
            raise ValueError(f"Ship type_id {ship_type_id} not found in sdetypes")
        ship_name = srow[0].strip()

        return cls.from_resolved(
            doctrine_id=doctrine_id,
            fit_id=fit_id,
            target=target,
            doctrine_name=doctrine_name,
            fit_name=fit_name,
            ship_type_id=ship_type_id,
            ship_name=ship_name,
        )


def upsert_doctrine_fits(
    conn,
    doctrine_fit: DoctrineFit,
    *,
    market_flag: str = "primary",
) -> None:
    """Upsert a ``doctrine_fits`` row keyed by ``(doctrine_id, fit_id)``."""
    if market_flag == "all":
        market_flag = "both"  # canonical stored value (no DB migration)

    existing = conn.execute(
        text("SELECT id FROM doctrine_fits WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"),
        {"fit_id": doctrine_fit.fit_id, "doctrine_id": doctrine_fit.doctrine_id},
    ).fetchone()
    if existing:
        stmt = text(
            """
            UPDATE doctrine_fits
            SET doctrine_name = :doctrine_name,
                fit_name = :fit_name,
                ship_type_id = :ship_type_id,
                doctrine_id = :doctrine_id,
                ship_name = :ship_name,
                target = :target,
                market_flag = :market_flag
            WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id
            """
        )
    else:
        stmt = text(
            """
            INSERT INTO doctrine_fits (doctrine_name, fit_name, ship_type_id, doctrine_id, fit_id, ship_name, target, market_flag)
            VALUES (:doctrine_name, :fit_name, :ship_type_id, :doctrine_id, :fit_id, :ship_name, :target, :market_flag)
            """
        )
    conn.execute(
        stmt,
        {
            "doctrine_name": doctrine_fit.doctrine_name,
            "fit_name": doctrine_fit.fit_name,
            "ship_type_id": doctrine_fit.ship_type_id,
            "doctrine_id": doctrine_fit.doctrine_id,
            "fit_id": doctrine_fit.fit_id,
            "ship_name": doctrine_fit.ship_name,
            "target": doctrine_fit.target,
            "market_flag": market_flag,
        },
    )
    logger.info(
        f"Upserted doctrine_fits for fit_id {doctrine_fit.fit_id} with market_flag={market_flag}"
    )


def update_fit_market_flag(
    conn,
    fit_id: int,
    market_flag: str,
    *,
    doctrine_id: int | None = None,
    db_label: str = "",
) -> bool:
    """Update ``market_flag`` on ``doctrine_fits`` for a fit (or single doctrine row)."""
    if market_flag not in ("primary", "deployment", "both", "all"):
        raise ValueError(
            f"Invalid market_flag: {market_flag}. Must be 'primary', 'deployment', or 'all'"
        )
    if market_flag == "all":
        market_flag = "both"  # canonical stored value (no DB migration)

    if doctrine_id is not None:
        result = conn.execute(
            text(
                "UPDATE doctrine_fits SET market_flag = :market_flag "
                "WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"
            ),
            {"fit_id": fit_id, "market_flag": market_flag, "doctrine_id": doctrine_id},
        )
    else:
        result = conn.execute(
            text("UPDATE doctrine_fits SET market_flag = :market_flag WHERE fit_id = :fit_id"),
            {"fit_id": fit_id, "market_flag": market_flag},
        )

    rows_affected = result.rowcount
    label = db_label or "[conn]"
    if rows_affected > 0:
        logger.info(
            f"[{label}] Updated market_flag to '{market_flag}' for fit_id {fit_id} ({rows_affected} rows)"
        )
        return True
    doc_suffix = f" doctrine_id={doctrine_id}" if doctrine_id is not None else ""
    logger.warning(
        f"[{label}] No doctrine_fits row for fit_id {fit_id}{doc_suffix} — provisioning may be needed"
    )
    return False


def get_fit_target(conn, fit_id: int) -> int | None:
    """Return the existing target for ``fit_id`` from ``doctrine_fits``, or ``None``."""
    row = conn.execute(
        text("SELECT target FROM doctrine_fits WHERE fit_id = :fit_id LIMIT 1"),
        {"fit_id": fit_id},
    ).fetchone()
    return row[0] if row else None


def upsert_doctrine_map(conn, doctrine_id: int, fit_id: int) -> None:
    """Insert a ``doctrine_map`` entry if not already present."""
    exists = conn.execute(
        text("SELECT 1 FROM doctrine_map WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id"),
        {"doctrine_id": doctrine_id, "fit_id": fit_id},
    ).fetchone()
    if exists:
        logger.info(f"doctrine_map already present for doctrine_id={doctrine_id}, fit_id={fit_id}")
        return
    next_id = conn.execute(
        text("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM doctrine_map")
    ).scalar_one()
    conn.execute(
        text("INSERT INTO doctrine_map (id, doctrine_id, fitting_id) VALUES (:id, :doctrine_id, :fit_id)"),
        {"id": next_id, "doctrine_id": doctrine_id, "fit_id": fit_id},
    )
    logger.info(f"Upserted doctrine_map entry doctrine_id={doctrine_id}, fit_id={fit_id}")


def remove_doctrine_fits(conn, doctrine_id: int, fit_id: int) -> bool:
    """Remove a single ``(doctrine_id, fit_id)`` row from ``doctrine_fits``."""
    rows_affected = conn.execute(
        text("DELETE FROM doctrine_fits WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"),
        {"fit_id": fit_id, "doctrine_id": doctrine_id},
    ).rowcount
    if rows_affected > 0:
        logger.info(
            f"Removed fit_id {fit_id} from doctrine_id {doctrine_id} in doctrine_fits ({rows_affected} rows)"
        )
        return True
    logger.warning(f"No doctrine_fits row found for fit_id={fit_id}, doctrine_id={doctrine_id}")
    return False


def remove_doctrine_map(conn, doctrine_id: int, fit_id: int) -> bool:
    """Remove a single ``(doctrine_id, fit_id)`` row from ``doctrine_map``."""
    rows_affected = conn.execute(
        text("DELETE FROM doctrine_map WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id"),
        {"doctrine_id": doctrine_id, "fit_id": fit_id},
    ).rowcount
    if rows_affected > 0:
        logger.info(f"Removed doctrine_map entry for doctrine_id={doctrine_id}, fit_id={fit_id}")
        return True
    logger.warning(f"No doctrine_map row found for doctrine_id={doctrine_id}, fit_id={fit_id}")
    return False


def remove_ship_target(conn, fit_id: int) -> bool:
    """Remove the ``ship_targets`` row for ``fit_id``."""
    rows_affected = conn.execute(
        text("DELETE FROM ship_targets WHERE fit_id = :fit_id"),
        {"fit_id": fit_id},
    ).rowcount
    if rows_affected > 0:
        logger.info(f"Removed ship_targets row for fit_id {fit_id}")
        return True
    logger.warning(f"No ship_targets row found for fit_id {fit_id}")
    return False


def remove_all_doctrine_fits_for_fit(conn, fit_id: int) -> int:
    """Remove ALL ``doctrine_fits`` rows for ``fit_id`` across all doctrines."""
    rows_affected = conn.execute(
        text("DELETE FROM doctrine_fits WHERE fit_id = :fit_id"),
        {"fit_id": fit_id},
    ).rowcount
    logger.info(f"Removed {rows_affected} doctrine_fits rows for fit_id {fit_id}")
    return rows_affected


def remove_all_doctrine_map_for_fit(conn, fit_id: int) -> int:
    """Remove ALL ``doctrine_map`` rows for ``fit_id`` across all doctrines."""
    rows_affected = conn.execute(
        text("DELETE FROM doctrine_map WHERE fitting_id = :fit_id"),
        {"fit_id": fit_id},
    ).rowcount
    logger.info(f"Removed {rows_affected} doctrine_map rows for fit_id {fit_id}")
    return rows_affected


def remove_doctrines_for_fit(conn, fit_id: int) -> int:
    """Remove all ``doctrines`` rows for ``fit_id``."""
    rows_affected = conn.execute(
        text("DELETE FROM doctrines WHERE fit_id = :fit_id"),
        {"fit_id": fit_id},
    ).rowcount
    logger.info(f"Removed {rows_affected} rows from doctrines table for fit_id {fit_id}")
    return rows_affected


def upsert_ship_target(
    conn,
    fit_id: int,
    fit_name: str,
    ship_id: int,
    ship_name: str,
    ship_target: int,
) -> bool:
    """Upsert a ``ship_targets`` entry keyed by ``fit_id``."""
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # Some schemas (e.g., wcmktnorth2) lack PK/unique constraint on fit_id; use delete-then-insert.
    conn.execute(text("DELETE FROM ship_targets WHERE fit_id = :fit_id"), {"fit_id": fit_id})
    conn.execute(
        text(
            """
            INSERT INTO ship_targets (fit_id, fit_name, ship_id, ship_name, ship_target, created_at)
            VALUES (:fit_id, :fit_name, :ship_id, :ship_name, :ship_target, :created_at)
            """
        ),
        {
            "fit_id": fit_id,
            "fit_name": fit_name,
            "ship_id": ship_id,
            "ship_name": ship_name,
            "ship_target": ship_target,
            "created_at": created_at,
        },
    )
    logger.info(f"Upserted ship_targets for fit_id {fit_id}")
    return True


def lead_ship_exists(conn, doctrine_id: int) -> bool:
    """Return True if a ``lead_ships`` row already exists for ``doctrine_id``."""
    row = conn.execute(
        text("SELECT 1 FROM lead_ships WHERE doctrine_id = :doctrine_id"),
        {"doctrine_id": doctrine_id},
    ).fetchone()
    return row is not None


def upsert_lead_ship(
    conn,
    doctrine_id: int,
    doctrine_name: str,
    fit_id: int,
    ship_type_id: int,
) -> bool:
    """Insert a ``lead_ships`` row for a doctrine only if one does not already exist."""
    if lead_ship_exists(conn, doctrine_id):
        logger.info(f"lead_ships already has entry for doctrine_id {doctrine_id}, skipping")
        return False
    conn.execute(
        text(
            "INSERT INTO lead_ships (doctrine_name, doctrine_id, lead_ship, fit_id) "
            "VALUES (:doctrine_name, :doctrine_id, :lead_ship, :fit_id)"
        ),
        {
            "doctrine_name": doctrine_name,
            "doctrine_id": doctrine_id,
            "lead_ship": ship_type_id,
            "fit_id": fit_id,
        },
    )
    logger.info(
        f"Added lead_ship for doctrine_id {doctrine_id}: fit_id={fit_id}, ship={ship_type_id}"
    )
    return True


def set_lead_ship(
    conn,
    doctrine_id: int,
    doctrine_name: str,
    fit_id: int,
    ship_type_id: int,
) -> bool:
    """Set or replace the lead ship for a doctrine."""
    conn.execute(
        text("DELETE FROM lead_ships WHERE doctrine_id = :doctrine_id"),
        {"doctrine_id": doctrine_id},
    )
    conn.execute(
        text(
            "INSERT INTO lead_ships (doctrine_name, doctrine_id, lead_ship, fit_id) "
            "VALUES (:doctrine_name, :doctrine_id, :lead_ship, :fit_id)"
        ),
        {
            "doctrine_name": doctrine_name,
            "doctrine_id": doctrine_id,
            "lead_ship": ship_type_id,
            "fit_id": fit_id,
        },
    )
    logger.info(
        f"Set lead_ship for doctrine_id {doctrine_id}: fit_id={fit_id}, ship={ship_type_id}"
    )
    return True


def refresh_doctrines_for_fit(
    conn,
    fit_id: int,
    ship_id: int,
    ship_name: str,
    *,
    fittings_conn=None,
) -> None:
    """Rebuild ``doctrines`` rows for a fit based on ``fittings_fittingitem``."""
    if fittings_conn is not None:
        rows = fittings_conn.execute(
            text(
                "SELECT type_id, SUM(quantity) as qty FROM fittings_fittingitem "
                "WHERE fit_id = :fit_id GROUP BY type_id"
            ),
            {"fit_id": fit_id},
        ).fetchall()
    else:
        fittings_engine = DatabaseConfig("fittings").engine
        try:
            with fittings_engine.connect() as fconn:
                rows = fconn.execute(
                    text(
                        "SELECT type_id, SUM(quantity) as qty FROM fittings_fittingitem "
                        "WHERE fit_id = :fit_id GROUP BY type_id"
                    ),
                    {"fit_id": fit_id},
                ).fetchall()
        finally:
            fittings_engine.dispose()

    components = [(row.type_id, row.qty) for row in rows]
    if ship_id not in [c[0] for c in components]:
        components.append((ship_id, 1))

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stats_rows = conn.execute(
        text(
            "SELECT type_id, price, avg_price, avg_volume, days_remaining, total_volume_remain "
            "FROM marketstats"
        )
    ).fetchall()
    stats_map = {r.type_id: r for r in stats_rows}

    hull_stats = stats_map.get(ship_id)
    hull_stock = (
        int(hull_stats.total_volume_remain)
        if hull_stats and hull_stats.total_volume_remain is not None
        else 0
    )

    conn.execute(text("DELETE FROM doctrines WHERE fit_id = :fit_id"), {"fit_id": fit_id})
    insert_stmt = text(
        """
        INSERT INTO doctrines (
            fit_id, ship_id, ship_name, type_id, type_name, fit_qty, hulls,
            fits_on_mkt, total_stock, price, avg_vol, days,
            group_id, group_name, category_id, category_name, timestamp
        ) VALUES (
            :fit_id, :ship_id, :ship_name, :type_id, :type_name, :fit_qty, :hulls,
            :fits_on_mkt, :total_stock, :price, :avg_vol, :days,
            :group_id, :group_name, :category_id, :category_name, :timestamp
        )
        """
    )
    for type_id, qty in components:
        type_info = TypeInfo(type_id)
        stats = stats_map.get(type_id)
        total_stock = (
            int(stats.total_volume_remain)
            if stats and stats.total_volume_remain is not None
            else 0
        )
        price_val = float(stats.price) if stats and stats.price is not None else 0.0
        avg_vol = float(stats.avg_volume) if stats and stats.avg_volume is not None else 0.0
        days_rem = float(stats.days_remaining) if stats and stats.days_remaining is not None else 0.0
        fits_on_mkt = (total_stock / qty) if qty else 0

        if stats is None:
            logger.warning(f"No marketstats for type_id {type_id}; defaulting price/stock to 0")

        conn.execute(
            insert_stmt,
            {
                "fit_id": fit_id,
                "ship_id": ship_id,
                "ship_name": ship_name,
                "type_id": type_id,
                "type_name": type_info.type_name,
                "fit_qty": int(qty),
                "hulls": hull_stock,
                "fits_on_mkt": fits_on_mkt,
                "total_stock": total_stock,
                "price": price_val,
                "avg_vol": avg_vol,
                "days": days_rem,
                "group_id": int(type_info.group_id),
                "group_name": type_info.group_name,
                "category_id": int(type_info.category_id),
                "category_name": type_info.category_name,
                "timestamp": timestamp,
            },
        )
    logger.info(f"Rebuilt doctrines rows for fit_id {fit_id} ({len(components)} components)")


def ensure_friendly_name_column(conn, *, db_label: str = "") -> bool:
    """Ensure the ``friendly_name`` column exists on ``doctrine_fits``."""
    try:
        cols = conn.execute(text("PRAGMA table_info(doctrine_fits)")).fetchall()
        col_names = [c[1] for c in cols]
        if "friendly_name" not in col_names:
            conn.execute(text("ALTER TABLE doctrine_fits ADD COLUMN friendly_name TEXT DEFAULT NULL"))
            logger.info(f"Added friendly_name column to doctrine_fits ({db_label or '[conn]'})")
        return True
    except Exception as e:
        logger.error(f"Failed to ensure friendly_name column on {db_label or '[conn]'}: {e}")
        return False


def update_doctrine_friendly_name(
    conn,
    doctrine_id: int,
    friendly_name: str,
) -> bool:
    """Update ``friendly_name`` for all ``doctrine_fits`` rows matching ``doctrine_id``."""
    rows_affected = conn.execute(
        text("UPDATE doctrine_fits SET friendly_name = :name WHERE doctrine_id = :doctrine_id"),
        {"doctrine_id": doctrine_id, "name": friendly_name},
    ).rowcount
    if rows_affected > 0:
        logger.info(
            f"Updated friendly_name to '{friendly_name}' for doctrine_id {doctrine_id} ({rows_affected} rows)"
        )
        return True
    logger.warning(f"No rows found for doctrine_id {doctrine_id}")
    return False


def populate_friendly_names_from_json(conn, json_path: str) -> int:
    """Bulk-update ``friendly_name`` from a JSON file keyed by ``(fit_id, doctrine_id)``."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated = 0
    for entry in data:
        fname = entry.get("friendly_name")
        if fname is None:
            continue
        result = conn.execute(
            text(
                "UPDATE doctrine_fits SET friendly_name = :name "
                "WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"
            ),
            {
                "fit_id": entry["fit_id"],
                "doctrine_id": entry["doctrine_id"],
                "name": fname,
            },
        )
        updated += result.rowcount
    logger.info(f"Populated {updated} friendly_name values from {json_path}")
    return updated
