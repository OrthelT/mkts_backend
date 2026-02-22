from mkts_backend.config.logging_config import configure_logging
from mkts_backend.utils.db_utils import add_missing_items_to_watchlist
from mkts_backend.cli_tools.market_args import MARKET_DB_MAP

logger = configure_logging(__name__)


def add_watchlist(args: list[str], market_alias: str = "primary") -> None:
    """Add items to the watchlist for the specified market(s).

    Args:
        args: Raw CLI arguments (used to extract --type_id and --local).
        market_alias: Normalized market alias from parse_market_args
                      ("primary", "deployment", or "both").
    """
    # Find the --type_id parameter
    type_ids_str = None
    for arg in args:
        if arg.startswith("--type_id="):
            type_ids_str = arg.split("=", 1)[1]
            break

    if not type_ids_str:
        print("Error: --type_id parameter is required for add_watchlist command")
        print("Usage: mkts-backend add_watchlist --type_id=12345,67890")
        print("       mkts-backend add_watchlist --type_id=12345 --deployment")
        print("       mkts-backend add_watchlist --type_id=12345 --both")
        return None

    # Default to remote database, use --local flag for local database
    remote = "--local" not in args

    # Determine target database(s) from market alias
    if market_alias == "both":
        target_aliases = [MARKET_DB_MAP["primary"], MARKET_DB_MAP["deployment"]]
    else:
        target_aliases = [MARKET_DB_MAP.get(market_alias, "wcmkt")]

    all_ok = True
    for db_alias in target_aliases:
        print(f"Adding to watchlist on {db_alias} (remote={remote})...")
        success = process_add_watchlist(type_ids_str, remote=remote, db_alias=db_alias)
        if not success:
            all_ok = False

    if all_ok:
        label = " + ".join(target_aliases)
        print(f"Watchlist update complete for {label}")
    exit()


def process_add_watchlist(type_ids_str: str, remote: bool = False, db_alias: str = "wcmkt"):
    """
    Process the add_watchlist command to add items to the watchlist.

    Args:
        type_ids_str: Comma-separated string of type IDs
        remote: Whether to use remote database
        db_alias: Target database alias
    """
    try:
        # Parse comma-separated type IDs
        type_ids = [int(tid.strip()) for tid in type_ids_str.split(',') if tid.strip()]

        if not type_ids:
            logger.error("No valid type IDs provided")
            print("Error: No valid type IDs provided")
            return False

        logger.info(f"Adding {len(type_ids)} items to watchlist ({db_alias}): {type_ids}")
        print(f"Adding {len(type_ids)} items to watchlist ({db_alias}): {type_ids}")

        # Call add_missing_items_to_watchlist with all type IDs at once
        result = add_missing_items_to_watchlist(type_ids, remote=remote, db_alias=db_alias)

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
