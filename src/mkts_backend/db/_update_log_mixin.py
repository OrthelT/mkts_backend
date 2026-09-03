"""Shared schema for the per-database ``updatelog`` ledger.

Both ``buildcost.db`` and the wcmktprod market DB carry an ``updatelog`` table
that the wcmkts_new frontend probes with
``MAX(timestamp) WHERE table_name=<name>`` to decide whether to trigger a sync.
The two tables must have identical column shape; this mixin is the single
source of truth so the schemas cannot drift independently. Each declarative
``Base`` gets a thin subclass because each base is bound to a different
physical database — but column definitions are owned here.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, String


class UpdateLogMixin:
    __tablename__ = "updatelog"

    # table_name is the natural key — one row per table. Using it as the
    # PRIMARY KEY (instead of a surrogate autoincrement id) makes
    # ON CONFLICT(table_name) valid on every DB and removes the id whose
    # churn under delete+insert poisoned the Turso CDC push queue.
    table_name = Column(String, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
