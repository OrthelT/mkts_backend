import os
from sqlalchemy import create_engine, text
import pandas as pd
import pathlib
from typing import Optional, TYPE_CHECKING

os.environ.setdefault("RUST_LOG", "debug")

import libsql
from dotenv import load_dotenv
from mkts_backend.config.logging_config import configure_logging
from datetime import datetime, timezone
from time import perf_counter
import json
from pathlib import Path
import tomllib

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

load_dotenv()
settings_file = "src/mkts_backend/config/settings.toml"

logger = configure_logging(__name__)

def load_settings(file_path: str = settings_file):
    with open(file_path, "rb") as f:
        settings = tomllib.load(f)
        logger.info(f"Settings loaded from {file_path}")
    return settings

class DatabaseConfig:
    settings = load_settings()
    _production_db_alias = settings["db"]["production_database_alias"]
    _production_db_file = settings["db"]["production_database_file"]
    _testing_db_alias = settings["db"]["testing_database_alias"]
    _testing_db_file = settings["db"]["testing_database_file"]
    _deployment_db_alias = settings["db"].get("deployment_database_alias")
    _deployment_db_file = settings["db"].get("deployment_database_file")


    _db_paths = {
        _testing_db_alias: _testing_db_file,
        "sde": "sde.db",
        "fittings": "wcfitting.db",
        _production_db_alias: _production_db_file,
        _deployment_db_alias: _deployment_db_file,
    }

    _db_turso_urls = {
        _production_db_alias + "_turso": os.getenv("TURSO_WCMKTPROD_URL"),
        _testing_db_alias + "_turso": os.getenv("TURSO_WCMKTTEST_URL"),
        "sde_turso": os.getenv("TURSO_SDE_URL"),
        "fittings_turso": os.getenv("TURSO_FITTING_URL"),
        _deployment_db_alias + "_turso": os.getenv("TURSO_WCMKTNORTH_URL"),
    }

    _db_turso_auth_tokens = {
        _production_db_alias + "_turso": os.getenv("TURSO_WCMKTPROD_TOKEN"),
        _testing_db_alias + "_turso": os.getenv("TURSO_WCMKTTEST_TOKEN"),
        "sde_turso": os.getenv("TURSO_SDE_TOKEN"),
        "fittings_turso": os.getenv("TURSO_FITTING_TOKEN"),
        _deployment_db_alias + "_turso": os.getenv("TURSO_WCMKTNORTH_TOKEN"),
    }

    def __init__(
        self,
        alias: str = None,
        dialect: str = "sqlite+libsql",
        market_context: Optional["MarketContext"] = None
    ):
        """
        Initialize database configuration.

        Args:
            alias: Database alias (e.g., "wcmkt", "wcmktprod", "wcmktnorth").
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
            logger.info(f"DatabaseConfig initialized from MarketContext: {market_context.name}")
        else:
            # Legacy alias-based initialization (backward compatibility)
            if alias is None:
                alias = "wcmkt"

            if alias == "wcmkt":
                if self.settings["app"]["environment"] == "development":
                    alias = self._testing_db_alias
                else:
                    alias = self._production_db_alias

            if alias not in self._db_paths:
                raise ValueError(
                    f"Unknown database alias '{alias}'. Available: {list(self._db_paths.keys())}"
                )

            self.alias = alias
            self.path = self._db_paths[alias]
            self.turso_url = self._db_turso_urls.get(f"{self.alias}_turso")
            self.token = self._db_turso_auth_tokens.get(f"{self.alias}_turso")

        self.url = f"{dialect}:///{self.path}"
        self._engine = None
        self._remote_engine = None
        self._libsql_connect = None
        self._libsql_sync_connect = None
        self._sqlite_local_connect = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = create_engine(self.url)
        return self._engine

    @property
    def remote_engine(self):
        if self._remote_engine is None:
            turso_url = self._db_turso_urls[f"{self.alias}_turso"]
            auth_token = self._db_turso_auth_tokens[f"{self.alias}_turso"]
            self._remote_engine = create_engine(
                f"sqlite+{turso_url}?secure=true",
                connect_args={
                    "auth_token": auth_token,
                },
            )
        return self._remote_engine

    @property
    def libsql_local_connect(self):
        if self._libsql_connect is None:
            self._libsql_connect = libsql.connect(self.path)
        return self._libsql_connect

    @property
    def libsql_sync_connect(self):
        self._libsql_sync_connect = libsql.connect(
                f"{self.path}", sync_url=self.turso_url, auth_token=self.token
            )
        return self._libsql_sync_connect

    @property
    def sqlite_local_connect(self):
        if self._sqlite_local_connect is None:
            self._sqlite_local_connect = libsql.connect(self.path)
        return self._sqlite_local_connect

    def sync(self):
        conn = self.libsql_sync_connect
        
        # Record db state before and after sync to monitor WAL frame generation
        raw_info = self.read_db_info()
        start_info = json.loads(raw_info) if raw_info else None
        
        logger.info(f"Start sync for {self.alias} at {self.path}")
        if start_info is not None:
            logger.info(f"Start info: {start_info}")
        else:
            logger.info("No start info found (fresh database sync)")

        # time the sync for performance monitoring
        sync_start_time = datetime.now()
        logger.info(f"Sync start time: {sync_start_time}")
        start_time = perf_counter()
        
        with conn:
            conn.sync()
        conn.close()

        end_time = perf_counter()
        
        logger.info(f"Sync time: {end_time - start_time:.1f} seconds")
        logger.info(f"Sync time: {(end_time - start_time)/60:.1f} minutes")
        logger.info(f"Sync end time: {datetime.now()}")
        raw_end_info = self.read_db_info()
        end_info = json.loads(raw_end_info) if raw_end_info else None
        if start_info and end_info:
            generation_change = end_info["generation"] - start_info["generation"]
            frames_synced = end_info["durable_frame_num"] - start_info["durable_frame_num"]
            logger.info(f"Generation change: {generation_change}")
            logger.info(f"Frames synced: {frames_synced}")
        elif end_info:
            logger.info(f"Fresh sync completed. Generation: {end_info['generation']}, Frames: {end_info['durable_frame_num']}")
        logger.info("Sync complete")
        logger.info("=" * 80)

    def validate_sync(self) -> bool:
        logger.info(f"Validating sync for {self.alias}, url: {self.turso_url}, self.path: {self.path}")
        
        #check that db is initialized
        verify_db_exists = self.verify_db_exists()
        if not verify_db_exists:
            logger.warning(f"Database {self.alias} is not up to date. Syncing...")
            self.sync()
            verify_db_exists = self.verify_db_exists()
            if not verify_db_exists:
                logger.error(f"Database {self.alias} is still not up to date. Exiting...")
                return False
        else:
            logger.info(f"Database {self.alias} is up to date")

        with self.remote_engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(last_update) FROM marketstats")).fetchone()
            remote_last_update = result[0]
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(last_update) FROM marketstats")).fetchone()
            local_last_update = result[0]
        logger.info(f"remote_last_update: {remote_last_update}")
        logger.info(f"local_last_update: {local_last_update}")
        validation_test = remote_last_update == local_last_update
        logger.info(f"validation_test: {validation_test}")
        return validation_test

    def get_table_list(self, local_only: bool = True) -> list[tuple]:
        if local_only:
            engine = self.engine
            with engine.connect() as conn:
                stmt = text("PRAGMA table_list")
                result = conn.execute(stmt)
                tables = result.fetchall()
                table_list = [table.name for table in tables if "sqlite" not in table.name]
                return table_list
        else:
            engine = self.remote_engine
            with engine.connect() as conn:
                stmt = text("PRAGMA table_list")
                result = conn.execute(stmt)
                tables = result.fetchall()
                table_list = [table.name for table in tables if "sqlite" not in table.name]
                return table_list

    def get_table_columns(self, table_name: str, local_only: bool = True, full_info: bool = False) -> list[dict]:
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
            return result[0]

    def get_status(self):
        status_dict = {}
        tables = self.get_table_list()
        for table in tables:
            with self.remote_engine.connect() as conn:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
                status_dict[table] = result[0]
            conn.close()
        return status_dict

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

        logger.info(f"Verifying db state: db_exists={db_exists}, metadata_exists={metadata_exists}")

        if db_exists and metadata_exists:
            # Case 2: Valid state
            logger.info(f"Database {self.path} is properly initialized")
            return True

        if db_exists and not metadata_exists:
            # Case 3: DB without metadata (improperly created, e.g., bare sqlite.connect)
            # MUST nuke before sync - cannot sync a db without its -info file
            logger.warning(f"DB exists without metadata, nuking: {self.path}")
            if not self._nuke_db_file():
                logger.error(f"Failed to delete db file: {self.path}")
                return False

        if not db_exists and metadata_exists:
            # Case 4: Orphaned metadata
            logger.warning(f"Orphaned metadata found, removing: {self.path}-info")
            if not self._nuke_metadata_file():
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

    def read_db_info(self) -> str:
        info_path = f"{self.path}-info"
        info_path = Path(info_path)
        if not info_path.exists():
            return None
        with open(info_path, "r") as f:
            db_info = f.read()
        return db_info

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
        logger.info(f"needs_init check: db_exists={db_exists}, metadata_exists={metadata_exists}, needs_init={needs_init}")
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

    def _nuke_db_file(self) -> bool:
        """
        Delete just the database file.

        Returns:
            True if file was deleted or didn't exist, False on error.
        """
        db_path = Path(self.path)
        if db_path.exists():
            try:
                db_path.unlink()
                logger.info(f"Deleted db file: {db_path}")
                return True
            except OSError as e:
                logger.error(f"Failed to delete db file {db_path}: {e}")
                return False
        return True  # Already gone

    def _nuke_metadata_file(self) -> bool:
        """
        Delete just the metadata (-info) file.

        Returns:
            True if file was deleted or didn't exist, False on error.
        """
        info_path = Path(f"{self.path}-info")
        if info_path.exists():
            try:
                info_path.unlink()
                logger.info(f"Deleted metadata file: {info_path}")
                return True
            except OSError as e:
                logger.error(f"Failed to delete metadata file {info_path}: {e}")
                return False
        return True  # Already gone

    def nuke_db(self) -> bool:
        """
        Delete both database and metadata files.

        Returns:
            True if both files were deleted (or didn't exist), False on any error.
        """
        db_ok = self._nuke_db_file()
        meta_ok = self._nuke_metadata_file()
        return db_ok and meta_ok
  

if __name__ == "__main__":
    pass
