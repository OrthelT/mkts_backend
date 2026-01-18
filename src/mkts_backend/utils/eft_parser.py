"""
EFT (EVE Fitting Tool) format parser.

This module provides functions for parsing EFT-formatted ship fitting files
into structured data that can be used for market analysis and database storage.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config import DatabaseConfig

logger = configure_logging(__name__)

# Module-level SDE database connection
_sde_db = DatabaseConfig("sde")


@dataclass
class FitParseResult:
    """Result of parsing an EFT fit file."""
    items: List[Dict]
    ship_name: str
    ship_type_id: Optional[int]
    fit_name: str
    missing_types: List[str]

    @property
    def total_items(self) -> int:
        """Total number of unique item types in the fit."""
        return len(self.items)

    @property
    def has_missing_types(self) -> bool:
        """Whether any items could not be resolved to type IDs."""
        return len(self.missing_types) > 0


def _slot_yielder() -> Generator[str, None, None]:
    """
    Generate slot names in EFT order.

    EFT format orders slots as: Low, Med, High, Rig, Drone, then Cargo.
    Empty lines signal slot transitions.
    """
    corrected_order = ['LoSlot', 'MedSlot', 'HiSlot', 'RigSlot', 'DroneBay']
    for slot in corrected_order:
        yield slot
    while True:
        yield 'Cargo'


def lookup_type_id(type_name: str, conn=None) -> Optional[int]:
    """
    Look up a type ID from the SDE by type name.

    Args:
        type_name: The item name to look up
        conn: Optional database connection. If None, creates a new connection.

    Returns:
        The type ID if found, None otherwise
    """
    if conn is None:
        engine = _sde_db.engine
        with engine.connect() as new_conn:
            result = new_conn.execute(
                text("SELECT typeID FROM inv_info WHERE typeName = :type_name"),
                {"type_name": type_name},
            ).fetchone()
            return result[0] if result else None
    else:
        result = conn.execute(
            text("SELECT typeID FROM inv_info WHERE typeName = :type_name"),
            {"type_name": type_name},
        ).fetchone()
        return result[0] if result else None


def resolve_ship_type_id(ship_name: str, conn=None) -> Optional[int]:
    """
    Resolve a ship name to its type ID.

    Args:
        ship_name: The ship name to look up
        conn: Optional database connection

    Returns:
        The ship type ID if found, None otherwise
    """
    return lookup_type_id(ship_name, conn)


def parse_eft_string(eft_text: str, fit_id: int = 0, sde_engine: Engine = None) -> FitParseResult:
    """
    Parse an EFT-formatted string into structured items.

    Args:
        eft_text: The EFT format text to parse
        fit_id: Optional fit ID to assign to parsed items
        sde_engine: Optional SDE database engine. Uses default if None.

    Returns:
        FitParseResult with parsed items, ship info, and any missing types
    """
    if sde_engine is None:
        sde_engine = _sde_db.engine

    items: List[Dict] = []
    missing: List[str] = []
    slot_gen = _slot_yielder()
    current_slot = None
    ship_name = ""
    ship_type_id = None
    fit_name = ""
    slot_counters = defaultdict(int)

    with sde_engine.connect() as sde_conn:
        for line in eft_text.strip().split('\n'):
            line = line.strip()

            # Parse header line: [Ship Name, Fit Name]
            if line.startswith("[") and line.endswith("]"):
                clean_name = line.strip("[]")
                parts = clean_name.split(",")
                ship_name = parts[0].strip()
                fit_name = parts[1].strip() if len(parts) > 1 else "Unnamed Fit"
                ship_type_id = resolve_ship_type_id(ship_name, sde_conn)
                continue

            # Empty line signals slot transition
            if line == "":
                current_slot = next(slot_gen)
                continue

            if current_slot is None:
                current_slot = next(slot_gen)

            # Parse quantity suffix (e.g., "Nanite Repair Paste x100")
            qty_match = re.search(r"\s+x(\d+)$", line)
            if qty_match:
                qty = int(qty_match.group(1))
                item_name = line[: qty_match.start()].strip()
            else:
                qty = 1
                item_name = line.strip()

            # Skip empty item names
            if not item_name:
                continue

            # Generate slot name with index for fitted slots
            if current_slot in {"LoSlot", "MedSlot", "HiSlot", "RigSlot"}:
                suffix = slot_counters[current_slot]
                slot_counters[current_slot] += 1
                slot_name = f"{current_slot}{suffix}"
            else:
                slot_name = current_slot

            # Look up type ID
            type_id = lookup_type_id(item_name, sde_conn)
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
                    "type_name": item_name,
                }
            )

    return FitParseResult(
        items=items,
        ship_name=ship_name,
        ship_type_id=ship_type_id,
        fit_name=fit_name,
        missing_types=missing
    )


def parse_eft_file(fit_file: str, fit_id: int = 0, sde_engine: Engine = None) -> FitParseResult:
    """
    Parse an EFT-formatted fit file into structured items.

    Args:
        fit_file: Path to the EFT format file
        fit_id: Optional fit ID to assign to parsed items
        sde_engine: Optional SDE database engine. Uses default if None.

    Returns:
        FitParseResult with parsed items, ship info, and any missing types
    """
    with open(fit_file, "r", encoding="utf-8") as f:
        eft_text = f.read()

    return parse_eft_string(eft_text, fit_id, sde_engine)


def aggregate_fit_items(parse_result: FitParseResult) -> Dict[int, Dict]:
    """
    Aggregate fit items by type_id, summing quantities.

    Args:
        parse_result: The parsed fit result

    Returns:
        Dict mapping type_id to item info with aggregated quantity
    """
    aggregated = {}

    for item in parse_result.items:
        type_id = item["type_id"]
        if type_id in aggregated:
            aggregated[type_id]["quantity"] += item["quantity"]
        else:
            aggregated[type_id] = {
                "type_id": type_id,
                "type_name": item.get("type_name", ""),
                "quantity": item["quantity"],
            }

    return aggregated
