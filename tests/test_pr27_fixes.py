"""Regression locks for fixes landed in PR #27 (fit-update cleanup).

These tests cover contracts introduced by the fix round:
- ``_require_single_context`` rejects simultaneous conn= + engine=
- ``_execute_market_plan`` aborts cleanly when no markets are configured
- the ``fit-manager`` handler defaults to 'primary' and warns in non-TTY sessions
"""
from unittest.mock import patch, MagicMock

import pytest

from mkts_backend.cli_tools import fit_update
from mkts_backend.utils.doctrine_update import _require_single_context


class TestFlagToAliasesShim:
    """market3 is shimmed to primary; all/both cover every configured market."""

    def _aliases(self):
        from mkts_backend.config.market_context import MarketContext
        return {
            m: MarketContext.from_settings(m).database_alias
            for m in ("primary", "deployment", "market3")
        }

    def test_primary_includes_market3_shim(self):
        a = self._aliases()
        result = fit_update._flag_to_aliases("primary")
        assert a["primary"] in result
        assert a["market3"] in result          # shim: market3 mirrors primary
        assert a["deployment"] not in result

    def test_deployment_is_isolated(self):
        a = self._aliases()
        assert fit_update._flag_to_aliases("deployment") == {a["deployment"]}

    def test_all_and_both_cover_every_market(self):
        expected = set(self._aliases().values())
        assert fit_update._flag_to_aliases("all") == expected
        assert fit_update._flag_to_aliases("both") == expected

    def test_unknown_flag_is_empty(self):
        assert fit_update._flag_to_aliases("bogus") == set()


class TestRequireSingleContext:
    def test_raises_when_both_conn_and_engine_non_none(self):
        with pytest.raises(ValueError, match="conn= or engine="):
            _require_single_context(conn=object(), engine=object())

    def test_accepts_neither(self):
        _require_single_context(conn=None, engine=None)

    def test_accepts_conn_only(self):
        _require_single_context(conn=object(), engine=None)

    def test_accepts_engine_only(self):
        _require_single_context(conn=None, engine=object())


class TestExecuteMarketPlanZeroAliasGuard:
    def test_aborts_with_skipped_count_when_no_markets_configured(self, capsys):
        plans = [
            {"fit_id": 1, "doctrine_id": 10, "action": "update",
             "market_flag": "primary", "new_flag": "both"},
            {"fit_id": 2, "doctrine_id": 10, "action": "remove"},
        ]
        with patch.object(fit_update, "_configured_market_db_aliases", return_value=[]):
            result = fit_update._execute_market_plan(plans, remote=False, db_alias="wcmkt")
        assert result == {"updated": 0, "deleted": 0, "skipped": 2, "step_failures": 0}
        captured = capsys.readouterr().out
        assert "no markets configured" in captured.lower()

    def test_empty_plan_list_still_returns_counters_shape(self):
        with patch.object(fit_update, "_configured_market_db_aliases", return_value=[]):
            result = fit_update._execute_market_plan([], remote=False, db_alias="wcmkt")
        assert result == {"updated": 0, "deleted": 0, "skipped": 0, "step_failures": 0}


class TestFitUpdateDispatcherNonTtyFallback:
    """Cover the fit-manager handler's non-TTY market resolution.

    In non-TTY with no --market flag, the handler must default to 'primary'
    and emit a warning rather than silently picking a DB (which was the
    pre-fix behavior that motivated PR #27's dispatcher rewrite).
    """

    def _handler(self):
        from mkts_backend.cli_tools.command_registry import get_registry
        entry = get_registry().resolve("fit-manager")
        assert entry is not None
        return entry.handler

    def test_non_tty_no_market_flag_defaults_to_primary_and_warns(self, caplog):
        from mkts_backend.cli_tools.market_args import MARKET_DB_MAP

        handler = self._handler()

        with patch("sys.stdin.isatty", return_value=False), \
             patch("mkts_backend.cli_tools.fit_update.list_fits_command") as spy, \
             caplog.at_level("WARNING"):
            # args simulate: `mkts-backend fit-manager list-fits` with no --market
            result = handler(["list-fits"], "primary")

        assert result is True
        spy.assert_called_once_with(db_alias=MARKET_DB_MAP["primary"])
        assert any("non-TTY" in rec.message for rec in caplog.records), \
            f"expected non-TTY warning, got: {[r.message for r in caplog.records]}"

    def test_explicit_all_markets_flag_is_preserved_through_dispatcher(self):
        """Regression lock for the C-1 fix: an explicit all-markets flag must not collapse to primary.

        ``--market=all`` is the canonical alias; the legacy ``--market=both`` spelling
        normalizes to it. Either way the dispatcher must forward ``all``, not ``primary``.
        """
        handler = self._handler()

        for flag in ("--market=all", "--market=both"):
            with patch(
                "mkts_backend.cli_tools.fit_update.update_lead_ship_command",
                return_value=True,
            ) as spy:
                handler(
                    ["update-lead-ship", "--doctrine-id=21", "--fit-id=313", flag],
                    "all",
                )

            assert spy.call_args.kwargs["market_flag"] == "all", f"flag={flag}"
