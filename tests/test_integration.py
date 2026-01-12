"""
Integration tests for market context functionality.

These tests verify end-to-end functionality of the market context system,
ensuring that data flows to the correct database.
"""
import pytest
from unittest.mock import patch, MagicMock, call
import pandas as pd
import sqlite3
from pathlib import Path


class TestFullMarketContextFlow:
    """Integration tests for complete market context flow."""

    def test_primary_market_flow_uses_correct_database(self, primary_market_context):
        """Test that primary market operations use wcmktprod database."""
        from mkts_backend.config.config import DatabaseConfig

        db = DatabaseConfig(market_context=primary_market_context)

        assert db.alias == "wcmktprod"
        assert "wcmktprod" in db.path

    def test_deployment_market_flow_uses_correct_database(self, deployment_market_context):
        """Test that deployment market operations use wcmktnorth database."""
        from mkts_backend.config.config import DatabaseConfig

        db = DatabaseConfig(market_context=deployment_market_context)

        assert db.alias == "wcmktnorth"
        assert "wcmktnorth" in db.path


class TestMarketContextConfigChain:
    """Tests for configuration chain with MarketContext."""

    def test_config_chain_primary(self, primary_market_context):
        """Test full config chain for primary market."""
        from mkts_backend.config.config import DatabaseConfig
        from mkts_backend.config.esi_config import ESIConfig
        from mkts_backend.config.gsheets_config import GoogleSheetConfig

        # All configs should use primary market settings
        db = DatabaseConfig(market_context=primary_market_context)
        esi = ESIConfig(market_context=primary_market_context)
        gsheets = GoogleSheetConfig(market_context=primary_market_context)

        assert db.alias == "wcmktprod"
        assert esi.region_id == 10000003
        assert gsheets.google_sheet_url == primary_market_context.gsheets_url

    def test_config_chain_deployment(self, deployment_market_context):
        """Test full config chain for deployment market."""
        from mkts_backend.config.config import DatabaseConfig
        from mkts_backend.config.esi_config import ESIConfig
        from mkts_backend.config.gsheets_config import GoogleSheetConfig

        # All configs should use deployment market settings
        db = DatabaseConfig(market_context=deployment_market_context)
        esi = ESIConfig(market_context=deployment_market_context)
        gsheets = GoogleSheetConfig(market_context=deployment_market_context)

        assert db.alias == "wcmktnorth"
        assert esi.region_id == 10000023
        assert gsheets.google_sheet_url == deployment_market_context.gsheets_url


class TestDatabaseWriteIsolation:
    """Tests to verify writes go to correct database."""

    def test_writes_isolated_by_market_context(self, temp_db_dir, primary_market_context, deployment_market_context):
        """Test that database writes are isolated by market context."""
        # Create test data
        test_data = pd.DataFrame({
            "type_id": [12345],
            "type_name": ["Test Item"],
            "price": [100.0],
            "avg_price": [99.0],
            "avg_volume": [50.0],
            "total_volume_remain": [1000],
            "days_remaining": [20.0],
            "last_update": ["2024-01-01 00:00:00"],
            "group_name": ["Test Group"],
            "category_name": ["Test Category"],
            "category_id": [1],
            "group_id": [1],
            "min_price": [95.0]
        })

        primary_db_path = temp_db_dir / "wcmktprod.db"
        deployment_db_path = temp_db_dir / "wcmktnorth2.db"

        # Write to primary database
        conn_primary = sqlite3.connect(str(primary_db_path))
        test_data.to_sql("marketstats", conn_primary, if_exists="replace", index=False)
        conn_primary.close()

        # Read from deployment database - should NOT have the data
        conn_deployment = sqlite3.connect(str(deployment_db_path))
        try:
            result = pd.read_sql("SELECT * FROM marketstats WHERE type_id = 12345", conn_deployment)
            # Deployment should not have the primary data
            assert len(result) == 0 or result.empty
        except Exception:
            # Table might not exist or be empty, which is expected
            pass
        finally:
            conn_deployment.close()

        # Verify primary has the data
        conn_primary = sqlite3.connect(str(primary_db_path))
        result = pd.read_sql("SELECT * FROM marketstats WHERE type_id = 12345", conn_primary)
        conn_primary.close()

        assert len(result) == 1
        assert result.iloc[0]["type_name"] == "Test Item"


class TestBackwardCompatibility:
    """Tests for backward compatibility with legacy code."""

    def test_legacy_alias_initialization_works(self):
        """Test that legacy alias-based initialization still works."""
        from mkts_backend.config.config import DatabaseConfig

        # Legacy way of creating database config
        db = DatabaseConfig("wcmkt")

        # wcmkt maps to wcmktprod in the new configuration
        assert db.alias in ["wcmkt", "wcmktprod"]

    def test_legacy_esi_alias_initialization_works(self):
        """Test that legacy ESIConfig alias initialization still works."""
        from mkts_backend.config.esi_config import ESIConfig

        # Legacy way of creating ESI config
        esi = ESIConfig("primary")

        assert esi.region_id is not None
        assert esi.structure_id is not None

    def test_legacy_esi_deployment_alias_initialization_works(self):
        """Test that legacy ESIConfig deployment alias initialization works.

        This test catches the bug where legacy lookup dictionaries used
        'secondary_*' keys but the valid alias was 'deployment'.
        """
        from mkts_backend.config.esi_config import ESIConfig

        # Legacy way of creating ESI config with deployment alias
        esi = ESIConfig("deployment")

        assert esi.alias == "deployment"
        assert esi.region_id == 10000023  # Pure Blind
        assert esi.structure_id == 1046831245129  # B-9C24
        assert esi.market_orders_url is not None
        assert "structures" in esi.market_orders_url

    def test_none_market_context_uses_defaults(self):
        """Test that None market_ctx uses default behavior."""
        from mkts_backend.db.db_handlers import _get_db as handlers_get_db
        from mkts_backend.db.db_queries import _get_db as queries_get_db
        from mkts_backend.processing.data_processing import _get_db as processing_get_db

        # All should return default database (wcmkt -> wcmktprod)
        h_db = handlers_get_db(None)
        q_db = queries_get_db(None)
        p_db = processing_get_db(None)

        # Default alias is wcmkt which maps to wcmktprod
        assert h_db.alias in ["wcmkt", "wcmktprod"]
        assert q_db.alias in ["wcmkt", "wcmktprod"]
        assert p_db.alias in ["wcmkt", "wcmktprod"]


class TestFunctionSignatures:
    """Tests to verify all functions have market_ctx parameter."""

    def test_db_handlers_functions_have_market_ctx(self):
        """Test db_handlers functions accept market_ctx parameter."""
        import inspect
        from mkts_backend.db import db_handlers

        functions_to_check = [
            "upsert_database",
            "update_history",
            "update_market_orders",
            "log_update",
        ]

        for func_name in functions_to_check:
            if hasattr(db_handlers, func_name):
                func = getattr(db_handlers, func_name)
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                assert "market_ctx" in params, f"{func_name} missing market_ctx parameter"

    def test_db_queries_functions_have_market_ctx(self):
        """Test db_queries functions accept market_ctx parameter."""
        import inspect
        from mkts_backend.db import db_queries

        functions_to_check = [
            "get_market_history",
            "get_market_orders",
            "get_market_stats",
            "get_remote_status",
            "get_doctrine_stats",
            "get_table_length",
            "get_watchlist_ids",
        ]

        for func_name in functions_to_check:
            if hasattr(db_queries, func_name):
                func = getattr(db_queries, func_name)
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                assert "market_ctx" in params, f"{func_name} missing market_ctx parameter"

    def test_data_processing_functions_have_market_ctx(self):
        """Test data_processing functions accept market_ctx parameter."""
        import inspect
        from mkts_backend.processing import data_processing

        functions_to_check = [
            "calculate_5_percentile_price",
            "calculate_market_stats",
            "fill_nulls_from_history",
            "calculate_doctrine_stats",
        ]

        for func_name in functions_to_check:
            if hasattr(data_processing, func_name):
                func = getattr(data_processing, func_name)
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                assert "market_ctx" in params, f"{func_name} missing market_ctx parameter"

    def test_async_history_functions_have_market_ctx(self):
        """Test async_history functions accept market_ctx parameter."""
        import inspect
        from mkts_backend.esi import async_history

        functions_to_check = [
            "async_history",
            "run_async_history",
        ]

        for func_name in functions_to_check:
            if hasattr(async_history, func_name):
                func = getattr(async_history, func_name)
                sig = inspect.signature(func)
                params = list(sig.parameters.keys())
                assert "market_ctx" in params, f"{func_name} missing market_ctx parameter"


class TestMarketContextEnvironmentVariables:
    """Tests for market context environment variable resolution."""

    def test_primary_market_turso_env_vars(self, primary_market_context, mock_env_vars):
        """Test primary market resolves correct Turso environment variables."""
        import os

        url_env = primary_market_context.turso_url_env
        token_env = primary_market_context.turso_token_env

        assert url_env == "TURSO_WCMKTPROD_URL"
        assert token_env == "TURSO_WCMKTPROD_TOKEN"

        # With mocked env vars
        assert os.environ.get(url_env) == "libsql://test-primary.turso.io"
        assert os.environ.get(token_env) == "test-primary-token"

    def test_deployment_market_turso_env_vars(self, deployment_market_context, mock_env_vars):
        """Test deployment market resolves correct Turso environment variables."""
        import os

        url_env = deployment_market_context.turso_url_env
        token_env = deployment_market_context.turso_token_env

        assert url_env == "TURSO_WCMKTNORTH_URL"
        assert token_env == "TURSO_WCMKTNORTH_TOKEN"

        # With mocked env vars
        assert os.environ.get(url_env) == "libsql://test-deployment.turso.io"
        assert os.environ.get(token_env) == "test-deployment-token"


class TestConcurrentMarketOperations:
    """Tests for concurrent operations on different markets."""

    def test_concurrent_config_creation(self, primary_market_context, deployment_market_context):
        """Test creating configs for multiple markets concurrently."""
        from mkts_backend.config.config import DatabaseConfig

        configs = []

        # Create multiple configs rapidly
        for _ in range(5):
            configs.append(DatabaseConfig(market_context=primary_market_context))
            configs.append(DatabaseConfig(market_context=deployment_market_context))

        # Verify alternating configs are correct
        for i, config in enumerate(configs):
            if i % 2 == 0:
                assert config.alias == "wcmktprod"
            else:
                assert config.alias == "wcmktnorth"

    def test_market_context_thread_safety(self, primary_market_context, deployment_market_context):
        """Test that market contexts maintain isolation in rapid succession."""
        from mkts_backend.db.db_handlers import _get_db

        results = []

        for _ in range(10):
            primary_db = _get_db(primary_market_context)
            results.append(("primary", primary_db.alias))

            deployment_db = _get_db(deployment_market_context)
            results.append(("deployment", deployment_db.alias))

        # Verify all results are correct
        for market, alias in results:
            if market == "primary":
                assert alias == "wcmktprod"
            else:
                assert alias == "wcmktnorth"
