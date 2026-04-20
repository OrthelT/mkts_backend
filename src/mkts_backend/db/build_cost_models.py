"""SQLAlchemy ORM models for buildcost.db.

Mirrors wcmkts_new/build_cost_models.py. Independent Base so these models
can be used for targeted `create_all` calls without pulling in market models.
"""

from sqlalchemy import Column, Float, Integer, String
from sqlalchemy.orm import declarative_base

BuildCostBase = declarative_base()


class Structure(BuildCostBase):
    __tablename__ = "structures"

    structure_id = Column(Integer, primary_key=True)
    system = Column(String, nullable=True)
    structure = Column(String, nullable=True)
    system_id = Column(Integer, nullable=True)
    rig_1 = Column(String, nullable=True)
    rig_2 = Column(String, nullable=True)
    rig_3 = Column(String, nullable=True)
    structure_type = Column(String, nullable=True)
    structure_type_id = Column(Integer, nullable=True)
    tax = Column(Float, nullable=True)
    region = Column(String, nullable=True)
    region_id = Column(Integer, nullable=True)


class IndustryIndex(BuildCostBase):
    __tablename__ = "industry_index"

    solar_system_id = Column(Integer, primary_key=True)
    manufacturing = Column(Float)
    researching_time_efficiency = Column(Float)
    researching_material_efficiency = Column(Float)
    copying = Column(Float)
    invention = Column(Float)
    reaction = Column(Float)


class Rig(BuildCostBase):
    __tablename__ = "rigs"

    type_id = Column(Integer, primary_key=True)
    type_name = Column(String)
    icon_id = Column(Integer)
