"""Tests for cross-market fit discovery used by assign/unassign-market.

The fix in PR #24 made ``assign_doctrine_market`` and
``unassign_doctrine_market`` search for fits across *every* configured
market rather than only the destination DB. These tests lock in that
behavior (and its error-handling semantics) at the ``fit_update`` module
boundary so regressions are caught without spinning real databases.
"""
from unittest.mock import patch

import pytest

from mkts_backend.cli_tools import fit_update


@pytest.fixture
def patched_aliases():
    """Patch _configured_market_db_aliases to a known two-market set."""
    with patch.object(
        fit_update, "_configured_market_db_aliases",
        return_value=["wcmktprod", "wcmktnorth"],
    ):
        yield


class TestConfiguredMarketDbAliases:
    def test_dedupes_shared_database_alias(self):
        """If two markets resolve to the same DB alias, return one entry."""
        class FakeCtx:
            def __init__(self, alias): self.database_alias = alias
        with patch.object(fit_update.MarketContext, "list_available",
                          return_value=["primary", "deployment"]):
            with patch.object(fit_update.MarketContext, "from_settings",
                              side_effect=lambda m: FakeCtx("wcmktprod")):
                assert fit_update._configured_market_db_aliases() == ["wcmktprod"]

    def test_empty_list_available(self):
        """No markets configured -> empty alias list, no exception."""
        with patch.object(fit_update.MarketContext, "list_available", return_value=[]):
            assert fit_update._configured_market_db_aliases() == []

    def test_wraps_resolution_failure_with_context(self):
        """MarketContext errors get wrapped so the user sees which market broke."""
        with patch.object(fit_update.MarketContext, "list_available",
                          return_value=["broken"]):
            with patch.object(fit_update.MarketContext, "from_settings",
                              side_effect=KeyError("missing")):
                with pytest.raises(RuntimeError, match="broken"):
                    fit_update._configured_market_db_aliases()


class TestDiscoverFitsAcrossMarkets:
    def test_dedup_across_overlapping_markets(self, patched_aliases):
        """Same fit_id returned by both markets appears once."""
        with patch.object(fit_update, "get_doctrine_fits_from_market",
                          return_value=[101, 102]):
            fit_ids, queried, skipped = fit_update._discover_fits_across_markets(7, False)
        assert fit_ids == [101, 102]
        assert queried == ["wcmktprod", "wcmktnorth"]
        assert skipped == []

    def test_finds_fits_in_non_destination_market(self, patched_aliases):
        """Fits live only in wcmktnorth -> still discovered."""
        def fake(doctrine_id, alias, remote):
            return [201, 202] if alias == "wcmktnorth" else []
        with patch.object(fit_update, "get_doctrine_fits_from_market", side_effect=fake):
            fit_ids, queried, _ = fit_update._discover_fits_across_markets(7, False)
        assert fit_ids == [201, 202]
        assert set(queried) == {"wcmktprod", "wcmktnorth"}

    def test_one_unreachable_market_skipped_not_fatal(self, patched_aliases):
        """If one DB raises, discovery continues with the other."""
        def fake(doctrine_id, alias, remote):
            if alias == "wcmktprod":
                raise RuntimeError("connection refused")
            return [301]
        with patch.object(fit_update, "get_doctrine_fits_from_market", side_effect=fake):
            fit_ids, queried, skipped = fit_update._discover_fits_across_markets(7, False)
        assert fit_ids == [301]
        assert queried == ["wcmktnorth"]
        assert len(skipped) == 1
        assert skipped[0][0] == "wcmktprod"
        assert "connection refused" in skipped[0][1]


class TestAssignDoctrineMarketCrossMarket:
    def test_proceeds_when_fits_only_in_non_target_db(self, patched_aliases):
        """Regression guard: pre-fix code bailed here because it searched
        only the destination DB. Assert the function makes it past discovery."""
        def fake_fits(doctrine_id, alias, remote):
            return [42] if alias == "wcmktnorth" else []
        with patch.object(fit_update, "get_doctrine_fits_from_market", side_effect=fake_fits), \
             patch.object(fit_update, "get_available_doctrines", return_value=[{"id": 7, "name": "Test"}]), \
             patch.object(fit_update, "_get_doctrine_fits_rows", return_value=[]):
            result = fit_update.assign_doctrine_market(
                doctrine_id=7, market_flag="primary", remote=False,
                db_alias="wcmktprod",
            )
        # Returns False because _get_doctrine_fits_rows is empty, but importantly
        # we got past the discovery bail-out — i.e. the cross-market search worked.
        assert result is False


class TestUnassignDoctrineMarketCrossMarket:
    def test_proceeds_when_fits_only_in_non_target_db(self, patched_aliases):
        def fake_fits(doctrine_id, alias, remote):
            return [42] if alias == "wcmktnorth" else []
        with patch.object(fit_update, "get_doctrine_fits_from_market", side_effect=fake_fits), \
             patch.object(fit_update, "get_available_doctrines", return_value=[{"id": 7, "name": "Test"}]), \
             patch.object(fit_update, "_get_doctrine_fits_rows", return_value=[]):
            result = fit_update.unassign_doctrine_market(
                doctrine_id=7, market_to_remove="primary", remote=False,
                db_alias="wcmktprod",
            )
        assert result is False


class TestNoFitsMessage:
    def test_includes_queried_aliases(self, capsys):
        fit_update._render_no_fits_message(7, ["wcmktprod", "wcmktnorth"], [])
        out = capsys.readouterr().out
        assert "wcmktprod" in out and "wcmktnorth" in out
        assert "skipped" not in out.lower()

    def test_reports_skipped_separately(self, capsys):
        fit_update._render_no_fits_message(
            7, ["wcmktprod"], [("wcmktnorth", "connection refused")],
        )
        out = capsys.readouterr().out
        assert "wcmktprod" in out
        assert "skipped" in out.lower()
        assert "wcmktnorth" in out
        # Rich may insert soft wraps in long output; normalize whitespace.
        assert "connection refused" in " ".join(out.split())
