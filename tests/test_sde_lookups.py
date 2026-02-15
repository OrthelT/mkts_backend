"""
Tests for SDE type name resolution in src/mkts_backend/utils/utils.py.

Covers:
  - get_type_name(type_id)  — single lookup from sdetypes
  - get_type_names_from_df(df) — bulk lookup with invTypes fallback
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine


class _MockSdeDB:
    """Mimics DatabaseConfig for SDE — creates a fresh engine per access."""
    def __init__(self, db_path):
        self._db_path = db_path

    @property
    def engine(self):
        return create_engine(f"sqlite:///{self._db_path}")

    def verify_db_exists(self):
        return True

    def sync(self):
        pass


# ===== get_type_name ========================================================

class TestGetTypeName:

    def test_returns_name(self, in_memory_sde_db):
        """Basic lookup: type_id 34 → 'Tritanium'."""
        mock_sde = _MockSdeDB(in_memory_sde_db)

        with patch("mkts_backend.utils.utils.DatabaseConfig", return_value=mock_sde):
            from mkts_backend.utils.utils import get_type_name
            result = get_type_name(34)

        assert result == "Tritanium"

    def test_missing_raises(self, in_memory_sde_db):
        """Missing type_id should raise (fetchone()[0] on None)."""
        mock_sde = _MockSdeDB(in_memory_sde_db)

        with patch("mkts_backend.utils.utils.DatabaseConfig", return_value=mock_sde):
            from mkts_backend.utils.utils import get_type_name
            with pytest.raises(TypeError):
                get_type_name(99999)


# ===== get_type_names_from_df ===============================================

class TestGetTypeNamesFromDf:

    def test_all_found_in_sdetypes(self, in_memory_sde_db):
        """All type_ids exist in sdetypes — no fallback needed."""
        mock_sde = _MockSdeDB(in_memory_sde_db)
        df = pd.DataFrame({"type_id": [34, 35, 36]})

        with patch("mkts_backend.utils.utils.sde_db", mock_sde):
            from mkts_backend.utils.utils import get_type_names_from_df
            result = get_type_names_from_df(df)

        assert len(result) == 3
        assert set(result["type_name"]) == {"Tritanium", "Pyerite", "Mexallon"}

    def test_fallback_to_invtypes(self, in_memory_sde_db):
        """type_id 37 (Isogen) only in invTypes — fallback should resolve it."""
        mock_sde = _MockSdeDB(in_memory_sde_db)
        df = pd.DataFrame({"type_id": [34, 37]})

        with patch("mkts_backend.utils.utils.sde_db", mock_sde):
            from mkts_backend.utils.utils import get_type_names_from_df
            result = get_type_names_from_df(df)

        names = set(result["type_name"])
        assert "Tritanium" in names
        assert "Isogen" in names

    def test_returns_correct_columns(self, in_memory_sde_db):
        """Output should have exactly: type_id, type_name, group_name, category_name, category_id."""
        mock_sde = _MockSdeDB(in_memory_sde_db)
        df = pd.DataFrame({"type_id": [34]})

        with patch("mkts_backend.utils.utils.sde_db", mock_sde):
            from mkts_backend.utils.utils import get_type_names_from_df
            result = get_type_names_from_df(df)

        expected_cols = {"type_id", "type_name", "group_name", "category_name", "category_id"}
        assert set(result.columns) == expected_cols
