"""
Unit tests for the EFT parser module.
"""

import pytest
from unittest.mock import MagicMock, patch
import tempfile
import os


class TestEFTParserString:
    """Tests for parse_eft_string function."""

    @pytest.fixture
    def mock_sde_engine(self):
        """Create a mock SDE database engine."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()

        # Map of type names to type IDs for testing
        type_mapping = {
            "Hurricane Fleet Issue": 33157,
            "Damage Control II": 2048,
            "Gyrostabilizer II": 519,
            "Large Shield Extender II": 3841,
            "720mm Howitzer Artillery II": 2961,
            "Valkyrie II": 2446,
            "Nanite Repair Paste": 28668,
        }

        def execute_query(query, params=None):
            result = MagicMock()
            if params and "type_name" in params:
                type_name = params["type_name"]
                if type_name in type_mapping:
                    result.fetchone.return_value = (type_mapping[type_name],)
                else:
                    result.fetchone.return_value = None
            return result

        mock_conn.execute = execute_query
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        return mock_engine

    def test_parse_simple_fit(self, mock_sde_engine):
        """Test parsing a simple EFT fit string."""
        from mkts_backend.utils.eft_parser import parse_eft_string

        eft_text = """[Hurricane Fleet Issue, Test Fit]
Damage Control II
Gyrostabilizer II

Large Shield Extender II

720mm Howitzer Artillery II


Valkyrie II x5

Nanite Repair Paste x100
"""
        result = parse_eft_string(eft_text, fit_id=123, sde_engine=mock_sde_engine)

        assert result.ship_name == "Hurricane Fleet Issue"
        assert result.fit_name == "Test Fit"
        assert result.ship_type_id == 33157
        assert len(result.items) > 0

    def test_parse_header_line(self, mock_sde_engine):
        """Test that header line is correctly parsed."""
        from mkts_backend.utils.eft_parser import parse_eft_string

        eft_text = "[Hurricane Fleet Issue, WC HFI 2025]"
        result = parse_eft_string(eft_text, sde_engine=mock_sde_engine)

        assert result.ship_name == "Hurricane Fleet Issue"
        assert result.fit_name == "WC HFI 2025"

    def test_parse_quantity_suffix(self, mock_sde_engine):
        """Test parsing items with quantity suffix (x100)."""
        from mkts_backend.utils.eft_parser import parse_eft_string

        eft_text = """[Hurricane Fleet Issue, Test]


Valkyrie II x5

Nanite Repair Paste x100
"""
        result = parse_eft_string(eft_text, sde_engine=mock_sde_engine)

        # Find the Nanite Repair Paste entry
        paste_items = [i for i in result.items if i["type_name"] == "Nanite Repair Paste"]
        assert len(paste_items) == 1
        assert paste_items[0]["quantity"] == 100

    def test_missing_types_tracked(self, mock_sde_engine):
        """Test that missing type names are tracked."""
        from mkts_backend.utils.eft_parser import parse_eft_string

        eft_text = """[Hurricane Fleet Issue, Test]
Nonexistent Module That Does Not Exist
"""
        result = parse_eft_string(eft_text, sde_engine=mock_sde_engine)

        assert result.has_missing_types
        assert "Nonexistent Module That Does Not Exist" in result.missing_types

    def test_empty_fit(self, mock_sde_engine):
        """Test parsing an empty fit."""
        from mkts_backend.utils.eft_parser import parse_eft_string

        eft_text = "[Hurricane Fleet Issue, Empty Fit]"
        result = parse_eft_string(eft_text, sde_engine=mock_sde_engine)

        assert result.ship_name == "Hurricane Fleet Issue"
        assert result.fit_name == "Empty Fit"
        assert len(result.items) == 0

    def test_slot_assignment(self, mock_sde_engine):
        """Test that slots are correctly assigned."""
        from mkts_backend.utils.eft_parser import parse_eft_string

        eft_text = """[Hurricane Fleet Issue, Test]
Damage Control II
Gyrostabilizer II

Large Shield Extender II

720mm Howitzer Artillery II
"""
        result = parse_eft_string(eft_text, sde_engine=mock_sde_engine)

        # Check slot assignments
        dc2 = next((i for i in result.items if i["type_name"] == "Damage Control II"), None)
        assert dc2 is not None
        assert dc2["flag"].startswith("LoSlot")

        lse = next((i for i in result.items if i["type_name"] == "Large Shield Extender II"), None)
        assert lse is not None
        assert lse["flag"].startswith("MedSlot")


class TestEFTParserFile:
    """Tests for parse_eft_file function."""

    @pytest.fixture
    def mock_sde_engine(self):
        """Create a mock SDE database engine."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()

        type_mapping = {
            "Hurricane Fleet Issue": 33157,
            "Damage Control II": 2048,
            "Large Shield Extender II": 3841,
            "720mm Howitzer Artillery II": 2961,
            "Valkyrie II": 2446,
        }

        def execute_query(query, params=None):
            result = MagicMock()
            if params and "type_name" in params:
                type_name = params["type_name"]
                if type_name in type_mapping:
                    result.fetchone.return_value = (type_mapping[type_name],)
                else:
                    result.fetchone.return_value = None
            return result

        mock_conn.execute = execute_query
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_engine.connect.return_value = mock_conn

        return mock_engine

    def test_parse_file_not_found(self):
        """Test that FileNotFoundError is raised for missing file."""
        from mkts_backend.utils.eft_parser import parse_eft_file

        with pytest.raises(FileNotFoundError):
            parse_eft_file("/nonexistent/path/to/fit.txt")

    def test_parse_real_fit_file(self, mock_sde_engine):
        """Test parsing a real fit file."""
        from mkts_backend.utils.eft_parser import parse_eft_file

        # Create a temporary EFT file
        eft_content = """[Hurricane Fleet Issue, Test Fit]
Damage Control II

Large Shield Extender II

720mm Howitzer Artillery II


Valkyrie II x5
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(eft_content)
            temp_path = f.name

        try:
            with patch('mkts_backend.utils.eft_parser._sde_db') as mock_db:
                mock_db.engine = mock_sde_engine
                result = parse_eft_file(temp_path, fit_id=123, sde_engine=mock_sde_engine)

                assert result.ship_name == "Hurricane Fleet Issue"
                assert result.fit_name == "Test Fit"
        finally:
            os.unlink(temp_path)


class TestAggregateFitItems:
    """Tests for aggregate_fit_items function."""

    def test_aggregate_duplicate_items(self):
        """Test that duplicate items are aggregated."""
        from mkts_backend.utils.eft_parser import FitParseResult, aggregate_fit_items

        # Create a parse result with duplicate items
        result = FitParseResult(
            items=[
                {"type_id": 100, "type_name": "Item A", "quantity": 5},
                {"type_id": 100, "type_name": "Item A", "quantity": 3},
                {"type_id": 200, "type_name": "Item B", "quantity": 1},
            ],
            ship_name="Test Ship",
            ship_type_id=12345,
            fit_name="Test Fit",
            missing_types=[],
        )

        aggregated = aggregate_fit_items(result)

        assert 100 in aggregated
        assert aggregated[100]["quantity"] == 8  # 5 + 3
        assert 200 in aggregated
        assert aggregated[200]["quantity"] == 1


class TestSlotYielder:
    """Tests for the slot yielder generator."""

    def test_slot_order(self):
        """Test that slots are yielded in correct order."""
        from mkts_backend.utils.eft_parser import _slot_yielder

        gen = _slot_yielder()

        assert next(gen) == "LoSlot"
        assert next(gen) == "MedSlot"
        assert next(gen) == "HiSlot"
        assert next(gen) == "RigSlot"
        assert next(gen) == "DroneBay"
        assert next(gen) == "Cargo"
        assert next(gen) == "Cargo"  # Should keep returning Cargo
