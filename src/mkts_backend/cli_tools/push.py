"""The one way a CLI command pushes a database alias to Turso.

Under pyturso a committed write sits in the local CDC queue until ``push()``
sends it, so every writing command ends with a push. That "push, then report
the failure" block used to be hand-copied at a dozen call sites in three
slightly different styles: each copy was correct, but a future edit would land
on one of them and not the others.
"""

from rich.console import Console

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)
console = Console()


def push_or_log(alias: str) -> bool:
    """Push ``alias``'s pending local writes to Turso.

    Callers decide what a failure means for their command — this only
    attempts the push and reports it.

    Returns:
        True if the push succeeded, False if it raised (already logged and
        printed to the console).
    """
    try:
        DatabaseConfig(alias).push()
        return True
    except Exception as exc:
        logger.exception(f"{alias}: push failed: {exc}")
        console.print(f"[red]Push failed for {alias}: {exc}[/red]")
        return False
