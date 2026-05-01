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
from types import MappingProxyType
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mkts_backend.config.character_config import CharacterConfig
    from mkts_backend.config.market_context import MarketContext

logger = logging.getLogger(__name__)

_DEFAULT_SETTINGS_PATH = Path(__file__).parent / "settings.toml"
_cached_settings: dict | None = None
_cached_settings_view: MappingProxyType | None = None


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
    global _cached_settings, _cached_settings_view
    if path is not None:
        return _read_settings_file(path)
    if _cached_settings is not None:
        return _cached_settings
    _cached_settings = _read_settings_file(_DEFAULT_SETTINGS_PATH)
    _cached_settings_view = MappingProxyType(_cached_settings)
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
    global _cached_settings, _cached_settings_view
    _cached_settings = None
    _cached_settings_view = None


class SettingsService:
    """Read-only accessor for application settings.

    Settings are cached at module level after the first read. Instantiating
    multiple ``SettingsService()`` objects is cheap — they all share the
    same cached dict.
    """

    def __init__(self, settings_path: str | Path | None = None):
        path = Path(settings_path) if settings_path is not None else None
        self.settings = _load_settings(path)
        # Reuse the module-level proxy when possible (default path, cache hit)
        # so two instances return identical view objects. Explicit-path
        # instances build a per-instance view since they bypass the cache.
        if path is None and _cached_settings_view is not None and self.settings is _cached_settings:
            self._view: MappingProxyType = _cached_settings_view
        else:
            self._view = MappingProxyType(self.settings)

    @property
    def settings_dict(self) -> MappingProxyType:
        """Return the full settings dictionary as a read-only view.

        The wrap is shallow — nested dicts can still be mutated, but the
        top-level cache cannot be replaced or have keys added/removed.
        Prefer the typed properties below for stable access.
        """
        return self._view

    def _require(self, *path: str) -> str | int | bool | list:
        """Walk a nested key path, raising KeyError with a helpful message
        if any segment is absent or null."""
        node = self.settings
        for i, key in enumerate(path):
            if not isinstance(node, dict) or key not in node:
                trail = ".".join(path[: i + 1])
                raise KeyError(f"settings.toml: [{trail}] is required but missing.")
            node = node[key]
        if node is None:
            trail = ".".join(path)
            raise KeyError(f"settings.toml: [{trail}] is required but null.")
        return node

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
    def markets_raw(self) -> MappingProxyType:
        """Raw [markets] section as a read-only view, including the 'default' key."""
        return MappingProxyType(self.settings.get("markets", {}))

    # ---- [db] ----

    @property
    def db_section(self) -> MappingProxyType:
        """Raw [db] section as a read-only view."""
        return MappingProxyType(self.settings.get("db", {}))

    @property
    def db_production_alias(self) -> str:
        return self._require("db", "production_database_alias")

    @property
    def db_production_file(self) -> str:
        return self._require("db", "production_database_file")

    @property
    def db_testing_alias(self) -> str:
        return self._require("db", "testing_database_alias")

    @property
    def db_testing_file(self) -> str:
        return self._require("db", "testing_database_file")

    @property
    def db_deployment_alias(self) -> str:
        return self._require("db", "deployment_database_alias")

    @property
    def db_deployment_file(self) -> str:
        return self._require("db", "deployment_database_file")

    @property
    def db_sde_file(self) -> str:
        return self._require("db", "shared", "sde_file")

    @property
    def db_fittings_file(self) -> str:
        return self._require("db", "shared", "fittings_file")

    @property
    def db_buildcost_file(self) -> str:
        return self._require("db", "shared", "buildcost_file")

    @property
    def db_cli_cache_file(self) -> str:
        return self._require("db", "shared", "cli_cache_file")

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
