from mkts_backend.config.db_config import DatabaseConfig
from sqlalchemy import text, select, delete, func
from sqlalchemy.orm import Session
from mkts_backend.db.models import Doctrines
from mkts_backend.config.logging_config import configure_logging
import pandas as pd
logger = configure_logging(__name__)

mkt_db = DatabaseConfig("wcmkt")
fits_db = DatabaseConfig("fittings")
sde_db = DatabaseConfig("sde")


def get_fit_items(fit_id: int, ship_id: int, ship_name: str)->list[Doctrines]:
    # Aggregate by type_id and sum quantities
    stmt = text("""
        SELECT type_id, SUM(quantity) as total_quantity
        FROM fittings_fittingitem
        WHERE fit_id = :fit_id
        GROUP BY type_id
    """)
    items = []
    engine = fits_db.engine
    with engine.connect() as conn:
        result = conn.execute(stmt, {"fit_id": fit_id})
        rows = result.fetchall()
        for row in rows:
            item = Doctrines(
                fit_id=fit_id,
                ship_id=ship_id,
                type_id=row.type_id,
                ship_name=ship_name,
                fit_qty=row.total_quantity,  # This is now the aggregated sum
            )
            items.append(item)
        items.append(Doctrines(
            fit_id=fit_id,
            ship_id=ship_id,
            type_id=ship_id,
            ship_name=ship_name,
            fit_qty=1,
        ))
    return items

def update_items(items: list[Doctrines]):
    updated_items = []
    engine = sde_db.engine
    with engine.connect() as conn:
        for item in items:
            result = conn.execute(text("SELECT * FROM inv_info WHERE typeID = :type_id"), {"type_id": item.type_id})
            new_item = result.fetchone()
            if new_item is None:
                logger.warning(f"TypeID {item.type_id} not found in inv_info; skipping")
                continue

            item.type_name = new_item.typeName
            item.group_name = new_item.groupName
            item.category_name = new_item.categoryName
            item.category_id = new_item.categoryID
            item.group_id = new_item.groupID
            updated_items.append(item)
    return updated_items

def add_items_to_doctrines_table(items: list[Doctrines], remote: bool = False):
    engine = mkt_db.remote_engine if remote else mkt_db.engine
    session = Session(engine)
    with session.begin():
        try:
            added_count = 0
            skipped_count = 0

            for item in items:
                # Check if this fit_id + type_id combination already exists
                existing = session.scalar(
                    select(Doctrines).where(
                        Doctrines.fit_id == item.fit_id,
                        Doctrines.type_id == item.type_id
                    )
                )

                if existing:
                    logger.info(f"Skipping duplicate: {item.type_name} (type_id: {item.type_id}) already exists for fit_id {item.fit_id}")
                    skipped_count += 1
                else:
                    session.add(item)
                    logger.info(f"Added {item.type_name} to doctrines {item.fit_id}")
                    added_count += 1

            session.commit()
            logger.info(f"Completed: {added_count} items added, {skipped_count} duplicates skipped")

        except Exception as e:
            session.rollback()
            logger.error(f"Error adding items to doctrines table: {e}")
            raise
        finally:
            session.close()
            engine.dispose()

def add_fit_to_doctrines_table(fit_id: int, ship_id: int, ship_name: str, remote: bool = False, dry_run: bool = False)->list[Doctrines] | None:
    """
    Add a fit to the doctrines table
    Args:
        fit_id: int
        ship_id: int
        ship_name: str
        remote: bool
        dry_run: bool

    Returns:
        list[Doctrines] | None
    Example:
        add_fit_to_doctrines_table(494, 33157, "Hurricane Fleet Issue", remote=True, dry_run=False)

    """
    items = get_fit_items(fit_id, ship_id, ship_name)
    updated_items = update_items(items)
    if dry_run:
        return updated_items
    else:
      add_items_to_doctrines_table(updated_items, remote)

def select_doctrines_table(fit_id: int, remote: bool = False)->list[dict]:
    engine = mkt_db.remote_engine if remote else mkt_db.engine
    session = Session(engine)
    items = []

    with session.begin():
        result = select(Doctrines).where(Doctrines.fit_id == fit_id)
        for item in session.scalars(result):

            item = item.__dict__
            item.pop('_sa_instance_state')

            items.append(item)
    session.close()
    engine.dispose()
    print(f"Found {len(items)} items in doctrines table")
    return pd.DataFrame(items)

def delete_doctrines_table(fit_id: int, remote: bool = False):
    engine = mkt_db.remote_engine if remote else mkt_db.engine
    session = Session(engine)
    with session.begin():
        count = session.execute(select(func.count(Doctrines.fit_id)).where(Doctrines.fit_id == fit_id))
        print(f"Count of {fit_id} in doctrines table: {count.scalar()}")
        session.execute(delete(Doctrines).where(Doctrines.fit_id == fit_id))
        session.commit()
    session.close()
    engine.dispose()
    print(f"Deleted {fit_id} from doctrines table")

def count_doctrines_table(fit_id: int, remote: bool = False):
    engine = mkt_db.remote_engine if remote else mkt_db.engine
    session = Session(engine)
    with session.begin():
        result = session.execute(select(func.count(Doctrines.fit_id)).where(Doctrines.fit_id == fit_id))
        count = result.scalar()

    session.close()
    engine.dispose()
    print(f"Item from doctrines table: {count}")
    return count
if __name__ == "__main__":
    pass