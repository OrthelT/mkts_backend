"""
Character configuration dataclass.

Reads character definitions from settings.toml for asset lookups. To load
the configured characters, call ``settings_service.get_all_characters()``.
"""

from dataclasses import dataclass


@dataclass
class CharacterConfig:
    key: str
    name: str
    char_id: int
    token_env: str
    short_name: str
