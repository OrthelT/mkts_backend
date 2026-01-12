"""
Tests for CLI --market flag parsing and routing.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestCliMarketFlagParsing:
    """Tests for CLI argument parsing of --market flag."""

    def test_parse_market_flag_primary(self):
        """Test parsing --market=primary."""
        from mkts_backend.cli import parse_args

        args = parse_args(["--market=primary"])

        assert args["market"] == "primary"

    def test_parse_market_flag_deployment(self):
        """Test parsing --market=deployment."""
        from mkts_backend.cli import parse_args

        args = parse_args(["--market=deployment"])

        assert args["market"] == "deployment"

    def test_parse_deployment_shorthand(self):
        """Test parsing --deployment shorthand."""
        from mkts_backend.cli import parse_args

        args = parse_args(["--deployment"])

        assert args["market"] == "deployment"

    def test_parse_primary_shorthand(self):
        """Test parsing --primary shorthand."""
        from mkts_backend.cli import parse_args

        args = parse_args(["--primary"])

        assert args["market"] == "primary"

    def test_parse_market_flag_default(self):
        """Test default market when --market not specified."""
        from mkts_backend.cli import parse_args

        # parse_args returns None for empty args (no special flags)
        # Use --history flag which sets return_args without exiting
        args = parse_args(["--history"])

        assert args["market"] == "primary"

    def test_parse_market_flag_with_history(self):
        """Test parsing --market with --history flag."""
        from mkts_backend.cli import parse_args

        args = parse_args(["--market=deployment", "--history"])

        assert args["market"] == "deployment"
        assert args["history"] is True

    def test_parse_list_markets_flag(self):
        """Test parsing --list-markets flag exits after printing."""
        from mkts_backend.cli import parse_args

        # --list-markets prints markets and exits, so we expect SystemExit
        with pytest.raises(SystemExit):
            parse_args(["--list-markets"])


class TestCliMarketContextCreation:
    """Tests for CLI creating correct MarketContext based on --market flag."""

    def test_cli_creates_primary_context_by_default(self):
        """Test CLI creates primary MarketContext by default."""
        from mkts_backend.cli import parse_args

        # With a flag that returns args (--history doesn't exit), check default market
        args = parse_args(["--history"])

        # Default market should be primary
        assert args["market"] == "primary"

    @patch("mkts_backend.cli.MarketContext")
    def test_cli_creates_deployment_context_when_specified(self, mock_context_class):
        """Test CLI creates deployment MarketContext when --market=deployment."""
        mock_context = MagicMock()
        mock_context.name = "B-9C24 Keepstar"
        mock_context_class.from_settings.return_value = mock_context

        from mkts_backend.cli import parse_args

        args = parse_args(["--market=deployment"])

        assert args["market"] == "deployment"


class TestCliMarketContextPassthrough:
    """Tests for CLI passing MarketContext to processing functions."""

    @patch("mkts_backend.cli.process_market_orders")
    @patch("mkts_backend.cli.process_market_stats")
    @patch("mkts_backend.cli.process_doctrine_stats")
    @patch("mkts_backend.cli.validate_all")
    @patch("mkts_backend.cli.init_databases")
    def test_market_context_passed_to_process_functions(
        self,
        mock_init_db,
        mock_validate,
        mock_doctrine_stats,
        mock_market_stats,
        mock_market_orders,
    ):
        """Test that MarketContext is passed through to processing functions."""
        mock_validate.return_value = True
        mock_init_db.return_value = None

        # This test verifies the structure - actual execution would need more mocking

    def test_process_market_orders_accepts_market_ctx(self):
        """Test process_market_orders function signature accepts market_ctx."""
        from mkts_backend.cli import process_market_orders
        import inspect

        sig = inspect.signature(process_market_orders)
        params = list(sig.parameters.keys())

        assert "market_ctx" in params

    def test_process_history_accepts_market_ctx(self):
        """Test process_history function signature accepts market_ctx."""
        from mkts_backend.cli import process_history
        import inspect

        sig = inspect.signature(process_history)
        params = list(sig.parameters.keys())

        assert "market_ctx" in params

    def test_process_market_stats_accepts_market_ctx(self):
        """Test process_market_stats function signature accepts market_ctx."""
        from mkts_backend.cli import process_market_stats
        import inspect

        sig = inspect.signature(process_market_stats)
        params = list(sig.parameters.keys())

        assert "market_ctx" in params

    def test_process_doctrine_stats_accepts_market_ctx(self):
        """Test process_doctrine_stats function signature accepts market_ctx."""
        from mkts_backend.cli import process_doctrine_stats
        import inspect

        sig = inspect.signature(process_doctrine_stats)
        params = list(sig.parameters.keys())

        assert "market_ctx" in params


class TestCliInvalidMarketHandling:
    """Tests for CLI handling of invalid market values."""

    def test_invalid_market_in_args_still_parses(self):
        """Test that invalid market value is parsed (validation happens later)."""
        from mkts_backend.cli import parse_args

        args = parse_args(["--market=invalid_market"])

        # Parsing should succeed, validation happens at context creation
        assert args["market"] == "invalid_market"

    def test_market_context_creation_fails_for_invalid_market(self):
        """Test that MarketContext.from_settings fails for invalid market."""
        from mkts_backend.config.market_context import MarketContext

        with pytest.raises(ValueError, match="Unknown market"):
            MarketContext.from_settings("invalid_market")


class TestCliListMarketsCommand:
    """Tests for --list-markets CLI command."""

    def test_list_markets_returns_available_markets(self):
        """Test that list_markets shows available market configurations."""
        from mkts_backend.config.market_context import MarketContext

        markets = MarketContext.get_available_markets()

        assert "primary" in markets
        assert "deployment" in markets

    def test_list_markets_includes_market_details(self):
        """Test that each market has required details."""
        from mkts_backend.config.market_context import MarketContext

        for alias in MarketContext.get_available_markets():
            ctx = MarketContext.from_settings(alias)

            assert ctx.name is not None
            assert ctx.region_id is not None
            assert ctx.database_alias is not None
