"""
Character configuration loader.

Reads character definitions from settings.toml for asset lookups.
"""

import tomllib
from dataclasses import dataclass
from typing import List

from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)

SETTINGS_FILE = "src/mkts_backend/config/settings.toml"


@dataclass
class CharacterConfig:
    key: str
    name: str
    char_id: int
    token_env: str
    short_name: str


def load_characters(settings_file: str = SETTINGS_FILE) -> List[CharacterConfig]:
    """
    Load character configs from settings.toml.

    Merges both [characters.*] and [chareacters.*] sections for backward
    compatibility. Characters from [characters] take precedence on key
    collision.

    Returns:
        List of CharacterConfig objects
    """
    with open(settings_file, "rb") as f:
        settings = tomllib.load(f)

    merged: dict[str, dict] = {}

    # Load legacy typo section first (lower precedence)
    for key, cfg in settings.get("chareacters", {}).items():
        if isinstance(cfg, dict):
            merged[key] = cfg

    # Load correct section (overrides on collision)
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
