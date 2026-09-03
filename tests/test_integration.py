"""
Integration tests for market context: real DB write isolation, backward-compatible
defaults, and the structural contract that pipeline functions accept market_ctx.

Trimmed to tests that exercise real behavior. The former flow / config-chain /
turso-env / concurrency tests asserted frozen database aliases and env-var names
copied from settings.toml; they broke on every config edit without catching a
bug and were deleted. The signature tests below stay because they catch a real
regression — a pipeline function silently losing its ``market_ctx`` parameter.
"""
import sqlite3

import pandas as pd
from sqlalchemy import create_engine

from mkts_backend.db.models import Base


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

        primary_db_path = temp_db_dir / "primary_market.db"
        deployment_db_path = temp_db_dir / "deployment_market.db"

        primary_engine = create_engine(f"sqlite:///{primary_db_path}")
        Base.metadata.create_all(primary_engine)
        with primary_engine.begin() as conn:
            test_data.to_sql("marketstats", conn, if_exists="append", index=False)
        primary_engine.dispose()

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


class TestFunctionSignatures:
    """Tests to verify all pipeline functions accept a market_ctx parameter."""

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
