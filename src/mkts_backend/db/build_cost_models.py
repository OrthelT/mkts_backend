"""SQLAlchemy ORM models for buildcost.db.

Authoritative schema for the structures / rigs / industry_index tables.
`STRUCTURE_COLUMNS` in ``build_cost_utils.py`` is derived from ``Structure``
so column order stays in sync with this model.

``structures`` and ``structure_rigs`` were recreated from these models in
Nov 2026. ``rig_1..rig_3`` duplicate ``structure_rigs`` for one frontend
release; drop them once wcmkts_new reads ``structure_rigs``.
"""

from sqlalchemy import Column, DateTime, Float, Integer, String, text
from sqlalchemy.orm import declarative_base

from mkts_backend.db._update_log_mixin import UpdateLogMixin

BuildCostBase = declarative_base()


class BuildWatchlist(BuildCostBase):
    """Independent, market-agnostic list of buildable items.

    Source of truth — written by ``build-watchlist add|remove|mirror`` and the
    auto-mirror in ``add_watchlist``. Drives the EverRef cost fetch in
    ``update-builder-costs``, which only reads from this table.
    """

    __tablename__ = "build_watchlist"

    type_id = Column(Integer, primary_key=True)
    type_name = Column(String, nullable=True)
    group_name = Column(String, nullable=True)
    category_id = Column(Integer, nullable=True)
    added_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)


class BuilderCosts(BuildCostBase):
    """EverRef manufacturing cost snapshot per buildable type_id.

    Market-independent. One row per type_id. ``fetched_at`` is the timestamp
    of the latest successful EverRef fetch and is replaced on every refresh.
    """

    __tablename__ = "builder_costs"

    type_id = Column(Integer, primary_key=True)
    total_cost_per_unit = Column(Float, nullable=False)
    time_per_unit = Column(Float, nullable=False)
    me = Column(Integer, nullable=False)
    runs = Column(Integer, nullable=False)
    fetched_at = Column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"BuilderCosts(type_id={self.type_id!r}, "
            f"total_cost_per_unit={self.total_cost_per_unit!r}, "
            f"time_per_unit={self.time_per_unit!r}, me={self.me!r}, "
            f"runs={self.runs!r}, fetched_at={self.fetched_at!r})"
        )


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
    owner = Column(String, nullable=True)
    owner_id = Column(Integer, nullable=True)
    # 1 when the Structure Engineering Service Module is online. The
    # Supercapital Shipyard also builds capitals (SDE assembly line 176).
    has_manufacturing_plant = Column(Integer, nullable=False, server_default=text("0"))
    has_capital_shipyard = Column(Integer, nullable=False, server_default=text("0"))
    has_supercapital_shipyard = Column(Integer, nullable=False, server_default=text("0"))
    is_open = Column(Integer, nullable=False, server_default=text("0"))


class StructureRig(BuildCostBase):
    """One row per rig fitted to a structure; names come from a join on ``rigs``."""

    __tablename__ = "structure_rigs"

    structure_id = Column(Integer, primary_key=True)
    rig_type_id = Column(Integer, primary_key=True)


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


class UpdateLog(UpdateLogMixin, BuildCostBase):
    """Per-database update timestamp ledger for ``buildcost.db``.

    The wcmkts_new frontend probes ``MAX(timestamp) WHERE table_name='buildcost'``
    to decide whether to trigger a sync. Column shape is owned by
    ``UpdateLogMixin`` and shared with the wcmktprod ``UpdateLog`` in
    ``db/models.py``; one class per ``Base`` because each base is bound to a
    different physical database.
    """

    def __repr__(self) -> str:
        return (
            f"updatelog(table_name={self.table_name!r}, "
            f"timestamp={self.timestamp!r})"
        )
