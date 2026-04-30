"""
Market Context - Bundles all market-specific configuration for a given market.

This module provides a MarketContext dataclass that encapsulates all configuration
needed for a specific market (e.g., primary/4-HWWF or deployment/B-9C24).
"""

from dataclasses import dataclass
from typing import Optional
import os

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.config.settings_service import SettingsService

logger = configure_logging(__name__)


@dataclass
class MarketContext:
    """
    Bundles all configuration for a specific market.

    This is the central configuration object that should be passed through
    the entire execution pipeline. It provides all market-specific values
    for database connections, ESI API calls, and Google Sheets updates.
    """
    alias: str                  # "primary" or "deployment"
    name: str                   # "4-HWWF Keepstar" or "B-9C24 Keepstar"
    region_id: int
    system_id: int
    structure_id: int
    database_alias: str         # "wcmktprod" or "wcmktnorth"
    database_file: str          # "wcmktprod.db" or "wcmktnorth2.db"
    turso_url_env: str          # env var name for Turso URL
    turso_token_env: str        # env var name for Turso token
    gsheets_url: str
    gsheets_worksheets: dict    # e.g., {"market_data": "market_data_4h", "doctrines": "doctrines_4h"}

    @classmethod
    def from_settings(cls, alias: str) -> "MarketContext":
        """
        Load market context from settings via the settings service.

        Args:
            alias: Market alias (e.g., "primary", "deployment")

        Returns:
            MarketContext instance with all configuration for the specified market

        Raises:
            ValueError: If the alias is not found in settings
        """
        service = SettingsService()
        markets = service.markets_raw

        if alias not in markets or not isinstance(markets.get(alias), dict):
            available = [k for k, v in markets.items() if k != "default" and isinstance(v, dict)]
            raise ValueError(f"Unknown market '{alias}'. Available: {available}")

        market_config = markets[alias]

        environment = service.environment
        db_alias = market_config["database_alias"]
        db_file = market_config["database_file"]
        turso_url_env = market_config["turso_url_env"]
        turso_token_env = market_config["turso_token_env"]

        if environment == "development" and alias == "primary":
            db_section = service.db_section
            db_alias = db_section.get("testing_database_alias", db_alias)
            db_file = db_section.get("testing_database_file", db_file)
            turso_url_env = db_section.get("testing_turso_url_env", turso_url_env)
            turso_token_env = db_section.get("testing_turso_token_env", turso_token_env)
            logger.info(f"Development environment: using testing database '{db_alias}' for primary market")

        context = cls(
            alias=alias,
            name=market_config["name"],
            region_id=market_config["region_id"],
            system_id=market_config["system_id"],
            structure_id=market_config["structure_id"],
            database_alias=db_alias,
            database_file=db_file,
            turso_url_env=turso_url_env,
            turso_token_env=turso_token_env,
            gsheets_url=market_config["gsheets_url"],
            gsheets_worksheets=market_config.get("gsheets_worksheets", {}),
        )

        logger.info(f"Loaded MarketContext for '{alias}': {context.name} (env={environment})")
        return context

    @classmethod
    def get_default(cls) -> "MarketContext":
        """
        Get the default market context as specified in settings.

        Returns:
            MarketContext for the default market (typically "primary")
        """
        return cls.from_settings(SettingsService().default_market_alias)

    @classmethod
    def list_available(cls) -> list[str]:
        """
        List all available market aliases.

        Returns:
            List of market alias strings (excludes "default" key)
        """
        markets = SettingsService().markets_raw
        return [k for k, v in markets.items() if k != "default" and isinstance(v, dict)]

    @classmethod
    def get_available_markets(cls) -> list[str]:
        """
        Alias for list_available() - returns all available market aliases.

        Returns:
            List of market alias strings
        """
        return cls.list_available()

    @property
    def turso_url(self) -> Optional[str]:
        """Get the Turso URL from environment variables."""
        return os.getenv(self.turso_url_env)

    @property
    def turso_token(self) -> Optional[str]:
        """Get the Turso auth token from environment variables."""
        return os.getenv(self.turso_token_env)

    def __repr__(self) -> str:
        return (
            f"MarketContext(alias='{self.alias}', name='{self.name}', "
            f"region_id={self.region_id}, structure_id={self.structure_id}, "
            f"database='{self.database_alias}')"
        )
