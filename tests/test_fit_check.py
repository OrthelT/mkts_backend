"""
Integration tests for the fit-check CLI command.
"""

import pytest
from unittest.mock import MagicMock, patch
import sqlite3
import tempfile
import os
from pathlib import Path


class TestFitCheckMarketData:
    """Tests for market data retrieval."""

    @pytest.fixture
    def temp_market_db(self, tmp_path):
        """Create a temporary market database with test data."""
        db_path = tmp_path / "wcmktprod.db"
        conn = sqlite3.connect(str(db_path))

        # Create marketstats table
        conn.execute("""
            CREATE TABLE marketstats (
                type_id INTEGER PRIMARY KEY,
                type_name TEXT,
                price REAL,
                min_price REAL,
                avg_price REAL,
                avg_volume REAL,
                total_volume_remain INTEGER,
                days_remaining REAL,
                last_update TEXT,
                group_id INTEGER,
                group_name TEXT,
                category_id INTEGER,
                category_name TEXT
            )
        """)

        # Insert test data
        conn.execute("""
            INSERT INTO marketstats VALUES
            (33157, 'Hurricane Fleet Issue', 250000000, 245000000, 260000000, 50, 100, 30.5, '2025-01-01', 6, 'Battlecruiser', 6, 'Ship'),
            (2048, 'Damage Control II', 1500000, 1400000, 1600000, 500, 5000, 45.2, '2025-01-01', 7, 'Damage Control', 7, 'Module'),
            (519, 'Gyrostabilizer II', 2000000, 1900000, 2100000, 300, 3000, 40.0, '2025-01-01', 8, 'Gyrostabilizer', 7, 'Module'),
            (3841, 'Large Shield Extender II', 3500000, 3400000, 3600000, 200, 2000, 35.0, '2025-01-01', 9, 'Shield Extender', 7, 'Module')
        """)

        # Create marketorders table for fallback testing
        conn.execute("""
            CREATE TABLE marketorders (
                order_id INTEGER PRIMARY KEY,
                type_id INTEGER,
                price REAL,
                volume_remain INTEGER,
                is_buy_order INTEGER
            )
        """)

        # Insert fallback order data
        conn.execute("""
            INSERT INTO marketorders VALUES
            (1, 99999, 1000000, 100, 0),
            (2, 99999, 1100000, 50, 0),
            (3, 99999, 1200000, 75, 0)
        """)

        conn.commit()
        conn.close()

        return db_path

    @pytest.fixture
    def temp_sde_db(self, tmp_path):
        """Create a temporary SDE database with test data."""
        db_path = tmp_path / "sde.db"
        conn = sqlite3.connect(str(db_path))

        conn.execute("""
            CREATE TABLE inv_info (
                typeID INTEGER PRIMARY KEY,
                typeName TEXT,
                groupID INTEGER,
                groupName TEXT,
                categoryID INTEGER,
                categoryName TEXT
            )
        """)

        conn.execute("""
            INSERT INTO inv_info VALUES
            (33157, 'Hurricane Fleet Issue', 6, 'Battlecruiser', 6, 'Ship'),
            (2048, 'Damage Control II', 7, 'Damage Control', 7, 'Module'),
            (519, 'Gyrostabilizer II', 8, 'Gyrostabilizer', 7, 'Module'),
            (3841, 'Large Shield Extender II', 9, 'Shield Extender', 7, 'Module'),
            (99999, 'Fallback Item', 10, 'Test Group', 8, 'Test Category')
        """)

        conn.commit()
        conn.close()

        return db_path

    def test_get_marketstats_data(self, temp_market_db, tmp_path):
        """Test retrieving data from marketstats."""
        from mkts_backend.cli_tools.fit_check import _get_marketstats_data

        with patch('mkts_backend.cli_tools.fit_check.DatabaseConfig') as mock_db_config:
            # Setup mock
            mock_instance = MagicMock()
            mock_db_config.return_value = mock_instance

            from sqlalchemy import create_engine
            mock_instance.engine = create_engine(f"sqlite:///{temp_market_db}")

            # Test the function
            with patch('mkts_backend.config.market_context.MarketContext') as mock_ctx:
                mock_ctx.database_alias = "wcmkt"
                results = _get_marketstats_data([33157, 2048], market_ctx=None)

            assert 33157 in results
            assert results[33157]["type_name"] == "Hurricane Fleet Issue"
            assert results[33157]["price"] == 250000000

    def test_get_fallback_data(self, temp_market_db, tmp_path):
        """Test fallback data retrieval from marketorders."""
        from mkts_backend.cli_tools.fit_check import _get_fallback_data

        with patch('mkts_backend.cli_tools.fit_check.DatabaseConfig') as mock_db_config:
            mock_instance = MagicMock()
            mock_db_config.return_value = mock_instance

            from sqlalchemy import create_engine
            mock_instance.engine = create_engine(f"sqlite:///{temp_market_db}")

            result = _get_fallback_data(99999, market_ctx=None)

            assert result is not None
            assert result["type_id"] == 99999
            assert result["total_volume_remain"] == 225  # 100 + 50 + 75
            assert result["is_fallback"] == True

    def test_get_fallback_data_no_orders(self, temp_market_db):
        """Test fallback returns None when no orders exist."""
        from mkts_backend.cli_tools.fit_check import _get_fallback_data

        with patch('mkts_backend.cli_tools.fit_check.DatabaseConfig') as mock_db_config:
            mock_instance = MagicMock()
            mock_db_config.return_value = mock_instance

            from sqlalchemy import create_engine
            mock_instance.engine = create_engine(f"sqlite:///{temp_market_db}")

            result = _get_fallback_data(88888, market_ctx=None)  # Non-existent type

            assert result is None


class TestFitCheckDisplay:
    """Tests for Rich display formatting."""

    def test_format_isk_billions(self):
        """Test ISK formatting for billions."""
        from mkts_backend.cli_tools.rich_display import format_isk

        assert format_isk(1_500_000_000) == "1.50B ISK"
        assert format_isk(250_000_000) == "250.00M ISK"

    def test_format_isk_millions(self):
        """Test ISK formatting for millions."""
        from mkts_backend.cli_tools.rich_display import format_isk

        assert format_isk(5_000_000) == "5.00M ISK"
        assert format_isk(1_500_000) == "1.50M ISK"

    def test_format_isk_thousands(self):
        """Test ISK formatting for thousands."""
        from mkts_backend.cli_tools.rich_display import format_isk

        assert format_isk(50_000) == "50.00K ISK"
        assert format_isk(1_500) == "1.50K ISK"

    def test_format_isk_none(self):
        """Test ISK formatting for None value."""
        from mkts_backend.cli_tools.rich_display import format_isk

        assert format_isk(None) == "N/A"

    def test_format_quantity(self):
        """Test quantity formatting."""
        from mkts_backend.cli_tools.rich_display import format_quantity

        assert format_quantity(1000) == "1,000"
        assert format_quantity(1000000) == "1,000,000"
        assert format_quantity(None) == "0"

    def test_format_fits(self):
        """Test fits formatting."""
        from mkts_backend.cli_tools.rich_display import format_fits

        assert format_fits(10.5) == "10.5"
        assert format_fits(0.5) == "0.5"
        assert format_fits(None) == "N/A"


class TestFitCheckCommand:
    """Tests for the fit-check CLI command."""

    @pytest.fixture
    def temp_fit_file(self, tmp_path):
        """Create a temporary EFT fit file."""
        fit_content = """[Hurricane Fleet Issue, Test Fit]
Damage Control II
Gyrostabilizer II

Large Shield Extender II

"""
        fit_path = tmp_path / "test_fit.txt"
        fit_path.write_text(fit_content)
        return str(fit_path)

    def test_fit_check_command_file_not_found(self):
        """Test fit-check with non-existent file."""
        from mkts_backend.cli_tools.fit_check import fit_check_command

        with patch('mkts_backend.cli_tools.fit_check.MarketContext') as mock_ctx:
            mock_ctx.from_settings.return_value = MagicMock(
                name="Test Market",
                database_alias="wcmkt"
            )

            result = fit_check_command(
                file_path="/nonexistent/path.txt",
                market_alias="primary"
            )

            assert result == False

    def test_fit_check_command_no_input(self):
        """Test fit-check with neither file nor paste."""
        from mkts_backend.cli_tools.fit_check import fit_check_command

        result = fit_check_command(file_path=None, eft_text=None)
        assert result == False

    def test_fit_check_command_invalid_market(self, temp_fit_file):
        """Test fit-check with invalid market alias."""
        from mkts_backend.cli_tools.fit_check import fit_check_command

        with patch('mkts_backend.cli_tools.fit_check.MarketContext') as mock_ctx:
            mock_ctx.from_settings.side_effect = ValueError("Unknown market")
            mock_ctx.list_available.return_value = ["primary", "deployment"]

            result = fit_check_command(
                file_path=temp_fit_file,
                market_alias="invalid_market"
            )

            assert result == False


class TestGetFitMarketStatus:
    """Tests for the get_fit_market_status function."""

    def test_calculates_fits_correctly(self):
        """Test that fits are calculated correctly."""
        from mkts_backend.cli_tools.fit_check import get_fit_market_status
        from mkts_backend.utils.eft_parser import FitParseResult

        # Create a mock parse result
        parse_result = FitParseResult(
            items=[
                {"type_id": 100, "type_name": "Test Module", "quantity": 2},
            ],
            ship_name="Test Ship",
            ship_type_id=200,
            fit_name="Test Fit",
            missing_types=[],
        )

        with patch('mkts_backend.cli_tools.fit_check._get_marketstats_data') as mock_stats:
            with patch('mkts_backend.cli_tools.fit_check._get_target_for_fit') as mock_target:
                mock_target.return_value = None
                mock_stats.return_value = {
                    100: {"type_name": "Test Module", "price": 1000000, "avg_price": 1100000, "total_volume_remain": 100},
                    200: {"type_name": "Test Ship", "price": 50000000, "avg_price": 55000000, "total_volume_remain": 10},
                }

                result = get_fit_market_status(parse_result, market_ctx=None)

                # Find the module entry
                module_entry = next(e for e in result.market_data if e["type_id"] == 100)
                assert module_entry["fits"] == 50.0  # 100 stock / 2 qty

                # Find the ship entry
                ship_entry = next(e for e in result.market_data if e["type_id"] == 200)
                assert ship_entry["fits"] == 10.0  # 10 stock / 1 qty

    def test_calculates_fit_price(self):
        """Test that fit price is calculated correctly."""
        from mkts_backend.cli_tools.fit_check import get_fit_market_status
        from mkts_backend.utils.eft_parser import FitParseResult

        parse_result = FitParseResult(
            items=[
                {"type_id": 100, "type_name": "Test Module", "quantity": 5},
            ],
            ship_name="Test Ship",
            ship_type_id=200,
            fit_name="Test Fit",
            missing_types=[],
        )

        with patch('mkts_backend.cli_tools.fit_check._get_marketstats_data') as mock_stats:
            with patch('mkts_backend.cli_tools.fit_check._get_target_for_fit') as mock_target:
                mock_target.return_value = None
                mock_stats.return_value = {
                    100: {"type_name": "Test Module", "price": 1000000, "avg_price": 1100000, "total_volume_remain": 100},
                    200: {"type_name": "Test Ship", "price": 50000000, "avg_price": 55000000, "total_volume_remain": 10},
                }

                result = get_fit_market_status(parse_result, market_ctx=None)

                # Find the module entry
                module_entry = next(e for e in result.market_data if e["type_id"] == 100)
                assert module_entry["fit_price"] == 5000000  # 5 * 1000000


class TestFitCheckResult:
    """Tests for FitCheckResult dataclass and export methods."""

    def test_missing_for_target_calculation(self):
        """Test that missing_for_target calculates correctly."""
        from mkts_backend.cli_tools.fit_check import FitCheckResult

        market_data = [
            {"type_id": 100, "type_name": "Module A", "fits": 50.0, "fit_qty": 2},
            {"type_id": 101, "type_name": "Module B", "fits": 80.0, "fit_qty": 1},
            {"type_id": 102, "type_name": "Module C", "fits": 120.0, "fit_qty": 3},
        ]

        result = FitCheckResult(
            fit_name="Test Fit",
            ship_name="Test Ship",
            ship_type_id=12345,
            market_data=market_data,
            total_fit_cost=100000000,
            min_fits=50.0,
            target=100,
            market_name="primary",
        )

        missing = result.missing_for_target
        assert len(missing) == 2  # Module A and B are below target
        # Module A: (100 - 50) * 2 = 100
        assert any(m["type_name"] == "Module A" and m["qty_needed"] == 100 for m in missing)
        # Module B: (100 - 80) * 1 = 20
        assert any(m["type_name"] == "Module B" and m["qty_needed"] == 20 for m in missing)

    def test_missing_for_target_no_target(self):
        """Test that missing_for_target returns empty when no target."""
        from mkts_backend.cli_tools.fit_check import FitCheckResult

        result = FitCheckResult(
            fit_name="Test Fit",
            ship_name="Test Ship",
            ship_type_id=12345,
            market_data=[{"type_id": 100, "type_name": "Module A", "fits": 50.0, "fit_qty": 2}],
            total_fit_cost=100000000,
            min_fits=50.0,
            target=None,
            market_name="primary",
        )

        assert result.missing_for_target == []

    def test_to_multibuy_format(self):
        """Test multi-buy export format."""
        from mkts_backend.cli_tools.fit_check import FitCheckResult

        market_data = [
            {"type_id": 100, "type_name": "Damage Control II", "fits": 50.0, "fit_qty": 1},
            {"type_id": 101, "type_name": "Gyrostabilizer II", "fits": 80.0, "fit_qty": 3},
            {"type_id": 102, "type_name": "Large Shield Extender II", "fits": 120.0, "fit_qty": 2},
        ]

        result = FitCheckResult(
            fit_name="Test Fit",
            ship_name="Test Ship",
            ship_type_id=12345,
            market_data=market_data,
            total_fit_cost=100000000,
            min_fits=50.0,
            target=100,
            market_name="primary",
        )

        multibuy = result.to_multibuy()
        lines = multibuy.strip().split("\n")
        assert len(lines) == 2
        # Check format: item_name space qty_needed
        assert "Damage Control II 50" in lines
        assert "Gyrostabilizer II 60" in lines

    def test_to_csv_export(self, tmp_path):
        """Test CSV export."""
        from mkts_backend.cli_tools.fit_check import FitCheckResult

        market_data = [
            {"type_id": 100, "type_name": "Module A", "market_stock": 100, "fit_qty": 2, "fits": 50.0, "price": 1000000, "fit_price": 2000000},
            {"type_id": 101, "type_name": "Module B", "market_stock": 80, "fit_qty": 1, "fits": 80.0, "price": 500000, "fit_price": 500000},
        ]

        result = FitCheckResult(
            fit_name="Test Fit",
            ship_name="Test Ship",
            ship_type_id=12345,
            market_data=market_data,
            total_fit_cost=2500000,
            min_fits=50.0,
            target=100,
            market_name="primary",
        )

        csv_path = tmp_path / "test_export.csv"
        exported_path = result.to_csv(str(csv_path))

        assert Path(exported_path).exists()

        import csv
        with open(exported_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert "qty_needed" in rows[0].keys()
        assert rows[0]["type_name"] == "Module A"
        assert int(rows[0]["qty_needed"]) == 100  # (100 - 50) * 2

    def test_to_csv_export_no_target(self, tmp_path):
        """Test CSV export without target column."""
        from mkts_backend.cli_tools.fit_check import FitCheckResult

        market_data = [
            {"type_id": 100, "type_name": "Module A", "market_stock": 100, "fit_qty": 2, "fits": 50.0, "price": 1000000, "fit_price": 2000000},
        ]

        result = FitCheckResult(
            fit_name="Test Fit",
            ship_name="Test Ship",
            ship_type_id=12345,
            market_data=market_data,
            total_fit_cost=2000000,
            min_fits=50.0,
            target=None,
            market_name="primary",
        )

        csv_path = tmp_path / "test_export_no_target.csv"
        exported_path = result.to_csv(str(csv_path))

        import csv
        with open(exported_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert "qty_needed" not in rows[0].keys()
