"""
Tests for verify_db_exists() and related database initialization methods.

These tests verify that:
1. verify_db_exists() correctly handles all four database state scenarios
2. needs_init() is a pure predicate with no side effects
3. nuke methods properly clean up files and handle errors
4. sync() is never called on a db file that lacks its -info metadata
"""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock


class TestVerifyDbExists:
    """Tests for verify_db_exists() handling all four database state scenarios."""

    @pytest.fixture
    def temp_db_path(self, tmp_path):
        """Create a temporary path for test database files."""
        return tmp_path / "test.db"

    @pytest.fixture
    def mock_db_config(self, temp_db_path):
        """Create a DatabaseConfig with mocked sync and temp path."""
        from mkts_backend.config.config import DatabaseConfig

        with patch.object(DatabaseConfig, '__init__', lambda self, *args, **kwargs: None):
            db = DatabaseConfig()
            db.path = str(temp_db_path)
            db.alias = "test"
            db.turso_url = "libsql://test.turso.io"
            db.token = "test-token"
            db._engine = None
            yield db

    def _create_db_file(self, path: Path):
        """Helper to create a bare database file (simulates sqlite.connect)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def _create_metadata_file(self, path: Path):
        """Helper to create a metadata -info file."""
        info_path = Path(f"{path}-info")
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_data = {"generation": 1, "durable_frame_num": 100}
        info_path.write_text(json.dumps(info_data))

    def test_case1_neither_exists_syncs_and_creates_both(self, mock_db_config, temp_db_path):
        """
        Case 1: Neither db nor metadata exists.
        Expected: sync() is called, then both files are verified.
        """
        # Ensure neither file exists
        assert not temp_db_path.exists()
        assert not Path(f"{temp_db_path}-info").exists()

        # Mock sync to create both files (simulating successful remote sync)
        def mock_sync():
            self._create_db_file(temp_db_path)
            self._create_metadata_file(temp_db_path)

        with patch.object(mock_db_config, 'sync', side_effect=mock_sync):
            result = mock_db_config.verify_db_exists()

        assert result is True
        assert temp_db_path.exists()
        assert Path(f"{temp_db_path}-info").exists()

    def test_case2_both_exist_returns_true_no_sync(self, mock_db_config, temp_db_path):
        """
        Case 2: Both db and metadata exist (valid state).
        Expected: Returns True immediately, sync() is NOT called.
        """
        # Create both files
        self._create_db_file(temp_db_path)
        self._create_metadata_file(temp_db_path)

        # Mock sync to fail if called (it shouldn't be)
        with patch.object(mock_db_config, 'sync', side_effect=AssertionError("sync should not be called")):
            result = mock_db_config.verify_db_exists()

        assert result is True

    def test_case3_db_without_metadata_nukes_then_syncs(self, mock_db_config, temp_db_path):
        """
        Case 3: DB exists without metadata (improperly created).
        Expected: DB is deleted first, then sync() creates both files.

        CRITICAL: sync() must NOT be called while db file exists without metadata.
        """
        # Create only the db file (simulates bare sqlite.connect)
        self._create_db_file(temp_db_path)
        assert temp_db_path.exists()
        assert not Path(f"{temp_db_path}-info").exists()

        sync_called_when_db_existed = False

        def mock_sync():
            nonlocal sync_called_when_db_existed
            # Check if db file exists when sync is called - it should NOT
            if temp_db_path.exists():
                sync_called_when_db_existed = True
            # Simulate successful sync
            self._create_db_file(temp_db_path)
            self._create_metadata_file(temp_db_path)

        with patch.object(mock_db_config, 'sync', side_effect=mock_sync):
            result = mock_db_config.verify_db_exists()

        assert result is True
        assert not sync_called_when_db_existed, "sync() was called while db existed without metadata!"
        assert temp_db_path.exists()
        assert Path(f"{temp_db_path}-info").exists()

    def test_case4_orphaned_metadata_nukes_then_syncs(self, mock_db_config, temp_db_path):
        """
        Case 4: Metadata exists without db (orphaned metadata).
        Expected: Metadata is deleted first, then sync() creates both files.
        """
        # Create only the metadata file
        self._create_metadata_file(temp_db_path)
        assert not temp_db_path.exists()
        assert Path(f"{temp_db_path}-info").exists()

        def mock_sync():
            self._create_db_file(temp_db_path)
            self._create_metadata_file(temp_db_path)

        with patch.object(mock_db_config, 'sync', side_effect=mock_sync):
            result = mock_db_config.verify_db_exists()

        assert result is True
        assert temp_db_path.exists()
        assert Path(f"{temp_db_path}-info").exists()

    def test_sync_failure_returns_false(self, mock_db_config, temp_db_path):
        """
        Test that if sync() fails to create files, verify_db_exists returns False.
        """
        # Neither file exists
        assert not temp_db_path.exists()

        # Mock sync that does nothing (simulates failure)
        with patch.object(mock_db_config, 'sync', return_value=None):
            result = mock_db_config.verify_db_exists()

        assert result is False

    def test_nuke_failure_returns_false(self, mock_db_config, temp_db_path):
        """
        Test that if nuke fails, verify_db_exists returns False without calling sync.
        """
        # Create db file without metadata
        self._create_db_file(temp_db_path)

        # Mock _nuke_db_file to fail
        with patch.object(mock_db_config, '_nuke_db_file', return_value=False):
            with patch.object(mock_db_config, 'sync', side_effect=AssertionError("sync should not be called after nuke failure")):
                result = mock_db_config.verify_db_exists()

        assert result is False


class TestNeedsInit:
    """Tests for needs_init() as a pure predicate."""

    @pytest.fixture
    def temp_db_path(self, tmp_path):
        return tmp_path / "test.db"

    @pytest.fixture
    def mock_db_config(self, temp_db_path):
        from mkts_backend.config.config import DatabaseConfig

        with patch.object(DatabaseConfig, '__init__', lambda self, *args, **kwargs: None):
            db = DatabaseConfig()
            db.path = str(temp_db_path)
            db.alias = "test"
            yield db

    def _create_db_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def _create_metadata_file(self, path: Path):
        info_path = Path(f"{path}-info")
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text(json.dumps({"generation": 1}))

    def test_needs_init_true_when_neither_exists(self, mock_db_config, temp_db_path):
        """Returns True when neither db nor metadata exists."""
        assert not temp_db_path.exists()
        assert not Path(f"{temp_db_path}-info").exists()

        assert mock_db_config.needs_init() is True

    def test_needs_init_false_when_both_exist(self, mock_db_config, temp_db_path):
        """Returns False when both db and metadata exist."""
        self._create_db_file(temp_db_path)
        self._create_metadata_file(temp_db_path)

        assert mock_db_config.needs_init() is False

    def test_needs_init_true_when_only_db_exists(self, mock_db_config, temp_db_path):
        """Returns True when only db exists (no metadata)."""
        self._create_db_file(temp_db_path)

        assert mock_db_config.needs_init() is True

    def test_needs_init_true_when_only_metadata_exists(self, mock_db_config, temp_db_path):
        """Returns True when only metadata exists (no db)."""
        self._create_metadata_file(temp_db_path)

        assert mock_db_config.needs_init() is True

    def test_needs_init_has_no_side_effects(self, mock_db_config, temp_db_path):
        """Verify needs_init() does not modify any files."""
        # Create db without metadata
        self._create_db_file(temp_db_path)

        db_mtime_before = temp_db_path.stat().st_mtime

        # Call needs_init multiple times
        mock_db_config.needs_init()
        mock_db_config.needs_init()
        mock_db_config.needs_init()

        # File should still exist with same mtime
        assert temp_db_path.exists()
        assert temp_db_path.stat().st_mtime == db_mtime_before


class TestNukeMethods:
    """Tests for _nuke_db_file, _nuke_metadata_file, and nuke_db methods."""

    @pytest.fixture
    def temp_db_path(self, tmp_path):
        return tmp_path / "test.db"

    @pytest.fixture
    def mock_db_config(self, temp_db_path):
        from mkts_backend.config.config import DatabaseConfig

        with patch.object(DatabaseConfig, '__init__', lambda self, *args, **kwargs: None):
            db = DatabaseConfig()
            db.path = str(temp_db_path)
            db.alias = "test"
            yield db

    def _create_db_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def _create_metadata_file(self, path: Path):
        info_path = Path(f"{path}-info")
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text(json.dumps({"generation": 1}))

    def test_nuke_db_file_deletes_existing_file(self, mock_db_config, temp_db_path):
        """_nuke_db_file deletes the db file when it exists."""
        self._create_db_file(temp_db_path)
        assert temp_db_path.exists()

        result = mock_db_config._nuke_db_file()

        assert result is True
        assert not temp_db_path.exists()

    def test_nuke_db_file_returns_true_when_not_exists(self, mock_db_config, temp_db_path):
        """_nuke_db_file returns True when file doesn't exist."""
        assert not temp_db_path.exists()

        result = mock_db_config._nuke_db_file()

        assert result is True

    def test_nuke_metadata_file_deletes_existing_file(self, mock_db_config, temp_db_path):
        """_nuke_metadata_file deletes the metadata file when it exists."""
        self._create_metadata_file(temp_db_path)
        info_path = Path(f"{temp_db_path}-info")
        assert info_path.exists()

        result = mock_db_config._nuke_metadata_file()

        assert result is True
        assert not info_path.exists()

    def test_nuke_metadata_file_returns_true_when_not_exists(self, mock_db_config, temp_db_path):
        """_nuke_metadata_file returns True when file doesn't exist."""
        info_path = Path(f"{temp_db_path}-info")
        assert not info_path.exists()

        result = mock_db_config._nuke_metadata_file()

        assert result is True

    def test_nuke_db_deletes_both_files(self, mock_db_config, temp_db_path):
        """nuke_db deletes both db and metadata files."""
        self._create_db_file(temp_db_path)
        self._create_metadata_file(temp_db_path)
        info_path = Path(f"{temp_db_path}-info")

        assert temp_db_path.exists()
        assert info_path.exists()

        result = mock_db_config.nuke_db()

        assert result is True
        assert not temp_db_path.exists()
        assert not info_path.exists()

    def test_nuke_db_handles_only_db_exists(self, mock_db_config, temp_db_path):
        """nuke_db handles case where only db exists."""
        self._create_db_file(temp_db_path)

        result = mock_db_config.nuke_db()

        assert result is True
        assert not temp_db_path.exists()

    def test_nuke_db_handles_only_metadata_exists(self, mock_db_config, temp_db_path):
        """nuke_db handles case where only metadata exists."""
        self._create_metadata_file(temp_db_path)
        info_path = Path(f"{temp_db_path}-info")

        result = mock_db_config.nuke_db()

        assert result is True
        assert not info_path.exists()

    def test_nuke_db_handles_neither_exists(self, mock_db_config, temp_db_path):
        """nuke_db returns True when neither file exists."""
        result = mock_db_config.nuke_db()

        assert result is True


class TestConfirmMetadataExists:
    """Tests for confirm_metadata_exists() method."""

    @pytest.fixture
    def temp_db_path(self, tmp_path):
        return tmp_path / "test.db"

    @pytest.fixture
    def mock_db_config(self, temp_db_path):
        from mkts_backend.config.config import DatabaseConfig

        with patch.object(DatabaseConfig, '__init__', lambda self, *args, **kwargs: None):
            db = DatabaseConfig()
            db.path = str(temp_db_path)
            db.alias = "test"
            yield db

    def test_returns_true_when_metadata_exists(self, mock_db_config, temp_db_path):
        """Returns True when -info file exists."""
        info_path = Path(f"{temp_db_path}-info")
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text(json.dumps({"generation": 1}))

        assert mock_db_config.confirm_metadata_exists() is True

    def test_returns_false_when_metadata_missing(self, mock_db_config, temp_db_path):
        """Returns False when -info file doesn't exist."""
        info_path = Path(f"{temp_db_path}-info")
        assert not info_path.exists()

        assert mock_db_config.confirm_metadata_exists() is False


class TestReadDbInfo:
    """Tests for read_db_info() method."""

    @pytest.fixture
    def temp_db_path(self, tmp_path):
        return tmp_path / "test.db"

    @pytest.fixture
    def mock_db_config(self, temp_db_path):
        from mkts_backend.config.config import DatabaseConfig

        with patch.object(DatabaseConfig, '__init__', lambda self, *args, **kwargs: None):
            db = DatabaseConfig()
            db.path = str(temp_db_path)
            db.alias = "test"
            yield db

    def test_returns_content_when_exists(self, mock_db_config, temp_db_path):
        """Returns file content when -info file exists."""
        info_path = Path(f"{temp_db_path}-info")
        info_path.parent.mkdir(parents=True, exist_ok=True)
        expected_data = {"generation": 5, "durable_frame_num": 500}
        info_path.write_text(json.dumps(expected_data))

        result = mock_db_config.read_db_info()

        assert result is not None
        parsed = json.loads(result)
        assert parsed == expected_data

    def test_returns_none_when_missing(self, mock_db_config, temp_db_path):
        """Returns None when -info file doesn't exist."""
        result = mock_db_config.read_db_info()

        assert result is None


class TestIntegrationScenarios:
    """Integration tests for realistic database initialization scenarios."""

    @pytest.fixture
    def temp_db_path(self, tmp_path):
        return tmp_path / "test.db"

    @pytest.fixture
    def mock_db_config(self, temp_db_path):
        from mkts_backend.config.config import DatabaseConfig

        with patch.object(DatabaseConfig, '__init__', lambda self, *args, **kwargs: None):
            db = DatabaseConfig()
            db.path = str(temp_db_path)
            db.alias = "test"
            db.turso_url = "libsql://test.turso.io"
            db.token = "test-token"
            yield db

    def _create_db_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def _create_metadata_file(self, path: Path):
        info_path = Path(f"{path}-info")
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text(json.dumps({"generation": 1, "durable_frame_num": 100}))

    def test_fresh_ci_environment_initialization(self, mock_db_config, temp_db_path):
        """
        Simulates fresh CI environment where no database exists.
        Should sync and create both files.
        """
        assert not temp_db_path.exists()
        sync_calls = []

        def mock_sync():
            sync_calls.append(True)
            self._create_db_file(temp_db_path)
            self._create_metadata_file(temp_db_path)

        with patch.object(mock_db_config, 'sync', side_effect=mock_sync):
            result = mock_db_config.verify_db_exists()

        assert result is True
        assert len(sync_calls) == 1
        assert temp_db_path.exists()
        assert Path(f"{temp_db_path}-info").exists()

    def test_corrupted_db_recovery(self, mock_db_config, temp_db_path):
        """
        Simulates scenario where someone ran sqlite3.connect() directly,
        creating a db without the required metadata.
        """
        # Create corrupted state (db without metadata)
        self._create_db_file(temp_db_path)
        assert temp_db_path.exists()
        assert not Path(f"{temp_db_path}-info").exists()

        nuke_before_sync = False

        def mock_sync():
            nonlocal nuke_before_sync
            # Verify db was nuked before sync
            nuke_before_sync = not temp_db_path.exists()
            self._create_db_file(temp_db_path)
            self._create_metadata_file(temp_db_path)

        with patch.object(mock_db_config, 'sync', side_effect=mock_sync):
            result = mock_db_config.verify_db_exists()

        assert result is True
        assert nuke_before_sync, "DB should be deleted before sync is called"

    def test_repeated_verify_is_idempotent(self, mock_db_config, temp_db_path):
        """
        Calling verify_db_exists multiple times on a valid db should be no-op.
        """
        # Create valid state
        self._create_db_file(temp_db_path)
        self._create_metadata_file(temp_db_path)

        sync_calls = []

        with patch.object(mock_db_config, 'sync', side_effect=lambda: sync_calls.append(True)):
            result1 = mock_db_config.verify_db_exists()
            result2 = mock_db_config.verify_db_exists()
            result3 = mock_db_config.verify_db_exists()

        assert result1 is True
        assert result2 is True
        assert result3 is True
        assert len(sync_calls) == 0, "sync should never be called for valid db state"
