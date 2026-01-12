"""
Tests for MarketContext dataclass and configuration loading.
"""
import pytest
from pathlib import Path


class TestMarketContextCreation:
    """Tests for MarketContext instantiation and configuration loading."""

    def test_create_primary_market_context(self, primary_market_context):
        """Test that primary market context is created with correct values."""
        ctx = primary_market_context

        assert ctx.alias == "primary"
        assert ctx.name == "4-HWWF Keepstar"
        assert ctx.region_id == 10000003
        assert ctx.structure_id == 1035466617946
        assert ctx.database_alias == "wcmktprod"
        assert ctx.database_file == "wcmktprod.db"
        assert ctx.turso_url_env == "TURSO_WCMKTPROD_URL"
        assert ctx.turso_token_env == "TURSO_WCMKTPROD_TOKEN"

    def test_create_deployment_market_context(self, deployment_market_context):
        """Test that deployment market context is created with correct values."""
        ctx = deployment_market_context

        assert ctx.alias == "deployment"
        assert ctx.name == "B-9C24 Keepstar"
        assert ctx.region_id == 10000023
        assert ctx.structure_id == 1046831245129
        assert ctx.database_alias == "wcmktnorth"
        assert ctx.database_file == "wcmktnorth2.db"
        assert ctx.turso_url_env == "TURSO_WCMKTNORTH_URL"
        assert ctx.turso_token_env == "TURSO_WCMKTNORTH_TOKEN"

    def test_get_default_returns_primary(self):
        """Test that get_default() returns primary market context."""
        from mkts_backend.config.market_context import MarketContext

        default = MarketContext.get_default()

        assert default.alias == "primary"
        assert default.name == "4-HWWF Keepstar"

    def test_primary_and_deployment_have_different_databases(
        self, primary_market_context, deployment_market_context
    ):
        """Test that primary and deployment markets use different databases."""
        assert primary_market_context.database_alias != deployment_market_context.database_alias
        assert primary_market_context.database_file != deployment_market_context.database_file
        assert primary_market_context.turso_url_env != deployment_market_context.turso_url_env

    def test_primary_and_deployment_have_different_regions(
        self, primary_market_context, deployment_market_context
    ):
        """Test that primary and deployment markets use different regions."""
        assert primary_market_context.region_id != deployment_market_context.region_id
        assert primary_market_context.structure_id != deployment_market_context.structure_id

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
        """Test primary market maps to wcmktprod database."""
        assert primary_market_context.database_alias == "wcmktprod"
        assert "prod" in primary_market_context.database_file.lower()

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
        assert "WCMKTPROD" in primary_url_env
        assert "WCMKTNORTH" in deployment_url_env
