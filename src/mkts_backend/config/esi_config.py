from typing import TYPE_CHECKING

from mkts_backend.esi.esi_auth import get_token
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.settings_service import SettingsService

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

logger = configure_logging(__name__)


_service = SettingsService()


class ESIConfig:
    """ESI configuration bound to a specific :class:`MarketContext`.

    A market context is required — it carries the region / system / structure
    IDs the ESI URLs depend on.
    """

    def __init__(self, market_context: "MarketContext"):
        self.alias = market_context.alias
        self.name = market_context.name
        self.region_id = market_context.region_id
        self.system_id = market_context.system_id
        self.structure_id = market_context.structure_id
        logger.info(f"ESIConfig initialized from MarketContext: {market_context.name}")

        self.user_agent = _service.esi_user_agent
        self.compatibility_date = _service.esi_compatibility_date

    def token(self, scope: str = "esi-markets.structure_markets.v1"):
        return get_token(scope)

    @property
    def market_orders_url(self):
        """URL for fetching market orders (structure-based endpoint)."""
        # Both primary and deployment use structure markets requiring authentication
        return f"https://esi.evetech.net/markets/structures/{self.structure_id}"

    @property
    def market_history_url(self):
        """URL for fetching market history (region-based endpoint)."""
        return f"https://esi.evetech.net/markets/{self.region_id}/history"

    @property
    def headers(self) -> dict:
        """HTTP headers for ESI requests (includes OAuth for structure markets)."""
        token = self.token()
        return {
            "Accept-Language": "en",
            "X-Compatibility-Date": self.compatibility_date,
            "X-Tenant": "tranquility",
            "Accept": "application/json",
            "Authorization": f"Bearer {token['access_token']}",
            "User-Agent": self.user_agent,
        }
