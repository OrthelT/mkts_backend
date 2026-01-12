"""
Pytest configuration and shared fixtures for market context tests.
"""
import pytest
import os
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add the src directory to the path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def primary_market_context():
    """Create a primary market context for testing."""
    from mkts_backend.config.market_context import MarketContext
    return MarketContext.from_settings("primary")


@pytest.fixture
def deployment_market_context():
    """Create a deployment market context for testing."""
    from mkts_backend.config.market_context import MarketContext
    return MarketContext.from_settings("deployment")


@pytest.fixture
def mock_env_vars():
    """Mock environment variables for Turso database connections."""
    env_vars = {
        # Primary market
        "TURSO_WCMKTPROD_URL": "libsql://test-primary.turso.io",
        "TURSO_WCMKTPROD_TOKEN": "test-primary-token",
        # Deployment market
        "TURSO_WCMKTNORTH_URL": "libsql://test-deployment.turso.io",
        "TURSO_WCMKTNORTH_TOKEN": "test-deployment-token",
        # SDE and fittings
        "TURSO_SDE_URL": "libsql://test-sde.turso.io",
        "TURSO_SDE_TOKEN": "test-sde-token",
        "TURSO_FITTING_URL": "libsql://test-fitting.turso.io",
        "TURSO_FITTING_TOKEN": "test-fitting-token",
        # ESI credentials
        "CLIENT_ID": "test-client-id",
        "SECRET_KEY": "test-secret-key",
        "REFRESH_TOKEN": "test-refresh-token",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def temp_db_dir(tmp_path):
    """Create a temporary directory with mock database files."""
    # Create mock database files
    for db_name in ["wcmktprod.db", "wcmktnorth2.db", "wcmkttest.db", "sde.db", "wcfitting.db"]:
        db_path = tmp_path / db_name
        conn = sqlite3.connect(str(db_path))
        # Create minimal schema for testing
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                type_id INTEGER PRIMARY KEY,
                type_name TEXT,
                group_name TEXT,
                category_name TEXT,
                category_id INTEGER,
                group_id INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marketorders (
                order_id INTEGER PRIMARY KEY,
                type_id INTEGER,
                price REAL,
                volume_remain INTEGER,
                is_buy_order INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marketstats (
                type_id INTEGER PRIMARY KEY,
                type_name TEXT,
                price REAL,
                avg_price REAL,
                avg_volume REAL,
                total_volume_remain INTEGER,
                days_remaining REAL,
                last_update TEXT,
                group_name TEXT,
                category_name TEXT,
                category_id INTEGER,
                group_id INTEGER,
                min_price REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_history (
                id INTEGER PRIMARY KEY,
                type_id INTEGER,
                date TEXT,
                average REAL,
                volume INTEGER,
                highest REAL,
                lowest REAL,
                order_count INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS doctrines (
                id INTEGER PRIMARY KEY,
                fit_id INTEGER,
                ship_id INTEGER,
                ship_name TEXT,
                type_id INTEGER,
                type_name TEXT,
                fit_qty INTEGER,
                group_id INTEGER,
                group_name TEXT,
                category_id INTEGER,
                category_name TEXT,
                hulls INTEGER,
                fits_on_mkt REAL,
                total_stock INTEGER,
                avg_vol REAL,
                days REAL,
                timestamp TEXT,
                price REAL
            )
        """)
        # Insert some test data
        conn.execute("INSERT OR IGNORE INTO watchlist VALUES (34, 'Tritanium', 'Mineral', 'Material', 4, 18)")
        conn.execute("INSERT OR IGNORE INTO watchlist VALUES (35, 'Pyerite', 'Mineral', 'Material', 4, 18)")
        conn.commit()
        conn.close()

    return tmp_path


@pytest.fixture
def mock_database_config(temp_db_dir):
    """Mock DatabaseConfig to use temporary database files."""
    original_init = None

    def mock_init(self, alias=None, dialect="sqlite+libsql", market_context=None):
        from mkts_backend.config.config import load_settings
        self.settings = load_settings()

        if market_context is not None:
            self.alias = market_context.database_alias
            self.path = str(temp_db_dir / market_context.database_file)
            self.turso_url = None  # Use local file for testing
            self.token = None
        elif alias:
            self.alias = alias
            if alias in ["wcmkt", "wcmktprod"]:
                self.path = str(temp_db_dir / "wcmktprod.db")
            elif alias in ["wcmktnorth", "wcmktnorth2"]:
                self.path = str(temp_db_dir / "wcmktnorth2.db")
            elif alias == "sde":
                self.path = str(temp_db_dir / "sde.db")
            elif alias == "fittings":
                self.path = str(temp_db_dir / "wcfitting.db")
            else:
                self.path = str(temp_db_dir / f"{alias}.db")
            self.turso_url = None
            self.token = None
        else:
            self.alias = "wcmktprod"
            self.path = str(temp_db_dir / "wcmktprod.db")
            self.turso_url = None
            self.token = None

        self.dialect = "sqlite"
        self._engine = None
        self._session = None

    with patch("mkts_backend.config.config.DatabaseConfig.__init__", mock_init):
        yield temp_db_dir


@pytest.fixture
def captured_database_calls():
    """Fixture to capture database calls and verify which database was used."""
    calls = []

    class CallCapture:
        def __init__(self):
            self.calls = []

        def record(self, alias, operation, market_ctx=None):
            self.calls.append({
                "alias": alias,
                "operation": operation,
                "market_ctx": market_ctx.alias if market_ctx else None
            })

        def get_calls_for_alias(self, alias):
            return [c for c in self.calls if c["alias"] == alias]

        def get_calls_for_market(self, market_alias):
            return [c for c in self.calls if c["market_ctx"] == market_alias]

        def clear(self):
            self.calls = []

    return CallCapture()
