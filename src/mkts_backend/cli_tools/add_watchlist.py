import csv

from mkts_backend.config.logging_config import configure_logging
from mkts_backend.utils.db_utils import add_missing_items_to_watchlist
from mkts_backend.cli_tools.arg_utils import ParsedArgs
from mkts_backend.cli_tools.market_args import MARKET_DB_MAP
from mkts_backend.cli_tools.prompter import get_multiline_input
from mkts_backend.utils.get_type_info import get_type_from_list
logger = configure_logging(__name__)


def _read_type_ids_from_csv(path: str) -> list[int]:
    """Read type IDs from a CSV file. Expects a 'type_ids' column."""
    type_ids = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            val = row.get("type_ids", "").strip()
            if val:
                type_ids.append(int(val))
    return type_ids

def parse_pasted_input(paste):
    type_info_list = get_type_from_list([item.strip() for item in paste if item.strip()])
    type_ids = [type_info.type_id for type_info in type_info_list]
    if type_ids:
        type_ids_str = ",".join(str(tid) for tid in type_ids)
        return type_ids_str
    else:
        return None

def add_watchlist(args: list[str], market_alias: str = "primary") -> None:
    """Add items to the watchlist for the specified market(s).

    Args:
        args: Raw CLI arguments (used to extract --type_id, --file, and --local).
        market_alias: Normalized market alias from parse_market_args
                      ("primary", "deployment", or "both").
    """
    p = ParsedArgs(args)
    type_ids_str = p.get_string("type_id", "type-id")
    file_path = p.get_string("file")

    if p.has_flag("paste"):
        paste = get_multiline_input()
        if not paste:
            logger.error("No paste input provided")
            print("Error: No paste input provided")
            return False
        with open("paste.txt", "w") as f:
            f.write(paste)
        file_path = "paste.txt"

    if type_ids_str and file_path:
        print("Error: --type_id and --file are mutually exclusive")
        return None

    if file_path:
        with open(file_path, "r") as f:
            paste = f.readlines()
        type_ids_str = parse_pasted_input(paste)
        if not type_ids_str:
            print("Error: No valid type IDs provided")
            return None
        else:
            print(f"Processing add_watchlist command for {len(type_ids_str)} items")
            

    if not type_ids_str:
        print("Error: --type_id or --file parameter is required for add_watchlist command")
        print("Usage: mkts-backend add_watchlist --type_id=12345,67890")
        print("       mkts-backend add_watchlist --file=data/expanded_typeids.csv")
        print("       mkts-backend add_watchlist --type_id=12345 --deployment")
        print("       mkts-backend add_watchlist --type_id=12345 --both")
        return None

    # Default to remote database, use --local flag for local database
    remote = not p.has_flag("local")

    # Determine target database(s) from market alias
    if market_alias == "both":
        target_aliases = [MARKET_DB_MAP["primary"], MARKET_DB_MAP["deployment"]]
    else:
        target_aliases = [MARKET_DB_MAP.get(market_alias, "wcmkt")]

    type_ids = [int(tid.strip()) for tid in type_ids_str.split(',') if tid.strip()]

    all_ok = True
    for db_alias in target_aliases:
        print(f"Adding to watchlist on {db_alias} (remote={remote})...")
        success = process_add_watchlist(type_ids, remote=remote, db_alias=db_alias)
        if not success:
            all_ok = False

    if all_ok:
        label = " + ".join(target_aliases)
        print(f"Watchlist update complete for {label}")
    exit()

def process_add_watchlist(type_ids: list[int], remote: bool = False, db_alias: str = "wcmkt"):
    """
    Process the add_watchlist command to add items to the watchlist.

    Args:
        type_ids_str: Comma-separated string of type IDs
        remote: Whether to use remote database
        db_alias: Target database alias
    """
    print("Processing add_watchlist command")
    try:
        # Parse comma-separated type IDs
        if not type_ids:
            logger.error("No valid type IDs provided")
            print("Error: No valid type IDs provided")
            return None
        return add_missing_items_to_watchlist(type_ids, remote=remote, db_alias=db_alias)

    except ValueError as e:
        logger.error(f"Invalid type ID format: {e}")
        print(f"Error: Invalid type ID format. Please provide comma-separated integers. {e}")
        return False
    except Exception as e:
        logger.error(f"Error adding items to watchlist: {e}")
        print(f"Error: {e}")
        return False
