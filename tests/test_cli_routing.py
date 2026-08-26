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

    def test_bare_flag_only_exits_nonzero(self):
        """Flag-only invocation with no subcommand must fail (no silent success)."""
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--history"])
        assert exc_info.value.code == 2

    def test_bare_market_flag_exits_nonzero(self):
        """A lone --primary is not a shortcut; must not silently succeed."""
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--primary"])
        assert exc_info.value.code == 2

    @patch("mkts_backend.cli.run_market_update", return_value=True)
    def test_update_markets_routes_history_flag(self, mock_run):
        """update-markets --history forwards history=True to run_market_update."""
        from mkts_backend.cli_tools.args_parser import parse_args

        with patch("sys.argv", ["mkts-backend", "update-markets", "--history"]):
            with pytest.raises(SystemExit) as exc_info:
                parse_args(["update-markets", "--history"])
            assert exc_info.value.code == 0
        _, kwargs = mock_run.call_args
        assert kwargs["history"] is True

    @patch("mkts_backend.cli.run_market_update", return_value=True)
    def test_update_markets_defaults_to_all(self, mock_run):
        """update-markets with no --market flag dispatches market_alias='all'."""
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit):
            parse_args(["update-markets"])
        _, kwargs = mock_run.call_args
        assert kwargs["market_alias"] == "all"

    @patch("mkts_backend.cli.run_market_update", return_value=True)
    def test_update_markets_history_before_subcommand(self, mock_run):
        """--history before the subcommand must still be honored (position-agnostic)."""
        from mkts_backend.cli_tools.args_parser import parse_args

        with patch("sys.argv", ["mkts-backend", "--history", "update-markets"]):
            with pytest.raises(SystemExit):
                parse_args(["--history", "update-markets"])
        _, kwargs = mock_run.call_args
        assert kwargs["history"] is True

    @patch("mkts_backend.cli.run_market_update", return_value=True)
    def test_update_markets_honors_explicit_primary(self, mock_run):
        """Explicit --primary overrides the subcommand's 'all' default."""
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit):
            parse_args(["update-markets", "--primary"])
        _, kwargs = mock_run.call_args
        assert kwargs["market_alias"] == "primary"

    @patch("mkts_backend.builder_costs.runner.run")
    def test_update_builder_costs_routes(self, mock_run):
        from mkts_backend.builder_costs.runner import RunResult
        from mkts_backend.cli_tools.args_parser import parse_args

        mock_run.return_value = RunResult(success=True, fetched=10, watchlist_size=10)

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["update-builder-costs"])

        assert exc_info.value.code == 0
        mock_run.assert_called_once_with()

    def test_update_builder_costs_help_reaches_subcommand_help(self, capsys):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc_info:
            parse_args(["update-builder-costs", "--help"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "Refresh manufacturing costs in buildcost.db" in captured.out

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


# ---------------------------------------------------------------------------
# Database path exposure for CI and shell automation (Task 5)
# ---------------------------------------------------------------------------


class TestDbPathFlags:
    """CI derives cache paths from these, so the format is contractual:
    --list-db-paths prints `alias\\tfile` per line; --db-path prints one
    bare filename with no decoration."""

    def test_list_db_paths_covers_every_routed_alias(self, capsys):
        from mkts_backend.cli_tools.args_parser import parse_args
        from mkts_backend.config.settings_service import SettingsService

        with pytest.raises(SystemExit) as exc:
            parse_args(["--list-db-paths"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        routing = SettingsService().database_routing()
        printed = dict(line.split("\t") for line in out.strip().splitlines())
        assert printed == {a: c["file"] for a, c in routing.items()}

    def test_db_path_by_database_alias(self, capsys):
        from mkts_backend.cli_tools.args_parser import parse_args
        from mkts_backend.config.settings_service import SettingsService

        alias, cfg = next(iter(SettingsService().database_routing().items()))
        with pytest.raises(SystemExit) as exc:
            parse_args([f"--db-path={alias}"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == cfg["file"]

    def test_db_path_by_market_section_name(self, capsys):
        """CI matrix legs are named primary/deployment/market3, which are
        section names, not database aliases."""
        from mkts_backend.cli_tools.args_parser import parse_args
        from mkts_backend.config.market_context import MarketContext

        expected = MarketContext.from_settings("deployment").database_file
        with pytest.raises(SystemExit) as exc:
            parse_args(["--db-path=deployment"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == expected

    def test_unknown_alias_exits_nonzero(self, capsys):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit) as exc:
            parse_args(["--db-path=nosuchdb"])
        assert exc.value.code != 0
