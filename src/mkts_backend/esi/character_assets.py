"""
Character asset fetching via ESI.

Fetches packaged (non-singleton) assets for configured characters,
returning {type_id: quantity} maps suitable for display in needed tables.
"""

import os
from collections import defaultdict
from typing import Dict, List, Optional

import requests

from mkts_backend.config.character_config import CharacterConfig, load_characters
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.esi.esi_auth import get_token_for_character

logger = configure_logging(__name__)

ESI_ASSETS_URL = (
    "https://esi.evetech.net/latest/characters/{char_id}/assets/"
    "?datasource=tranquility&page={page}"
)
ASSETS_SCOPE = "esi-assets.read_assets.v1"


def fetch_character_assets(char: CharacterConfig) -> Dict[int, int]:
    """
    Fetch packaged assets for a single character via ESI.

    Paginates through all pages, filters to is_singleton=False (packaged),
    and sums quantities by type_id.

    Args:
        char: Character configuration with key, char_id, token_env

    Returns:
        Dict mapping type_id to total packaged quantity.
        Returns empty dict on auth or request failure.
    """
    refresh_token = os.getenv(char.token_env, "")

    try:
        token = get_token_for_character(char.key, refresh_token, ASSETS_SCOPE)
    except Exception as e:
        logger.error(f"Token fetch failed for {char.name}: {e}")
        return {}

    access_token = token.get("access_token", "")
    headers = {"Authorization": f"Bearer {access_token}"}

    assets: Dict[int, int] = defaultdict(int)
    page = 1
    max_pages = 1

    while page <= max_pages:
        url = ESI_ASSETS_URL.format(char_id=char.char_id, page=page)
        try:
            resp = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as e:
            logger.error(f"ESI request failed for {char.name} page {page}: {e}")
            break

        if resp.status_code == 403:
            logger.error(f"ESI 403 for {char.name} — token may lack scope")
            print(
                f"\nToken for {char.name} lacks required scope. Run:\n"
                f"  mkts-backend esi-auth --char={char.key}\n"
            )
            break
        if resp.status_code != 200:
            logger.error(
                f"ESI {resp.status_code} for {char.name} page {page}"
            )
            break

        if page == 1:
            max_pages = int(resp.headers.get("X-Pages", 1))

        for item in resp.json():
            if not item.get("is_singleton", False):
                assets[item["type_id"]] += item.get("quantity", 0)

        page += 1

    logger.info(
        f"Fetched {sum(assets.values())} packaged items "
        f"({len(assets)} types) for {char.name}"
    )
    return dict(assets)


def fetch_all_character_assets(
    type_ids: Optional[List[int]] = None,
) -> List[tuple]:
    """
    Fetch assets for all configured characters.

    Args:
        type_ids: If provided, only include these type_ids in results.

    Returns:
        List of (CharacterConfig, {type_id: qty}) tuples, one per character.
        Characters that fail auth are included with empty dicts.
    """
    characters = load_characters()
    results = []

    for char in characters:
        assets = fetch_character_assets(char)

        if type_ids is not None:
            assets = {tid: qty for tid, qty in assets.items() if tid in type_ids}

        results.append((char, assets))

    return results
