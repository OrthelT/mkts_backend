import asyncio
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
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
MAX_CONCURRENCY = 10
# EverRef has no rate limit; the maintainer has confirmed bursts are fine.
# 120 req/min keeps the average at 2/sec (polite for a single-maintainer hobby
# API) and lets the full ~1300-item watchlist finish inside a 15-min CI budget.
EVEREF_REQUESTS_PER_MINUTE = 120

MANUFACTURABLE_META_GROUPS = frozenset({1, 2, 14})
ALLOWED_CATEGORIES = frozenset({7, 18, 8, 6, 87, 22, 32})
EXCLUDED_GROUPS = frozenset(
    {
        "Interdiction Nullifier",
        "Exotic Plasma Charge",
        "Condenser Pack",
        "Jump Drive Economizer",
        "Warp Accelerator",
        "Vorton Projector",
        "Advanced Condenser Pack",
        "SCARAB Breacher Pods",
    }
)
EXCLUDED_NAMES = frozenset(
    {
        "Vedmak",
        "Leshak",
        "Damavik",
        "Zirnitra",
        "Metamorphosis",
        "Stormbringer",
        "Pacifier",
        "Enforcer",
        "'Magpie' Mobile Tractor Unit",
        "'Packrat' Mobile Tractor Unit",
        "'Yurt' Mobile Depot",
        "'Wetu' Mobile Depot",
    }
)
HIGH_VALUE_THRESHOLD = 40_000_000
T2_MODULE_CATEGORIES = frozenset({7, 18, 8})

DEFAULT_TE = 0
DEFAULT_MATERIAL_PRICE_SOURCE = "ESI_AVG"

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)

PROGRESS_LOG_INTERVAL = 100


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)} sec"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs} sec"


class _ProgressTracker:
    """Counts completed fetches and logs every PROGRESS_LOG_INTERVAL items.

    Safe for asyncio because all increments happen between awaits in the
    single-threaded event loop. ETA is derived from wall-clock rate, which
    converges to the rate-limiter's steady-state after the initial burst.
    """

    def __init__(self, total: int, interval: int = PROGRESS_LOG_INTERVAL) -> None:
        self.total = total
        self.interval = interval
        self.completed = 0
        self.start = time.monotonic()

    def tick(self) -> None:
        self.completed += 1
        if self.completed % self.interval != 0 or self.completed >= self.total:
            return
        elapsed = time.monotonic() - self.start
        if elapsed <= 0:
            return
        rate = self.completed / elapsed
        eta = (self.total - self.completed) / rate if rate > 0 else 0.0
        logger.info(
            f"Fetched {self.completed} of {self.total} items, "
            f"estimated time remaining: {_format_eta(eta)}"
        )


class WatchlistMetadata(TypedDict):
    type_id: int
    type_name: str | None
    group_name: str | None
    category_id: int | None


class BuilderCostRecord(TypedDict):
    type_id: int
    total_cost_per_unit: float
    time_per_unit: float
    me: int
    runs: int
    fetched_at: datetime


@dataclass
class FetchSummary:
    """Outcome of a builder-cost fetch run.

    ``attempted`` counts items that entered the EverRef queue (i.e. survived
    the design-time filters in ``_resolve_api_params`` and the SDE buildable
    join). ``failed = attempted - len(records)`` is the real EverRef miss
    count — the right denominator when deciding whether the run "missed"
    fresh data.
    """

    records: list[BuilderCostRecord] = field(default_factory=list)
    attempted: int = 0
    filtered_unbuildable: int = 0  # no manufacturing blueprint in SDE
    filtered_out_of_scope: int = 0  # excluded by meta-group/category/name filters

    @property
    def succeeded(self) -> int:
        return len(self.records)

    @property
    def failed(self) -> int:
        return self.attempted - self.succeeded


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


_META_GROUP_CHUNK_SIZE = (
    500  # keep well under libsql/sqlite var limits (matches db_handlers)
)


def _get_meta_groups(type_ids: list[int], sde_engine: Engine) -> dict[int, int]:
    """Return meta_group_id only for type_ids that are produced by a manufacturing blueprint.

    Inner-joins `industryActivityProducts` (activityID=1) so meta-level T1 NPC
    drops (e.g. "Compact"/"Enduring"/"Scoped" modules) are filtered out before
    we waste EverRef requests that would return HTTP 400 "not produced from a
    blueprint". Items missing from the SDE or without a blueprint are absent
    from the returned dict.
    """
    return _query_buildable(type_ids, sde_engine, return_meta_group=True)


def filter_buildable(type_ids: list[int], sde_engine: Engine) -> set[int]:
    """Return the subset of ``type_ids`` produced by a manufacturing blueprint.

    Uses the same ``industryActivityProducts`` join as ``_get_meta_groups``;
    used at write time by the build_watchlist mutation helpers so unbuildable
    items never make it into the table in the first place.
    """
    return set(_query_buildable(type_ids, sde_engine, return_meta_group=False).keys())


def _query_buildable(
    type_ids: list[int],
    sde_engine: Engine,
    *,
    return_meta_group: bool,
) -> dict[int, int]:
    if not type_ids:
        return {}

    out: dict[int, int] = {}
    with sde_engine.connect() as conn:
        for start in range(0, len(type_ids), _META_GROUP_CHUNK_SIZE):
            chunk = type_ids[start : start + _META_GROUP_CHUNK_SIZE]
            placeholders = ", ".join(
                f":type_id_{index}" for index, _ in enumerate(chunk)
            )
            params = {
                f"type_id_{index}": type_id for index, type_id in enumerate(chunk)
            }
            query = text(
                f"""
                SELECT s.typeID, s.metaGroupID
                FROM sdetypes s
                INNER JOIN industryActivityProducts iap
                  ON iap.productTypeID = s.typeID AND iap.activityID = 1
                WHERE s.typeID IN ({placeholders})
                """
            )
            for row in conn.execute(query, params).mappings():
                type_id = row.get("typeID")
                if type_id is None:
                    continue
                if return_meta_group:
                    meta_group_id = row.get("metaGroupID")
                    if meta_group_id is None:
                        continue
                    out[int(type_id)] = int(meta_group_id)
                else:
                    out[int(type_id)] = 1
    return out


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
    progress: _ProgressTracker | None = None,
) -> BuilderCostRecord | None:
    try:
        return await _fetch_one_inner(client, semaphore, limiter, type_id, me, runs)
    finally:
        if progress is not None:
            progress.tick()


async def _fetch_one_inner(
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
        logger.warning(
            f"EverRef response missing manufacturing data for {type_id}: {exc}"
        )
        return None

    total_cost = result.get("total_cost_per_unit")
    if total_cost is None:
        logger.warning(f"EverRef response missing total_cost_per_unit for {type_id}")
        return None

    time_per_unit = _parse_iso_duration(result.get("time_per_unit"))
    if time_per_unit is None:
        logger.warning(f"EverRef response missing time_per_unit for {type_id}")
        return None

    return {
        "type_id": type_id,
        "total_cost_per_unit": float(total_cost),
        "time_per_unit": time_per_unit,
        "me": me,
        "runs": runs,
        "fetched_at": datetime.now(timezone.utc),
    }


async def async_fetch_builder_costs(
    type_ids: list[int],
    jita_prices: dict[int, float],
    sde_engine: Engine,
    watchlist_metadata: Mapping[int, WatchlistMetadata] | None = None,
) -> FetchSummary:
    watchlist_metadata = watchlist_metadata or {}
    meta_groups = _get_meta_groups(type_ids, sde_engine)
    unbuildable = len(type_ids) - len(meta_groups)
    if unbuildable:
        logger.info(
            f"Filtered {unbuildable}/{len(type_ids)} watchlist items "
            "with no manufacturing blueprint in the SDE"
        )

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

    out_of_scope = len(type_ids) - unbuildable - len(fetch_jobs)

    if not fetch_jobs:
        logger.info(
            "No manufacturable watchlist items matched the builder cost filters"
        )
        return FetchSummary(
            attempted=0,
            filtered_unbuildable=unbuildable,
            filtered_out_of_scope=out_of_scope,
        )

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    limiter = AsyncLimiter(EVEREF_REQUESTS_PER_MINUTE, time_period=60.0)
    headers = {"User-Agent": SettingsService().esi_user_agent}
    progress = _ProgressTracker(total=len(fetch_jobs))

    async with httpx.AsyncClient(http2=True, headers=headers) as client:
        results = await asyncio.gather(
            *(
                _fetch_one(client, semaphore, limiter, type_id, me, runs, progress)
                for type_id, me, runs in fetch_jobs
            )
        )

    successful = [result for result in results if result is not None]
    failed = len(fetch_jobs) - len(successful)
    if failed:
        logger.warning(
            f"{failed}/{len(fetch_jobs)} items failed; persisting the {len(successful)} successful results"
        )
    else:
        logger.info(f"{len(successful)}/{len(fetch_jobs)} items fetched successfully")
    return FetchSummary(
        records=successful,
        attempted=len(fetch_jobs),
        filtered_unbuildable=unbuildable,
        filtered_out_of_scope=out_of_scope,
    )


def run_async_fetch_builder_costs(
    type_ids: list[int],
    jita_prices: dict[int, float],
    sde_engine: Engine,
    watchlist_metadata: Mapping[int, WatchlistMetadata] | None = None,
) -> FetchSummary:
    return asyncio.run(
        async_fetch_builder_costs(
            type_ids,
            jita_prices,
            sde_engine,
            watchlist_metadata=watchlist_metadata,
        )
    )

