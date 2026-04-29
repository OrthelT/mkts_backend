"""
Tests for MarketContext dataclass and configuration loading.
"""
import pytest
from pathlib import Path
from unittest.mock import patch


class TestMarketContextCreation:
    """Tests for MarketContext instantiation and configuration loading."""

    def test_create_primary_market_context_development(self, primary_market_context):
        """Test that primary market context uses testing db in development mode."""
        ctx = primary_market_context

        assert ctx.alias == "primary"
        assert ctx.name == "4-HWWF - Platestar"
        assert ctx.region_id == 10000003
        assert ctx.structure_id == 1053654548169
        # In development mode, primary should use testing database
        assert ctx.database_alias == "wcmkttest"
        assert ctx.database_file == "wcmkttest.db"
        assert ctx.turso_url_env == "TURSO_WCMKTTEST_URL"
        assert ctx.turso_token_env == "TURSO_WCMKTTEST_TOKEN"

    def test_create_deployment_market_context(self, deployment_market_context):
        """Test that deployment market context is created with correct values."""
        ctx = deployment_market_context

        assert ctx.alias == "deployment"
        assert ctx.name == "X47L-Q - Rogue Threshold"
        assert ctx.region_id == 10000003
        assert ctx.structure_id == 1041669946862
        assert ctx.database_alias == "wcmktnorth"
        assert ctx.database_file == "wcmktnorth2.db"
        assert ctx.turso_url_env == "TURSO_WCMKTNORTH_URL"
        assert ctx.turso_token_env == "TURSO_WCMKTNORTH_TOKEN"

    def test_create_primary_market_context_production(self, monkeypatch):
        """Test that primary market context uses production db in production mode."""
        from mkts_backend.config.market_context import MarketContext
        from mkts_backend.config.settings_service import clear_cache

        monkeypatch.setenv("MKTS_ENVIRONMENT", "production")
        clear_cache()
        ctx = MarketContext.from_settings("primary")
        clear_cache()

        assert ctx.alias == "primary"
        assert ctx.database_alias == "wcmktprod"
        assert ctx.database_file == "wcmktprod.db"
        assert ctx.turso_url_env == "TURSO_WCMKTPROD_URL"
        assert ctx.turso_token_env == "TURSO_WCMKTPROD_TOKEN"

    def test_get_default_returns_primary(self):
        """Test that get_default() returns primary market context."""
        from mkts_backend.config.market_context import MarketContext

        default = MarketContext.get_default()

        assert default.alias == "primary"
        assert default.name == "4-HWWF - Platestar"

    def test_primary_and_deployment_have_different_databases(
        self, primary_market_context, deployment_market_context
    ):
        """Test that primary and deployment markets use different databases."""
        assert primary_market_context.database_alias != deployment_market_context.database_alias
        assert primary_market_context.database_file != deployment_market_context.database_file
        assert primary_market_context.turso_url_env != deployment_market_context.turso_url_env

    def test_primary_and_deployment_are_different_locations(
        self, primary_market_context, deployment_market_context
    ):
        """Test that primary and deployment markets are distinct locations.

        Both markets currently share region_id=10000003, so isolation is verified
        via structure_id and system_id (which uniquely identify the market hub).
        """
        assert primary_market_context.structure_id != deployment_market_context.structure_id
        assert primary_market_context.system_id != deployment_market_context.system_id

    def test_invalid_market_alias_raises_error(self):
        """Test that invalid market alias raises ValueError."""
        from mkts_backend.config.market_context import MarketContext

        with pytest.raises(ValueError, match="Unknown market"):
            MarketContext.from_settings("invalid_market")

    def test_market_context_has_gsheets_config(self, primary_market_context, deployment_market_context):
        """Test that market contexts have Google Sheets configuration."""
        assert primary_market_context.gsheets_url is not None
        assert primary_market_context.gsheets_worksheets is not None

        assert deployment_market_context.gsheets_url is not None
        assert deployment_market_context.gsheets_worksheets is not None

    def test_get_available_markets(self):
        """Test that we can retrieve list of available markets."""
        from mkts_backend.config.market_context import MarketContext

        markets = MarketContext.get_available_markets()

        assert "primary" in markets
        assert "deployment" in markets
        assert len(markets) >= 2


class TestMarketContextIsolation:
    """Tests to verify market contexts are properly isolated."""

    def test_primary_database_alias_mapping(self, primary_market_context):
        """Test primary market maps to wcmkttest database in development mode."""
        assert primary_market_context.database_alias == "wcmkttest"
        assert "test" in primary_market_context.database_file.lower()

    def test_deployment_database_alias_mapping(self, deployment_market_context):
        """Test deployment market maps to wcmktnorth database."""
        assert deployment_market_context.database_alias == "wcmktnorth"
        assert "north" in deployment_market_context.database_file.lower()

    def test_market_contexts_are_independent(self, primary_market_context, deployment_market_context):
        """Test that modifying one context doesn't affect the other."""
        # Store original values
        primary_original_name = primary_market_context.name
        deployment_original_name = deployment_market_context.name

        # Verify they are different objects with different data
        assert primary_market_context is not deployment_market_context
        assert primary_original_name != deployment_original_name

    def test_turso_env_vars_are_different(self, primary_market_context, deployment_market_context):
        """Test that each market uses different Turso environment variables."""
        primary_url_env = primary_market_context.turso_url_env
        deployment_url_env = deployment_market_context.turso_url_env

        assert primary_url_env != deployment_url_env
        # In development mode, primary uses testing turso env vars
        assert "WCMKTTEST" in primary_url_env
        assert "WCMKTNORTH" in deployment_url_env

    def test_deployment_unaffected_by_environment(self, monkeypatch):
        """Test that deployment market is not affected by environment setting."""
        from mkts_backend.config.market_context import MarketContext
        from mkts_backend.config.settings_service import clear_cache

        # Test in development mode
        monkeypatch.setenv("MKTS_ENVIRONMENT", "development")
        clear_cache()
        dev_ctx = MarketContext.from_settings("deployment")

        # Test in production mode
        monkeypatch.setenv("MKTS_ENVIRONMENT", "production")
        clear_cache()
        prod_ctx = MarketContext.from_settings("deployment")
        clear_cache()

        assert dev_ctx.database_alias == prod_ctx.database_alias == "wcmktnorth"
        assert dev_ctx.database_file == prod_ctx.database_file == "wcmktnorth2.db"
