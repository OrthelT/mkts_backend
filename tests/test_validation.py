"""Tests for required-credential validation (utils/validation.py).

validate_required_credentials gates every pipeline run (cli.py exits non-zero
on failure), so these tests pin its contract: per-market Turso env vars are
derived from settings.toml routing, scoped to the selected markets, and the
shared test DB is never required. Expected values are derived from
SettingsService — no frozen TOML literals.
"""
import os
from unittest.mock import patch

from mkts_backend.config.settings_service import SettingsService
from mkts_backend.utils.validation import validate_required_credentials


def _market_env_vars(service: SettingsService, market_alias: str) -> set[str]:
    cfg = service.markets_raw[market_alias]
    return {cfg["turso_url_env"], cfg["turso_token_env"]}


def _testing_env_vars(service: SettingsService) -> set[str]:
    testing = service.settings_dict.get("shared", {}).get("testing", {})
    return {v for v in (testing.get("turso_url_env"), testing.get("turso_token_env")) if v}


class TestRequiredCredentials:
    def test_all_markets_required_by_default(self, mock_env_vars):
        """With no scope, every configured market's Turso env vars are required."""
        service = SettingsService()
        is_valid, missing, present = validate_required_credentials()

        assert is_valid, f"unexpected missing credentials: {missing}"
        for alias in service.market_aliases:
            assert _market_env_vars(service, alias) <= set(present)

    def test_test_db_credentials_never_required(self, mock_env_vars):
        """The [shared.testing] DB is optional — its env vars must not be required."""
        service = SettingsService()
        _, missing, present = validate_required_credentials()

        checked = set(missing) | set(present)
        assert not (_testing_env_vars(service) & checked)

    def test_scoped_run_ignores_other_markets_credentials(self, mock_env_vars):
        """A single-market run must not require other markets' Turso credentials."""
        service = SettingsService()
        aliases = service.market_aliases
        assert len(aliases) >= 2, "test needs at least two configured markets"
        selected, *others = aliases

        env = dict(mock_env_vars)
        for other in others:
            for var in _market_env_vars(service, other):
                env.pop(var, None)

        with patch.dict(os.environ, env, clear=True):
            is_valid, missing, present = validate_required_credentials([selected])

        assert is_valid, f"unexpected missing credentials: {missing}"
        assert _market_env_vars(service, selected) <= set(present)
        for other in others:
            assert not (_market_env_vars(service, other) & (set(missing) | set(present)))

    def test_missing_selected_market_credential_fails(self, mock_env_vars):
        """A missing credential for the selected market is reported by name."""
        service = SettingsService()
        selected = service.market_aliases[0]
        url_env = service.markets_raw[selected]["turso_url_env"]

        env = dict(mock_env_vars)
        env.pop(url_env, None)

        with patch.dict(os.environ, env, clear=True):
            is_valid, missing, _ = validate_required_credentials([selected])

        assert not is_valid
        assert url_env in missing

    def test_market_without_turso_env_vars_does_not_crash(self, mock_env_vars):
        """A local-only market (no turso_*_env keys) must be skipped, not crash
        with os.getenv(None)."""
        routing = {"localonly": {
            "file": "local.db",
            "turso_url_env": None,
            "turso_token_env": None,
            "optional": False,
        }}
        with patch.object(SettingsService, "database_routing", return_value=routing):
            is_valid, missing, _ = validate_required_credentials()

        assert is_valid, f"unexpected missing credentials: {missing}"
