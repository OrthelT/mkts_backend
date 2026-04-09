from dataclasses import dataclass, field

from typing import Union
from numpy._core.multiarray import RAISE
from numpy.strings import isdigit, isnumeric
from sqlalchemy.orm import query
from mkts_backend.config.config import DatabaseConfig
from sqlalchemy import false, text
from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)

@dataclass(init=false)
class TypeInfo:
    type_id: int
    type_name: str
    group_name: str
    category_name: str
    category_id: int
    group_id: int
    volume: int
    
    def __init__(self, value: Union[int, str]):
        if isinstance(value, int) or isnumeric(value):
            self.type_id = value
            self._load_by_id()
        elif isinstance(value, str):
            self.type_id = self.from_name(value)
            self._load_by_id()
        else:
            logger.error("A type_id or type_name is required")
            raise ValueError ("TypeInfo requires an int or str value")

    @classmethod
    def from_name(self, type_name) -> int:

        db = DatabaseConfig("sde")
        stmt = text("""
            SELECT typeID
            FROM inv_info
            WHERE typeName = :type_name
            LIMIT 1
        """)
        with db.engine.connect() as conn:
            row = conn.execute(stmt, {"type_name": type_name}).mappings().first()

        if row is None:
            raise ValueError(f"type_name not found: {type_name}")

        return row["typeID"]

    def _load_by_id(self) -> None:
        db = DatabaseConfig("sde")
        stmt = text("""
            SELECT
                typeName,
                groupName,
                categoryName,
                categoryID,
                groupID,
                volume
            FROM inv_info
            WHERE typeID = :type_id
            LIMIT 1
        """)
        with db.engine.connect() as conn:
            row = conn.execute(stmt, {"type_id": self.type_id}).mappings().first()

        if row is None:
            raise ValueError(f"type_id not found: {self.type_id}")

        self.type_name = row["typeName"]
        self.group_name = row["groupName"]
        self.category_name = row["categoryName"]
        self.category_id = row["categoryID"]
        self.group_id = row["groupID"]
        self.volume = row["volume"]

    def to_dict(self):
        type_dict = {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "group_name": self.group_name,
            "category_name": self.category_name,
            "category_id": self.category_id,
            "group_id": self.group_id,
            "volume": self.volume
        }
        return type_dict

def get_type_from_list(type_list: list[int]) -> list[TypeInfo]:
    type_info_list = []
    for type_id in type_list:
        type_info = TypeInfo(type_id)
        type_info_list.append(type_info)
    return type_info_list

if __name__ == "__main__":
   pass 
