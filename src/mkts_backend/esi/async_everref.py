import asyncio
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, TypedDict

import httpx
from aiolimiter import AsyncLimiter
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mkts_backend.config.settings_service import SettingsService
from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)

EVEREF_BASE_URL = "https://api.everef.net/v1/industry/cost"
EVEREF_STATIC_PARAMS = (
    "structure_type_id=35826&security=NULL_SEC"
    "&system_cost_bonus=0&manufacturing_cost=0&facility_tax=0"
)
API_TIMEOUT = 20.0
MAX_CONCURRENCY = 6

MANUFACTURABLE_META_GROUPS = frozenset({1, 2, 14})
ALLOWED_CATEGORIES = frozenset({7, 18, 8, 6, 87, 22, 32})
EXCLUDED_GROUPS = frozenset(
    {"Interdiction Nullifier", "Exotic Plasma Charge", "Condenser Pack"}
)
EXCLUDED_NAMES = frozenset({"Vedmak", "Leshak", "Damavik", "Zirnitra"})
HIGH_VALUE_THRESHOLD = 40_000_000
T2_MODULE_CATEGORIES = frozenset({7, 18, 8})

DEFAULT_TE = 0
DEFAULT_MATERIAL_PRICE_SOURCE = "ESI_AVG"

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


class WatchlistMetadata(TypedDict):
    type_id: int
    type_name: str | None
    group_name: str | None
    category_id: int | None


class BuilderCostRecord(TypedDict):
    type_id: int
    total_cost_per_unit: float
    time_per_unit: float | None
    me: int
    runs: int
    fetched_at: str


def _parse_iso_duration(value: str | None) -> float | None:
    if not value:
        return None

    match = _DURATION_RE.match(value)
    if match is None:
        return None

    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0.0)
    return float(days * 86400 + hours * 3600 + minutes * 60) + seconds


def _resolve_api_params(
    meta_group_id: int | None,
    category_id: int | None,
    group_name: str | None,
    type_name: str | None,
    jita_price: float | None,
) -> tuple[int, int] | None:
    if meta_group_id not in MANUFACTURABLE_META_GROUPS:
        return None
    if category_id not in ALLOWED_CATEGORIES:
        return None
    if group_name in EXCLUDED_GROUPS or type_name in EXCLUDED_NAMES:
        return None

    if meta_group_id == 1:
        return (10, 10)

    if meta_group_id == 2 and category_id in T2_MODULE_CATEGORIES:
        if jita_price is not None and jita_price > HIGH_VALUE_THRESHOLD:
            return (4, 5)
        return (0, 10)

    if meta_group_id == 2 and category_id == 6:
        return (3, 3)

    return (0, 1)


def _get_meta_groups(type_ids: list[int], sde_engine: Engine) -> dict[int, int]:
    if not type_ids:
        return {}

    placeholders = ", ".join(f":type_id_{index}" for index, _ in enumerate(type_ids))
    params = {f"type_id_{index}": type_id for index, type_id in enumerate(type_ids)}
    query = text(
        f"SELECT typeID, metaGroupID FROM sdetypes WHERE typeID IN ({placeholders})"
    )

    with sde_engine.connect() as conn:
        result = conn.execute(query, params)
        meta_groups: dict[int, int] = {}
        for row in result.mappings():
            type_id = row.get("typeID")
            meta_group_id = row.get("metaGroupID")
            if type_id is None or meta_group_id is None:
                continue
            meta_groups[int(type_id)] = int(meta_group_id)
        return meta_groups


def _build_request_url(type_id: int, me: int, runs: int) -> str:
    return (
        f"{EVEREF_BASE_URL}?product_id={type_id}&runs={runs}&me={me}"
        f"&te={DEFAULT_TE}&material_prices={DEFAULT_MATERIAL_PRICE_SOURCE}"
        f"&{EVEREF_STATIC_PARAMS}"
    )


async def _fetch_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    limiter: AsyncLimiter,
    type_id: int,
    me: int,
    runs: int,
) -> BuilderCostRecord | None:
    url = _build_request_url(type_id, me, runs)

    async with limiter:
        async with semaphore:
            try:
                response = await client.get(url, timeout=API_TIMEOUT)
            except Exception as exc:
                logger.warning(f"EverRef fetch failed for {type_id}: {exc}")
                return None

    if response.status_code != 200:
        logger.warning(
            f"EverRef returned HTTP {response.status_code} for {type_id}: {response.text[:200]}"
        )
        return None

    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("payload is not a dictionary")
        manufacturing = payload.get("manufacturing")
        if not isinstance(manufacturing, dict):
            raise KeyError("manufacturing")
        result = manufacturing[str(type_id)]
        if not isinstance(result, dict):
            raise TypeError("manufacturing result is not a dictionary")
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(f"EverRef response missing manufacturing data for {type_id}: {exc}")
        return None

    total_cost = result.get("total_cost_per_unit")
    if total_cost is None:
        logger.warning(f"EverRef response missing total_cost_per_unit for {type_id}")
        return None

    return {
        "type_id": type_id,
        "total_cost_per_unit": float(total_cost),
        "time_per_unit": _parse_iso_duration(result.get("time_per_unit")),
        "me": me,
        "runs": runs,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def async_fetch_builder_costs(
    type_ids: list[int],
    jita_prices: dict[int, float],
    sde_engine: Engine,
    watchlist_metadata: Mapping[int, WatchlistMetadata] | None = None,
) -> list[BuilderCostRecord]:
    watchlist_metadata = watchlist_metadata or {}
    meta_groups = _get_meta_groups(type_ids, sde_engine)

    fetch_jobs: list[tuple[int, int, int]] = []
    for type_id in type_ids:
        metadata = watchlist_metadata.get(type_id, {})
        params = _resolve_api_params(
            meta_group_id=meta_groups.get(type_id),
            category_id=metadata.get("category_id") if metadata else None,
            group_name=metadata.get("group_name") if metadata else None,
            type_name=metadata.get("type_name") if metadata else None,
            jita_price=jita_prices.get(type_id),
        )
        if params is None:
            continue
        me, runs = params
        fetch_jobs.append((type_id, me, runs))

    if not fetch_jobs:
        logger.info("No manufacturable watchlist items matched the builder cost filters")
        return []

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    limiter = AsyncLimiter(30, time_period=60.0)
    headers = {"User-Agent": SettingsService().esi_user_agent}

    async with httpx.AsyncClient(http2=True, headers=headers) as client:
        results = await asyncio.gather(
            *(
                _fetch_one(client, semaphore, limiter, type_id, me, runs)
                for type_id, me, runs in fetch_jobs
            )
        )

    successful = [result for result in results if result is not None]
    logger.info(f"{len(successful)}/{len(fetch_jobs)} items fetched successfully")
    if len(successful) != len(fetch_jobs):
        logger.warning("Builder cost fetch incomplete; aborting write to avoid partial replacement")
        return []
    return successful


def run_async_fetch_builder_costs(
    type_ids: list[int],
    jita_prices: dict[int, float],
    sde_engine: Engine,
    watchlist_metadata: Mapping[int, WatchlistMetadata] | None = None,
) -> list[BuilderCostRecord]:
    return asyncio.run(
        async_fetch_builder_costs(
            type_ids,
            jita_prices,
            sde_engine,
            watchlist_metadata=watchlist_metadata,
        )
    )