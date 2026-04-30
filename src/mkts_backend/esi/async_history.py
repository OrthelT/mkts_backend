import asyncio
import os
import time
import httpx
from aiolimiter import AsyncLimiter
from typing import Optional, TYPE_CHECKING
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.config.logging_config import configure_logging

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

logger = configure_logging(__name__)
request_count = 0
error_count = 0

# Check if terminal output (progress prints) should be suppressed.
# Set MKTS_QUIET=1 in CI/GitHub Actions to disable progress output.
QUIET = os.environ.get("MKTS_QUIET", "0") == "1"

# Default headers - can be overridden when market_ctx is provided
_DEFAULT_HEADERS = None

def _get_headers(market_ctx: Optional["MarketContext"] = None) -> dict:
    """Get headers with user agent and ESI best-practice fields."""
    global _DEFAULT_HEADERS
    if market_ctx is not None:
        esi = ESIConfig(market_context=market_ctx)
        return {
            "User-Agent": esi.user_agent,
            "Accept": "application/json",
            "X-Compatibility-Date": esi.compatibility_date,
            "X-Tenant": "tranquility",
        }
    if _DEFAULT_HEADERS is None:
        esi = ESIConfig("primary")
        _DEFAULT_HEADERS = {
            "User-Agent": esi.user_agent,
            "Accept": "application/json",
            "X-Compatibility-Date": esi.compatibility_date,
            "X-Tenant": "tranquility",
        }
    return _DEFAULT_HEADERS


async def call_one(
    client: httpx.AsyncClient,
    type_id: int,
    length: int,
    region_id: int,
    limiter: AsyncLimiter,
    sema: asyncio.Semaphore,
    headers: dict,
    cache_entry: dict | None = None,
) -> dict:
    global request_count, error_count

    total_req = length

    # Build per-request headers with conditional request fields
    req_headers = dict(headers)
    if cache_entry:
        if cache_entry.get("etag"):
            req_headers["If-None-Match"] = cache_entry["etag"]
        if cache_entry.get("last_modified"):
            req_headers["If-Modified-Since"] = cache_entry["last_modified"]

    async with limiter:
        async with sema:
            try:
                r = await client.get(
                    f"https://esi.evetech.net/markets/{region_id}/history",
                    headers=req_headers,
                    params={"type_id": str(type_id)},
                    timeout=30.0,
                )
            except httpx.TransportError as exc:
                logger.error(f"Transport error for type_id {type_id}: {exc}")
                error_count += 1
                return {"type_id": type_id, "data": None, "status": 0, "error": str(exc)}

            request_count += 1
            if not QUIET:
                print(f"\r fetching history. ({round(100*(request_count/total_req),3)}%)", end="", flush=True)

            # --- ESI Error Limit Headers ---
            # Track error budget; pause all requests if we're close to exhaustion.
            error_remain = r.headers.get("X-ESI-Error-Limit-Remain")
            error_reset = r.headers.get("X-ESI-Error-Limit-Reset")

            if error_remain is not None:
                try:
                    remain = int(error_remain)
                    reset = int(error_reset) if error_reset else 60
                    if remain < 10:
                        logger.critical(
                            f"ESI error budget nearly exhausted: {remain} errors remain, resets in {reset}s. "
                            f"Pausing requests for {reset}s."
                        )
                        await asyncio.sleep(reset)
                    elif remain < 50:
                        logger.warning(f"ESI error budget low: {remain} errors remain, resets in {reset}s")
                except (ValueError, TypeError):
                    pass

            # Handle 304 Not Modified — refund rate-limit token since ESI
            # does not count conditional hits against the caller's budget.
            if r.status_code == 304:
                limiter._level = max(0, limiter._level - 1)
                return {
                    "type_id": type_id,
                    "data": None,
                    "status": 304,
                    "etag": r.headers.get("ETag") or (cache_entry.get("etag") if cache_entry else None),
                    "last_modified": r.headers.get("Last-Modified") or (cache_entry.get("last_modified") if cache_entry else None),
                }

            # Handle success
            if r.status_code == 200:
                return {
                    "type_id": type_id,
                    "data": r.json(),
                    "status": 200,
                    "etag": r.headers.get("ETag"),
                    "last_modified": r.headers.get("Last-Modified"),
                }

            # --- Error handling for non-200/304 responses ---
            error_count += 1
            logger.error(
                f"ESI error for type_id {type_id}: HTTP {r.status_code} | "
                f"error_remain={error_remain} error_reset={error_reset} | "
                f"body={r.text[:200]}"
            )

            if r.status_code == 420:
                # Error limit exceeded — stop hammering and wait for reset
                reset_seconds = int(error_reset) if error_reset else 60
                logger.critical(
                    f"HTTP 420 Error Limit Exceeded. Sleeping {reset_seconds}s until reset."
                )
                await asyncio.sleep(reset_seconds)
                return {"type_id": type_id, "data": None, "status": 420, "error": "error limit exceeded"}

            if r.status_code == 429:
                # Rate limited — honour Retry-After if present
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra else 5.0
                logger.warning(f"HTTP 429 for type_id {type_id}, retrying after {wait}s")
                await asyncio.sleep(wait)
                return {"type_id": type_id, "data": None, "status": 429, "error": "rate limited"}

            # For client errors (4xx) don't retry — these won't succeed on retry
            if 400 <= r.status_code < 500:
                logger.warning(
                    f"Client error {r.status_code} for type_id {type_id}. Skipping (will not retry)."
                )
                return {"type_id": type_id, "data": None, "status": r.status_code, "error": f"HTTP {r.status_code}"}

            # For server errors (5xx), log and return error
            logger.error(f"Server error {r.status_code} for type_id {type_id}")
            return {"type_id": type_id, "data": None, "status": r.status_code, "error": f"HTTP {r.status_code}"}


async def async_history(watchlist: list[int] = None, region_id: int = None, market_ctx: Optional["MarketContext"] = None):
    global request_count, error_count
    request_count = 0
    error_count = 0

    from mkts_backend.db.db_handlers import load_esi_cache, save_esi_cache

    # Get headers and defaults based on market context
    headers = _get_headers(market_ctx)

    # Default to market context region, then primary region if none specified
    if region_id is None:
        if market_ctx is not None:
            region_id = market_ctx.region_id
        else:
            region_id = ESIConfig("primary").region_id

    if watchlist is None:
        if market_ctx is not None:
            db = DatabaseConfig(market_context=market_ctx)
        else:
            db = DatabaseConfig("wcmkt")
        watchlist = db.get_watchlist()
        type_ids = watchlist["type_id"].unique().tolist()
    else:
        type_ids = watchlist

    length = len(type_ids)

    # Load ESI request cache for conditional headers
    cache = load_esi_cache(region_id, market_ctx)
    if cache:
        logger.info(f"Loaded {len(cache)} ESI cache entries for region {region_id}")

    # Rate limit to 300 requests per 60 seconds and cap concurrency at 50
    limiter = AsyncLimiter(300, time_period=60.0)
    sema = asyncio.Semaphore(50)

    t0 = time.perf_counter()
    async with httpx.AsyncClient(http2=True) as client:
        results = await asyncio.gather(*(
            call_one(client, tid, length, region_id, limiter, sema, headers, cache_entry=cache.get(tid))
            for tid in type_ids
        ))

    if not QUIET:
        print()  # newline after progress output

    # Log 200 vs 304 counts
    count_200 = sum(1 for r in results if r and r.get("status") == 200)
    count_304 = sum(1 for r in results if r and r.get("status") == 304)
    count_err = sum(1 for r in results if r and r.get("status") not in (200, 304))
    logger.info(
        f"Got {len(results)} results in {time.perf_counter()-t0:.1f}s "
        f"({count_200} updated, {count_304} unchanged, {count_err} errors)"
    )
    logger.info(f"Request count: {request_count}, error count: {error_count}")

    if count_err > 0:
        error_types = {}
        for r in results:
            if r and r.get("status") not in (200, 304):
                status = r.get("status", "unknown")
                error_types[status] = error_types.get(status, 0) + 1
        logger.warning(f"Error breakdown: {error_types}")

    # Save updated cache entries (only for successful results)
    save_esi_cache(results, region_id, market_ctx)

    return results


def run_async_history(watchlist: list[int] = None, region_id: int = None, market_ctx: Optional["MarketContext"] = None):
    return asyncio.run(async_history(watchlist, region_id, market_ctx))


if __name__ == "__main__":
    pass
