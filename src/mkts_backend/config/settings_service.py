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
import sys
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
    """Load and cache the TOML file as-is.

    The cached dict is the raw TOML — runtime overlays (notably
    ``MKTS_ENVIRONMENT``) are NOT baked in here. Consumers that need the
    runtime-effective value should go through :class:`SettingsService`'s
    typed properties (e.g. ``service.environment``), which read env vars
    at access time. This lets a CLI flag set after import still take effect.

    Default path: cached on first call.
    Explicit path: bypasses the cache entirely and never populates it.
    """
    global _cached_settings
    if path is not None:
        return _read_settings_file(path)
    if _cached_settings is not None:
        return _cached_settings
    _cached_settings = _read_settings_file(_DEFAULT_SETTINGS_PATH)
    return _cached_settings


def _read_settings_file(settings_path: Path) -> dict:
    try:
        with open(settings_path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        # Logging may not yet be configured at this bootstrap point — write
        # to stderr so the user sees the failure even before configure_logging.
        sys.stderr.write(f"FATAL: Failed to load settings from {settings_path}: {e}\n")
        logger.error("Failed to load settings from %s: %s", settings_path, e)
        raise


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
        """Resolved runtime environment.

        Returns ``MKTS_ENVIRONMENT`` if set, else the TOML ``app.environment``.
        Read on every access — a ``--env=development`` CLI flag that sets the
        env var after module imports still takes effect for downstream
        consumers like ``MarketContext.from_settings()``.
        """
        return os.environ.get("MKTS_ENVIRONMENT", self.settings["app"]["environment"])

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
        keep working. Raises ``KeyError`` if a required ID is missing — there
        is no EVE region/system/structure 0, so silent zero-defaults would just
        produce 404s far from the root cause. New code should use
        ``MarketContext`` via ``get_all_market_contexts()`` instead.
        """
        market_data = self.settings.get("market_data") or {}
        if market_data:
            return market_data

        markets = self.settings.get("markets", {})
        primary = markets.get("primary") if isinstance(markets.get("primary"), dict) else None
        deployment = markets.get("deployment") if isinstance(markets.get("deployment"), dict) else None
        if primary is None:
            raise KeyError(
                "settings.toml: [market_data] is absent and [markets.primary] "
                "is missing — cannot derive legacy market data."
            )
        if deployment is None:
            raise KeyError(
                "settings.toml: [market_data] is absent and [markets.deployment] "
                "is missing — cannot derive legacy market data."
            )
        return {
            "primary_region_id": primary["region_id"],
            "primary_system_id": primary["system_id"],
            "primary_structure_id": primary["structure_id"],
            "primary_market_name": primary.get("name", ""),
            "deployment_region_id": deployment["region_id"],
            "deployment_system_id": deployment["system_id"],
            "deployment_structure_id": deployment["structure_id"],
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
