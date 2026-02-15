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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS esi_request_cache (
                type_id INTEGER NOT NULL,
                region_id INTEGER NOT NULL,
                etag TEXT,
                last_modified TEXT,
                last_checked DATETIME,
                PRIMARY KEY (type_id, region_id)
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


# ---------------------------------------------------------------------------
# In-memory database fixtures for unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def in_memory_market_db(tmp_path):
    """File-backed SQLite engine pre-populated with market test data.

    Tables: watchlist, marketorders, market_history, doctrines, marketstats.
    Returns (engine, db_path) — db_path is needed to create new engines
    after the production code calls engine.dispose().
    """
    from sqlalchemy import create_engine, text as sa_text

    # Use a temp file so the DB survives engine.dispose() calls in
    # production code (in-memory DBs vanish when connections close).
    db_path = tmp_path / "test_market.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        # -- watchlist --
        conn.execute(sa_text("""
            CREATE TABLE watchlist (
                type_id INTEGER PRIMARY KEY,
                type_name TEXT,
                group_name TEXT,
                category_name TEXT,
                category_id INTEGER,
                group_id INTEGER
            )
        """))
        conn.execute(sa_text(
            "INSERT INTO watchlist VALUES (34,'Tritanium','Mineral','Material',4,18)"
        ))
        conn.execute(sa_text(
            "INSERT INTO watchlist VALUES (35,'Pyerite','Mineral','Material',4,18)"
        ))
        conn.execute(sa_text(
            "INSERT INTO watchlist VALUES (36,'Mexallon','Mineral','Material',4,18)"
        ))

        # -- marketorders (sell orders for percentile tests) --
        conn.execute(sa_text("""
            CREATE TABLE marketorders (
                order_id INTEGER PRIMARY KEY,
                type_id INTEGER,
                price REAL,
                volume_remain INTEGER,
                is_buy_order INTEGER
            )
        """))
        # 10 sell orders for type_id=34 (prices 5.0..14.0)
        for i in range(10):
            conn.execute(sa_text(
                f"INSERT INTO marketorders VALUES ({100+i}, 34, {5.0+i}, {1000-i*50}, 0)"
            ))
        # 1 sell order for type_id=35
        conn.execute(sa_text(
            "INSERT INTO marketorders VALUES (200, 35, 10.0, 500, 0)"
        ))
        # buy order (should be excluded)
        conn.execute(sa_text(
            "INSERT INTO marketorders VALUES (300, 34, 3.0, 200, 1)"
        ))

        # -- market_history --
        conn.execute(sa_text("""
            CREATE TABLE market_history (
                date TEXT,
                type_id TEXT,
                type_name TEXT,
                average REAL,
                volume INTEGER,
                highest REAL,
                lowest REAL,
                order_count INTEGER,
                timestamp TEXT
            )
        """))
        conn.execute(sa_text("""
            INSERT INTO market_history
            VALUES ('2026-02-10','34','Tritanium',8.5,2000,10.0,6.0,50,'2026-02-10 12:00:00')
        """))
        conn.execute(sa_text("""
            INSERT INTO market_history
            VALUES ('2026-02-11','34','Tritanium',9.0,1800,11.0,7.0,45,'2026-02-11 12:00:00')
        """))
        conn.execute(sa_text("""
            INSERT INTO market_history
            VALUES ('2026-02-10','35','Pyerite',10.0,500,12.0,8.0,20,'2026-02-10 12:00:00')
        """))

        # -- marketstats --
        conn.execute(sa_text("""
            CREATE TABLE marketstats (
                type_id INTEGER PRIMARY KEY,
                total_volume_remain INTEGER,
                min_price REAL,
                price REAL,
                avg_price REAL,
                avg_volume REAL,
                group_id INTEGER,
                type_name TEXT,
                group_name TEXT,
                category_id INTEGER,
                category_name TEXT,
                days_remaining REAL,
                last_update TEXT
            )
        """))
        conn.execute(sa_text("""
            INSERT INTO marketstats
            VALUES (34,5500,5.0,5.45,8.75,1900.0,18,'Tritanium','Mineral',4,'Material',2.9,'2026-02-12 00:00:00')
        """))
        conn.execute(sa_text("""
            INSERT INTO marketstats
            VALUES (35,500,10.0,10.0,10.0,500.0,18,'Pyerite','Mineral',4,'Material',1.0,'2026-02-12 00:00:00')
        """))

        # -- doctrines --
        conn.execute(sa_text("""
            CREATE TABLE doctrines (
                id INTEGER PRIMARY KEY,
                fit_id INTEGER,
                ship_id INTEGER,
                ship_name TEXT,
                hulls INTEGER,
                type_id INTEGER,
                type_name TEXT,
                fit_qty INTEGER,
                fits_on_mkt REAL,
                total_stock INTEGER,
                price REAL,
                avg_vol REAL,
                days REAL,
                group_id INTEGER,
                group_name TEXT,
                category_id INTEGER,
                category_name TEXT,
                timestamp TEXT
            )
        """))
        conn.execute(sa_text("""
            INSERT INTO doctrines
            VALUES (1,1,587,'Rifter',0,34,'Tritanium',100,0,0,0,0,0,18,'Mineral',4,'Material',NULL)
        """))
        conn.execute(sa_text("""
            INSERT INTO doctrines
            VALUES (2,1,587,'Rifter',0,35,'Pyerite',50,0,0,0,0,0,18,'Mineral',4,'Material',NULL)
        """))
        conn.execute(sa_text("""
            INSERT INTO doctrines
            VALUES (3,2,24690,'Drake',0,34,'Tritanium',200,0,0,0,0,0,18,'Mineral',4,'Material',NULL)
        """))

        conn.commit()
    yield db_path
    engine.dispose()


@pytest.fixture
def in_memory_sde_db(tmp_path):
    """File-backed SQLite engine with sdetypes and invTypes/invGroups/invCategories.

    Known mappings: 34=Tritanium, 35=Pyerite, 36=Mexallon in sdetypes.
    type_id 37 (Isogen) only exists in invTypes fallback tables.
    """
    from sqlalchemy import create_engine, text as sa_text

    db_path = tmp_path / "test_sde.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(sa_text("""
            CREATE TABLE sdetypes (
                typeID INTEGER PRIMARY KEY,
                typeName TEXT,
                groupID INTEGER,
                groupName TEXT,
                categoryID INTEGER,
                categoryName TEXT,
                volume REAL,
                metaGroupID INTEGER,
                metaGroupName TEXT
            )
        """))
        conn.execute(sa_text("INSERT INTO sdetypes VALUES (34,'Tritanium',18,'Mineral',4,'Material',0.01,1,'Tech I')"))
        conn.execute(sa_text("INSERT INTO sdetypes VALUES (35,'Pyerite',18,'Mineral',4,'Material',0.01,1,'Tech I')"))
        conn.execute(sa_text("INSERT INTO sdetypes VALUES (36,'Mexallon',18,'Mineral',4,'Material',0.01,1,'Tech I')"))

        # Fallback tables
        conn.execute(sa_text("""
            CREATE TABLE invTypes (typeID INTEGER PRIMARY KEY, typeName TEXT, groupID INTEGER)
        """))
        conn.execute(sa_text("INSERT INTO invTypes VALUES (37,'Isogen',18)"))

        conn.execute(sa_text("""
            CREATE TABLE invGroups (groupID INTEGER PRIMARY KEY, groupName TEXT, categoryID INTEGER)
        """))
        conn.execute(sa_text("INSERT INTO invGroups VALUES (18,'Mineral',4)"))

        conn.execute(sa_text("""
            CREATE TABLE invCategories (categoryID INTEGER PRIMARY KEY, categoryName TEXT)
        """))
        conn.execute(sa_text("INSERT INTO invCategories VALUES (4,'Material')"))

        conn.commit()
    yield db_path
    engine.dispose()


@pytest.fixture
def mock_esi_config():
    """Mock ESIConfig with test URLs and headers (no real auth)."""
    mock = MagicMock()
    mock.alias = "primary"
    mock.name = "Test Market"
    mock.region_id = 10000003
    mock.system_id = 30000240
    mock.structure_id = 1035466617946
    mock.market_orders_url = "https://esi.evetech.net/markets/structures/1035466617946"
    mock.market_history_url = "https://esi.evetech.net/markets/10000003/history"
    mock.user_agent = "test-agent"
    mock.headers = {
        "Accept": "application/json",
        "Authorization": "Bearer test-token",
    }
    return mock
