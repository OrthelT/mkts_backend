import sys
import json
import time
import os
from typing import Optional
from sqlalchemy import text

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


def parse_args(args: list[str]) -> dict | None:
    return_args = {}

    if len(args) == 0:
        return None

    # Handle --help: check for subcommand-specific help first
    if "--help" in args:
        # Check if this is a subcommand help request
        subcommands_with_help = [
            "fit-check",
            "fit-update",
            "update-fit",
            "update-target",
        ]
        for subcmd in subcommands_with_help:
            if subcmd in args:
                # Let the subcommand handler show its help
                break
        else:
            # No subcommand found, show general help
            display_cli_help()
            exit()

    # Parse --market flag (supports --market=<alias>, --primary, --deployment shorthands)
    market_alias = "primary"  # default
    for arg in args:
        if arg.startswith("--market="):
            market_choice = arg.split("=", 1)[1]
            if market_choice == "north" or market_choice == "North":
                market_alias = "deployment"
            else:
                market_alias = market_choice

            break
        elif arg == "--deployment" or arg == "--north":
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
            print(
                f"  {alias}: {ctx.name} (region={ctx.region_id}, db={ctx.database_alias})"
            )
        exit()

    if "--check_tables" in args:
        check_tables(market_alias)
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
            print(
                "Error: Both --input and --output parameters are required for parse-items command"
            )
            print(
                "Usage: mkts-backend parse-items --input=structure_data.txt --output=market_prices.csv"
            )
            return None

        success = parse_items(input_file, output_file)

        if success:
            print("Parse items command completed successfully")
        else:
            print("Parse items command failed")
        exit()

    if "update-target" in args:
        # Check for subcommand help
        if "--help" in args:
            display_update_target_help()
            exit(0)
        fit_id = None
        target = None
        market_alias = "primary"
        remote = False
        target_alias = "wcmkt"
        for arg in args:
            if arg.startswith("--fit-id="):
                fit_id = int(arg.split("=", 1)[1])
            elif arg.startswith("--target="):
                target = int(arg.split("=", 1)[1])
            elif arg.startswith("--market="):
                market_alias = arg.split("=", 1)[1]
            elif arg.startswith("--remote"):
                remote = True
            elif arg.startswith("--db-alias="):
                target_alias = arg.split("=", 1)[1]
            elif arg.startswith("--north"):
                target_alias = "wcmktnorth"
            elif arg.startswith("--primary"):
                target_alias = "wcmkt"
            elif arg.startswith("--local-only"):
                remote = False
        if not fit_id or not target:
            print("Error: --fit-id and --target are required for update-target command")
            print("Use 'mkts-backend update-target --help' for usage information.")
            return None
        success = update_target_command(
            fit_id,
            target,
            market_flag=market_alias,
            remote=remote,
            db_alias=target_alias,
        )
        if success:
            print("Update target command completed successfully")
        else:
            print("Update target command failed")
        exit(0 if success else 1)

    if "update-fit" in args:
        # Check for subcommand help
        if "--help" in args:
            display_update_fit_help()
            exit(0)

        # Parse arguments
        fit_file = None
        meta_file = None
        fit_id = None
        interactive = "--interactive" in args
        update_targets = "--update-targets" in args

        # Parse market selection (default: primary)
        # Supports: --market=primary/deployment/both, --primary, --deployment, --both
        target_markets = ["primary"]  # default
        for arg in args:
            if arg.startswith("--fit-file="):
                fit_file = arg.split("=", 1)[1]
            elif arg.startswith("--meta-file="):
                meta_file = arg.split("=", 1)[1]
            elif arg.startswith("--fit-id="):
                try:
                    fit_id = int(arg.split("=", 1)[1])
                except ValueError:
                    print("Error: --fit-id must be an integer")
                    return None
            elif arg.startswith("--market="):
                market_val = arg.split("=", 1)[1].lower()
                if market_val == "both":
                    target_markets = ["primary", "deployment"]
                elif market_val in ("primary", "deployment"):
                    target_markets = [market_val]
                else:
                    print("Error: --market must be one of: primary, deployment, both")
                    return None
            elif arg == "--both":
                target_markets = ["primary", "deployment"]
            elif arg == "--deployment":
                target_markets = ["deployment"]
            elif arg == "--primary":
                target_markets = ["primary"]

        # Validate required arguments
        if not fit_file:
            print("Error: --fit-file is required for update-fit")
            print("Use 'mkts-backend update-fit --help' for usage information.")
            return None

        # Need either --meta-file OR (--fit-id with --interactive)
        if not meta_file and fit_id is None:
            print("Error: Either --meta-file or --fit-id is required")
            print("Use 'mkts-backend update-fit --help' for usage information.")
            return None

        if fit_id is not None and not meta_file and not interactive:
            print("Error: --fit-id requires either --meta-file or --interactive")
            print("Use 'mkts-backend update-fit --help' for usage information.")
            return None

        remote = "--remote" in args
        clear_existing = "--no-clear" not in args
        dry_run = "--dry-run" in args

        try:
            # Get metadata from file or interactive prompt
            if meta_file:
                metadata = parse_fit_metadata(meta_file)
                if fit_id is not None and metadata.fit_id != fit_id:
                    print(
                        f"Warning: --fit-id={fit_id} overrides fit_id={metadata.fit_id} from metadata file"
                    )
                    # Create new metadata dict with overridden fit_id
                    metadata_dict = {
                        "fit_id": fit_id,
                        "name": metadata.name,
                        "description": metadata.description,
                        "doctrine_id": metadata.doctrine_ids
                        if len(metadata.doctrine_ids) > 1
                        else metadata.doctrine_id,
                        "target": metadata.target,
                    }
                else:
                    metadata_dict = None  # Use metadata object directly
            else:
                # Interactive mode - collect metadata from user
                metadata_dict = collect_fit_metadata_interactive(fit_id, fit_file)

            # Map market aliases to database aliases
            market_to_db = {
                "primary": "wcmkt",
                "deployment": "wcmktnorth",
            }

            # Process for each target market
            for target_market in target_markets:
                target_alias = market_to_db[target_market]
                print(
                    f"\n--- Processing for {target_market} market ({target_alias}) ---"
                )

                if metadata_dict:
                    # Create FitMetadata from dict for workflow
                    from mkts_backend.utils.parse_fits import FitMetadata

                    metadata_obj = FitMetadata(**metadata_dict)
                else:
                    metadata_obj = metadata

                result = update_fit_workflow(
                    fit_id=metadata_obj.fit_id,
                    fit_file=fit_file,
                    fit_metadata_file=meta_file,
                    remote=remote,
                    clear_existing=clear_existing,
                    dry_run=dry_run,
                    target_alias=target_alias,
                    update_targets=update_targets,
                    metadata_override=metadata_dict,
                )

                if dry_run:
                    print("Dry run complete")
                    print(f"Ship: {result['ship_name']} ({result['ship_type_id']})")
                    print(f"Items parsed: {len(result['items'])}")
                    if result["missing_items"]:
                        print(f"Missing type_ids for: {result['missing_items']}")
                else:
                    print(
                        f"Fit update completed for fit_id {metadata_obj.fit_id} -> {target_alias} (remote={remote})"
                    )
                    if update_targets:
                        print(f"  ship_targets updated")

            exit(0)
        except Exception as e:
            logger.error(f"update-fit failed: {e}")
            print(f"Error running update-fit: {e}")
            exit(1)

    # Handle fit-check command
    if "fit-check" in args:
        # Check for subcommand help
        if "--help" in args:
            display_fit_check_help()
            exit(0)

        file_path = None
        paste_mode = "--paste" in args
        no_jita = "--no-jita" in args
        target = None
        output_format = None
        fit_id = None

        for arg in args:
            if arg.startswith("--file="):
                file_path = arg.split("=", 1)[1]
            elif arg.startswith("--fit-id=") or arg.startswith("--fit_id="):
                try:
                    fit_id = int(arg.split("=", 1)[1])
                except ValueError:
                    print("Error: --fit-id must be an integer")
                    return None
            elif arg.startswith("--target="):
                try:
                    target = int(arg.split("=", 1)[1])
                except ValueError:
                    print("Error: --target must be an integer")
                    return None
            elif arg.startswith("--output="):
                output_format = arg.split("=", 1)[1].lower()
                if output_format not in ("csv", "multibuy", "markdown"):
                    print(f"Error: --output must be one of: csv, multibuy, markdown")
                    return None

        if not file_path and not paste_mode and fit_id is None:
            print(
                "Error: --file=<path>, --paste, or --fit-id=<id> is required for fit-check command"
            )
            print("Use 'mkts-backend fit-check --help' for usage information.")
            return None

        eft_text = None
        if paste_mode:
            print("Paste your EFT fit below (Ctrl+D or blank line to finish):")
            lines = []
            try:
                import sys

                for line in sys.stdin:
                    if line.strip() == "":
                        # Second blank line signals end
                        if lines and lines[-1] == "":
                            break
                        lines.append("")
                    else:
                        lines.append(line.rstrip())
            except EOFError:
                pass
            eft_text = "\n".join(lines)

        success = fit_check_command(
            file_path=file_path,
            eft_text=eft_text,
            fit_id=fit_id,
            market_alias=market_alias,
            show_legend=True,
            target=target,
            output_format=output_format,
            show_jita=not no_jita,
        )
        exit(0 if success else 1)

    # Handle fit-update command with subcommands
    if "fit-update" in args:
        # Check for subcommand help
        if "--help" in args:
            display_fit_update_help()
            exit(0)

        # Determine subcommand (first positional arg after fit-update)
        fit_update_idx = args.index("fit-update")
        subcommand = None
        for arg in args[fit_update_idx + 1 :]:
            if not arg.startswith("--"):
                subcommand = arg
                break

        if not subcommand:
            print("Error: fit-update requires a subcommand")
            print("Use 'mkts-backend fit-update --help' for usage information.")
            return None

        # Parse options
        file_path = None
        meta_file = None
        fit_id = None
        db_alias = "wcmkt"  # Database alias
        target_qty = 100  # Default target quantity for new fits
        interactive = "--interactive" in args
        remote = "--remote" in args
        local_only = "--local-only" in args
        dry_run = "--dry-run" in args
        skip_targets = "--skip-targets" in args

        fit_ids_str = None
        for arg in args:
            if arg.startswith("--file="):
                file_path = arg.split("=", 1)[1]
            elif arg.startswith("--meta-file="):
                meta_file = arg.split("=", 1)[1]
            elif arg.startswith("--fit-id="):
                fit_ids_str = arg.split("=", 1)[1]
            elif arg.startswith("--target="):
                # Target quantity for doctrine-add-fit
                target_qty = int(arg.split("=", 1)[1])
            elif arg.startswith("--db-alias="):
                db_alias = arg.split("=", 1)[1]
            elif arg == "--north":
                db_alias = "wcmktnorth"
                market_alias = "deployment"

        # Parse fit_id(s) - supports comma-separated for doctrine-add-fit
        if fit_ids_str:
            if "," in fit_ids_str:
                # Multiple fit IDs for doctrine-add-fit
                fit_id = None  # Will use fit_ids list instead
                fit_ids = [int(f.strip()) for f in fit_ids_str.split(",") if f.strip()]
            else:
                fit_id = int(fit_ids_str)
                fit_ids = None
        else:
            fit_id = None
            fit_ids = None

        success = fit_update_command(
            subcommand=subcommand,
            fit_id=fit_id,
            fit_ids=fit_ids,  # For doctrine-add-fit with multiple fits
            file_path=file_path,
            meta_file=meta_file,
            market_flag=market_alias,  # Reuse market_alias parsed earlier
            remote=remote,
            local_only=local_only,
            dry_run=dry_run,
            interactive=interactive,
            target_alias=db_alias,
            target=target_qty,
            skip_targets=skip_targets,
        )
        exit(0 if success else 1)

    if "sync" in args:
        # Use market_alias parsed from --market/--deployment/--primary flags
        market_ctx = MarketContext.from_settings(market_alias)
        db = DatabaseConfig(market_context=market_ctx)
        print(f"Syncing database for market: {market_ctx.name} ({market_ctx.alias})")
        db.sync()
        logger.info(f"Database synced: {db.alias}")
        print(f"Database synced: {db.alias} ({db.path})")
        exit()
        return None

    if "validate" in args:
        # Use market_alias parsed from --market/--deployment/--primary flags
        market_ctx = MarketContext.from_settings(market_alias)
        db = DatabaseConfig(market_context=market_ctx)
        print(f"Validating database for market: {market_ctx.name} ({market_ctx.alias})")
        validation_test = db.validate_sync()
        if validation_test:
            print(f"Database validated: {db.alias}")
        else:
            print(
                f"Database {db.alias} is out of date. Run 'sync' to sync the database."
            )
        exit()

    if "--validate-env" in args:
        result = validate_all()
        if result["is_valid"]:
            print(result["message"])
            print(
                f"Required credentials present: {', '.join(result['present_required'])}"
            )
            if result["present_optional"]:
                print(
                    f"Optional credentials present: {', '.join(result['present_optional'])}"
                )
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
            print(
                "       mkts-backend add_watchlist --type_id=12345,67890,11111 --local"
            )
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

    # Create MarketContext for the selected market
    try:
        market_ctx = MarketContext.from_settings(market_alias)
        logger.info(f"MarketContext: {market_ctx}")
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

    # Update Google Sheets if enabled and primary market
    if settings["google_sheets"]["enabled"] and market_ctx.alias == "primary":
        logger.info(
            "Google Sheets are enabled in settings.toml. Updating Google Sheets"
        )
        google_sheets_update_workflow(market_ctx=market_ctx)
    else:
        logger.info(
            "Google Sheets are disabled in settings.toml. Skipping Google Sheets update"
        )

    logger.info("=" * 80)
    logger.info(
        f"Market job complete for {market_ctx.name} in {time.perf_counter() - start_time:.1f}s"
    )
    logger.info("=" * 80)


if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("Starting mkts-backend")
    logger.info("=" * 80 + "\n")

    main()
