"""Tests for the centralized settings service."""

import pytest

from mkts_backend.config.settings_service import (
    SettingsService,
    _load_settings,
    clear_cache,
    get_all_characters,
    get_all_market_contexts,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


def test_settings_loads_and_exposes_typed_properties():
    s = SettingsService()
    assert s.environment in {"production", "development"}
    assert s.log_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    assert s.esi_user_agent.startswith("wcmkts_backend/")
    assert s.wipe_replace_tables == ["marketstats", "doctrines", "jita_prices"]


def test_cache_is_shared_across_instances():
    a = SettingsService().settings_dict
    b = SettingsService().settings_dict
    assert a is b


def test_clear_cache_forces_reload():
    first = SettingsService().settings_dict
    clear_cache()
    second = SettingsService().settings_dict
    assert first is not second


def test_mkts_environment_override(monkeypatch):
    monkeypatch.setenv("MKTS_ENVIRONMENT", "development")
    clear_cache()
    assert SettingsService().environment == "development"


def test_get_all_market_contexts_returns_primary_and_deployment():
    contexts = get_all_market_contexts()
    assert "primary" in contexts
    assert "deployment" in contexts
    assert "default" not in contexts
    assert contexts["primary"].database_alias == "wcmktprod"
    assert contexts["deployment"].database_alias == "wcmktnorth"


def test_get_all_characters_returns_configured_characters():
    chars = get_all_characters()
    names = {c.name for c in chars}
    assert "Orthel Toralen" in names


def test_market_data_legacy_falls_back_to_markets_section():
    """When [market_data] is missing/empty, derive from [markets.*]."""
    fake = {
        "markets": {
            "primary": {
                "name": "Primary",
                "region_id": 1,
                "system_id": 2,
                "structure_id": 3,
            },
            "deployment": {
                "name": "Deployment",
                "region_id": 4,
                "system_id": 5,
                "structure_id": 6,
            },
        },
    }
    s = SettingsService.__new__(SettingsService)
    s.settings = fake
    md = s.get_market_data_legacy()
    assert md["primary_region_id"] == 1
    assert md["deployment_structure_id"] == 6
    assert md["primary_market_name"] == "Primary"
