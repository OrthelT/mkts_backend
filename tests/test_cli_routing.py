"""Regression tests for CLI subcommand routing.

Mocks downstream command functions so these tests verify *routing* only — no
DB, ESI, or filesystem side-effects.

Since both entry points now dispatch via the command registry, we mock the
actual command functions that the registry handlers call.
"""

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# args_parser.parse_args routing tests (mkts-backend entry point)
# ---------------------------------------------------------------------------


class TestArgsParserRouting:
    """Verify that parse_args() dispatches to the correct command handler."""

    @patch("mkts_backend.cli_tools.fit_check.fit_check_command", return_value=True)
    def test_fit_check_routes(self, mock_fc):
        """fit-check subcommand routes to fit_check_command."""
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["fit-check", "--fit-id=42"])
        assert exc_info.value.code == 0
        mock_fc.assert_called_once()
        _, kwargs = mock_fc.call_args
        assert kwargs["fit_id"] == 42

    @patch("mkts_backend.cli_tools.equiv_manager.equiv_command", return_value=True)
    def test_equiv_routes(self, mock_eq):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["equiv", "find", "--id=123"])
        assert exc_info.value.code == 0
        mock_eq.assert_called_once()

    @patch("mkts_backend.cli_tools.add_watchlist.add_watchlist")
    def test_add_watchlist_routes(self, mock_aw):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["add_watchlist", "--history"])
        assert exc_info.value.code == 0
        mock_aw.assert_called_once()

    @patch("mkts_backend.cli_tools.args_parser.check_tables")
    def test_check_tables_routes(self, mock_ct):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--check_tables"])
        mock_ct.assert_called_once()

    def test_empty_args_returns_none(self):
        from mkts_backend.cli_tools.args_parser import parse_args

        assert parse_args([]) is None

    def test_help_exits(self):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--help"])

    def test_history_flag_returned(self):
        from mkts_backend.cli_tools.args_parser import parse_args

        result = parse_args(["--history"])
        assert result is not None
        assert result["history"] is True

    def test_no_history_flag(self):
        from mkts_backend.cli_tools.args_parser import parse_args

        result = parse_args(["--primary"])
        assert result is not None
        assert result["history"] is False

    @patch(
        "mkts_backend.cli_tools.asset_check.asset_check_command", return_value=True
    )
    def test_assets_routes(self, mock_ac):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["assets", "--id=11379"])
        assert exc_info.value.code == 0
        mock_ac.assert_called_once()

    @patch("mkts_backend.cli_tools.args_parser.validate_all")
    def test_validate_env_routes(self, mock_val):
        from mkts_backend.cli_tools.args_parser import parse_args

        mock_val.return_value = {"is_valid": True, "message": "ok", "present_required": [], "present_optional": []}
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--validate-env"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# fit_check.main() routing tests (fitcheck entry point)
# ---------------------------------------------------------------------------


class TestFitCheckMainRouting:
    """Verify fitcheck entry point dispatches subcommands correctly."""

    @patch("mkts_backend.cli_tools.fit_check._handle_list_fits")
    def test_list_fits_subcommand(self, mock_handler):
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "list-fits"]):
            with pytest.raises(SystemExit):
                main()
        mock_handler.assert_called_once()

    @patch("mkts_backend.cli_tools.fit_check._handle_needed")
    def test_needed_subcommand(self, mock_handler):
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "needed"]):
            with pytest.raises(SystemExit):
                main()
        mock_handler.assert_called_once()

    @patch("mkts_backend.cli_tools.fit_check._handle_module")
    def test_module_subcommand(self, mock_handler):
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "module", "--id=123"]):
            with pytest.raises(SystemExit):
                main()
        mock_handler.assert_called_once()

    def test_help_exits_cleanly(self):
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    @patch("mkts_backend.cli_tools.fit_check.fit_check_command", return_value=True)
    def test_fit_id_flag_routes_to_fit_check_command(self, mock_fc):
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "--fit=42"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        mock_fc.assert_called_once()
        _, kwargs = mock_fc.call_args
        assert kwargs["fit_id"] == 42

    @patch("mkts_backend.cli_tools.asset_check.asset_check_command", return_value=True)
    def test_no_wrong_door_assets_via_fitcheck(self, mock_ac):
        """fitcheck assets --id=11379 should work (no-wrong-door)."""
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "assets", "--id=11379"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        mock_ac.assert_called_once()

    @patch("mkts_backend.cli_tools.fit_check.fit_check_command", return_value=True)
    def test_no_wrong_door_fit_check_via_fitcheck(self, mock_fc):
        """fitcheck fit-check --fit=42 should work (explicit subcommand)."""
        from mkts_backend.cli_tools.fit_check import main

        with patch("sys.argv", ["fitcheck", "fit-check", "--fit=42"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        mock_fc.assert_called_once()
