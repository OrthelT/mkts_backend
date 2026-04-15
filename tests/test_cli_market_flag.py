"""
Tests for CLI --market flag parsing and routing.

After the ``update-markets`` refactor, ``parse_args`` no longer returns a dict
for bare flag invocations — it exits. Market-flag parsing is therefore tested
at the ``parse_market_args`` / ``resolve_market_alias`` level, with routing
tested via subcommand dispatch.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestParseMarketArgs:
    """Low-level market flag parsing."""

    def test_market_flag_primary(self):
        from mkts_backend.cli_tools.market_args import parse_market_args

        assert parse_market_args(["--market=primary"]) == "primary"

    def test_market_flag_deployment(self):
        from mkts_backend.cli_tools.market_args import parse_market_args

        assert parse_market_args(["--market=deployment"]) == "deployment"

    def test_deployment_shorthand(self):
        from mkts_backend.cli_tools.market_args import parse_market_args

        assert parse_market_args(["--deployment"]) == "deployment"

    def test_primary_shorthand(self):
        from mkts_backend.cli_tools.market_args import parse_market_args

        assert parse_market_args(["--primary"]) == "primary"

    def test_both_shorthand(self):
        from mkts_backend.cli_tools.market_args import parse_market_args

        assert parse_market_args(["--both"]) == "both"

    def test_default_when_unspecified(self):
        from mkts_backend.cli_tools.market_args import parse_market_args

        assert parse_market_args([]) == "primary"

    def test_invalid_market_exits(self):
        from mkts_backend.cli_tools.market_args import parse_market_args

        with pytest.raises(SystemExit):
            parse_market_args(["--market=invalid_market"])


class TestResolveMarketAlias:
    """Optional-returning resolver used by the dispatcher."""

    def test_returns_none_when_unspecified(self):
        from mkts_backend.cli_tools.market_args import resolve_market_alias

        assert resolve_market_alias([]) is None
        assert resolve_market_alias(["--history"]) is None

    def test_returns_alias_when_specified(self):
        from mkts_backend.cli_tools.market_args import resolve_market_alias

        assert resolve_market_alias(["--primary"]) == "primary"
        assert resolve_market_alias(["--market=deployment"]) == "deployment"
        assert resolve_market_alias(["--both"]) == "both"


class TestCliListMarketsCommand:
    """--list-markets exits after printing."""

    def test_list_markets_flag_exits(self):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--list-markets"])

    def test_list_markets_returns_available_markets(self):
        from mkts_backend.config.market_context import MarketContext

        markets = MarketContext.get_available_markets()
        assert "primary" in markets
        assert "deployment" in markets

    def test_list_markets_includes_market_details(self):
        from mkts_backend.config.market_context import MarketContext

        for alias in MarketContext.get_available_markets():
            ctx = MarketContext.from_settings(alias)
            assert ctx.name is not None
            assert ctx.region_id is not None
            assert ctx.database_alias is not None


class TestCliInvalidMarketHandling:
    """Invalid market values fail at parse time."""

    def test_invalid_market_in_args_exits(self):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--market=invalid_market"])

    def test_market_context_creation_fails_for_invalid_market(self):
        from mkts_backend.config.market_context import MarketContext

        with pytest.raises(ValueError, match="Unknown market"):
            MarketContext.from_settings("invalid_market")


class TestCliMarketContextPassthrough:
    """Processing functions still accept market_ctx."""

    def test_process_market_orders_accepts_market_ctx(self):
        from mkts_backend.cli import process_market_orders
        import inspect

        assert "market_ctx" in inspect.signature(process_market_orders).parameters

    def test_process_history_accepts_market_ctx(self):
        from mkts_backend.cli import process_history
        import inspect

        assert "market_ctx" in inspect.signature(process_history).parameters

    def test_process_market_stats_accepts_market_ctx(self):
        from mkts_backend.cli import process_market_stats
        import inspect

        assert "market_ctx" in inspect.signature(process_market_stats).parameters

    def test_process_doctrine_stats_accepts_market_ctx(self):
        from mkts_backend.cli import process_doctrine_stats
        import inspect

        assert "market_ctx" in inspect.signature(process_doctrine_stats).parameters


class TestUpdateMarketsDispatch:
    """--market flag routes through update-markets subcommand."""

    @patch("mkts_backend.cli.run_market_update", return_value=True)
    def test_market_deployment_routes_to_run_market_update(self, mock_run):
        from mkts_backend.cli_tools.args_parser import parse_args

        with pytest.raises(SystemExit):
            parse_args(["update-markets", "--market=deployment"])
        _, kwargs = mock_run.call_args
        assert kwargs["market_alias"] == "deployment"

    @patch("mkts_backend.cli.run_market_update", return_value=True)
    def test_update_markets_with_history(self, mock_run):
        from mkts_backend.cli_tools.args_parser import parse_args

        argv = ["mkts-backend", "update-markets", "--market=deployment", "--history"]
        with patch("sys.argv", argv):
            with pytest.raises(SystemExit):
                parse_args(["update-markets", "--market=deployment", "--history"])
        _, kwargs = mock_run.call_args
        assert kwargs["market_alias"] == "deployment"
        assert kwargs["history"] is True
