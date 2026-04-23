"""Regression locks for fixes landed in PR #27 (fit-update cleanup).

These tests cover contracts introduced by the fix round:
- ``_require_single_context`` rejects simultaneous conn= + engine=
- ``_execute_market_plan`` aborts cleanly when no markets are configured
- ``_handle_fit_update`` defaults to 'primary' and warns in non-TTY sessions
"""
from unittest.mock import patch, MagicMock

import pytest

from mkts_backend.cli_tools import fit_update
from mkts_backend.utils.doctrine_update import _require_single_context


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
    """Cover the command_registry._handle_fit_update non-TTY branch.

    In non-TTY with no --market flag, the handler must default to 'primary'
    and emit a warning rather than silently picking a DB (which was the
    pre-fix behavior that motivated PR #27's dispatcher rewrite).
    """

    def _handler(self):
        from mkts_backend.cli_tools.command_registry import get_registry
        entry = get_registry().resolve("fit-update")
        assert entry is not None
        return entry.handler

    def test_non_tty_no_market_flag_defaults_to_primary_and_warns(self, caplog):
        handler = self._handler()
        captured_kwargs: dict = {}

        def fake_fit_update_command(**kwargs):
            captured_kwargs.update(kwargs)
            return True

        with patch("sys.stdin.isatty", return_value=False), \
             patch("mkts_backend.cli_tools.fit_update.fit_update_command",
                   side_effect=fake_fit_update_command), \
             caplog.at_level("WARNING"):
            # args simulate: `mkts-backend fit-update list-fits` with no --market
            result = handler(["fit-update", "list-fits"], "primary")

        assert result is True
        assert captured_kwargs.get("market_flag") == "primary"
        assert any("non-TTY" in rec.message for rec in caplog.records), \
            f"expected non-TTY warning, got: {[r.message for r in caplog.records]}"

    def test_explicit_market_both_is_preserved_through_dispatcher(self):
        """Regression lock for the C-1 fix: --market=both must not collapse to primary."""
        handler = self._handler()
        captured_kwargs: dict = {}

        def fake_fit_update_command(**kwargs):
            captured_kwargs.update(kwargs)
            return True

        with patch("mkts_backend.cli_tools.fit_update.fit_update_command",
                   side_effect=fake_fit_update_command):
            handler(["fit-update", "list-fits", "--market=both"], "both")

        assert captured_kwargs.get("market_flag") == "both"
