"""Multi-replica unit of work for pyturso-managed SQLite databases.

Under pyturso every write commits to a local replica and sits in its CDC queue
until ``push()`` sends it to Turso. ``ReplicaUnitOfWork`` owns replica
resolution, engine caching/disposal, per-alias transactions, and end-of-command
pushing in one place:

    with ReplicaUnitOfWork() as uow:
        with uow.tx("fittings") as conn:
            ensure_doctrine_link(conn, doctrine_id, fit_id)
        for alias in target_aliases:
            with uow.tx(alias) as conn:
                upsert_doctrine_fits(conn, doctrine_fit, market_flag=market_flag)

Guarantees:
- Each canonical database alias (``db.alias``) is opened at most once per unit
  of work, even if referenced through synonyms such as ``"wcmkt"`` and
  ``"wcmktnewkeep"``.
- Re-entering ``uow.tx(alias)`` while already inside a transaction for that
  alias participates in the active transaction without deadlocking SQLite.
- ``uow.read(alias)`` opens a read-only connection without marking the alias
  dirty for push (and reuses an active transaction connection if inside one).
- On clean exit, every opened engine is disposed and every alias with at least
  one committed write transaction (``self.touched``) is pushed once, in sorted
  canonical-alias order, using the exact ``DatabaseConfig`` instance that was
  written. An alias whose sole transaction rolled back is never added to
  ``self.touched`` and is not pushed; an alias with an earlier commit and a
  later rollback is still pushed so committed local CDC rows converge, while
  ``uow.failed`` records the rollback and ``uow.ok`` is ``False``.
- A push failure for one alias is logged and recorded in ``uow.failed_pushes``
  without aborting pushes for the remaining aliases.
- If an uncaught ``Exception`` escapes the ``with`` block, all engines are
  still disposed and ``self.touched`` is still pushed: those transactions
  already committed to the local CDC queue, so skipping the push would only
  strand them. ``KeyboardInterrupt`` and ``SystemExit`` skip the pushes.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy.engine import Connection, Engine

from mkts_backend.config import db_config
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.db.push import push_db


class ReplicaUnitOfWork:
    """Coordinate transactions, engine disposal, and Turso pushes across replicas."""

    def __init__(
        self,
        *,
        auto_push: bool = True,
        skip_failed_on_push: bool = False,
        db_factory: Callable[[str], DatabaseConfig] | None = None,
    ) -> None:
        self._auto_push = auto_push
        self._skip_failed_on_push = skip_failed_on_push
        self._db_factory = db_factory

        self._dbs: dict[str, DatabaseConfig] = {}
        self._alias_map: dict[str, str] = {}
        self._engines_opened: set[str] = set()
        self._active_conns: dict[str, Connection] = {}

        self.touched: set[str] = set()
        self.failed: set[str] = set()
        self.pushed: set[str] = set()
        self.failed_pushes: set[str] = set()

    def _make_db(self, alias: str) -> DatabaseConfig:
        if self._db_factory is not None:
            return self._db_factory(alias)
        return db_config.DatabaseConfig(alias)

    def db(self, alias: str) -> DatabaseConfig:
        """Resolve and cache the ``DatabaseConfig`` for ``alias``.

        Deduplicates synonyms (e.g. ``"wcmkt"`` and the concrete primary DB
        alias) by canonical ``db.alias`` so one physical replica gets one
        engine and one push.
        """
        cached_canonical = self._alias_map.get(alias)
        if cached_canonical is not None:
            return self._dbs[cached_canonical]

        resolved = self._make_db(alias)
        canonical = getattr(resolved, "alias", alias)
        existing = self._dbs.get(canonical)
        if existing is not None:
            self._alias_map[alias] = canonical
            return existing

        self._dbs[canonical] = resolved
        self._alias_map[alias] = canonical
        self._alias_map[canonical] = canonical
        return resolved

    def engine(self, alias: str) -> Engine:
        """Return the cached engine for ``alias`` and register it for disposal."""
        db = self.db(alias)
        engine = db.engine
        self._engines_opened.add(getattr(db, "alias", alias))
        return engine

    @contextmanager
    def read(self, alias: str) -> Iterator[Connection]:
        """Open a read-only connection on ``alias`` without marking it dirty."""
        db = self.db(alias)
        canonical = getattr(db, "alias", alias)
        active = self._active_conns.get(canonical)
        if active is not None:
            yield active
            return

        with self.engine(canonical).connect() as conn:
            yield conn

    @contextmanager
    def tx(self, alias: str, *, auto_touch: bool = True) -> Iterator[Connection]:
        """Open a transactional connection on ``alias`` and mark it for push on commit.

        Re-entering ``tx()`` for an alias that already has an active
        transaction yields the existing connection without starting a second
        transaction. If the replica cannot be opened or the outermost
        transaction rolls back, the alias is recorded in ``self.failed`` and
        the exception propagates.

        Pass ``auto_touch=False`` when the transaction may be a no-op and the
        caller wants to call :meth:`mark_touched` only after confirming rows
        changed — this avoids ever calling ``touched.discard()`` and erasing
        an earlier committed transaction on the same alias.
        """
        try:
            db = self.db(alias)
        except Exception:
            self.failed.add(alias)
            raise
        canonical = getattr(db, "alias", alias)
        active = self._active_conns.get(canonical)
        if active is not None:
            yield active
            return

        try:
            with self.engine(canonical).begin() as conn:
                self._active_conns[canonical] = conn
                try:
                    yield conn
                finally:
                    self._active_conns.pop(canonical, None)
        except Exception:
            self.failed.add(canonical)
            raise
        else:
            if auto_touch:
                self.touched.add(canonical)

    def mark_touched(self, alias: str) -> str:
        """Mark ``alias`` as having committed writes that need pushing."""
        canonical = getattr(self.db(alias), "alias", alias)
        self.touched.add(canonical)
        return canonical

    def mark_failed(self, alias: str) -> str:
        """Mark ``alias`` as failed so it is excluded from pushes (by default)."""
        canonical = getattr(self.db(alias), "alias", alias)
        self.failed.add(canonical)
        return canonical

    def dispose_all(self) -> None:
        """Dispose every engine opened through this unit of work."""
        for canonical in list(self._engines_opened):
            db = self._dbs.get(canonical)
            if db is not None:
                db.engine.dispose()
        self._engines_opened.clear()

    def push_touched(self) -> bool:
        """Push every eligible touched replica once, in sorted canonical order."""
        targets = (
            sorted(self.touched - self.failed)
            if self._skip_failed_on_push
            else sorted(self.touched)
        )
        for canonical in targets:
            if push_db(self._dbs[canonical]):
                self.pushed.add(canonical)
            else:
                self.failed_pushes.add(canonical)
        return not self.failed_pushes

    @property
    def push_failed(self) -> bool:
        """True if any replica push failed."""
        return bool(self.failed_pushes)

    @property
    def ok(self) -> bool:
        """True if no transactions failed and no pushes failed."""
        return not self.failed and not self.failed_pushes

    def __enter__(self) -> ReplicaUnitOfWork:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            self.dispose_all()
            if self._auto_push and (exc_type is None or issubclass(exc_type, Exception)):
                self.push_touched()
        finally:
            self.dispose_all()
        return False
