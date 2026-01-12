from typing import Optional, TYPE_CHECKING

from mkts_backend.esi.esi_auth import get_token
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.config import load_settings, settings_file

if TYPE_CHECKING:
    from mkts_backend.config.market_context import MarketContext

logger = configure_logging(__name__)


settings = load_settings(settings_file)

class ESIConfig:
    """ESI configuration for primary and deployment markets."""

    # Legacy class-level lookups for backward compatibility
    _region_ids = {"primary_region_id": settings["market_data"]["primary_region_id"], "deployment_region_id": settings["market_data"]["deployment_region_id"]}
    _system_ids = {"primary_system_id": settings["market_data"]["primary_system_id"], "deployment_system_id": settings["market_data"]["deployment_system_id"]}
    _structure_ids = {"primary_structure_id": settings["market_data"]["primary_structure_id"], "deployment_structure_id": settings["market_data"]["deployment_structure_id"]}
    _valid_aliases = ["primary", "deployment"]
    _shortcut_aliases = {"4h": "primary"}
    _names = {"primary": settings["market_data"]["primary_market_name"], "deployment": settings["market_data"]["deployment_market_name"]}

    def __init__(
        self,
        alias: str = None,
        market_context: Optional["MarketContext"] = None
    ):
        """
        Initialize ESI configuration.

        Args:
            alias: Market alias (e.g., "primary", "deployment", "4h").
                   If market_context is provided, this is ignored.
            market_context: Optional MarketContext that provides all config values.
                           When provided, takes precedence over alias parameter.
        """
        if market_context is not None:
            # Use MarketContext for configuration (preferred method)
            self.alias = market_context.alias
            self.name = market_context.name
            self.region_id = market_context.region_id
            self.system_id = market_context.system_id
            self.structure_id = market_context.structure_id
            logger.info(f"ESIConfig initialized from MarketContext: {market_context.name}")
        else:
            # Legacy alias-based initialization (backward compatibility)
            if alias is None:
                alias = "primary"

            alias = alias.lower()
            if alias not in self._valid_aliases and alias not in self._shortcut_aliases:
                raise ValueError(
                    f"Invalid alias: {alias}. Valid aliases are: {self._valid_aliases} or {list(self._shortcut_aliases.keys())}"
                )
            elif alias in self._shortcut_aliases:
                self.alias = self._shortcut_aliases[alias]
            else:
                self.alias = alias

            self.name = self._names[self.alias]
            self.region_id = self._region_ids[f"{self.alias}_region_id"]
            self.system_id = self._system_ids[f"{self.alias}_system_id"]
            self.structure_id = self._structure_ids[f"{self.alias}_structure_id"]

        self.user_agent = settings["esi"]["user_agent"]
        self.compatibility_date = settings["esi"]["compatibility_date"]

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
    def headers(self, etag: str = None) -> dict:
        """HTTP headers for ESI requests (includes OAuth for structure markets)."""
        # Both primary and deployment use structure markets requiring authentication
        token = self.token()
        return {
            "Accept-Language": "en",
            "If-None-Match": f"{etag}",
            "X-Compatibility-Date": self.compatibility_date,
            "X-Tenant": "tranquility",
            "Accept": "application/json",
            "Authorization": f"Bearer {token['access_token']}",
        }
