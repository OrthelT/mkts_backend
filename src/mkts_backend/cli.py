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
from sqlalchemy import text
from mkts_backend.config.esi_config import ESIConfig
from mkts_backend.esi.esi_requests import fetch_market_orders
from mkts_backend.esi.async_history import run_async_history
from mkts_backend.utils.db_utils import add_missing_items_to_watchlist
from mkts_backend.utils.parse_items import parse_items
from mkts_backend.utils.validation import validate_all
from mkts_backend.utils.parse_fits import update_fit_workflow, parse_fit_metadata
from mkts_backend.config.config import load_settings, DatabaseConfig
from mkts_backend.config.gsheets_config import GoogleSheetConfig
from mkts_backend.config.market_context import MarketContext

settings = load_settings(file_path="src/mkts_backend/config/settings.toml")
logger = configure_logging(__name__)

def check_tables():
    tables = ["doctrines", "marketstats", "marketorders", "market_history"]
    db = DatabaseConfig("wcmkt")
    tables = db.get_table_list()

    for table in tables:
        print(f"Table: {table}")
        print("=" * 80)
        with db.engine.connect() as conn:
            result = conn.execute(text(f"SELECT * FROM {table} LIMIT 10"))
            for row in result:
                print(row)
            print("\n")
        conn.close()
    db.engine.dispose()

def display_cli_help():
    print("\nUsage: mkts-backend [--market=<alias>|--primary|--deployment] [--history|--include-history] [--check_tables] [add_watchlist --type_id=<list[int]>] [parse-items --input=<file> --output=<file>] [update-fit --fit-file=<path> --meta-file=<path> [--remote] [--no-clear] [--dry-run] [--target=<wcmkt|wcmktnorth>|--north]]\n")
    print("""Options:\n
  [--market=<alias>]: Select market to process (primary, deployment). Default: primary\n
  [--primary]: Shorthand for --market=primary\n
  [--deployment]: Shorthand for --market=deployment\n
  [--history | --include-history]: Include history processing\n
  [--check_tables]:  Check the tables in the database\n
  [add_watchlist]: --type_id=<list>: Add items to watchlist by type IDs (comma-separated --type_id=81144,88001,89240)\n
  [update-fit]: Process an EFT fit file and metadata and update doctrine tables (defaults local, add --remote for production, --no-clear to keep existing items, --dry-run to preview; use --target=wcmktnorth or --north to write to north DB)\n
  [--local]: Use local database instead of remote for commands that default to remote\n
  [parse-items --input=<file> --output=<file>]: Parse Eve structure data and create CSV with pricing from database\n
  [sync]: Sync the database\n
  [validate]: Validate the database\n
  [--validate-env]: Validate environment credentials and exit\n
  [--list-markets]: List available market configurations\n\n
""")

def process_add_watchlist(type_ids_str: str, remote: bool = False):
    """
    Process the add_watchlist command to add items to the watchlist.

    Args:
        type_ids_str: Comma-separated string of type IDs
        remote: Whether to use remote database
    """
    try:
        # Parse comma-separated type IDs
        type_ids = [int(tid.strip()) for tid in type_ids_str.split(',') if tid.strip()]

        if not type_ids:
            logger.error("No valid type IDs provided")
            print("Error: No valid type IDs provided")
            return False

        logger.info(f"Adding {len(type_ids)} items to watchlist: {type_ids}")
        print(f"Adding {len(type_ids)} items to watchlist: {type_ids}")

        # Call add_missing_items_to_watchlist with all type IDs at once
        result = add_missing_items_to_watchlist(type_ids, remote=remote)

        # Check if the operation was successful
        if result.startswith("Error"):
            logger.error(f"Failed to add items to watchlist: {result}")
            print(f"Error: {result}")
            return False
        else:
            logger.info(f"Successfully processed watchlist addition: {result}")
            print(result)
            return True

    except ValueError as e:
        logger.error(f"Invalid type ID format: {e}")
        print(f"Error: Invalid type ID format. Please provide comma-separated integers. {e}")
        return False
    except Exception as e:
        logger.error(f"Error adding items to watchlist: {e}")
        print(f"Error: {e}")
        return False

def process_market_orders(
    esi: ESIConfig,
    order_type: str = "all",
    test_mode: bool = False,
    market_ctx: Optional[MarketContext] = None
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
            logger.info(f"Orders updated:{get_table_length('marketorders', market_ctx=market_ctx)} items")
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
        with open("data/market_history_new.json", "w") as f:
            json.dump(data, f)
        status = update_history(data, market_ctx=market_ctx)
        if status:
            log_update("market_history", remote=True, market_ctx=market_ctx)
            logger.info(f"History updated:{get_table_length('market_history', market_ctx=market_ctx)} items")
            return True
        else:
            logger.error("Failed to update market history")
            return False

def process_market_stats(market_ctx: Optional[MarketContext] = None):
    logger.info("Calculating market stats")
    logger.info("syncing database")
    db = DatabaseConfig(market_context=market_ctx) if market_ctx else DatabaseConfig("wcmkt")
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
            logger.info(f"Market stats updated:{get_table_length('marketstats', market_ctx=market_ctx)} items")
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
    db = DatabaseConfig(market_context=market_ctx) if market_ctx else DatabaseConfig("wcmkt")
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
        logger.info(f"Doctrines updated:{get_table_length('doctrines', market_ctx=market_ctx)} items")
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
        update_google_sheet(google_sheet_config, sheet_name=market_orders_sheet, table_name="marketorders", market_ctx=market_ctx)

        # Update market data sheet
        market_data_sheet = worksheets.get("market_data", "market_data")
        update_google_sheet(google_sheet_config, sheet_name=market_data_sheet, table_name="marketstats", market_ctx=market_ctx)
    else:
        # Legacy behavior for backward compatibility
        settings = load_settings(file_path="src/mkts_backend/config/settings.toml")
        google_sheet_url2 = settings["google_sheets"]["sheet_url2"]
        google_sheet_config = GoogleSheetConfig(sheet_url=google_sheet_url2)
        update_google_sheet(google_sheet_config, sheet_name="market_orders_4h", table_name="marketorders")
        update_google_sheet(google_sheet_config, sheet_name="market_data_4h", table_name="marketstats")

def update_google_sheet(
    google_sheet_config: GoogleSheetConfig,
    sheet_name: str,
    table_name: str,
    market_ctx: Optional[MarketContext] = None
):
    import pandas as pd

    db = DatabaseConfig(market_context=market_ctx) if market_ctx else DatabaseConfig("wcmkt")
    engine = db.engine
    with engine.connect() as conn:
        df = pd.read_sql_table(table_name, conn)
        google_sheet_config.update_sheet(df, sheet_name=sheet_name)
        logger.info(f"Updated Google Sheet with {len(df)} rows of data")

def parse_args(args: list[str])->dict | None:
    return_args = {}

    if len(args) == 0:
        return None

    if "--help" in args:
        display_cli_help()
        exit()

    # Parse --market flag (supports --market=<alias>, --primary, --deployment shorthands)
    market_alias = "primary"  # default
    for arg in args:
        if arg.startswith("--market="):
            market_alias = arg.split("=", 1)[1]
            break
        elif arg == "--deployment":
            market_alias = "deployment"
            break
        elif arg == "--primary":
            market_alias = "primary"
            break
    return_args["market"] = market_alias

    if "--list-markets" in args:
        available = MarketContext.list_available()
        print(f"Available markets: {', '.join(available)}")
        for alias in available:
            ctx = MarketContext.from_settings(alias)
            print(f"  {alias}: {ctx.name} (region={ctx.region_id}, db={ctx.database_alias})")
        exit()

    if "--check_tables" in args:
        check_tables()
        exit()

    # Handle parse-items command
    if "parse-items" in args:
        input_file = None
        output_file = None

        for arg in args:
            if arg.startswith("--input="):
                input_file = arg.split("=", 1)[1]
            elif arg.startswith("--output="):
                output_file = arg.split("=", 1)[1]

        if not input_file or not output_file:
            print("Error: Both --input and --output parameters are required for parse-items command")
            print("Usage: mkts-backend parse-items --input=structure_data.txt --output=market_prices.csv")
            return None

        success = parse_items(input_file, output_file)

        if success:
            print("Parse items command completed successfully")
        else:
            print("Parse items command failed")
        exit()

    if "update-fit" in args:
        fit_file = None
        meta_file = None
        target_alias = "wcmkt"
        for arg in args:
            if arg.startswith("--fit-file="):
                fit_file = arg.split("=", 1)[1]
            if arg.startswith("--meta-file="):
                meta_file = arg.split("=", 1)[1]
            if arg.startswith("--target="):
                target_alias = arg.split("=", 1)[1]
            if arg == "--north":
                target_alias = "wcmktnorth"

        if not fit_file or not meta_file:
            print("Error: --fit-file and --meta-file are required for update-fit")
            return None

        remote = "--remote" in args  # default to local per user preference
        clear_existing = "--no-clear" not in args
        dry_run = "--dry-run" in args

        if target_alias not in {"wcmkt", "wcmktnorth"}:
            print("Error: --target must be one of: wcmkt, wcmktnorth")
            return None

        try:
            metadata = parse_fit_metadata(meta_file)
            result = update_fit_workflow(
                fit_id=metadata.fit_id,
                fit_file=fit_file,
                fit_metadata_file=meta_file,
                remote=remote,
                clear_existing=clear_existing,
                dry_run=dry_run,
                target_alias=target_alias,
            )
            if dry_run:
                print("Dry run complete")
                print(f"Ship: {result['ship_name']} ({result['ship_type_id']})")
                print(f"Items parsed: {len(result['items'])}")
                if result["missing_items"]:
                    print(f"Missing type_ids for: {result['missing_items']}")
            else:
                print(f"Fit update completed for fit_id {metadata.fit_id} (remote={remote})")
            exit()
        except Exception as e:
            logger.error(f"update-fit failed: {e}")
            print(f"Error running update-fit: {e}")
            exit(1)

    if "sync" in args:
        db = DatabaseConfig("wcmkt")
        db.sync()
        logger.info("Database synced")
        exit()
        return None

    if "validate" in args:
        db = DatabaseConfig("wcmkt")
        validation_test = db.validate_sync()
        if validation_test:
            print("Database validated")
        else:
            print("Database is out of date. Run --sync_db to sync the database.")
        exit()

    if "--validate-env" in args:
        result = validate_all()
        if result["is_valid"]:
            print(result["message"])
            print(f"Required credentials present: {', '.join(result['present_required'])}")
            if result["present_optional"]:
                print(f"Optional credentials present: {', '.join(result['present_optional'])}")
        else:
            print(result["message"])
            if result["missing_required"]:
                print(f"Missing required: {', '.join(result['missing_required'])}")
        exit(0 if result["is_valid"] else 1)

    # Handle add_watchlist command
    if "add_watchlist" in args:
        # Find the --type_id parameter
        type_ids_str = None
        for i, arg in enumerate(args):
            if arg.startswith("--type_id="):
                type_ids_str = arg.split("=", 1)[1]
                break

        if not type_ids_str:
            print("Error: --type_id parameter is required for add_watchlist command")
            print("Usage: mkts-backend add_watchlist --type_id=12345,67890,11111")
            print("       mkts-backend add_watchlist --type_id=12345,67890,11111 --local")
            return None

        # Default to remote database, use --local flag for local database
        remote = "--local" not in args

        success = process_add_watchlist(type_ids_str, remote=remote)
        if success:
            print(f"Added {type_ids_str} to watchlist")
        else:
            print(f"Failed to add {type_ids_str} to watchlist")
        exit()

    if "--history" in args or "--include-history" in args:
        return_args["history"] = True
    else:
        return_args["history"] = False

    # If we have a market specified but no other command, run the main workflow
    if return_args.get("market"):
        return return_args

    display_cli_help()
    exit()

def main(history: bool = False, market_alias: str = "primary"):
    """
    Main function to process market orders, history, market stats, and doctrines.

    Args:
        history: Whether to include historical data processing.
        market_alias: Market alias to process (e.g., "primary", "deployment").
    """
    start_time = time.perf_counter()

    # Validate environment credentials before proceeding
    validation_result = validate_all()
    if not validation_result["is_valid"]:
        logger.error(validation_result["message"])
        print(validation_result["message"])
        if validation_result["missing_required"]:
            print(f"Missing required credentials: {', '.join(validation_result['missing_required'])}")
            print("Please check your .env file or environment variables.")
        sys.exit(1)
    logger.info("Environment validation passed")

    init_databases()
    logger.info("Databases initialized")
    os.makedirs("data", exist_ok=True)
    logger.info(f"Data directory created: {os.path.abspath('data')}")
    logger.info("=" * 80)

    # Parse command line arguments
    if len(sys.argv) > 1:
        args = parse_args(sys.argv)

        if args is not None:
            history = args.get("history", False)
            market_alias = args.get("market", "primary")
        else:
            return

    # Create MarketContext for the selected market
    try:
        market_ctx = MarketContext.from_settings(market_alias)
    except ValueError as e:
        logger.error(f"Invalid market: {e}")
        print(f"Error: {e}")
        print(f"Available markets: {', '.join(MarketContext.list_available())}")
        sys.exit(1)

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
        logger.info("database synced")
        validation_test = db.validate_sync()
        if validation_test:
            logger.info("database validated")
        else:
            logger.error("database validation failed")
            raise Exception(f"database validation failed for {db.alias}")

    print("=" * 80)
    print(f"Fetching market orders for {market_ctx.name}")
    print("=" * 80)

    status = process_market_orders(esi, order_type="all", test_mode=False, market_ctx=market_ctx)
    if status:
        logger.info("Market orders updated")
    else:
        logger.error("Failed to update market orders")
        exit()

    logger.info("=" * 80)

    watchlist = db.get_watchlist()
    if len(watchlist) > 0:
        logger.info(f"Watchlist found: {len(watchlist)} items")
    else:
        logger.error("No watchlist found. Unable to proceed further.")
        exit()

    if history:
        logger.info("Processing history")
        status = process_history(market_ctx=market_ctx)
        if status:
            logger.info("History updated")
        else:
            logger.error("Failed to update history")
    else:
        logger.info("History mode disabled. Skipping history processing")

    status = process_market_stats(market_ctx=market_ctx)
    if status:
        logger.info("Market stats updated")
    else:
        logger.error("Failed to update market stats")
        exit()

    status = process_doctrine_stats(market_ctx=market_ctx)
    if status:
        logger.info("Doctrines updated")
    else:
        logger.error("Failed to update doctrines")
        exit()

    if settings["google_sheets"]["enabled"]:
        logger.info("Google Sheets are enabled in settings.toml. Updating Google Sheets")
        google_sheets_update_workflow(market_ctx=market_ctx)
    else:
        logger.info("Google Sheets are disabled in settings.toml. Skipping Google Sheets update")

    logger.info("=" * 80)
    logger.info(f"Market job complete for {market_ctx.name} in {time.perf_counter()-start_time:.1f}s")
    logger.info("=" * 80)


if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("Starting mkts-backend")
    logger.info("=" * 80 + "\n")

    main()
