"""Tests for the centralized settings service."""

import tomllib

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


# ---------------------------------------------------------------------------
# Error-path coverage
# ---------------------------------------------------------------------------


def test_missing_settings_file_raises(tmp_path):
    missing = tmp_path / "nope.toml"
    with pytest.raises(FileNotFoundError):
        SettingsService(settings_path=missing)


def test_malformed_toml_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = not = valid\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        SettingsService(settings_path=bad)


def test_explicit_path_bypasses_cache(tmp_path):
    """Explicit settings_path must not read or pollute the module cache."""
    fixture = tmp_path / "alt.toml"
    fixture.write_text(
        '[app]\nname = "alt"\nenvironment = "development"\nlog_level = "DEBUG"\n'
        '[esi]\nuser_agent = "alt"\ncompatibility_date = "2026-01-01"\n'
        '[auth]\ncallback_url = "http://x"\ntoken_file = "file:t.json"\n'
        '[buildcost]\nsheet_url = "http://x"\n'
    )
    alt = SettingsService(settings_path=fixture)
    assert alt.app_name == "alt"

    # Default path still loads fresh — explicit path didn't poison the cache.
    default = SettingsService()
    assert default.app_name != "alt"


def test_market_data_legacy_raises_when_primary_missing():
    """Missing [markets.primary] should raise — not return zeros."""
    s = SettingsService.__new__(SettingsService)
    s.settings = {"markets": {"deployment": {"region_id": 1, "system_id": 2, "structure_id": 3}}}
    with pytest.raises(KeyError, match=r"\[markets\.primary\]"):
        s.get_market_data_legacy()


def test_market_data_legacy_raises_when_deployment_missing():
    s = SettingsService.__new__(SettingsService)
    s.settings = {"markets": {"primary": {"region_id": 1, "system_id": 2, "structure_id": 3}}}
    with pytest.raises(KeyError, match=r"\[markets\.deployment\]"):
        s.get_market_data_legacy()


def test_market_data_legacy_raises_when_required_id_missing():
    """A [markets.primary] without region_id should fail loudly, not zero-default."""
    s = SettingsService.__new__(SettingsService)
    s.settings = {
        "markets": {
            "primary": {"name": "P", "system_id": 2, "structure_id": 3},  # no region_id
            "deployment": {"region_id": 4, "system_id": 5, "structure_id": 6},
        }
    }
    with pytest.raises(KeyError, match="region_id"):
        s.get_market_data_legacy()


def test_environment_override_applies_after_first_load(monkeypatch):
    """env var set AFTER cache priming must still take effect.

    Regression guard: previously the env override was baked into the cached
    dict at first load, so a CLI flag like ``--env=development`` that sets
    MKTS_ENVIRONMENT after module imports was silently ignored — primary
    market routed to the production DB instead of testing.
    """
    monkeypatch.setenv("MKTS_ENVIRONMENT", "production")
    clear_cache()
    SettingsService()  # primes cache while env=production
    monkeypatch.setenv("MKTS_ENVIRONMENT", "development")
    # No clear_cache — but environment property now reads env dynamically.
    assert SettingsService().environment == "development"


def test_environment_override_unset_falls_back_to_toml(monkeypatch):
    monkeypatch.delenv("MKTS_ENVIRONMENT", raising=False)
    clear_cache()
    s = SettingsService()
    # The TOML default is "production"; if you change it, update this assertion.
    assert s.environment == "production"


def test_market_context_picks_up_late_env_override(monkeypatch):
    """End-to-end regression: simulate the --env=development CLI path.

    Order matches what cli.py does in practice:
    1. Modules import → SettingsService() runs at module load (cache primed
       with env=production from TOML).
    2. parse_args() sets os.environ["MKTS_ENVIRONMENT"] = "development".
    3. MarketContext.from_settings("primary") is called downstream.

    Before the fix this returned the production DB; after, it returns
    the testing DB because environment is read dynamically.
    """
    from mkts_backend.config.market_context import MarketContext

    monkeypatch.delenv("MKTS_ENVIRONMENT", raising=False)
    clear_cache()
    SettingsService()  # simulate import-time priming with env unset

    monkeypatch.setenv("MKTS_ENVIRONMENT", "development")  # simulate --env=development
    ctx = MarketContext.from_settings("primary")
    assert ctx.database_alias == "wcmkttest"


def test_get_all_characters_requires_char_id(monkeypatch):
    """A character entry missing char_id should fail loudly."""
    from mkts_backend.config import settings_service as svc

    monkeypatch.setattr(
        svc,
        "_load_settings",
        lambda path=None: {"characters": {"foo": {"name": "Foo"}}},
    )
    with pytest.raises(KeyError, match="char_id"):
        get_all_characters()


def test_get_all_characters_correct_section_overrides_typo(monkeypatch):
    """[characters.*] should override [chareacters.*] on key collision."""
    from mkts_backend.config import settings_service as svc

    monkeypatch.setattr(
        svc,
        "_load_settings",
        lambda path=None: {
            "chareacters": {"foo": {"char_id": 1, "name": "Old"}},
            "characters": {"foo": {"char_id": 2, "name": "New"}, "bar": {"char_id": 3}},
        },
    )
    chars = {c.key: c for c in get_all_characters()}
    assert chars["foo"].char_id == 2
    assert chars["foo"].name == "New"
    assert "bar" in chars
