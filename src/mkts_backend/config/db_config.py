import os
from sqlalchemy import create_engine, text
import pandas as pd
from typing import Optional, TYPE_CHECKING

import turso
import turso.sync as tursosync
from dotenv import load_dotenv
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.settings_service import SettingsService
from datetime import datetime
from time import perf_counter
from pathlib import Path

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

load_dotenv()

logger = configure_logging(__name__)

# Every file pyturso keeps for one database: the database itself plus the WAL,
# shared-memory, sync-metadata, CDC change-queue and revert-WAL sidecars. They
# must be deleted together — a stale change queue or WAL left beside a freshly
# pulled database replays local state that is no longer there. Same set as
# dbdeltest.sh.
DB_FILE_SUFFIXES = ("", "-shm", "-wal", "-info", "-changes", "-wal-revert")

# Older names for market sections, still passed by some call sites and CLI
# invocations. "wcmkt" means "the default market" and is resolved separately
# because markets.default is configurable.
LEGACY_MARKET_SYNONYMS = {"north": "deployment"}


class DatabaseConfig:
    _service = SettingsService()
    # Routing for every database — per-market ([markets.*]) and
    # market-independent ([shared.*]: sde, fittings, buildcost, testing) —
    # comes entirely from the settings service, the single source of truth.
    # Materialized at import time: a malformed section (missing
    # database_alias/database_file, or a duplicate database_alias) fails here
    # with a clear, section-named error from database_routing() rather than a
    # cryptic KeyError or a silently mis-routed database later on.
    _routing = _service.database_routing()

    _db_paths = {alias: r["file"] for alias, r in _routing.items()}

    _db_turso_urls = {
        f"{alias}_turso": os.getenv(r["turso_url_env"])
        for alias, r in _routing.items() if r["turso_url_env"]
    }

    _db_turso_auth_tokens = {
        f"{alias}_turso": os.getenv(r["turso_token_env"])
        for alias, r in _routing.items() if r["turso_token_env"]
    }

    def __init__(
        self,
        alias: str | None = None,
        dialect: str = "sqlite+turso",
        sync_dialect: str = "sqlite+turso_sync",
        market_context: Optional["MarketContext"] = None,
    ):
        """
        Initialize database configuration.

        Args:
            alias: Database alias (e.g., "wcmkt", "primary", or a market's
                   configured database_alias). Legacy names map to the default market.
                   If market_context is provided, this is ignored.
            dialect: SQLAlchemy dialect string.
            market_context: Optional MarketContext that provides all config values.
                           When provided, takes precedence over alias parameter.
        """
        if market_context is not None:
            # Use MarketContext for configuration (preferred method)
            self.alias = market_context.database_alias
            self.path = market_context.database_file
            self.turso_url = market_context.turso_url
            self.token = market_context.turso_token
            logger.info(
                f"DatabaseConfig initialized from MarketContext: {market_context.name}"
            )
        else:
            # Use the property so MKTS_ENVIRONMENT set by --env=<env> after
            # module import is still picked up. Reading self.settings directly
            # would freeze on the cached TOML default.
            env = SettingsService().environment
            # A market section name ([markets.<name>]) resolves to that
            # market's database_alias, so adding a market to settings.toml
            # needs no code change here. LEGACY_MARKET_SYNONYMS keeps older
            # names working for existing CLI invocations and call sites.
            if alias == "wcmkt":
                market_alias = self._service.default_market_alias
            else:
                market_alias = LEGACY_MARKET_SYNONYMS.get(alias, alias)
            if env == 'development':
                alias = self._service.shared_testing["database_alias"]
            elif alias is None:
                alias = self._service.default_market_db_alias()
            elif market_alias in self._service.market_aliases:
                alias = self._service.market_db_alias(market_alias)
            if alias not in self._db_paths:
                raise ValueError(
                    f"Unknown database alias '{alias}'. Available: {list(self._db_paths.keys())}"
                )

            self.alias = alias
            self.path = self._db_paths[alias]
            self.turso_url = self._db_turso_urls.get(f"{self.alias}_turso")
            self.token = self._db_turso_auth_tokens.get(f"{self.alias}_turso")

        self.url = f"{dialect}:///{self.path}"
        self.sync_url = f"{sync_dialect}:///{self.path}"
        self._engine = None
        self._turso_connect: turso.Connection
        self._turso_sync_connection: tursosync.ConnectionSync

    @property
    def engine(self):
        if self._engine is None:
            if self.turso_url:
                # Sync-managed DBs must only be accessed through sync-aware
                # connections: a plain connection auto-checkpoints the WAL at
                # 1000 frames, destroying the baseline conn.pull() needs and
                # panicking turso core (wal.rs frame_watermark assertion).
                self._engine = create_engine(
                    self.sync_url,
                    connect_args={
                        "remote_url": self.turso_url,
                        "auth_token": self.token,
                    },
                )
            else:
                self._engine = create_engine(self.url)
        return self._engine

    @property
    def remote_engine(self):
        # Writes land locally and reach Turso via push(), so the local and
        # "remote" engines are the same sync-dialect engine.
        return self.engine

    @property
    def turso_sync_connection(self) -> tursosync.ConnectionSync:
        self._turso_sync_connection = tursosync.connect(
            self.path,
            remote_url=self.turso_url,
            auth_token=self.token,
        )
        return self._turso_sync_connection

    @property
    def turso_local_connect(self):
        self._turso_connect = turso.connect(self.path)
        return self._turso_connect

    def sync(self):
        logger.info(f"========== START SYNC {self.alias} ({self.path}) ==========")
        logger.info(f"Start sync for {self.alias} at {self.path}")
        logger.debug(f"using url: {self.turso_url}")
        self.pull()

    def push(self):
        push_start = perf_counter()
        conn = self.turso_sync_connection
        with conn:
            conn.push()
            conn.checkpoint()
            logger.debug(conn.stats())
        conn.close()
        push_end = perf_counter()
        logger.info(f"Database: {self.alias} ({self.path})")
        logger.info(f"Sync time: {push_end - push_start:.1f} seconds")
        logger.info(
            "========================================================================="
        )

    def validate_sync(self) -> bool:
        """True when no local writes are waiting to reach Turso.

        pyturso is local-first: writes land in the local CDC queue and reach
        the remote only on push(). ``cdc_operations`` counts the rows in that
        queue past the last pushed change, so a non-zero count means the local
        database is ahead of Turso and push() has not run (or failed).
        """
        conn = self.turso_sync_connection
        with conn:
            stats = conn.stats()
        conn.close()
        pending = stats.cdc_operations
        logger.info(f"Database: {self.alias} ({self.path})")
        logger.info(f"Pending operations to push: {pending}")
        logger.info(f"Last pull: {datetime.fromtimestamp(stats.last_pull_unix_time)}")
        logger.info(f"Last push: {datetime.fromtimestamp(stats.last_push_unix_time)}")
        return pending == 0

    def pull(self):
        pull_start = perf_counter()
        conn = self.turso_sync_connection
        with conn:
            conn.pull()
            conn.checkpoint()
            logger.debug(conn.stats())
        conn.close()
        pull_end = perf_counter()
        logger.info(f"Database: {self.alias} ({self.path})")
        logger.info(f"Sync time: {pull_end - pull_start:.1f} seconds")
        logger.info(
            "========================================================================="
        )


    def get_table_list(self, local_only: bool = True) -> list[tuple]:
        if local_only:
            engine = self.engine
            with engine.connect() as conn:
                stmt = text("PRAGMA table_list")
                result = conn.execute(stmt)
                tables = result.fetchall()
                table_list = [
                    table.name for table in tables if "sqlite" not in table.name
                ]
                return table_list
        else:
            engine = self.remote_engine
            with engine.connect() as conn:
                stmt = text("PRAGMA table_list")
                result = conn.execute(stmt)
                tables = result.fetchall()
                table_list = [
                    table.name for table in tables if "sqlite" not in table.name
                ]
                return table_list

    def get_table_columns(
        self, table_name: str, local_only: bool = True, full_info: bool = False
    ) -> list[dict]:
        if local_only:
            engine = self.engine
        else:
            engine = self.remote_engine

        with engine.connect() as conn:
            stmt = text(f"PRAGMA table_info({table_name})")
            result = conn.execute(stmt)
            columns = result.fetchall()
            if full_info:
                column_info = []
                for col in columns:
                    column_info.append(
                        {
                            "cid": col.cid,
                            "name": col.name,
                            "type": col.type,
                            "notnull": col.notnull,
                            "dflt_value": col.dflt_value,
                            "pk": col.pk,
                        }
                    )
            else:
                column_info = [col.name for col in columns]

            return column_info

    def get_table_length(self, table: str):
        with self.remote_engine.connect() as conn:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
            if result is None:
                return 0
            else:
                return result[0]

    def get_watchlist(self):
        engine = self.engine
        with engine.connect() as conn:
            df = pd.read_sql_table("watchlist", conn)
        conn.close()
        return df

    def verify_db_exists(self) -> bool:
        """
        Verifies database and metadata are in a consistent state.

        Cases handled:
        1. Neither exists → sync() to initialize
        2. Both exist → valid state, return True
        3. DB exists without metadata → nuke db then sync
        4. Metadata exists without DB → nuke metadata then sync

        Important: Never call sync() on a db file that lacks its -info file.
        It is safe to call sync() when neither file exists.

        Returns:
            True if database is in valid state, False otherwise.
        """
        db_exists = Path(self.path).exists()
        metadata_exists = self.confirm_metadata_exists()

        logger.info(
            f"Verifying db state: db_exists={db_exists}, metadata_exists={metadata_exists}"
        )

        if db_exists and metadata_exists:
            # Case 2: Valid state
            logger.info(f"Database {self.path} is properly initialized")
            return True

        if db_exists and not metadata_exists:
            # Case 3: DB without metadata (improperly created, e.g., bare sqlite.connect)
            # MUST nuke before sync - cannot sync a db without its -info file
            logger.warning(f"DB exists without metadata, nuking: {self.path}")
            if not self.nuke_db():
                logger.error(f"Failed to delete db file: {self.path}")
                return False

        if not db_exists and metadata_exists:
            # Case 4: Orphaned metadata
            logger.warning(f"Orphaned metadata found, removing: {self.path}-info")
            if not self.nuke_db():
                logger.error(f"Failed to delete metadata: {self.path}-info")
                return False

        # Case 1/3/4: Need to sync from remote
        logger.info(f"Initializing database via sync: {self.path}")
        self.sync()

        # Verify sync succeeded
        if Path(self.path).exists() and self.confirm_metadata_exists():
            logger.info(f"Database {self.path} successfully initialized")
            return True
        else:
            logger.error(f"Sync failed to create valid db state: {self.path}")
            return False

    def get_db_credentials_dicts(self):
        return {
            "turso_urls": self._db_turso_urls,
            "turso_tokens": self._db_turso_auth_tokens,
        }

    def needs_init(self) -> bool:
        """
        Pure check: Returns True if database needs initialization.

        A database needs initialization if either the db file or its metadata
        file is missing. Does NOT modify state - use verify_db_exists() for
        initialization with side effects.

        Returns:
            True if database needs initialization, False if properly initialized.
        """
        db_exists = Path(self.path).exists()
        metadata_exists = self.confirm_metadata_exists()
        needs_init = not (db_exists and metadata_exists)
        logger.info(
            f"needs_init check: db_exists={db_exists}, metadata_exists={metadata_exists}, needs_init={needs_init}"
        )
        return needs_init

    def confirm_metadata_exists(self) -> bool:
        """
        Confirms that the database metadata is consistent with the expected schema.
        """
        expected_metadata = f"{self.path}-info"
        expected_metadata = Path(expected_metadata)
        if not expected_metadata.exists():
            return False
        return True

    def nuke_db(self) -> bool:
        """Delete the database file and every pyturso sidecar beside it.

        Returns:
            True if every file was deleted or already absent, False on any error.
        """
        all_ok = True
        for suffix in DB_FILE_SUFFIXES:
            path = Path(f"{self.path}{suffix}")
            if not path.exists():
                continue
            try:
                path.unlink()
                logger.info(f"Deleted {path}")
            except OSError as e:
                logger.error(f"Failed to delete {path}: {e}")
                all_ok = False
        return all_ok

    def get_stats(self):
        with self.turso_sync_connection as conn:
            stats  = conn.stats()
        return stats

if __name__ == "__main__":
    pass
