"""
Tests for database routing based on market context.

These tests verify that functions correctly route to the appropriate database
when a market context is provided.
"""
import pytest
from unittest.mock import patch, MagicMock, call
import pandas as pd


class TestDatabaseConfigRouting:
    """Tests for DatabaseConfig routing with MarketContext."""

    def test_database_config_uses_primary_market_context(self, primary_market_context):
        """Test DatabaseConfig uses primary market context settings."""
        from mkts_backend.config.config import DatabaseConfig

        db = DatabaseConfig(market_context=primary_market_context)

        assert db.alias == "wcmktprod"
        assert "wcmktprod" in db.path

    def test_database_config_uses_deployment_market_context(self, deployment_market_context):
        """Test DatabaseConfig uses deployment market context settings."""
        from mkts_backend.config.config import DatabaseConfig

        db = DatabaseConfig(market_context=deployment_market_context)

        assert db.alias == "wcmktnorth"
        assert "wcmktnorth" in db.path

    def test_database_config_primary_and_deployment_different_paths(
        self, primary_market_context, deployment_market_context
    ):
        """Test that primary and deployment create different database paths."""
        from mkts_backend.config.config import DatabaseConfig

        primary_db = DatabaseConfig(market_context=primary_market_context)
        deployment_db = DatabaseConfig(market_context=deployment_market_context)

        assert primary_db.path != deployment_db.path
        assert primary_db.alias != deployment_db.alias

    def test_database_config_legacy_alias_still_works(self):
        """Test that legacy alias-based initialization still works."""
        from mkts_backend.config.config import DatabaseConfig

        # Legacy initialization without market_context
        # "wcmkt" alias maps to wcmktprod in the new config
        db = DatabaseConfig("wcmkt")

        # The alias is stored as passed, but path maps to wcmktprod
        assert db.alias in ["wcmkt", "wcmktprod"]

    def test_database_config_market_context_takes_precedence(self, primary_market_context):
        """Test that market_context takes precedence over alias parameter."""
        from mkts_backend.config.config import DatabaseConfig

        # Even if alias is provided, market_context should take precedence
        db = DatabaseConfig(alias="something_else", market_context=primary_market_context)

        assert db.alias == "wcmktprod"


class TestESIConfigRouting:
    """Tests for ESIConfig routing with MarketContext."""

    def test_esi_config_uses_primary_market_context(self, primary_market_context):
        """Test ESIConfig uses primary market context settings."""
        from mkts_backend.config.esi_config import ESIConfig

        esi = ESIConfig(market_context=primary_market_context)

        assert esi.region_id == 10000003
        assert esi.structure_id == 1035466617946

    def test_esi_config_uses_deployment_market_context(self, deployment_market_context):
        """Test ESIConfig uses deployment market context settings."""
        from mkts_backend.config.esi_config import ESIConfig

        esi = ESIConfig(market_context=deployment_market_context)

        assert esi.region_id == 10000023
        assert esi.structure_id == 1046831245129

    def test_esi_config_primary_and_deployment_different_regions(
        self, primary_market_context, deployment_market_context
    ):
        """Test that primary and deployment have different ESI settings."""
        from mkts_backend.config.esi_config import ESIConfig

        primary_esi = ESIConfig(market_context=primary_market_context)
        deployment_esi = ESIConfig(market_context=deployment_market_context)

        assert primary_esi.region_id != deployment_esi.region_id
        assert primary_esi.structure_id != deployment_esi.structure_id

    def test_esi_config_market_orders_url_primary(self, primary_market_context):
        """Test market_orders_url property works for primary market."""
        from mkts_backend.config.esi_config import ESIConfig

        esi = ESIConfig(market_context=primary_market_context)
        url = esi.market_orders_url

        # Primary uses structure endpoint
        assert "structures" in url
        assert str(primary_market_context.structure_id) in url

    def test_esi_config_market_orders_url_deployment(self, deployment_market_context):
        """Test market_orders_url property works for deployment market."""
        from mkts_backend.config.esi_config import ESIConfig

        esi = ESIConfig(market_context=deployment_market_context)
        url = esi.market_orders_url

        # Deployment uses structure endpoint (not region)
        assert "structures" in url
        assert str(deployment_market_context.structure_id) in url

    def test_esi_config_headers_primary_does_not_raise(self, primary_market_context):
        """Test headers property doesn't raise for primary market."""
        from mkts_backend.config.esi_config import ESIConfig
        from unittest.mock import patch

        esi = ESIConfig(market_context=primary_market_context)

        # Mock the token to avoid actual ESI auth
        with patch.object(esi, 'token', return_value={'access_token': 'mock_token'}):
            headers = esi.headers

        assert "Authorization" in headers
        assert "Bearer" in headers["Authorization"]

    def test_esi_config_headers_deployment_does_not_raise(self, deployment_market_context):
        """Test headers property doesn't raise for deployment market.

        This test catches the bug where headers property only checked for
        'primary' and 'secondary' aliases, not 'deployment'.
        """
        from mkts_backend.config.esi_config import ESIConfig
        from unittest.mock import patch

        esi = ESIConfig(market_context=deployment_market_context)

        # Mock the token to avoid actual ESI auth
        with patch.object(esi, 'token', return_value={'access_token': 'mock_token'}):
            # This would raise ValueError before the fix:
            # "Invalid alias: deployment. Valid aliases are: ['primary', 'deployment']"
            headers = esi.headers

        assert "Authorization" in headers
        assert "Bearer" in headers["Authorization"]

    def test_esi_config_market_history_url_primary(self, primary_market_context):
        """Test market_history_url property works for primary market."""
        from mkts_backend.config.esi_config import ESIConfig

        esi = ESIConfig(market_context=primary_market_context)
        url = esi.market_history_url

        assert "history" in url
        assert str(primary_market_context.region_id) in url

    def test_esi_config_market_history_url_deployment(self, deployment_market_context):
        """Test market_history_url property works for deployment market."""
        from mkts_backend.config.esi_config import ESIConfig

        esi = ESIConfig(market_context=deployment_market_context)
        url = esi.market_history_url

        assert "history" in url
        assert str(deployment_market_context.region_id) in url


class TestGoogleSheetsConfigRouting:
    """Tests for GoogleSheetConfig routing with MarketContext."""

    def test_gsheets_config_uses_primary_market_context(self, primary_market_context):
        """Test GoogleSheetConfig uses primary market context settings."""
        from mkts_backend.config.gsheets_config import GoogleSheetConfig

        gsheets = GoogleSheetConfig(market_context=primary_market_context)

        assert gsheets.google_sheet_url == primary_market_context.gsheets_url
        assert gsheets.worksheets == primary_market_context.gsheets_worksheets

    def test_gsheets_config_uses_deployment_market_context(self, deployment_market_context):
        """Test GoogleSheetConfig uses deployment market context settings."""
        from mkts_backend.config.gsheets_config import GoogleSheetConfig

        gsheets = GoogleSheetConfig(market_context=deployment_market_context)

        assert gsheets.google_sheet_url == deployment_market_context.gsheets_url
        assert gsheets.worksheets == deployment_market_context.gsheets_worksheets


class TestDbHandlersRouting:
    """Tests for db_handlers functions routing with MarketContext."""

    def test_get_db_helper_with_primary_context(self, primary_market_context):
        """Test _get_db helper returns correct config for primary market."""
        from mkts_backend.db.db_handlers import _get_db

        db = _get_db(primary_market_context)

        assert db.alias == "wcmktprod"

    def test_get_db_helper_with_deployment_context(self, deployment_market_context):
        """Test _get_db helper returns correct config for deployment market."""
        from mkts_backend.db.db_handlers import _get_db

        db = _get_db(deployment_market_context)

        assert db.alias == "wcmktnorth"

    def test_get_db_helper_without_context_uses_default(self):
        """Test _get_db helper without context uses default database."""
        from mkts_backend.db.db_handlers import _get_db

        db = _get_db(None)

        # Default is wcmkt which maps to wcmktprod
        assert db.alias in ["wcmkt", "wcmktprod"]


class TestDbQueriesRouting:
    """Tests for db_queries functions routing with MarketContext."""

    def test_get_db_helper_with_primary_context(self, primary_market_context):
        """Test _get_db helper returns correct config for primary market."""
        from mkts_backend.db.db_queries import _get_db

        db = _get_db(primary_market_context)

        assert db.alias == "wcmktprod"

    def test_get_db_helper_with_deployment_context(self, deployment_market_context):
        """Test _get_db helper returns correct config for deployment market."""
        from mkts_backend.db.db_queries import _get_db

        db = _get_db(deployment_market_context)

        assert db.alias == "wcmktnorth"


class TestDataProcessingRouting:
    """Tests for data_processing functions routing with MarketContext."""

    def test_get_db_helper_with_primary_context(self, primary_market_context):
        """Test _get_db helper returns correct config for primary market."""
        from mkts_backend.processing.data_processing import _get_db

        db = _get_db(primary_market_context)

        assert db.alias == "wcmktprod"

    def test_get_db_helper_with_deployment_context(self, deployment_market_context):
        """Test _get_db helper returns correct config for deployment market."""
        from mkts_backend.processing.data_processing import _get_db

        db = _get_db(deployment_market_context)

        assert db.alias == "wcmktnorth"

    def test_get_db_helper_without_context_uses_default(self):
        """Test _get_db helper without context uses lazy-initialized default."""
        from mkts_backend.processing.data_processing import _get_db

        db = _get_db(None)

        # Should use the lazy-initialized default (wcmkt -> wcmktprod)
        assert db.alias in ["wcmkt", "wcmktprod"]


class TestAsyncHistoryRouting:
    """Tests for async_history functions routing with MarketContext."""

    def test_get_headers_with_primary_context(self, primary_market_context):
        """Test _get_headers returns headers for primary market."""
        from mkts_backend.esi.async_history import _get_headers

        headers = _get_headers(primary_market_context)

        assert "User-Agent" in headers
        assert headers["User-Agent"] is not None

    def test_get_headers_with_deployment_context(self, deployment_market_context):
        """Test _get_headers returns headers for deployment market."""
        from mkts_backend.esi.async_history import _get_headers

        headers = _get_headers(deployment_market_context)

        assert "User-Agent" in headers
        assert headers["User-Agent"] is not None


class TestDatabaseIsolation:
    """Tests to verify database operations are isolated by market context."""

    def test_primary_operations_use_primary_database(self, primary_market_context):
        """Test that operations with primary context use primary database."""
        from mkts_backend.db.db_handlers import _get_db

        # Call with primary context
        db = _get_db(primary_market_context)

        # Verify correct database was selected
        assert db.alias == "wcmktprod"
        assert "wcmktprod" in db.path

    def test_deployment_operations_use_deployment_database(self, deployment_market_context):
        """Test that operations with deployment context use deployment database."""
        from mkts_backend.db.db_handlers import _get_db

        # Call with deployment context
        db = _get_db(deployment_market_context)

        # Verify correct database was selected
        assert db.alias == "wcmktnorth"
        assert "wcmktnorth" in db.path

    def test_database_path_contains_correct_market_identifier(
        self, primary_market_context, deployment_market_context
    ):
        """Test that database paths contain correct market identifiers."""
        from mkts_backend.config.config import DatabaseConfig

        primary_db = DatabaseConfig(market_context=primary_market_context)
        deployment_db = DatabaseConfig(market_context=deployment_market_context)

        # Primary should reference wcmktprod
        assert "wcmktprod" in primary_db.path.lower() or "prod" in primary_db.path.lower()

        # Deployment should reference wcmktnorth
        assert "wcmktnorth" in deployment_db.path.lower() or "north" in deployment_db.path.lower()


class TestCrossMarketIsolation:
    """Tests to verify cross-market isolation is maintained."""

    def test_sequential_market_operations_use_correct_databases(
        self, primary_market_context, deployment_market_context
    ):
        """Test that sequential operations on different markets use correct databases."""
        from mkts_backend.config.config import DatabaseConfig

        # First operation - primary market
        db1 = DatabaseConfig(market_context=primary_market_context)
        primary_alias = db1.alias
        primary_path = db1.path

        # Second operation - deployment market
        db2 = DatabaseConfig(market_context=deployment_market_context)
        deployment_alias = db2.alias
        deployment_path = db2.path

        # Third operation - back to primary
        db3 = DatabaseConfig(market_context=primary_market_context)

        # Verify isolation
        assert primary_alias == "wcmktprod"
        assert deployment_alias == "wcmktnorth"
        assert db3.alias == primary_alias  # Back to primary
        assert db3.path == primary_path

    def test_interleaved_market_operations_maintain_isolation(
        self, primary_market_context, deployment_market_context
    ):
        """Test that interleaved operations maintain proper isolation."""
        from mkts_backend.db.db_handlers import _get_db as handlers_get_db
        from mkts_backend.db.db_queries import _get_db as queries_get_db
        from mkts_backend.processing.data_processing import _get_db as processing_get_db

        # Interleaved calls
        h_primary = handlers_get_db(primary_market_context)
        q_deployment = queries_get_db(deployment_market_context)
        p_primary = processing_get_db(primary_market_context)
        h_deployment = handlers_get_db(deployment_market_context)

        # Verify each got the correct database
        assert h_primary.alias == "wcmktprod"
        assert q_deployment.alias == "wcmktnorth"
        assert p_primary.alias == "wcmktprod"
        assert h_deployment.alias == "wcmktnorth"
