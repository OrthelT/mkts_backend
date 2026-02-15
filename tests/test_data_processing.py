"""
Tests for src/mkts_backend/processing/data_processing.py

Covers the core calculation pipeline:
  - calculate_5_percentile_price
  - calculate_market_stats
  - fill_nulls_from_history
  - calculate_doctrine_stats
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock, PropertyMock
from sqlalchemy import create_engine


# ---------------------------------------------------------------------------
# Helper: build a fake DatabaseConfig-like object from a db file path
# ---------------------------------------------------------------------------
class _MockDB:
    """Mimics DatabaseConfig — creates a fresh engine on each access
    (mirrors production behavior where engine.dispose() is called frequently).
    """
    def __init__(self, db_path):
        self._db_path = db_path

    @property
    def engine(self):
        return create_engine(f"sqlite:///{self._db_path}")


# ===== calculate_5_percentile_price =========================================

class TestCalculate5PercentilePrice:

    def test_basic(self, in_memory_market_db):
        """Verify percentile math with known sell orders for type_id=34."""
        mock_db = _MockDB(in_memory_market_db)
        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import calculate_5_percentile_price
            result = calculate_5_percentile_price()

        assert "type_id" in result.columns
        assert "5_perc_price" in result.columns
        # type_id 34 has 10 sell orders (5.0..14.0); 5th percentile ≈ 5.45
        row34 = result[result.type_id == 34]
        assert len(row34) == 1
        assert row34.iloc[0]["5_perc_price"] == pytest.approx(5.45, abs=0.01)

    def test_single_order(self, in_memory_market_db):
        """Single order per type → percentile equals that price."""
        mock_db = _MockDB(in_memory_market_db)
        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import calculate_5_percentile_price
            result = calculate_5_percentile_price()

        row35 = result[result.type_id == 35]
        assert len(row35) == 1
        assert row35.iloc[0]["5_perc_price"] == 10.0

    def test_excludes_buy_orders(self, in_memory_market_db):
        """Buy orders (is_buy_order=1) must not appear in results."""
        mock_db = _MockDB(in_memory_market_db)
        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import calculate_5_percentile_price
            result = calculate_5_percentile_price()

        # type_id 34 buy order at 3.0 should not pull the percentile below 5.0
        row34 = result[result.type_id == 34]
        assert row34.iloc[0]["5_perc_price"] >= 5.0


# ===== fill_nulls_from_history ==============================================

class TestFillNullsFromHistory:

    def test_no_nulls_returns_unchanged(self, in_memory_market_db):
        """Early-exit: if no nulls exist, return the same DataFrame."""
        from mkts_backend.processing.data_processing import fill_nulls_from_history

        stats = pd.DataFrame({
            "type_id": [34, 35],
            "avg_price": [8.0, 10.0],
            "min_price": [5.0, 10.0],
            "price": [5.5, 10.0],
            "avg_volume": [1900.0, 500.0],
            "days_remaining": [2.9, 1.0],
            "total_volume_remain": [5500, 500],
        })
        result = fill_nulls_from_history(stats)
        pd.testing.assert_frame_equal(result, stats)

    def test_fills_from_history(self, in_memory_market_db):
        """Null price/volume fields should be filled from market_history averages."""
        mock_db = _MockDB(in_memory_market_db)

        stats = pd.DataFrame({
            "type_id": [34],
            "avg_price": [np.nan],
            "min_price": [np.nan],
            "price": [np.nan],
            "avg_volume": [np.nan],
            "days_remaining": [0.0],
            "total_volume_remain": [5500],
        })

        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import fill_nulls_from_history
            result = fill_nulls_from_history(stats)

        # avg of history averages for type_id 34: (8.5+9.0)/2 = 8.75
        assert result.iloc[0]["avg_price"] == pytest.approx(8.75, abs=0.01)
        assert result.iloc[0]["min_price"] == pytest.approx(8.75, abs=0.01)
        assert result.iloc[0]["price"] == pytest.approx(8.75, abs=0.01)
        # avg of history volumes: (2000+1800)/2 = 1900
        assert result.iloc[0]["avg_volume"] == pytest.approx(1900.0, abs=1.0)

    def test_no_history_fills_zero(self, in_memory_market_db):
        """When no history exists for a type_id, nulls should become 0."""
        mock_db = _MockDB(in_memory_market_db)

        stats = pd.DataFrame({
            "type_id": [36],  # Mexallon — no history rows in fixture
            "avg_price": [np.nan],
            "min_price": [np.nan],
            "price": [np.nan],
            "avg_volume": [np.nan],
            "days_remaining": [0.0],
            "total_volume_remain": [0],
        })

        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import fill_nulls_from_history
            result = fill_nulls_from_history(stats)

        # No history → final fillna(0) should set everything to 0
        assert result.iloc[0]["avg_price"] == 0
        assert result.iloc[0]["price"] == 0
        assert result.iloc[0]["avg_volume"] == 0


# ===== calculate_doctrine_stats =============================================

class TestCalculateDoctrineStats:

    def test_basic(self, in_memory_market_db):
        """Verify doctrine stats maps market data onto doctrine items."""
        mock_db = _MockDB(in_memory_market_db)
        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import calculate_doctrine_stats
            result = calculate_doctrine_stats()

        assert len(result) == 3  # 3 doctrine rows in fixture
        assert "fits_on_mkt" in result.columns
        assert "total_stock" in result.columns
        assert "price" in result.columns

    def test_fits_on_mkt_calculation(self, in_memory_market_db):
        """fits_on_mkt = total_stock / fit_qty, rounded to 1 decimal."""
        mock_db = _MockDB(in_memory_market_db)
        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import calculate_doctrine_stats
            result = calculate_doctrine_stats()

        # doctrine row 1: type_id=34, fit_qty=100, total_stock maps from marketstats=5500
        row = result[result.id == 1].iloc[0]
        expected = round(5500 / 100, 1)  # 55.0
        assert row["fits_on_mkt"] == expected

    def test_zero_fit_qty(self, in_memory_market_db):
        """fit_qty=0 should produce fits_on_mkt=0 (safe division)."""
        from sqlalchemy import text as sa_text

        # Insert a doctrine row with fit_qty=0
        engine = create_engine(f"sqlite:///{in_memory_market_db}")
        with engine.connect() as conn:
            conn.execute(sa_text("""
                INSERT INTO doctrines
                VALUES (4,3,587,'Rifter',0,34,'Tritanium',0,0,0,0,0,0,18,'Mineral',4,'Material',NULL)
            """))
            conn.commit()
        engine.dispose()

        mock_db = _MockDB(in_memory_market_db)
        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import calculate_doctrine_stats
            result = calculate_doctrine_stats()

        row = result[result.id == 4].iloc[0]
        assert row["fits_on_mkt"] == 0

    def test_nan_cleaning(self, in_memory_market_db):
        """inf and NaN values in numeric columns should be replaced with 0."""
        mock_db = _MockDB(in_memory_market_db)
        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            from mkts_backend.processing.data_processing import calculate_doctrine_stats
            result = calculate_doctrine_stats()

        numeric_cols = result.select_dtypes(include=["number"]).columns
        for col in numeric_cols:
            assert not result[col].isna().any(), f"NaN found in column {col}"
            assert not np.isinf(result[col]).any(), f"inf found in column {col}"


# ===== calculate_market_stats ===============================================

class TestCalculateMarketStats:

    def test_merges_data(self, in_memory_market_db):
        """Verify join across watchlist/orders/history and merge with percentile."""
        mock_db = _MockDB(in_memory_market_db)

        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            # Reset the global _wcmkt_db so _get_db uses our mock
            import mkts_backend.processing.data_processing as dp
            dp._wcmkt_db = None

            result = dp.calculate_market_stats()

        # Should have rows for all 3 watchlist items
        assert len(result) == 3
        # Must contain all MarketStats columns
        from mkts_backend.db.models import MarketStats
        expected_cols = set(MarketStats.__table__.columns.keys())
        assert set(result.columns) == expected_cols

    def test_handles_zero_volume(self, in_memory_market_db):
        """When avg_volume is 0 or NULL, days_remaining should default to 30."""
        # Mexallon (type_id=36) has no orders and no history → avg_volume=NULL
        mock_db = _MockDB(in_memory_market_db)

        with patch("mkts_backend.processing.data_processing._get_db", return_value=mock_db):
            import mkts_backend.processing.data_processing as dp
            dp._wcmkt_db = None
            result = dp.calculate_market_stats()

        row36 = result[result.type_id == 36]
        assert len(row36) == 1
        # days_remaining defaults to 30 when avg_volume is 0/NULL (per SQL CASE)
        assert row36.iloc[0]["days_remaining"] == 30.0
