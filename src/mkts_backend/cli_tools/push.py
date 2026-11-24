"""The one way a CLI command pushes a database alias to Turso.

Under pyturso a committed write sits in the local CDC queue until ``push()``
sends it, so every writing command ends with a push. That "push, then report
the failure" block used to be hand-copied at a dozen call sites in three
slightly different styles: each copy was correct, but a future edit would land
on one of them and not the others.
"""

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.db.push import console, logger, push_db


def push_or_log(alias: str) -> bool:
    """Push ``alias``'s pending local writes to Turso.

    Returns:
        True if the push succeeded, False if it raised (already logged and
        printed to the console).
    """
    return push_db(DatabaseConfig(alias))


__all__ = ["console", "logger", "push_db", "push_or_log"]
