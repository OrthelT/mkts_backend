import datetime
from dataclasses import dataclass, field
from typing import List

import pandas as pd
from sqlalchemy import text, select
from sqlalchemy.orm import Session

from mkts_backend.db.models import Doctrines, LeadShips, DoctrineFitItems, Base
from mkts_backend.db.db_queries import get_watchlist_ids, get_fit_ids, get_fit_items
from mkts_backend.utils.get_type_info import TypeInfo
from mkts_backend.config.config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.utils.utils import get_type_name



logger = configure_logging(__name__)

def _get_engine(db_alias: str, remote: bool = False):
    cfg = DatabaseConfig(db_alias)
    return cfg.remote_engine if remote else cfg.engine


doctrines_fields = ['id', 'fit_id', 'ship_id', 'ship_name', 'hulls', 'type_id', 'type_name', 'fit_qty', 'fits_on_mkt', 'total_stock', 'price', 'avg_vol', 'days', 'group_id', 'group_name', 'category_id', 'category_name', 'timestamp']

doctrine_fit_id = 494
ship_id = 33157
ship_name = 'Hurricane Fleet Issue'
ship_target = 100
doctrine_name = '2507  WC-EN Shield DPS HFI v1.0'
fit_name = '2507  WC-EN Shield DPS HFI v1.0'
ship_type_id = 33157


@dataclass
class DoctrineFit:
    doctrine_id: int
    fit_id: int
    target: int
    doctrine_name: str = field(init=False)
    fit_name: str = field(init=False)
    ship_type_id: int = field(init=False)
    ship_name: str = field(init=False)

    def __post_init__(self):
        self.doctrine_name = self.get_doctrine_name()
        self.fit_name = self.get_fit_name()
        self.ship_type_id = self.get_ship_type_id()
        self.ship_name = self.get_ship_name()

    def get_doctrine_name(self):
        db = DatabaseConfig("fittings")
        engine = db.engine
        with engine.connect() as conn:
            stmt = text("SELECT * FROM fittings_doctrine WHERE id = :doctrine_id")
            result = conn.execute(stmt, {"doctrine_id": self.doctrine_id})
            row = result.fetchone()
            if row is None:
                raise ValueError(f"Doctrine {self.doctrine_id} not found in fittings_doctrine")
            name = row[1]
            return name.strip()

    def get_ship_type_id(self):
        db = DatabaseConfig("fittings")
        engine = db.engine
        with engine.connect() as conn:
            stmt = text("SELECT * FROM fittings_fitting WHERE id = :fit_id")
            result = conn.execute(stmt, {"fit_id": self.fit_id})
            row = result.fetchone()
            if row is None:
                raise ValueError(f"Fit {self.fit_id} not found in fittings_fitting")
            type_id = row[4]
            return type_id

    def get_fit_name(self):
        db = DatabaseConfig("fittings")
        engine = db.engine
        with engine.connect() as conn:
            stmt = text("SELECT * FROM fittings_fitting WHERE id = :fit_id")
            result = conn.execute(stmt, {"fit_id": self.fit_id})
            row = result.fetchone()
            if row is None:
                raise ValueError(f"Fit {self.fit_id} not found in fittings_fitting")
            name = row[2]
            return name.strip()

    def get_ship_name(self, remote=False):
        db = DatabaseConfig("sde")
        engine = db.engine
        with engine.connect() as conn:
            stmt = text("SELECT * FROM inv_info WHERE typeID = :type_id")
            result = conn.execute(stmt, {"type_id": self.ship_type_id})
            row = result.fetchone()
            if row is None:
                raise ValueError(f"Ship type_id {self.ship_type_id} not found in inv_info")
            name = row[1]
            return name.strip()

    def add_wcmkts2_doctrine_fits(self, remote=False):
        db = DatabaseConfig("wcmkt")
        engine = db.remote_engine if remote else db.engine
        with engine.connect() as conn:
            stmt = text("SELECT * FROM doctrine_fits")
            df = pd.read_sql_query(stmt, conn)
            if self.fit_id in df['fit_id'].values:
                logger.info(f"fit_id {self.fit_id} already exists, updating")
                stmt = text("""
                    UPDATE doctrine_fits SET doctrine_name = :doctrine_name,
                    fit_name = :fit_name, ship_type_id = :ship_type_id, ship_name = :ship_name, doctrine_id = :doctrine_id
                    WHERE fit_id = :fit_id
                """)
                conn.execute(stmt, {
                    "doctrine_name": self.doctrine_name,
                    "fit_name": self.fit_name,
                    "ship_type_id": self.ship_type_id,
                    "ship_name": self.ship_name,
                    "doctrine_id": self.doctrine_id,
                    "fit_id": self.fit_id,
                })
                conn.commit()
            else:
                logger.info(f"fit_id {self.fit_id} does not exist, adding")
                stmt = text("""
                    INSERT INTO doctrine_fits (doctrine_name, fit_name, ship_type_id, doctrine_id, fit_id, ship_name)
                    VALUES (:doctrine_name, :fit_name, :ship_type_id, :doctrine_id, :fit_id, :ship_name)
                """)
                conn.execute(stmt, {
                    "doctrine_name": self.doctrine_name,
                    "fit_name": self.fit_name,
                    "ship_type_id": self.ship_type_id,
                    "doctrine_id": self.doctrine_id,
                    "fit_id": self.fit_id,
                    "ship_name": self.ship_name,
                })
                conn.commit()


def upsert_doctrine_fits(
    doctrine_fit: DoctrineFit,
    remote: bool = False,
    db_alias: str = "wcmkt",
    market_flag: str = "primary"
) -> None:
    """
    Upsert doctrine_fits entry keyed by (doctrine_id, fit_id).

    Args:
        doctrine_fit: DoctrineFit dataclass with fit information
        remote: Whether to use remote database
        db_alias: Database alias to use
        market_flag: Market assignment ('primary', 'deployment', or 'both')
    """
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
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
        conn.commit()
    engine.dispose()
    logger.info(f"Upserted doctrine_fits for fit_id {doctrine_fit.fit_id} with market_flag={market_flag}")


def update_fit_market_flag(
    fit_id: int,
    market_flag: str,
    remote: bool = False,
    db_alias: str = "wcmkt"
) -> bool:
    """
    Update the market_flag for a fit.

    Args:
        fit_id: The fit ID to update
        market_flag: New market assignment ('primary', 'deployment', or 'both')
        remote: Whether to use remote database
        db_alias: Database alias to use

    Returns:
        True if update succeeded, False if fit not found
    """
    if market_flag not in ("primary", "deployment", "both"):
        raise ValueError(f"Invalid market_flag: {market_flag}. Must be 'primary', 'deployment', or 'both'")

    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        result = conn.execute(
            text("UPDATE doctrine_fits SET market_flag = :market_flag WHERE fit_id = :fit_id"),
            {"fit_id": fit_id, "market_flag": market_flag},
        )
        conn.commit()
        rows_affected = result.rowcount

    engine.dispose()

    if rows_affected > 0:
        logger.info(f"Updated market_flag to '{market_flag}' for fit_id {fit_id} ({rows_affected} rows)")
        return True
    else:
        logger.warning(f"No rows found for fit_id {fit_id}")
        return False


def get_fit_market_flag(fit_id: int, remote: bool = False, db_alias: str = "wcmkt") -> str | None:
    """
    Get the market_flag for a fit.

    Args:
        fit_id: The fit ID to query
        remote: Whether to use remote database
        db_alias: Database alias to use

    Returns:
        The market_flag value, or None if fit not found
    """
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT market_flag FROM doctrine_fits WHERE fit_id = :fit_id LIMIT 1"),
            {"fit_id": fit_id},
        ).fetchone()

    engine.dispose()

    if result:
        return result[0]
    return None


def get_fit_target(fit_id: int, remote: bool = False, db_alias: str = "wcmkt") -> int | None:
    """
    Get the existing target for a fit from doctrine_fits.

    Args:
        fit_id: The fit ID to query
        remote: Whether to use remote database
        db_alias: Database alias to use

    Returns:
        The target value, or None if fit not found
    """
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT target FROM doctrine_fits WHERE fit_id = :fit_id LIMIT 1"),
            {"fit_id": fit_id},
        ).fetchone()

    engine.dispose()

    if result:
        return result[0]
    return None


def upsert_doctrine_map(doctrine_id: int, fit_id: int, remote: bool = False, db_alias: str = "wcmkt") -> None:
    engine = _get_engine(db_alias, remote)
    try:
        with engine.connect() as conn:
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
            conn.commit()
            logger.info(f"Upserted doctrine_map entry doctrine_id={doctrine_id}, fit_id={fit_id}")
    finally:
        engine.dispose()


def remove_doctrine_fits(
    doctrine_id: int,
    fit_id: int,
    remote: bool = False,
    db_alias: str = "wcmkt"
) -> bool:
    """
    Remove a fit from the doctrine_fits table.

    Args:
        doctrine_id: The doctrine ID
        fit_id: The fit ID to remove
        remote: Whether to use remote database
        db_alias: Database alias to use

    Returns:
        True if a row was deleted, False if no matching row found
    """
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        result = conn.execute(
            text("DELETE FROM doctrine_fits WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"),
            {"fit_id": fit_id, "doctrine_id": doctrine_id},
        )
        conn.commit()
        rows_affected = result.rowcount
    engine.dispose()

    if rows_affected > 0:
        logger.info(f"Removed fit_id {fit_id} from doctrine_id {doctrine_id} in doctrine_fits ({rows_affected} rows)")
        return True
    else:
        logger.warning(f"No doctrine_fits row found for fit_id={fit_id}, doctrine_id={doctrine_id}")
        return False


def remove_doctrine_map(
    doctrine_id: int,
    fit_id: int,
    remote: bool = False,
    db_alias: str = "wcmkt"
) -> bool:
    """
    Remove a fit-doctrine mapping from the doctrine_map table.

    Args:
        doctrine_id: The doctrine ID
        fit_id: The fit ID (fitting_id in doctrine_map)
        remote: Whether to use remote database
        db_alias: Database alias to use

    Returns:
        True if a row was deleted, False if no matching row found
    """
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        result = conn.execute(
            text("DELETE FROM doctrine_map WHERE doctrine_id = :doctrine_id AND fitting_id = :fit_id"),
            {"doctrine_id": doctrine_id, "fit_id": fit_id},
        )
        conn.commit()
        rows_affected = result.rowcount
    engine.dispose()

    if rows_affected > 0:
        logger.info(f"Removed doctrine_map entry for doctrine_id={doctrine_id}, fit_id={fit_id}")
        return True
    else:
        logger.warning(f"No doctrine_map row found for doctrine_id={doctrine_id}, fit_id={fit_id}")
        return False


def remove_doctrines_for_fit(
    fit_id: int,
    remote: bool = False,
    db_alias: str = "wcmkt"
) -> int:
    """
    Remove all doctrines table rows for a specific fit.

    Args:
        fit_id: The fit ID to remove rows for
        remote: Whether to use remote database
        db_alias: Database alias to use

    Returns:
        Number of rows deleted
    """
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        result = conn.execute(
            text("DELETE FROM doctrines WHERE fit_id = :fit_id"),
            {"fit_id": fit_id},
        )
        conn.commit()
        rows_affected = result.rowcount
    engine.dispose()

    logger.info(f"Removed {rows_affected} rows from doctrines table for fit_id {fit_id}")
    return rows_affected

@dataclass
class DoctrineComponent:
    fit_id: int
    ship_id: int
    ship_name: str
    hulls: int
    type_id: int
    type_name: str
    fit_qty: int
    fits_on_mkt: float
    total_stock: int
    price: float
    avg_vol: float
    days: float
    group_id: int
    group_name: str
    category_id: int
    category_name: str
    timestamp: str = field(init=False)

    def __post_init__(self):
        self.timestamp = datetime.datetime.strftime(datetime.datetime.now(datetime.timezone.utc), '%Y-%m-%d %H:%M:%S')

def upsert_ship_target(fit_id: int, fit_name: str, ship_id: int, ship_name: str, ship_target: int, remote: bool = False, db_alias: str = "wcmkt") -> bool:
    """
    Upsert ship_targets entry keyed by fit_id.
    """
    created_at = datetime.datetime.strftime(datetime.datetime.now(datetime.timezone.utc), '%Y-%m-%d %H:%M:%S')
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        # Some schemas (e.g., wcmktnorth2) lack PK/unique constraint on fit_id; use delete-then-insert.
        conn.execute(text("DELETE FROM ship_targets WHERE fit_id = :fit_id"), {"fit_id": fit_id})
        insert_stmt = text(
            """
            INSERT INTO ship_targets (fit_id, fit_name, ship_id, ship_name, ship_target, created_at)
            VALUES (:fit_id, :fit_name, :ship_id, :ship_name, :ship_target, :created_at)
            """
        )
        conn.execute(
            insert_stmt,
            {
                "fit_id": fit_id,
                "fit_name": fit_name,
                "ship_id": ship_id,
                "ship_name": ship_name,
                "ship_target": ship_target,
                "created_at": created_at,
            },
        )
        conn.commit()
    engine.dispose()
    logger.info(f"Upserted ship_targets for fit_id {fit_id}")
    return True

def add_doctrine_map_from_fittings_doctrine_fittings(doctrine_id: int):
    db = DatabaseConfig("fittings")
    engine = db.remote_engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id")
        df = pd.read_sql_query(stmt, conn, params={"doctrine_id": doctrine_id})
    conn.close()
    doctrine_map_db = DatabaseConfig("wcmkt")
    engine = doctrine_map_db.remote_engine
    with engine.connect() as conn:
        for index, row in df.iterrows():
            stmt = text("INSERT INTO doctrine_map ('doctrine_id', 'fitting_id') VALUES (:doctrine_id, :fitting_id)")
            conn.execute(stmt, {"doctrine_id": doctrine_id, "fitting_id": row.fitting_id})
            logger.info(f"Added doctrine_map for doctrine_id: {doctrine_id}, fitting_id: {row.fitting_id}")
        conn.commit()
        print("Doctrine map added")
    conn.close()
    engine.dispose()

def add_hurricane_fleet_issue_to_doctrines():

    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM marketstats WHERE type_id = 33157")
        market_data = conn.execute(stmt).fetchone()
    conn.close()
    engine.dispose()

    if not market_data:
        logger.error("No market data found for Hurricane Fleet Issue (type_id 33157)")
        return False

    type_info = TypeInfo(33157)

    engine = db.remote_engine
    with engine.connect() as conn:
        stmt = text('SELECT MAX(id) as max_id FROM doctrines')
        result = conn.execute(stmt).fetchone()
        max_id = result.max_id if result.max_id else 0
        next_id = max_id + 1
        logger.info(f"Next available ID: {next_id}")
    conn.close()
    engine.dispose()

    fit_qty = 1
    hulls_on_market = market_data.total_volume_remain
    total_stock_on_market = market_data.total_volume_remain
    fits_on_mkt = total_stock_on_market / fit_qty

    stmt = text("""
        INSERT INTO doctrines (
            id, fit_id, ship_id, ship_name, hulls, type_id, type_name, fit_qty,
            fits_on_mkt, total_stock, price, avg_vol, days, group_id,
            group_name, category_id, category_name, timestamp
        ) VALUES (
            :id, :fit_id, :ship_id, :ship_name, :hulls, :type_id, :type_name, :fit_qty,
            :fits_on_mkt, :total_stock, :price, :avg_vol, :days, :group_id,
            :group_name, :category_id, :category_name, :timestamp
        )
    """)

    insert_data = {
        'id': next_id,
        'fit_id': 494,
        'ship_id': 33157,
        'ship_name': 'Hurricane Fleet Issue',
        'hulls': int(hulls_on_market),
        'type_id': 33157,
        'type_name': type_info.type_name,
        'fit_qty': fit_qty,
        'fits_on_mkt': float(fits_on_mkt),
        'total_stock': int(total_stock_on_market),
        'price': float(market_data.price),
        'avg_vol': float(market_data.avg_volume),
        'days': float(market_data.days_remaining),
        'group_id': int(type_info.group_id),
        'group_name': type_info.group_name,
        'category_id': int(type_info.category_id),
        'category_name': type_info.category_name,
        'timestamp': datetime.now(datetime.timezone.utc).isoformat()
    }

    engine = db.remote_engine
    with engine.connect() as conn:
        conn.execute(stmt, insert_data)
        conn.commit()
        logger.info("Successfully added Hurricane Fleet Issue (fit_id 494) to doctrines table")
        print("Hurricane Fleet Issue added to doctrines table successfully!")
    conn.close()
    engine.dispose()

    return True

def add_fit_to_doctrines_table(DoctrineFit: DoctrineFitItems):
    db = DatabaseConfig("wcmkt")
    stmt = text("""INSERT INTO doctrines ('fit_id', 'fit_name', 'ship_id', 'ship_name', 'ship_target', 'created_at')
    VALUES (494, '2507  WC-EN Shield DPS HFI v1.0', 33157, 'Hurricane Fleet Issue', 100, '2025-07-05 00:00:00')""")
    engine = db.remote_engine
    with engine.connect() as conn:
        conn.execute(stmt)
        conn.commit()
        print("Fit added to doctrines table")
    conn.close()
    engine.dispose()

def add_lead_ship():
    hfi = LeadShips(doctrine_name=doctrine_name, doctrine_id=84, lead_ship=ship_id, fit_id=doctrine_fit_id)
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine
    session = Session(bind=engine)
    with session.begin():
        session.add(hfi)
        session.commit()
        print("Lead ship added")
    session.close()

def process_hfi_fit_items(type_ids: list[int]) -> list[DoctrineComponent]:
    items = []
    for type_id in type_ids:
        item = DoctrineComponent(
            fit_id=494,
            ship_id=33157,
            ship_name='Hurricane Fleet Issue',
            type_id=type_id,
            type_name='Hurricane Fleet Issue',
            fit_qty=1,
            fits_on_mkt=100,
            total_stock=100,
            price=100,
            avg_vol=100,
            days=100,
            group_id=100,
            group_name='Hurricane Fleet Issue',
            category_id=100,
            category_name='Hurricane Fleet Issue'
        )
        items.append(item)
    return items

def get_fit_item_ids(doctrine_id: int) -> dict[int, list[int]]:
    fit_items = {}
    db = DatabaseConfig("fittings")
    engine = db.remote_engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM fittings_doctrine_fittings WHERE doctrine_id = :doctrine_id")
        result = conn.execute(stmt, {"doctrine_id": doctrine_id})
        for row in result:
            fit_id = row[2]
            stmt = text("SELECT type_id FROM fittings_fittingitem WHERE fit_id = :fit_id")
            res2 = conn.execute(stmt, {"fit_id": fit_id})
            type_ids = [row[0] for row in res2]
            fit_items[fit_id] = type_ids
    conn.close()
    engine.dispose()
    return fit_items

def add_doctrine_type_info_to_watchlist(doctrine_id: int):
    watchlist_ids = get_watchlist_ids()
    fit_ids = get_fit_ids(doctrine_id)

    missing_fit_items = []

    for fit_id in fit_ids:
        fit_items = get_fit_items(fit_id)
        for item in fit_items:
            if item not in watchlist_ids:
                missing_fit_items.append(item)

    missing_type_info = []
    logger.info(f"Adding {len(missing_fit_items)} missing items to watchlist")
    print(f"Adding {len(missing_fit_items)} missing items to watchlist")
    continue_adding = input("Continue adding? (y/n)")
    if continue_adding == "n":
        return
    else:
        logger.info(f"Continuing to add {len(missing_fit_items)} missing items to watchlist")
        print(f"Continuing to add {len(missing_fit_items)} missing items to watchlist")

    for item in missing_fit_items:
        stmt4 = text("SELECT * FROM inv_info WHERE typeID = :item")
        db = DatabaseConfig("sde")
        engine = db.engine
        with engine.connect() as conn:
            result = conn.execute(stmt4, {"item": item})
            for row in result:
                type_info = TypeInfo(type_id=item)
                missing_type_info.append(type_info)

    for type_info in missing_type_info:
        stmt5 = text("INSERT INTO watchlist (type_id, type_name, group_name, category_name, category_id, group_id) VALUES (:type_id, :type_name, :group_name, :category_name, :category_id, :group_id)")
        db = DatabaseConfig("wcmkt")
        engine = db.engine
        with engine.connect() as conn:
            conn.execute(stmt5, {"type_id": type_info.type_id, "type_name": type_info.type_name, "group_name": type_info.group_name, "category_name": type_info.category_name, "category_id": type_info.category_id, "group_id": type_info.group_id})
            conn.commit()
        conn.close()
        engine.dispose()
        logger.info(f"Added {type_info.type_name} to watchlist")
        print(f"Added {type_info.type_name} to watchlist")

def refresh_doctrines_for_fit(fit_id: int, ship_id: int, ship_name: str, remote: bool = False, db_alias: str = "wcmkt") -> None:
    """
    Rebuild doctrines table rows for a fit based on fittings_fittingitem content.
    """
    # Aggregate component quantities from fittings
    fittings_engine = _get_engine("fittings", remote)
    try:
        with fittings_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT type_id, SUM(quantity) as qty FROM fittings_fittingitem WHERE fit_id = :fit_id GROUP BY type_id"
                ),
                {"fit_id": fit_id},
            ).fetchall()

        components = [(row.type_id, row.qty) for row in rows]
        # Ensure hull present (qty 1)
        if ship_id not in [c[0] for c in components]:
            components.append((ship_id, 1))
    finally:
        fittings_engine.dispose()

    doctrines_engine = _get_engine(db_alias, remote)
    stats_engine = _get_engine(db_alias, remote)
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Pull market stats once into dict for quick lookup
    stats_map = {}
    with stats_engine.connect() as conn:
        stats_rows = conn.execute(
            text(
                "SELECT type_id, price, avg_price, avg_volume, days_remaining, total_volume_remain FROM marketstats"
            )
        ).fetchall()
        for r in stats_rows:
            stats_map[r.type_id] = r

    hull_stats = stats_map.get(ship_id)
    hull_stock = int(hull_stats.total_volume_remain) if hull_stats and hull_stats.total_volume_remain is not None else 0

    with doctrines_engine.connect() as conn:
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
            total_stock = int(stats.total_volume_remain) if stats and stats.total_volume_remain is not None else 0
            price_val = float(stats.price) if stats and stats.price is not None else 0.0
            avg_vol = float(stats.avg_volume) if stats and stats.avg_volume is not None else 0.0
            days_rem = float(stats.days_remaining) if stats and stats.days_remaining is not None else 0.0
            fits_on_mkt = (total_stock / qty) if qty else 0
            # Set hulls for all rows based on the hull's total_volume_remain
            hulls = hull_stock

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
                    "hulls": hulls,
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
        conn.commit()
    doctrines_engine.dispose()
    stats_engine.dispose()
    logger.info(f"Rebuilt doctrines rows for fit_id {fit_id} ({len(components)} components)")

def add_doctrine_fits_to_wcmkt(df: pd.DataFrame, remote: bool = False):

    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    print(db.alias + " " + " " + str(remote))
    session = Session(engine)
    with session.begin():
        for index, row in df.iterrows():
            fit = DoctrineFitItems(doctrine_name=row["doctrine_name"], fit_name=row["fit_name"], ship_type_id=row["ship_type_id"], ship_name=row["ship_name"], fit_id=row["fit_id"], doctrine_id=row["doctrine_id"], target=row["target"])
            session.add(fit)
            print(f"Added {fit.fit_name} to doctrine_fits table")
    session.commit()
    session.close()
    engine.dispose()

def check_doctrine_fits_in_wcmkt(doctrine_id: int, remote: bool = False)->pd.DataFrame:
    db = DatabaseConfig("wcmkt")
    print(db.alias + " " + " " + str(remote))
    engine = db.remote_engine if remote else db.engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM doctrine_fits WHERE doctrine_id = :doctrine_id")
        df = pd.read_sql_query(stmt, conn, params={"doctrine_id": doctrine_id})
    return df

def add_doctrine_fit_to_doctrines_table(df: pd.DataFrame, fit_id: int, ship_id: int, ship_name: str, remote: bool = False):
    db = DatabaseConfig("wcmkt")
    print(db.alias + " " + " " + str(remote))
    engine = db.remote_engine if remote else db.engine
    session = Session(bind=engine)

    with session.begin():
        for index, row in df.iterrows():
            try:
                type_name = get_type_name(row["type_id"])
            except Exception as e:
                logger.error(f"Error getting type name for {row['type_id']}: {e}")
                type_name = "Unknown"
                continue

            fit = Doctrines(fit_id=fit_id, ship_id=ship_id, ship_name=ship_name, type_id=row["type_id"], type_name=type_name, fit_qty=row["quantity"])
            session.add(fit)
            print(f"Added {fit.type_name} to doctrines table")
    session.commit()
    session.close()
    engine.dispose()
    print(f"Added {len(df)} rows to doctrines table")

def clean_doctrines_table(remote: bool = False):
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    session = Session(bind=engine)
    with session.begin():
        session.execute(text("DROP TABLE IF EXISTS doctrines"))
        session.commit()
    session.close()
    Base.metadata.create_all(engine)
    engine.dispose()
    print("Tables created")

def add_doctrines_to_table(df: pd.DataFrame, remote: bool = False):
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    clean_doctrines_table(remote)
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    session = Session(bind=engine)
    with session.begin():
        for index, row in df.iterrows():
            fit = Doctrines(**row)
            session.add(fit)
            print(f"Added {fit.type_name} to doctrines table")
    session.commit()
    session.close()
    engine.dispose()
    print(f"Added {len(df)} rows to doctrines table")

def check_doctrines_table(remote: bool = False):
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    session = Session(bind=engine)
    row_count = 0
    with session.begin():
        result = session.execute(select(Doctrines))
        for row in result:
            print(row)
            row_count += 1
    session.close()
    engine.dispose()
    print(f"Doctrines table checked, {row_count} rows found")

def get_doctrines_table(remote: bool = False) -> pd.DataFrame:
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM doctrines")
        df = pd.read_sql_query(stmt, conn)
    return df

def get_fit_by_id(fit_id: int, remote: bool = False) -> pd.DataFrame:
    db = DatabaseConfig("wcmkt")
    engine = db.remote_engine if remote else db.engine
    with engine.connect() as conn:
        stmt = text("SELECT * FROM doctrines WHERE fit_id = :fit_id")
        df = pd.read_sql_query(stmt, conn, params={"fit_id": fit_id})
    return df

def replace_doctrines_table(df: pd.DataFrame, remote: bool = False):
    df = df.rename(columns={"quantity": "fit_qty"})
    add_doctrines_to_table(df, remote=True)
    check_doctrines_table(remote=True)

def ensure_friendly_name_column(db_alias: str = "wcmkt", remote: bool = False) -> bool:
    """
    Ensure the friendly_name column exists on doctrine_fits.
    Uses PRAGMA table_info to check, then ALTER TABLE ADD COLUMN if missing.

    Returns:
        True if column exists (or was created), False on error
    """
    engine = _get_engine(db_alias, remote)
    try:
        with engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(doctrine_fits)")).fetchall()
            col_names = [c[1] for c in cols]
            if "friendly_name" not in col_names:
                conn.execute(text("ALTER TABLE doctrine_fits ADD COLUMN friendly_name TEXT DEFAULT NULL"))
                conn.commit()
                logger.info(f"Added friendly_name column to doctrine_fits ({db_alias}, remote={remote})")
            return True
    except Exception as e:
        logger.error(f"Failed to ensure friendly_name column on {db_alias}: {e}")
        return False
    finally:
        engine.dispose()


def update_doctrine_friendly_name(
    doctrine_id: int,
    friendly_name: str,
    db_alias: str = "wcmkt",
    remote: bool = False,
) -> bool:
    """
    Update the friendly_name for all doctrine_fits rows matching doctrine_id.
    Friendly names are a doctrine-level property — every fit in the doctrine
    shares the same friendly name.

    Returns:
        True if rows were updated, False if no matching rows
    """
    engine = _get_engine(db_alias, remote)
    with engine.connect() as conn:
        result = conn.execute(
            text("UPDATE doctrine_fits SET friendly_name = :name WHERE doctrine_id = :doctrine_id"),
            {"doctrine_id": doctrine_id, "name": friendly_name},
        )
        conn.commit()
        rows_affected = result.rowcount
    engine.dispose()

    if rows_affected > 0:
        logger.info(f"Updated friendly_name to '{friendly_name}' for doctrine_id {doctrine_id} ({rows_affected} rows)")
        return True
    else:
        logger.warning(f"No rows found for doctrine_id {doctrine_id}")
        return False


def populate_friendly_names_from_json(
    json_path: str,
    db_alias: str = "wcmkt",
    remote: bool = False,
) -> int:
    """
    Bulk-update friendly_name from a doctrine_names.json file.
    Keys on (fit_id, doctrine_id) to match the correct row.

    Returns:
        Number of rows updated
    """
    import json

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    engine = _get_engine(db_alias, remote)
    updated = 0
    with engine.connect() as conn:
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
        conn.commit()
    engine.dispose()
    logger.info(f"Populated {updated} friendly_name values from {json_path} ({db_alias}, remote={remote})")
    return updated


def sync_friendly_names_to_remote(
    source_alias: str = "wcmkt",
    target_alias: str = "wcmkt",
) -> bool:
    """
    Push friendly_name data from a local database to a remote Turso database.
    Reads (fit_id, doctrine_id, friendly_name) from the local source, then
    updates matching rows on the target remote.

    Args:
        source_alias: Local database to read friendly_names from (default: wcmkt)
        target_alias: Remote database to push to (default: wcmkt)

    Returns:
        True on success, False on failure
    """
    # Read local friendly_name data
    source_engine = _get_engine(source_alias, remote=False)
    with source_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT fit_id, doctrine_id, friendly_name FROM doctrine_fits WHERE friendly_name IS NOT NULL")
        ).fetchall()
    source_engine.dispose()

    if not rows:
        logger.warning(f"No friendly_name data found in local {source_alias}")
        return False

    # Get remote engine for target
    target_db = DatabaseConfig(target_alias)
    try:
        remote_engine = target_db.remote_engine
    except (KeyError, Exception) as e:
        logger.warning(f"No remote engine for {target_alias}, skipping remote sync: {e}")
        return False

    # Ensure column exists on remote
    try:
        with remote_engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(doctrine_fits)")).fetchall()
            col_names = [c[1] for c in cols]
            if "friendly_name" not in col_names:
                conn.execute(text("ALTER TABLE doctrine_fits ADD COLUMN friendly_name TEXT DEFAULT NULL"))
                conn.commit()
                logger.info(f"Added friendly_name column to remote doctrine_fits ({target_alias})")
    except Exception as e:
        logger.error(f"Failed to ensure friendly_name column on remote {target_alias}: {e}")
        return False

    # Bulk-update from local data
    updated = 0
    with remote_engine.connect() as conn:
        for row in rows:
            result = conn.execute(
                text(
                    "UPDATE doctrine_fits SET friendly_name = :name "
                    "WHERE fit_id = :fit_id AND doctrine_id = :doctrine_id"
                ),
                {
                    "fit_id": row[0],
                    "doctrine_id": row[1],
                    "name": row[2],
                },
            )
            updated += result.rowcount
        conn.commit()

    remote_engine.dispose()
    logger.info(f"Synced {updated} friendly_name values to remote {target_alias} (from local {source_alias})")
    return True


if __name__ == "__main__":
    pass
