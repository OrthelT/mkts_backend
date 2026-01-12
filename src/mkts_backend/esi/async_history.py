import asyncio
import random
import time
import httpx
from aiolimiter import AsyncLimiter
import backoff
from typing import Optional, TYPE_CHECKING
from mkts_backend.config.config import DatabaseConfig
from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.config.logging_config import configure_logging

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

logger = configure_logging(__name__)
request_count = 0

# Default headers - can be overridden when market_ctx is provided
_DEFAULT_HEADERS = None

def _get_headers(market_ctx: Optional["MarketContext"] = None) -> dict:
    """Get headers with user agent, optionally using market context."""
    global _DEFAULT_HEADERS
    if market_ctx is not None:
        esi = ESIConfig(market_context=market_ctx)
        return {"User-Agent": esi.user_agent}
    if _DEFAULT_HEADERS is None:
        _DEFAULT_HEADERS = {"User-Agent": ESIConfig("primary").user_agent}
    return _DEFAULT_HEADERS


def _on_backoff(details):
    print(f"Retrying after {details['tries']} tries; waited {details['wait']:.2f}s")


@backoff.on_exception(
    backoff.expo,
    (httpx.HTTPStatusError, httpx.TransportError),
    max_time=180,
    giveup=lambda e: isinstance(e, httpx.HTTPStatusError) and e.response.status_code in {400, 403, 404},
    on_backoff=_on_backoff,
)
async def call_one(client: httpx.AsyncClient, type_id: int, length: int, region_id: int, limiter: AsyncLimiter, sema: asyncio.Semaphore, headers: dict) -> dict:
    global request_count

    total_req = length
    async with limiter:
        await asyncio.sleep(random.uniform(0, 0.05))
        async with sema:
            r = await client.get(
                f"https://esi.evetech.net/markets/{region_id}/history",
                headers=headers,
                params={"type_id": str(type_id)},
                timeout=30.0,
            )
            request_count += 1
            print(f"\r fetching history. ({round(100*(request_count/total_req),3)}%)", end="", flush=True)
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                if ra:
                    try:
                        await asyncio.sleep(float(ra))
                    except ValueError:
                        pass
                r.raise_for_status()
            r.raise_for_status()
            return {"type_id": type_id, "data": r.json()}


async def async_history(watchlist: list[int] = None, region_id: int = None, market_ctx: Optional["MarketContext"] = None):
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

    # Create limiter and semaphore within the async function to avoid event loop issues
    limiter = AsyncLimiter(300, time_period=60.0)
    sema = asyncio.Semaphore(50)

    t0 = time.perf_counter()
    async with httpx.AsyncClient(http2=True) as client:
        results = await asyncio.gather(*(call_one(client, tid, length, region_id, limiter, sema, headers) for tid in type_ids))
    logger.info(f"Got {len(results)} results in {time.perf_counter()-t0:.1f}s")
    logger.info(f"Request count: {request_count}")
    return results


def run_async_history(watchlist: list[int] = None, region_id: int = None, market_ctx: Optional["MarketContext"] = None):
    return asyncio.run(async_history(watchlist, region_id, market_ctx))


if __name__ == "__main__":
    pass
