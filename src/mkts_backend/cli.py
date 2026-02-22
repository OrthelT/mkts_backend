import sys
import json
import time
import os
from typing import Optional

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.db_queries import get_table_length
from mkts_backend.db.db_handlers import (
    upsert_database,
    update_history,
    update_market_orders,
    log_update,
)
from mkts_backend.db.models import MarketStats, Doctrines
from mkts_backend.utils.utils import (
    validate_columns,
    convert_datetime_columns,
    init_databases,
)
from mkts_backend.processing.data_processing import (
    calculate_market_stats,
    calculate_doctrine_stats,
)
from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.esi.esi_requests import fetch_market_orders
from mkts_backend.esi.async_history import run_async_history
from mkts_backend.utils.validation import validate_all
from mkts_backend.config.config import load_settings, DatabaseConfig
from mkts_backend.cli_tools.args_parser import parse_args
from mkts_backend.config.gsheets_config import GoogleSheetConfig
from mkts_backend.config.market_context import MarketContext

settings = load_settings(file_path="src/mkts_backend/config/settings.toml")
logger = configure_logging(__name__)


def process_market_orders(
    esi: ESIConfig,
    order_type: str = "all",
    test_mode: bool = False,
    market_ctx: Optional[MarketContext] = None,
) -> bool:
    """Fetches market orders from ESI and updates the database."""
    save_path = "data/market_orders_new.json"
    data = fetch_market_orders(esi, order_type=order_type, test_mode=test_mode)
    if data:
        with open(save_path, "w") as f:
            json.dump(data, f)
        logger.info(f"ESI returned {len(data)} market orders. Saved to {save_path}")
        status = update_market_orders(data, market_ctx=market_ctx)
        if status:
            log_update("marketorders", remote=True, market_ctx=market_ctx)
            logger.info(
                f"Orders updated:{get_table_length('marketorders', market_ctx=market_ctx)} items"
            )
            return True
        else:
            logger.error(
                "Failed to update market orders. ESI call succeeded but something went wrong updating the database"
            )
            return False
    else:
        logger.error("no data returned from ESI call.")
        return False


def process_history(market_ctx: Optional[MarketContext] = None):
    logger.info("History mode enabled")
    logger.info("Processing history")
    data = run_async_history(market_ctx=market_ctx)
    if data:
        # Only write results with actual data to the debug JSON file
        data_with_content = [r for r in data if r and r.get("data") is not None]
        if data_with_content:
            with open("data/market_history_new.json", "w") as f:
                json.dump(data_with_content, f)
        status = update_history(data, market_ctx=market_ctx)
        if status:
            log_update("market_history", remote=True, market_ctx=market_ctx)
            logger.info(
                f"History updated:{get_table_length('market_history', market_ctx=market_ctx)} items"
            )
            return True
        else:
            logger.error("Failed to update market history")
            return False


def process_market_stats(market_ctx: Optional[MarketContext] = None):
    logger.info("Calculating market stats")
    logger.info("syncing database")
    db = (
        DatabaseConfig(market_context=market_ctx)
        if market_ctx
        else DatabaseConfig("wcmkt")
    )
    db.sync()
    logger.info("database synced")
    logger.info("validating database")
    validation_test = db.validate_sync()
    if validation_test:
        logger.info("database validated")
    else:
        logger.error("database validation failed")
        raise Exception("database validation failed in market stats")

    try:
        market_stats_df = calculate_market_stats(market_ctx=market_ctx)
        if len(market_stats_df) > 0:
            logger.info(f"Market stats calculated: {len(market_stats_df)} items")
        else:
            logger.error("Failed to calculate market stats")
            return False
    except Exception as e:
        logger.error(f"Failed to calculate market stats: {e}")
        return False
    try:
        logger.info("Validating market stats columns")
        valid_market_stats_columns = MarketStats.__table__.columns.keys()
        market_stats_df = validate_columns(market_stats_df, valid_market_stats_columns)
        if len(market_stats_df) > 0:
            logger.info(f"Market stats validated: {len(market_stats_df)} items")
        else:
            logger.error("Failed to validate market stats")
            return False
    except Exception as e:
        logger.error(f"Failed to get market stats columns: {e}")
        return False
    try:
        logger.info("Updating market stats in database")
        status = upsert_database(MarketStats, market_stats_df, market_ctx=market_ctx)
        if status:
            log_update("marketstats", remote=True, market_ctx=market_ctx)
            logger.info(
                f"Market stats updated:{get_table_length('marketstats', market_ctx=market_ctx)} items"
            )
            return True
        else:
            logger.error("Failed to update market stats")
            return False
    except Exception as e:
        logger.error(f"Failed to update market stats: {e}")
        return False


def process_doctrine_stats(market_ctx: Optional[MarketContext] = None):
    logger.info("Calculating doctrines stats")
    logger.info("syncing database")
    db = (
        DatabaseConfig(market_context=market_ctx)
        if market_ctx
        else DatabaseConfig("wcmkt")
    )
    db.sync()
    logger.info("database synced")
    logger.info("validating database")
    validation_test = db.validate_sync()
    if validation_test:
        logger.info("database validated")
    else:
        logger.error("database validation failed")
        raise Exception("database validation failed in doctrines stats")

    doctrine_stats_df = calculate_doctrine_stats(market_ctx=market_ctx)
    doctrine_stats_df = convert_datetime_columns(doctrine_stats_df, ["timestamp"])
    status = upsert_database(Doctrines, doctrine_stats_df, market_ctx=market_ctx)
    if status:
        log_update("doctrines", remote=True, market_ctx=market_ctx)
        logger.info(
            f"Doctrines updated:{get_table_length('doctrines', market_ctx=market_ctx)} items"
        )
        return True
    else:
        logger.error("Failed to update doctrines")
        return False


def google_sheets_update_workflow(market_ctx: Optional[MarketContext] = None):
    """Update Google Sheets with market data."""
    if market_ctx is not None:
        # Use market-specific Google Sheets configuration
        google_sheet_config = GoogleSheetConfig(market_context=market_ctx)
        worksheets = market_ctx.gsheets_worksheets

        # Update market orders sheet
        market_orders_sheet = worksheets.get("market_orders", "market_orders")
        update_google_sheet(
            google_sheet_config,
            sheet_name=market_orders_sheet,
            table_name="marketorders",
            market_ctx=market_ctx,
        )

        # Update market data sheet
        market_data_sheet = worksheets.get("market_data", "market_data")
        update_google_sheet(
            google_sheet_config,
            sheet_name=market_data_sheet,
            table_name="marketstats",
            market_ctx=market_ctx,
        )
    else:
        # Legacy behavior for backward compatibility
        settings = load_settings(file_path="src/mkts_backend/config/settings.toml")
        google_sheet_url2 = settings["google_sheets"]["sheet_url2"]
        google_sheet_config = GoogleSheetConfig(sheet_url=google_sheet_url2)
        update_google_sheet(
            google_sheet_config,
            sheet_name="market_orders_4h",
            table_name="marketorders",
        )
        update_google_sheet(
            google_sheet_config, sheet_name="market_data_4h", table_name="marketstats"
        )


def update_google_sheet(
    google_sheet_config: GoogleSheetConfig,
    sheet_name: str,
    table_name: str,
    market_ctx: Optional[MarketContext] = None,
):
    import pandas as pd

    db = (
        DatabaseConfig(market_context=market_ctx)
        if market_ctx
        else DatabaseConfig("wcmkt")
    )
    engine = db.engine
    with engine.connect() as conn:
        df = pd.read_sql_table(table_name, conn)
        google_sheet_config.update_sheet(df, sheet_name=sheet_name)
        logger.info(f"Updated Google Sheet with {len(df)} rows of data")




def _run_market_pipeline(
    market_ctx: MarketContext,
    history: bool = False,
) -> None:
    """Run the full market data pipeline for a single market.

    Args:
        market_ctx: The market context to process.
        history: Whether to include historical data processing.
    """
    logger.info("=" * 80)
    logger.info(f"Processing market: {market_ctx.name} ({market_ctx.alias})")
    logger.info(f"  Region: {market_ctx.region_id}")
    logger.info(f"  Structure: {market_ctx.structure_id}")
    logger.info(f"  Database: {market_ctx.database_alias}")
    logger.info("=" * 80)

    # Initialize configurations using MarketContext
    esi = ESIConfig(market_context=market_ctx)
    db = DatabaseConfig(market_context=market_ctx)
    logger.info(f"Database: {db.alias} ({db.path})")

    # Validate and sync database
    validation_test = db.validate_sync()
    if not validation_test:
        logger.warning(f"{db.alias} database is not up to date. Syncing...")
        db.sync()
        logger.debug("database synced")
        validation_test = db.validate_sync()
        if validation_test:
            logger.debug("database validated")
        else:
            logger.error("database validation failed")
            raise Exception(f"database validation failed for {db.alias}")

    print("=" * 80)
    print(f"Fetching market orders for {market_ctx.name}")
    print("=" * 80)

    # Process market orders
    status = process_market_orders(
        esi, order_type="all", test_mode=False, market_ctx=market_ctx
    )
    if status:
        logger.debug("Market orders updated")
    else:
        logger.error("Failed to update market orders")
        exit()

    logger.info("=" * 80)

    # Get watchlist
    watchlist = db.get_watchlist()

    if len(watchlist) > 0:
        logger.debug(f"Watchlist found: {len(watchlist)} items")
    else:
        logger.error("No watchlist found. Unable to proceed further.")
        exit()

    # Process history
    if history:
        logger.info("Processing history")
        status = process_history(market_ctx=market_ctx)
        if status:
            logger.debug("History updated")
        else:
            logger.error("Failed to update history")
    else:
        logger.debug("History mode disabled. Skipping history processing")

    # Process market stats
    status = process_market_stats(market_ctx=market_ctx)
    if status:
        logger.debug("Market stats updated")
    else:
        logger.error("Failed to update market stats")
        exit()

    status = process_doctrine_stats(market_ctx=market_ctx)
    if status:
        logger.debug("Doctrines updated")
    else:
        logger.error("Failed to update doctrines")
        exit()

    env = os.environ.get("MKTS_ENVIRONMENT", settings["app"]["environment"])

    # Update Google Sheets if enabled and primary market
    if (
        settings["google_sheets"]["enabled"]
        and market_ctx.alias == "primary"
        and env != "development"
    ):
        logger.info(
            "Google Sheets are enabled in settings.toml. Updating Google Sheets"
        )
        google_sheets_update_workflow(market_ctx=market_ctx)
    else:
        logger.info(
            "Google Sheets are disabled in settings.toml. Skipping Google Sheets update"
        )


def main(history: bool = False, market_alias: str = "primary"):
    """
    Main function to process market orders, history, market stats, and doctrines.

    Args:
        history: Whether to include historical data processing.
        market_alias: Market alias to process (e.g., "primary", "deployment", "both").
    """
    start_time = time.perf_counter()

    # Validate environment credentials before proceeding
    validation_result = validate_all()
    if not validation_result["is_valid"]:
        logger.error(validation_result["message"])
        print(validation_result["message"])
        if validation_result["missing_required"]:
            print(
                f"Missing required credentials: {', '.join(validation_result['missing_required'])}"
            )
            print("Please check your .env file or environment variables.")
        sys.exit(1)
    logger.info("Environment validation passed")

    init_databases()
    logger.debug("Databases initialized")
    os.makedirs("data", exist_ok=True)
    logger.debug(f"Data directory created: {os.path.abspath('data')}")
    logger.debug("=" * 80)

    # Parse command line arguments
    if len(sys.argv) > 1:
        args = parse_args(sys.argv)

        if args is not None:
            history = args.get("history", False)
            market_alias = args.get("market", "primary")
        else:
            return

    # Determine which markets to process
    if market_alias == "both":
        market_aliases = ["primary", "deployment"]
    else:
        market_aliases = [market_alias]

    for alias in market_aliases:
        try:
            market_ctx = MarketContext.from_settings(alias)
            logger.info(f"MarketContext: {market_ctx}")
        except ValueError as e:
            logger.error(f"Invalid market: {e}")
            print(f"Error: {e}")
            print(f"Available markets: {', '.join(MarketContext.list_available())}")
            sys.exit(1)

        _run_market_pipeline(market_ctx, history=history)

    logger.info("=" * 80)
    label = " + ".join(market_aliases)
    logger.info(
        f"Market job complete for {label} in {time.perf_counter() - start_time:.1f}s"
    )
    logger.info("=" * 80)


if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("Starting mkts-backend")
    logger.info("=" * 80 + "\n")

    main()
