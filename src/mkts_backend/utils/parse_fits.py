"""Fittings database operations and end-to-end fit update workflow."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from sqlalchemy import bindparam, text

from mkts_backend.config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.unit_of_work import ReplicaUnitOfWork
from mkts_backend.utils.db_utils import add_missing_items_to_watchlist
from mkts_backend.utils.doctrine_update import (
    DoctrineFit,
    refresh_doctrines_for_fit,
    upsert_doctrine_fits,
    upsert_doctrine_map,
    upsert_ship_target,
)
from mkts_backend.utils.eft_parser import lookup_type_id

logger = configure_logging(__name__)


@contextmanager
def _fittings_conn(conn=None, *, write: bool = False):
    """Yield caller's ``conn`` or open a short-lived connection on ``fittings``."""
    if conn is not None:
        yield conn
        return
    engine = DatabaseConfig("fittings").engine
    try:
        cm = engine.begin() if write else engine.connect()
        with cm as own_conn:
            yield own_conn
    finally:
        engine.dispose()


@dataclass
class FitMetadata:
    description: str
    name: str
    fit_id: int
    doctrine_id: Any  # Accept int or list from metadata for backward compatibility
    target: int
    ship_type_id: Optional[int] = None
    ship_name: Optional[str] = None
    last_updated: str = field(init=False)
    doctrine_ids: List[int] = field(init=False)

    def __post_init__(self) -> None:
        self.last_updated = (
            datetime.now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        )
        self.doctrine_ids = self._normalize_doctrine_ids(self.doctrine_id)
        self.doctrine_id = self.doctrine_ids[0]

    @staticmethod
    def _normalize_doctrine_ids(raw_value: Any) -> List[int]:
        if isinstance(raw_value, (list, tuple, set)):
            ids = [int(v) for v in raw_value if v is not None]
        elif raw_value is None:
            raise ValueError("doctrine_id is required in metadata")
        else:
            ids = [int(raw_value)]
        if not ids:
            raise ValueError("doctrine_id list is empty after normalization")
        return ids


@dataclass
class FitParseResult:
    items: List[Dict]
    ship_name: str
    fit_name: str
    missing_types: List[str]


def slot_yielder() -> Generator[str, None, None]:
    corrected_order = ["LoSlot", "MedSlot", "HiSlot", "RigSlot", "DroneBay"]
    for slot in corrected_order:
        yield slot
    while True:
        yield "Cargo"


def _lookup_type_id(type_name: str, conn) -> Optional[int]:
    return lookup_type_id(type_name, conn)


def _resolve_ship_type_id(ship_name: str, conn) -> Optional[int]:
    return lookup_type_id(ship_name, conn)


def parse_eft_fit_file(fit_file: str, fit_id: int, sde_engine) -> FitParseResult:
    """Parse an EFT-formatted fit file into structured ``fittings_fittingitem`` dicts."""
    items: List[Dict] = []
    missing: List[str] = []
    slot_gen = slot_yielder()
    current_slot = None
    ship_name = ""
    fit_name = ""
    slot_counters: dict[str, int] = defaultdict(int)

    with sde_engine.connect() as sde_conn:
        with open(fit_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if line.startswith("[") and line.endswith("]"):
                    clean_name = line.strip("[]")
                    parts = clean_name.split(",")
                    ship_name = parts[0].strip()
                    fit_name = parts[1].strip() if len(parts) > 1 else "Unnamed Fit"
                    continue

                if line == "":
                    current_slot = next(slot_gen)
                    continue

                if current_slot is None:
                    current_slot = next(slot_gen)

                qty_match = re.search(r"\s+x(\d+)$", line)
                if qty_match:
                    qty = int(qty_match.group(1))
                    item_name = line[: qty_match.start()].strip()
                else:
                    qty = 1
                    item_name = line.strip()

                if current_slot in {"LoSlot", "MedSlot", "HiSlot", "RigSlot"}:
                    suffix = slot_counters[current_slot]
                    slot_counters[current_slot] += 1
                    slot_name = f"{current_slot}{suffix}"
                else:
                    slot_name = current_slot

                type_id = _lookup_type_id(item_name, sde_conn)
                if type_id is None:
                    missing.append(item_name)
                    logger.warning(f"Unable to resolve type_id for '{item_name}' (fit {fit_id})")
                    continue

                items.append(
                    {
                        "flag": slot_name,
                        "quantity": qty,
                        "type_id": type_id,
                        "fit_id": fit_id,
                        "type_fk_id": type_id,
                    }
                )

    return FitParseResult(
        items=items, ship_name=ship_name, fit_name=fit_name, missing_types=missing
    )


def create_doctrine(
    doctrine_id: int,
    name: str,
    description: str = "",
    icon_url: str = "",
    *,
    conn=None,
) -> bool:
    """Create a new doctrine in ``fittings_doctrine``. Returns False if it already exists."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _fittings_conn(conn, write=True) as c:
        existing = c.execute(
            text("SELECT 1 FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()
        if existing:
            logger.info(f"Doctrine {doctrine_id} already exists")
            return False

        c.execute(
            text(
                """
                INSERT INTO fittings_doctrine (id, name, icon_url, description, created, last_updated)
                VALUES (:id, :name, :icon_url, :description, :created, :last_updated)
                """
            ),
            {
                "id": doctrine_id,
                "name": name,
                "icon_url": icon_url,
                "description": description,
                "created": now,
                "last_updated": now,
            },
        )
    logger.info(f"Created doctrine {doctrine_id}: {name}")
    return True


def doctrine_exists(doctrine_id: int, *, conn=None) -> bool:
    """Check if a doctrine exists in ``fittings_doctrine``."""
    with _fittings_conn(conn, write=False) as c:
        result = c.execute(
            text("SELECT 1 FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()
    return result is not None


def get_next_doctrine_id(*, conn=None) -> int:
    """Get the next available doctrine ID."""
    with _fittings_conn(conn, write=False) as c:
        return c.execute(
            text("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM fittings_doctrine")
        ).scalar_one()


def add_doctrine_to_watch(doctrine_id: int, *, conn=None) -> None:
    """Copy a doctrine from ``fittings_doctrine`` to ``watch_doctrines`` if absent."""
    with _fittings_conn(conn, write=True) as c:
        doctrine_row = c.execute(
            text("SELECT * FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()
        if not doctrine_row:
            logger.error(f"Doctrine {doctrine_id} not found in fittings_doctrine")
            return

        count = c.execute(
            text("SELECT COUNT(*) FROM watch_doctrines WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()[0]
        if count > 0:
            logger.info(f"Doctrine {doctrine_id} already exists in watch_doctrines")
            return

        c.execute(
            text(
                """
                INSERT INTO watch_doctrines (id, name, icon_url, description, created, last_updated)
                VALUES (:id, :name, :icon_url, :description, :created, :last_updated)
                """
            ),
            {
                "id": doctrine_row[0],
                "name": doctrine_row[1],
                "icon_url": doctrine_row[2],
                "description": doctrine_row[3],
                "created": doctrine_row[4],
                "last_updated": doctrine_row[5],
            },
        )
        logger.info(f"Added doctrine {doctrine_id} ('{doctrine_row[1]}') to watch_doctrines")


def insert_fit_items_to_db(
    fit_items: list,
    fit_id: int,
    clear_existing: bool = True,
    *,
    conn=None,
) -> None:
    """Upsert parsed fit items into ``fittings_fittingitem`` in place.

    Rows are matched on the natural key ``(fit_id, flag, type_id)`` treated as a
    multiset so unchanged rows keep their AUTOINCREMENT id (avoiding delete +
    reinsert churn under Turso's remote FK enforcement).
    """
    normalized = []
    for item in fit_items:
        if isinstance(item, dict):
            flag = item.get("flag")
            quantity = item.get("quantity")
            type_id = item.get("type_id")
            type_fk_id = item.get("type_fk_id")
            item_fit_id = fit_id
        else:
            flag, quantity, type_id, item_fit_id, type_fk_id = item

        if type_id is None:
            logger.warning(f"Skipping item with missing type_id: {item}")
            continue
        normalized.append((flag, quantity, type_id, item_fit_id, type_fk_id))

    update_stmt = text(
        "UPDATE fittings_fittingitem SET quantity = :quantity, type_fk_id = :type_fk_id "
        "WHERE id = :id"
    )
    insert_stmt = text(
        "INSERT INTO fittings_fittingitem (flag, quantity, type_id, fit_id, type_fk_id) "
        "VALUES (:flag, :quantity, :type_id, :fit_id, :type_fk_id)"
    )

    with _fittings_conn(conn, write=True) as c:
        existing_rows = c.execute(
            text(
                "SELECT id, flag, type_id FROM fittings_fittingitem "
                "WHERE fit_id = :fit_id ORDER BY id"
            ),
            {"fit_id": fit_id},
        ).fetchall()

        existing_by_key: dict[tuple, list[int]] = {}
        for row in existing_rows:
            existing_by_key.setdefault((row.flag, row.type_id), []).append(row.id)

        inserted = 0
        updated = 0
        for flag, quantity, type_id, item_fit_id, type_fk_id in normalized:
            bucket = existing_by_key.get((flag, type_id))
            if bucket:
                existing_id = bucket.pop(0)
                c.execute(
                    update_stmt,
                    {"quantity": quantity, "type_fk_id": type_fk_id, "id": existing_id},
                )
                updated += 1
            else:
                c.execute(
                    insert_stmt,
                    {
                        "flag": flag,
                        "quantity": quantity,
                        "type_id": type_id,
                        "fit_id": item_fit_id,
                        "type_fk_id": type_fk_id,
                    },
                )
                inserted += 1

        removed = 0
        if clear_existing:
            stale_ids = [rid for bucket in existing_by_key.values() for rid in bucket]
            if stale_ids:
                c.execute(
                    text("DELETE FROM fittings_fittingitem WHERE id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": stale_ids},
                )
                removed = len(stale_ids)

        logger.info(
            f"Upserted items for fit_id {fit_id}: {inserted} inserted, "
            f"{updated} updated, {removed} removed"
        )


def ensure_fittings_types(type_ids, *, conn=None, sde_conn=None) -> List[int]:
    """Copy any of ``type_ids`` missing from ``fittings_type`` in from the SDE.

    Parents (category, group, type) are inserted before child fit rows so Turso
    cloud's foreign-key enforcement accepts the pushed CDC batch.
    """
    wanted = sorted({int(t) for t in type_ids})
    if not wanted:
        return []

    ids_param = bindparam("ids", expanding=True)

    def _query_sde(missing_ids: list[int]):
        sde_stmt = text(
            "SELECT typeID, typeName, groupID, groupName, categoryID, categoryName, "
            "volume, published FROM sdetypes WHERE typeID IN :ids"
        ).bindparams(ids_param)
        if sde_conn is not None:
            return sde_conn.execute(sde_stmt, {"ids": missing_ids}).fetchall()
        sde_engine = DatabaseConfig("sde").engine
        try:
            with sde_engine.connect() as sc:
                return sc.execute(sde_stmt, {"ids": missing_ids}).fetchall()
        finally:
            sde_engine.dispose()

    def _do(c) -> List[int]:
        present = {
            row[0]
            for row in c.execute(
                text("SELECT type_id FROM fittings_type WHERE type_id IN :ids").bindparams(ids_param),
                {"ids": wanted},
            )
        }
        missing = [t for t in wanted if t not in present]
        if not missing:
            return []

        sde_rows = _query_sde(missing)
        unknown = set(missing) - {row.typeID for row in sde_rows}
        if unknown:
            raise ValueError(f"type ids not in SDE sdetypes: {sorted(unknown)}")

        for row in sde_rows:
            c.execute(
                text(
                    "INSERT OR IGNORE INTO fittings_itemcategory (category_id, name, published) "
                    "VALUES (:category_id, :name, 1)"
                ),
                {"category_id": row.categoryID, "name": row.categoryName},
            )
            c.execute(
                text(
                    "INSERT OR IGNORE INTO fittings_itemgroup (group_id, name, published, category_id) "
                    "VALUES (:group_id, :name, 1, :category_id)"
                ),
                {"group_id": row.groupID, "name": row.groupName, "category_id": row.categoryID},
            )
            c.execute(
                text(
                    "INSERT INTO fittings_type (type_name, type_id, published, volume, group_id) "
                    "VALUES (:type_name, :type_id, :published, :volume, :group_id)"
                ),
                {
                    "type_name": row.typeName,
                    "type_id": row.typeID,
                    "published": 1 if row.published is None else int(row.published),
                    "volume": row.volume,
                    "group_id": row.groupID,
                },
            )
        logger.info(f"Added {len(missing)} types to fittings_type from SDE: {missing}")
        return missing

    with _fittings_conn(conn, write=True) as c:
        return _do(c)


def parse_fit_metadata(fit_metadata_file: str) -> FitMetadata:
    with open(fit_metadata_file, "r", encoding="utf-8") as f:
        metadata = json.load(f)
    return FitMetadata(**metadata)


def upsert_fittings_fitting(
    metadata: FitMetadata,
    ship_type_id: int,
    *,
    conn=None,
) -> None:
    """Upsert the shell record in ``fittings_fitting``."""
    now = datetime.now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    created = now
    last_updated = metadata.last_updated or now
    stmt = text(
        """
        INSERT INTO fittings_fitting (id, description, name, ship_type_type_id, ship_type_id, created, last_updated)
        VALUES (:id, :description, :name, :ship_type_type_id, :ship_type_id, :created, :last_updated)
        ON CONFLICT(id) DO UPDATE SET
            description = excluded.description,
            name = excluded.name,
            ship_type_type_id = excluded.ship_type_type_id,
            ship_type_id = excluded.ship_type_id,
            last_updated = excluded.last_updated
        """
    )
    with _fittings_conn(conn, write=True) as c:
        c.execute(
            stmt,
            {
                "id": metadata.fit_id,
                "description": metadata.description,
                "name": metadata.name,
                "ship_type_type_id": ship_type_id,
                "ship_type_id": ship_type_id,
                "created": created,
                "last_updated": last_updated,
            },
        )
    logger.info(
        f"Upserted fittings_fitting for fit_id {metadata.fit_id}: "
        f"{metadata.name} (ship_type_id={ship_type_id})"
    )


def ensure_doctrine_link(doctrine_id: int, fit_id: int, *, conn=None) -> None:
    """Insert a ``fittings_doctrine_fittings`` link if the doctrine exists and isn't linked yet."""
    with _fittings_conn(conn, write=True) as c:
        exists_doc = c.execute(
            text("SELECT 1 FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()
        if not exists_doc:
            logger.warning(
                f"Doctrine {doctrine_id} not found in fittings_doctrine. "
                "Skipping doctrine link but continuing with market DB updates."
            )
            return

        exists_link = c.execute(
            text(
                "SELECT 1 FROM fittings_doctrine_fittings "
                "WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id"
            ),
            {"doctrine_id": doctrine_id, "fit_id": fit_id},
        ).fetchone()
        if exists_link:
            return

        next_id = c.execute(
            text("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM fittings_doctrine_fittings")
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO fittings_doctrine_fittings (id, doctrine_id, fitting_id) "
                "VALUES (:id, :doctrine_id, :fit_id)"
            ),
            {"id": next_id, "doctrine_id": doctrine_id, "fit_id": fit_id},
        )
    logger.info(f"Linked doctrine_id {doctrine_id} to fit_id {fit_id} in fittings_doctrine_fittings")


def remove_doctrine_link(doctrine_id: int, fit_id: int, *, conn=None) -> bool:
    """Remove the link between a doctrine and a fit in ``fittings_doctrine_fittings``."""
    with _fittings_conn(conn, write=True) as c:
        rows_affected = c.execute(
            text(
                "DELETE FROM fittings_doctrine_fittings "
                "WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id"
            ),
            {"doctrine_id": doctrine_id, "fit_id": fit_id},
        ).rowcount

    if rows_affected > 0:
        logger.info(
            f"Removed link between doctrine_id {doctrine_id} and fit_id {fit_id} "
            "from fittings_doctrine_fittings"
        )
        return True
    logger.warning(
        f"No fittings_doctrine_fittings row found for doctrine_id={doctrine_id}, fit_id={fit_id}"
    )
    return False


def remove_all_doctrine_links_for_fit(fit_id: int, *, conn=None) -> int:
    """Remove ALL ``fittings_doctrine_fittings`` rows for ``fit_id``."""
    with _fittings_conn(conn, write=True) as c:
        rows_affected = c.execute(
            text("DELETE FROM fittings_doctrine_fittings WHERE fitting_id = :fit_id"),
            {"fit_id": fit_id},
        ).rowcount
    logger.info(f"Removed {rows_affected} fittings_doctrine_fittings rows for fit_id {fit_id}")
    return rows_affected


def get_doctrine_ids_for_fit(fit_id: int, *, conn=None) -> List[int]:
    """Return all doctrine IDs linked to ``fit_id`` in ``fittings_doctrine_fittings``."""
    with _fittings_conn(conn, write=False) as c:
        result = c.execute(
            text("SELECT doctrine_id FROM fittings_doctrine_fittings WHERE fitting_id = :fit_id"),
            {"fit_id": fit_id},
        ).fetchall()
    return [row[0] for row in result]


def update_fit_workflow(
    fit_id: int,
    fit_file: str,
    fit_metadata_file: Optional[str] = None,
    clear_existing: bool = True,
    dry_run: bool = False,
    target_alias: str = "wcmkt",
    target_aliases: Optional[List[str]] = None,
    metadata_override: Optional[Dict] = None,
    uow: Optional[ReplicaUnitOfWork] = None,
):
    """End-to-end update for a fit across shared ``fittings`` and target market(s).

    - Parses the EFT file once against SDE.
    - On ``dry_run=True``, returns the parsed summary dict without writing or pushing.
    - Writes shared ``fittings`` once in a single transaction (parents before
      children so Turso cloud FK enforcement succeeds).
    - Propagates to each market alias in ``target_aliases`` (or ``[target_alias]``)
      inside one transaction per market.
    - When ``uow`` is provided, participates in the caller's unit of work (the
      caller pushes once on exit). When omitted, opens its own
      :class:`ReplicaUnitOfWork` so standalone callers always push.
    """
    if metadata_override:
        metadata = FitMetadata(**metadata_override)
    elif fit_metadata_file:
        metadata = parse_fit_metadata(fit_metadata_file)
    else:
        raise ValueError("Either fit_metadata_file or metadata_override must be provided")

    aliases = list(target_aliases) if target_aliases is not None else [target_alias]

    sde_engine = DatabaseConfig("sde").engine
    try:
        parse_result = parse_eft_fit_file(fit_file, fit_id, sde_engine)
        metadata.ship_name = parse_result.ship_name

        with sde_engine.connect() as sde_conn:
            ship_type_id = metadata.ship_type_id or _resolve_ship_type_id(
                parse_result.ship_name, sde_conn
            )

        if ship_type_id is None:
            raise ValueError(f"Could not resolve ship type id for ship '{parse_result.ship_name}'")

        if dry_run:
            return {
                "fit_id": fit_id,
                "ship_name": parse_result.ship_name,
                "ship_type_id": ship_type_id,
                "items": parse_result.items,
                "missing_items": parse_result.missing_types,
            }
    finally:
        sde_engine.dispose()

    type_ids = sorted({ship_type_id, *(item["type_id"] for item in parse_result.items)})

    def _run_with_uow(active_uow: ReplicaUnitOfWork) -> None:
        doctrine_fits: list[DoctrineFit] = []
        with active_uow.read("sde") as sde_conn, active_uow.tx("fittings") as fconn:
            # Parents first: Turso cloud enforces fittings_type foreign keys on push.
            ensure_fittings_types(type_ids, conn=fconn, sde_conn=sde_conn)
            upsert_fittings_fitting(metadata, ship_type_id, conn=fconn)
            insert_fit_items_to_db(
                parse_result.items,
                fit_id=fit_id,
                clear_existing=clear_existing,
                conn=fconn,
            )
            for doctrine_id in metadata.doctrine_ids:
                if not doctrine_exists(doctrine_id, conn=fconn):
                    logger.info(
                        f"Doctrine {doctrine_id} doesn't exist, creating it with name '{metadata.name}'"
                    )
                    create_doctrine(
                        doctrine_id=doctrine_id,
                        name=metadata.name,
                        description=metadata.description,
                        conn=fconn,
                    )
                    add_doctrine_to_watch(doctrine_id, conn=fconn)

                ensure_doctrine_link(doctrine_id, fit_id, conn=fconn)
                doctrine_fits.append(
                    DoctrineFit.from_connections(
                        fconn, sde_conn, doctrine_id, fit_id, metadata.target
                    )
                )

        with active_uow.read("fittings") as fconn:
            for alias in aliases:
                with active_uow.tx(alias) as mconn:
                    for doctrine_fit in doctrine_fits:
                        upsert_doctrine_fits(mconn, doctrine_fit)
                        upsert_doctrine_map(mconn, doctrine_fit.doctrine_id, doctrine_fit.fit_id)

                    upsert_ship_target(
                        mconn,
                        fit_id=fit_id,
                        fit_name=parse_result.fit_name,
                        ship_id=ship_type_id,
                        ship_name=parse_result.ship_name,
                        ship_target=metadata.target,
                    )
                    refresh_doctrines_for_fit(
                        mconn,
                        fit_id=fit_id,
                        ship_id=ship_type_id,
                        ship_name=parse_result.ship_name,
                        fittings_conn=fconn,
                    )
                    add_missing_items_to_watchlist(type_ids, db_alias=alias, conn=mconn)

    if uow is not None:
        _run_with_uow(uow)
    else:
        with ReplicaUnitOfWork() as own_uow:
            _run_with_uow(own_uow)

    logger.info(
        f"Completed fit update for fit_id={fit_id}, doctrine_ids={metadata.doctrine_ids}"
    )
