"""Low-level Turso replica push helper shared by ``ReplicaUnitOfWork`` and CLI tools."""

from __future__ import annotations

from rich.console import Console

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)
console = Console()


def push_db(db: DatabaseConfig) -> bool:
    """Push ``db``'s pending local writes to Turso.

    Callers decide what a failure means for their command — this only
    attempts the push and reports it.

    Returns:
        True if the push succeeded, False if it raised (already logged and
        printed to the console).
    """
    try:
        db.push()
        return True
    except Exception as exc:
        logger.exception(f"{db.alias}: push failed: {exc}")
        console.print(f"[red]Push failed for {db.alias}: {exc}[/red]")
        return False
