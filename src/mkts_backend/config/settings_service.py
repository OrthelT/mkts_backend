"""Centralized settings loader for mkts_backend.

This module is the single entry point for reading ``settings.toml``. All other
config modules (``db_config``, ``market_context``, ``character_config``,
``esi_config``, ``logging_config``) delegate to this service rather than
parsing the TOML file themselves.

Architectural rules:
- Must not import from ``logging_config`` (which depends on this service for
  ``log_level``) — uses stdlib logging only.
- Must not import from ``db_config``, ``esi_config``, or other consumers, to
  avoid circular imports. ``MarketContext`` and ``CharacterConfig`` imports
  are done lazily inside helper functions.
"""

import logging
import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mkts_backend.config.character_config import CharacterConfig
    from mkts_backend.config.market_context import MarketContext

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS_PATH = Path(__file__).parent / "settings.toml"
_cached_settings: dict | None = None


def _load_settings(path: Optional[Path] = None) -> dict:
    """Load and cache settings from the TOML file.

    Applies ``MKTS_ENVIRONMENT`` env var override to ``[app][environment]``
    at load time. Subsequent calls return the cached dict (overrides are
    frozen at first load).
    """
    global _cached_settings
    if _cached_settings is not None:
        return _cached_settings

    settings_path = path if path is not None else _DEFAULT_SETTINGS_PATH
    try:
        with open(settings_path, "rb") as f:
            settings = tomllib.load(f)
    except Exception as e:
        logger.error("Failed to load settings from %s: %s", settings_path, e)
        raise

    env_override = os.environ.get("MKTS_ENVIRONMENT")
    if env_override and "app" in settings:
        logger.info("Environment overridden by MKTS_ENVIRONMENT: %s", env_override)
        settings["app"] = {**settings["app"], "environment": env_override}

    _cached_settings = settings
    return _cached_settings


def clear_cache() -> None:
    """Drop the cached settings dict. Intended for tests that mutate the TOML."""
    global _cached_settings
    _cached_settings = None


class SettingsService:
    """Read-only accessor for application settings.

    Settings are cached at module level after the first read. Instantiating
    multiple ``SettingsService()`` objects is cheap — they all share the
    same cached dict.
    """

    def __init__(self, settings_path: str | Path | None = None):
        path = Path(settings_path) if settings_path is not None else None
        self.settings = _load_settings(path)

    @property
    def settings_dict(self) -> dict:
        """Return the full settings dictionary for raw access."""
        return self.settings

    # ---- [app] ----

    @property
    def app_name(self) -> str:
        return self.settings["app"]["name"]

    @property
    def environment(self) -> str:
        return self.settings["app"]["environment"]

    @property
    def log_level(self) -> str:
        return self.settings["app"]["log_level"]

    # ---- [esi] ----

    @property
    def esi_user_agent(self) -> str:
        return self.settings["esi"]["user_agent"]

    @property
    def esi_compatibility_date(self) -> str:
        return self.settings["esi"]["compatibility_date"]

    # ---- [auth] ----

    @property
    def auth_callback_url(self) -> str:
        return self.settings["auth"]["callback_url"]

    @property
    def auth_token_file(self) -> str:
        return self.settings["auth"]["token_file"]

    # ---- [wipe_replace] ----

    @property
    def wipe_replace_tables(self) -> list[str]:
        """Tables that are fully wiped and re-inserted on each upsert run."""
        return list(self.settings.get("wipe_replace", {}).get("tables", []))

    # ---- [google_sheets] ----

    @property
    def gsheets_enabled(self) -> bool:
        return bool(self.settings.get("google_sheets", {}).get("enabled", False))

    # ---- [buildcost] ----

    @property
    def buildcost_sheet_url(self) -> str:
        return self.settings["buildcost"]["sheet_url"]

    @property
    def buildcost_default_worksheet(self) -> str:
        return self.settings["buildcost"].get("default_worksheet", "")

    # ---- [markets] ----

    @property
    def default_market_alias(self) -> str:
        return self.settings.get("markets", {}).get("default", "primary")

    @property
    def markets_raw(self) -> dict:
        """Raw [markets] section, including the 'default' key."""
        return self.settings.get("markets", {})

    # ---- [db] ----

    @property
    def db_section(self) -> dict:
        return self.settings.get("db", {})

    # ---- [market_data] (legacy) ----

    def get_market_data_legacy(self) -> dict:
        """Return the legacy ``[market_data]`` section.

        If the section is absent (or empty), derives equivalent values from the
        modern ``[markets.primary]`` and ``[markets.deployment]`` sections so
        callers that depend on the flat-keyed shape (``primary_region_id`` etc.)
        keep working. New code should use ``MarketContext`` via
        ``get_all_market_contexts()`` instead.
        """
        market_data = self.settings.get("market_data") or {}
        if market_data:
            return market_data

        markets = self.settings.get("markets", {})
        primary = markets.get("primary", {}) if isinstance(markets.get("primary"), dict) else {}
        deployment = markets.get("deployment", {}) if isinstance(markets.get("deployment"), dict) else {}
        return {
            "primary_region_id": primary.get("region_id", 0),
            "primary_system_id": primary.get("system_id", 0),
            "primary_structure_id": primary.get("structure_id", 0),
            "primary_market_name": primary.get("name", ""),
            "deployment_region_id": deployment.get("region_id", 0),
            "deployment_system_id": deployment.get("system_id", 0),
            "deployment_structure_id": deployment.get("structure_id", 0),
            "deployment_market_name": deployment.get("name", ""),
        }


# ---- Domain helpers ----


def get_all_market_contexts() -> dict[str, "MarketContext"]:
    """Return ``{alias: MarketContext}`` for every market in settings.

    The ``MarketContext`` import is lazy to avoid circular imports — this
    service is loaded before ``market_context`` in many call paths.
    """
    from mkts_backend.config.market_context import MarketContext

    settings = _load_settings()
    markets_raw = settings.get("markets", {})
    contexts: dict[str, "MarketContext"] = {}
    for alias, market_cfg in markets_raw.items():
        if alias == "default" or not isinstance(market_cfg, dict):
            continue
        contexts[alias] = MarketContext.from_settings(alias)
    return contexts


def get_all_characters() -> list["CharacterConfig"]:
    """Return all configured characters.

    Merges ``[chareacters.*]`` (legacy typo section, lower precedence) with
    ``[characters.*]`` (override on key collision).
    """
    from mkts_backend.config.character_config import CharacterConfig

    settings = _load_settings()
    merged: dict[str, dict] = {}
    for key, cfg in settings.get("chareacters", {}).items():
        if isinstance(cfg, dict):
            merged[key] = cfg
    for key, cfg in settings.get("characters", {}).items():
        if isinstance(cfg, dict):
            merged[key] = cfg

    chars = []
    for key, cfg in merged.items():
        chars.append(CharacterConfig(
            key=key,
            name=cfg.get("name", key),
            char_id=cfg["char_id"],
            token_env=cfg.get("token_env", f"REFRESH_TOKEN_{key.upper()}"),
            short_name=cfg.get("short_name", key[:3].capitalize()),
        ))
    return chars
