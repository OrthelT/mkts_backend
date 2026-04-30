"""
Character configuration loader.

Reads character definitions from settings.toml for asset lookups.
"""

from dataclasses import dataclass
from typing import List

from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)


@dataclass
class CharacterConfig:
    key: str
    name: str
    char_id: int
    token_env: str
    short_name: str


def load_characters() -> List[CharacterConfig]:
    """
    Load character configs via the settings service.

    Merges both [characters.*] and [chareacters.*] sections for backward
    compatibility. Characters from [characters] take precedence on key
    collision. The merge logic lives in
    ``settings_service.get_all_characters``.

    Returns:
        List of CharacterConfig objects
    """
    from mkts_backend.config.settings_service import get_all_characters
    return get_all_characters()
