import re
import json
from dataclasses import dataclass, field
from typing import Any, Generator, Optional, Tuple, List, Dict
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import create_engine, text, bindparam

import pandas as pd

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config import DatabaseConfig
from mkts_backend.utils.doctrine_update import (
    DoctrineFit,
    upsert_doctrine_fits,
    upsert_doctrine_map,
    upsert_ship_target,
    refresh_doctrines_for_fit,
)
from mkts_backend.utils.db_utils import add_missing_items_to_watchlist

logger = configure_logging(__name__)

# Database configs (keep objects; avoid overwriting with URLs)
_wcmkt_db = DatabaseConfig("wcmkt")
_sde_db = DatabaseConfig("sde")
_fittings_db = DatabaseConfig("fittings")

def _get_engine(db_alias: str, remote: bool = False):
    cfg = DatabaseConfig(db_alias)
    return cfg.remote_engine if remote else cfg.engine


@dataclass
class FittingItem:
    flag: str
    quantity: int
    fit_id: int
    type_name: str
    ship_type_name: str
    fit_name: Optional[str] = None

    type_id: int = field(init=False)
    type_fk_id: int = field(init=False)

    def __post_init__(self) -> None:
        self.type_id = self.get_type_id()
        self.type_fk_id = self.type_id
        self.details = self.get_fitting_details()
        if "description" in self.details:
            self.description = self.details['description']
        else:
            self.description = "No description"

        if self.fit_name is None:
            if "name" in self.details:
                self.fit_name = self.details["name"]
                if "name" in self.details and self.fit_name != self.details["name"]:
                    logger.warning(
                        f"Fit name mismatch: parsed='{self.fit_name}' vs DB='{self.details['name']}'"
                    )
            else:
                self.fit_name = f"Default {self.ship_type_name} fit"

    def get_type_id(self) -> int:
        engine = _sde_db.engine
        query = text("SELECT typeID FROM inv_info WHERE typeName = :type_name")
        with engine.connect() as conn:
            result = conn.execute(query, {"type_name": self.type_name}).fetchone()
            return result[0] if result else -1

    def get_fitting_details(self) -> dict:
        engine = _fittings_db.engine
        query = text("SELECT * FROM fittings_fitting WHERE id = :fit_id")
        with engine.connect() as conn:
            row = conn.execute(query, {"fit_id": self.fit_id}).fetchone()
            return dict(row._mapping) if row else {}

@dataclass
class FitMetadata:
    description: str
    name: str
    fit_id: int
    doctrine_id: Any  # Accept int or list from metadata for backward compatibility
    target: int
    ship_type_id: Optional[int] = None
    ship_name: Optional[str] = None
    last_updated: datetime = field(init=False)
    doctrine_ids: List[int] = field(init=False)

    def __post_init__(self):
        self.last_updated = datetime.now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.doctrine_ids = self._normalize_doctrine_ids(self.doctrine_id)
        # Maintain legacy single-doctrine access
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


def convert_fit_date(date: str) -> datetime:
    dt = datetime.strptime("15 Jan 2025 19:12:04", "%d %b %Y %H:%M:%S")
    return dt


def slot_yielder() -> Generator[str, None, None]:
    corrected_order = ['LoSlot', 'MedSlot', 'HiSlot', 'RigSlot', 'DroneBay']
    for slot in corrected_order:
        yield slot
    while True:
        yield 'Cargo'


def _lookup_type_id(type_name: str, conn) -> Optional[int]:
    result = conn.execute(
        text("SELECT typeID FROM inv_info WHERE typeName = :type_name"),
        {"type_name": type_name},
    ).fetchone()
    return result[0] if result else None


def _resolve_ship_type_id(ship_name: str, conn) -> Optional[int]:
    result = conn.execute(
        text("SELECT typeID FROM inv_info WHERE typeName = :type_name"),
        {"type_name": ship_name},
    ).fetchone()
    return result[0] if result else None


def parse_eft_fit_file(fit_file: str, fit_id: int, sde_engine) -> FitParseResult:
    """
    Parse an EFT-formatted fit file into structured items.

    Returns:
        FitParseResult with:
        - items: list of dicts matching fittings_fittingitem columns
        - ship_name, fit_name
        - missing_types: items we could not resolve to a type_id
    """
    items: List[Dict] = []
    missing: List[str] = []
    slot_gen = slot_yielder()
    current_slot = None
    ship_name = ""
    fit_name = ""
    slot_counters = defaultdict(int)

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

    return FitParseResult(items=items, ship_name=ship_name, fit_name=fit_name, missing_types=missing)


def process_fit(fit_file: str, fit_id: int):
    fit = []
    qty = 1
    slot_gen = slot_yielder()
    current_slot = None
    ship_name = ""
    fit_name = ""
    slot_counters = defaultdict(int)

    with open(fit_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()

            if line.startswith("[") and line.endswith("]"):
                clean_name = line.strip('[]')
                parts = clean_name.split(',')
                ship_name = parts[0].strip()
                fit_name = parts[1].strip() if len(parts) > 1 else "Unnamed Fit"
                continue

            if line == "":
                current_slot = next(slot_gen)
                continue

            if current_slot is None:
                current_slot = next(slot_gen)

            qty_match = re.search(r'\s+x(\d+)$', line)
            if qty_match:
                qty = int(qty_match.group(1))
                item = line[:qty_match.start()].strip()
            else:
                qty = 1
                item = line.strip()

            if current_slot in {'LoSlot', 'MedSlot', 'HiSlot', 'RigSlot'}:
                suffix = slot_counters[current_slot]
                slot_counters[current_slot] += 1
                slot_name = f"{current_slot}{suffix}"
            else:
                slot_name = current_slot

            fitting_item = FittingItem(
                flag=slot_name,
                fit_id=fit_id,
                type_name=item,
                ship_type_name=ship_name,
                fit_name=fit_name,
                quantity=qty,
            )

            fit.append([fitting_item.flag, fitting_item.quantity, fitting_item.type_id, fit_id, fitting_item.type_id])

    return fit, ship_name, fit_name


def create_doctrine(
    doctrine_id: int,
    name: str,
    description: str = "",
    icon_url: str = "",
    remote: bool = False
) -> bool:
    """
    Create a new doctrine in fittings_doctrine table.

    Args:
        doctrine_id: The doctrine ID to create
        name: Name of the doctrine
        description: Description of the doctrine
        icon_url: Optional icon URL
        remote: Whether to use remote database

    Returns:
        True if created successfully, False if doctrine already exists
    """
    from datetime import datetime, timezone

    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.connect() as conn:
        # Check if doctrine already exists
        existing = conn.execute(
            text("SELECT 1 FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()

        if existing:
            logger.info(f"Doctrine {doctrine_id} already exists")
            return False

        # Insert new doctrine
        conn.execute(
            text("""
                INSERT INTO fittings_doctrine (id, name, icon_url, description, created, last_updated)
                VALUES (:id, :name, :icon_url, :description, :created, :last_updated)
            """),
            {
                "id": doctrine_id,
                "name": name,
                "icon_url": icon_url,
                "description": description,
                "created": now,
                "last_updated": now,
            },
        )
        conn.commit()

    engine.dispose()
    logger.info(f"Created doctrine {doctrine_id}: {name}")
    return True


def doctrine_exists(doctrine_id: int, remote: bool = False) -> bool:
    """Check if a doctrine exists in fittings_doctrine."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()

    engine.dispose()
    return result is not None


def get_next_doctrine_id(remote: bool = False) -> int:
    """Get the next available doctrine ID."""
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM fittings_doctrine")
        ).scalar_one()

    engine.dispose()
    return result


def add_doctrine_to_watch(doctrine_id: int, remote: bool = False) -> None:
    """
    Add a doctrine from fittings_doctrine to watch_doctrines table.

    Args:
        doctrine_id: The doctrine ID to copy from fittings_doctrine to watch_doctrines
    """
    db = DatabaseConfig("fittings")
    engine = db.remote_engine if remote else db.engine

    with engine.connect() as conn:
        # Check if doctrine exists in fittings_doctrine
        select_stmt = text("SELECT * FROM fittings_doctrine WHERE id = :doctrine_id")
        result = conn.execute(select_stmt, {"doctrine_id": doctrine_id})
        doctrine_row = result.fetchone()

        if not doctrine_row:
            logger.error(f"Doctrine {doctrine_id} not found in fittings_doctrine")
            return

        # Check if already exists in watch_doctrines
        check_stmt = text("SELECT COUNT(*) FROM watch_doctrines WHERE id = :doctrine_id")
        result = conn.execute(check_stmt, {"doctrine_id": doctrine_id})
        count = result.fetchone()[0]

        if count > 0:
            logger.info(f"Doctrine {doctrine_id} already exists in watch_doctrines")
            return

        # Insert into watch_doctrines
        insert_stmt = text("""
            INSERT INTO watch_doctrines (id, name, icon_url, description, created, last_updated)
            VALUES (:id, :name, :icon_url, :description, :created, :last_updated)
        """)

        conn.execute(insert_stmt, {
            "id": doctrine_row[0],
            "name": doctrine_row[1],
            "icon_url": doctrine_row[2],
            "description": doctrine_row[3],
            "created": doctrine_row[4],
            "last_updated": doctrine_row[5]
        })
        conn.commit()

        logger.info(f"Added doctrine {doctrine_id} ('{doctrine_row[1]}') to watch_doctrines")

    engine.dispose()


def insert_fit_items_to_db(fit_items: list, fit_id: int, clear_existing: bool = True, remote: bool = False) -> None:
    """
    Insert parsed fit items into the fittings_fittingitem table.

    Args:
        fit_items: List of fit items where each item is [flag, quantity, type_id, fit_id, type_fk_id]
        fit_id: The fit ID these items belong to
        clear_existing: If True, delete existing items for this fit_id before inserting
    """
    engine = _get_engine("fittings", remote)

    with engine.connect() as conn:
        # Disable foreign key constraints for this transaction
        conn.execute(text("PRAGMA foreign_keys = OFF"))

        # Optionally clear existing items for this fit
        if clear_existing:
            delete_stmt = text("DELETE FROM fittings_fittingitem WHERE fit_id = :fit_id")
            conn.execute(delete_stmt, {"fit_id": fit_id})
            logger.info(f"Cleared existing items for fit_id {fit_id}")

        # Insert new items
        insert_stmt = text("""
            INSERT INTO fittings_fittingitem (flag, quantity, type_id, fit_id, type_fk_id)
            VALUES (:flag, :quantity, :type_id, :fit_id, :type_fk_id)
        """)

        for item in fit_items:
            if isinstance(item, dict):
                flag = item.get("flag")
                quantity = item.get("quantity")
                type_id = item.get("type_id")
                type_fk_id = item.get("type_fk_id")
            else:
                flag, quantity, type_id, fit_id, type_fk_id = item

            if type_id is None:
                logger.warning(f"Skipping item with missing type_id: {item}")
                continue

            conn.execute(
                insert_stmt,
                {
                    "flag": flag,
                    "quantity": quantity,
                    "type_id": type_id,
                    "fit_id": fit_id,
                    "type_fk_id": type_fk_id,
                },
            )

        conn.commit()

        # Re-enable foreign key constraints
        conn.execute(text("PRAGMA foreign_keys = ON"))

        logger.info(f"Inserted {len(fit_items)} items for fit_id {fit_id}")

    engine.dispose()

def parse_fit_metadata(fit_metadata_file: str) -> FitMetadata:
    with open(fit_metadata_file, 'r', encoding='utf-8') as f:
        metadata = json.load(f)
    return FitMetadata(**metadata)

def upsert_fittings_fitting(metadata: FitMetadata, ship_type_id: int, remote: bool = False) -> None:
    """
    Upsert the shell record in fittings_fitting.

    Note: Disables FK constraints because ship_type_id may not exist in fittings_type
    (types are sourced from SDE, not fittings database).
    """
    engine = _get_engine("fittings", remote)
    now = datetime.now().astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    created = now
    last_updated = metadata.last_updated or now
    with engine.connect() as conn:
        # Disable FK constraints - ship_type_id references fittings_type which may not have this type
        conn.execute(text("PRAGMA foreign_keys = OFF"))

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
        conn.execute(
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
        conn.commit()

        # Re-enable FK constraints
        conn.execute(text("PRAGMA foreign_keys = ON"))
    logger.info(
        f"Upserted fittings_fitting for fit_id {metadata.fit_id}: {metadata.name} (ship_type_id={ship_type_id})"
    )


def ensure_doctrine_link(doctrine_id: int, fit_id: int, remote: bool = False) -> None:
    engine = _get_engine("fittings", remote)
    with engine.connect() as conn:
        # Check if doctrine exists in fittings_doctrine
        doctrine_exists = conn.execute(
            text("SELECT 1 FROM fittings_doctrine WHERE id = :doctrine_id"),
            {"doctrine_id": doctrine_id},
        ).fetchone()
        if not doctrine_exists:
            logger.warning(
                f"Doctrine {doctrine_id} not found in fittings_doctrine. "
                f"Skipping doctrine link but continuing with market DB updates."
            )
            return

        exists = conn.execute(
            text(
                "SELECT 1 FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id"
            ),
            {"doctrine_id": doctrine_id, "fit_id": fit_id},
        ).fetchone()
        if exists:
            return

        # Disable FK constraints for this insert (doctrine may exist in source but not synced locally)
        conn.execute(text("PRAGMA foreign_keys = OFF"))
        next_id = conn.execute(
            text("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM fittings_doctrine_fittings")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO fittings_doctrine_fittings (id, doctrine_id, fitting_id) VALUES (:id, :doctrine_id, :fit_id)"
            ),
            {"id": next_id, "doctrine_id": doctrine_id, "fit_id": fit_id},
        )
        conn.commit()
        conn.execute(text("PRAGMA foreign_keys = ON"))
    logger.info(f"Linked doctrine_id {doctrine_id} to fit_id {fit_id} in fittings_doctrine_fittings")


def remove_doctrine_link(doctrine_id: int, fit_id: int, remote: bool = False) -> bool:
    """
    Remove the link between a doctrine and a fit in fittings_doctrine_fittings.

    Args:
        doctrine_id: The doctrine ID
        fit_id: The fit ID to unlink
        remote: Whether to use remote database

    Returns:
        True if a row was deleted, False if no matching row found
    """
    engine = _get_engine("fittings", remote)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "DELETE FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id"
            ),
            {"doctrine_id": doctrine_id, "fit_id": fit_id},
        )
        conn.commit()
        rows_affected = result.rowcount
    engine.dispose()

    if rows_affected > 0:
        logger.info(f"Removed link between doctrine_id {doctrine_id} and fit_id {fit_id} from fittings_doctrine_fittings")
        return True
    else:
        logger.warning(f"No fittings_doctrine_fittings row found for doctrine_id={doctrine_id}, fit_id={fit_id}")
        return False


def update_fit_workflow(
    fit_id: int,
    fit_file: str,
    fit_metadata_file: Optional[str] = None,
    remote: bool = False,
    clear_existing: bool = True,
    dry_run: bool = False,
    target_alias: str = "wcmkt",
    update_targets: bool = False,
    metadata_override: Optional[Dict] = None,
):
    """
    End-to-end update for a fit:
    - Parse EFT file
    - Upsert fittings_fitting and fittings_fittingitem
    - Ensure doctrine link in fittings_doctrine_fittings
    - Propagate to wcmktprod doctrine tables and watchlist

    Args:
        fit_id: The fit ID to update
        fit_file: Path to EFT fit file
        fit_metadata_file: Path to metadata JSON file (optional if metadata_override provided)
        remote: Use remote database
        clear_existing: Clear existing fit items before inserting
        dry_run: Preview changes without saving
        target_alias: Target database alias (wcmkt or wcmktnorth)
        update_targets: If True, update ship_targets table (default: False)
        metadata_override: Dict with metadata fields (overrides file if provided)
    """
    # Get metadata from override dict or file
    if metadata_override:
        metadata = FitMetadata(**metadata_override)
    elif fit_metadata_file:
        metadata = parse_fit_metadata(fit_metadata_file)
    else:
        raise ValueError("Either fit_metadata_file or metadata_override must be provided")

    sde_engine = _get_engine("sde", False)

    try:
        parse_result = parse_eft_fit_file(fit_file, fit_id, sde_engine)
        metadata.ship_name = parse_result.ship_name

        with sde_engine.connect() as conn:
            ship_type_id = metadata.ship_type_id or _resolve_ship_type_id(parse_result.ship_name, conn)

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

    # Upsert core fitting data
    upsert_fittings_fitting(metadata, ship_type_id, remote=remote)
    insert_fit_items_to_db(parse_result.items, fit_id=fit_id, clear_existing=clear_existing, remote=remote)
    for doctrine_id in metadata.doctrine_ids:
        # Auto-create doctrine if it doesn't exist
        if not doctrine_exists(doctrine_id, remote=remote):
            logger.info(f"Doctrine {doctrine_id} doesn't exist, creating it with name '{metadata.name}'")
            create_doctrine(
                doctrine_id=doctrine_id,
                name=metadata.name,
                description=metadata.description,
                remote=remote,
            )
            # Also add to watch_doctrines for tracking
            add_doctrine_to_watch(doctrine_id, remote=remote)

        ensure_doctrine_link(doctrine_id, fit_id, remote=remote)

        doctrine_fit = DoctrineFit(doctrine_id=doctrine_id, fit_id=fit_id, target=metadata.target)

        # Propagate to market/production dbs (wcmktprod.db or wcmktnorth2.db)
        upsert_doctrine_fits(doctrine_fit, remote=remote, db_alias=target_alias)
        upsert_doctrine_map(doctrine_fit.doctrine_id, doctrine_fit.fit_id, remote=remote, db_alias=target_alias)

    # Always update ship_targets - this tracks target quantities for each fit
    upsert_ship_target(
        fit_id=fit_id,
        fit_name=parse_result.fit_name,
        ship_id=ship_type_id,
        ship_name=parse_result.ship_name,
        ship_target=metadata.target,
        remote=remote,
        db_alias=target_alias,
    )
    logger.info(f"Updated ship_targets for fit_id={fit_id}")

    # Always refresh doctrines table - this contains item-level market data
    # that the frontend requires for displaying fit market availability
    refresh_doctrines_for_fit(
        fit_id=fit_id,
        ship_id=ship_type_id,
        ship_name=parse_result.ship_name,
        remote=remote,
        db_alias=target_alias,
    )
    logger.info(f"Refreshed doctrines table for fit_id={fit_id}")

    # Add missing items to watchlist in wcmkt
    type_ids = {item["type_id"] for item in parse_result.items}
    type_ids.add(ship_type_id)
    add_missing_items_to_watchlist(list(type_ids), remote=remote, db_alias=target_alias)
    logger.info(
        f"Completed fit update for fit_id={fit_id}, doctrine_ids={metadata.doctrine_ids} "
        f"(remote={remote}, update_targets={update_targets})"
    )


def update_existing_fit(fit_id: int, fit_file: str, fit_metadata_file: str, remote: bool = False, clear_existing: bool = True):
    update_fit_workflow(fit_id, fit_file, fit_metadata_file, remote=remote, clear_existing=clear_existing)


def update_fit(fit_id: int, fit_file: str, fit_metadata_file: str, remote: bool = False, clear_existing: bool = True):
    update_fit_workflow(fit_id, fit_file, fit_metadata_file, remote=remote, clear_existing=clear_existing)


def display_fit_market_status(parse_result: FitParseResult, db_alias: str = "wcmktprod"):
    """
    Display market status from wcmktprod.db for items in a parsed fit.

    Args:
        parse_result: FitParseResult from parse_eft_fit_file
        db_alias: Database alias to query (default: "wcmktprod")
    """
    from collections import defaultdict

    # Get database connection
    market_db = DatabaseConfig(db_alias)

    # Collect unique type_ids and their quantities
    item_quantities = defaultdict(int)
    for item in parse_result.items:
        type_id = item["type_id"]
        quantity = item["quantity"]
        item_quantities[type_id] += quantity

    if not item_quantities:
        print(f"\nNo items found in fit: {parse_result.fit_name}")
        return

    # Query market stats for all items
    market_data = []

    with market_db.engine.connect() as market_conn:
        with _sde_db.engine.connect() as sde_conn:
            for type_id, needed_qty in item_quantities.items():
                # Get market stats first
                stats_query = text("""
                    SELECT type_name, price, min_price, total_volume_remain,
                           avg_price, days_remaining, last_update
                    FROM marketstats
                    WHERE type_id = :type_id
                """)
                stats_result = market_conn.execute(stats_query, {"type_id": type_id}).fetchone()

                if stats_result:
                    # Item found in market stats
                    stats_dict = dict(stats_result._mapping)
                    stats_dict["type_id"] = type_id
                    stats_dict["needed_qty"] = needed_qty
                    stats_dict["available"] = stats_dict["total_volume_remain"] >= needed_qty
                    market_data.append(stats_dict)
                else:
                    # Item not in market stats - get type name from SDE
                    type_name_query = text("SELECT typeName FROM inv_info WHERE typeID = :type_id")
                    type_name_result = sde_conn.execute(type_name_query, {"type_id": type_id}).fetchone()
                    type_name = type_name_result[0] if type_name_result else f"Unknown (ID: {type_id})"

                    market_data.append({
                        "type_id": type_id,
                        "type_name": type_name,
                        "price": None,
                        "min_price": None,
                        "total_volume_remain": 0,
                        "avg_price": None,
                        "days_remaining": None,
                        "last_update": None,
                        "needed_qty": needed_qty,
                        "available": False,
                    })

    # Display results
    print(f"\n{'='*80}")
    print(f"Market Status for Fit: {parse_result.fit_name}")
    print(f"Ship: {parse_result.ship_name}")
    print(f"{'='*80}\n")

    # Sort by availability (available first), then by type name
    market_data.sort(key=lambda x: (not x["available"], x["type_name"]))

    # Calculate column widths
    max_name_len = max(len(item["type_name"]) for item in market_data) if market_data else 20
    max_name_len = max(max_name_len, len("Item Name"))

    # Print table header
    header = f"{'Item Name':<{max_name_len}} | {'Needed':>8} | {'Available':>12} | {'Status':>12} | {'Price (ISK)':>15} | {'Days':>8}"
    print(header)
    print("-" * len(header))

    # Print table rows
    for item in market_data:
        status = "✓ Available" if item["available"] else "✗ Insufficient"
        price_str = f"{item['price']:,.2f}" if item["price"] is not None else "N/A"
        volume_str = f"{item['total_volume_remain']:,}" if item["total_volume_remain"] is not None else "0"
        days_str = f"{item['days_remaining']:.1f}" if item["days_remaining"] is not None else "N/A"

        row = f"{item['type_name']:<{max_name_len}} | {item['needed_qty']:>8} | {volume_str:>12} | {status:>12} | {price_str:>15} | {days_str:>8}"
        print(row)

    # Summary
    available_count = sum(1 for item in market_data if item["available"])
    total_count = len(market_data)
    missing_count = total_count - available_count

    print(f"\nSummary: {available_count}/{total_count} items available on market")
    if missing_count > 0:
        missing_items = [item["type_name"] for item in market_data if not item["available"]]
        print(f"Missing items: {', '.join(missing_items[:10])}")
        if len(missing_items) > 10:
            print(f"  ... and {len(missing_items) - 10} more")

    # Calculate total cost
    total_cost = 0
    for item in market_data:
        if item["price"] is not None:
            total_cost += item["price"] * item["needed_qty"]

    if total_cost > 0:
        print(f"\nEstimated Total Cost: {total_cost:,.2f} ISK")

    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    # rni = "data/rni.txt"
    # maelstrom = "data/maelstrom.txt"
    # update_fit_workflow(901, rni, "data/rni.txt", remote=False, clear_existing=True, dry_run=True, target_alias="wcmkt")
    pass