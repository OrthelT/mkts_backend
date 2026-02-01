"""
CLI subpackage for mkts-backend doctrine tools.

This package contains CLI commands for managing EVE Online fits and doctrines:
- fit-check: Display market availability for EFT fits
- fit-update: Interactive tool for managing fits and doctrines
- add_watchlist: Add items to watchlist by type IDs
- args_parser: Parse command line arguments
- collect_fit_metadata_interactive: Collect fit metadata interactively
- display_cli_help: Display general CLI help
- display_update_fit_help: Display update fit help
- display_update_target_help: Display update target help
- check_tables: Check tables in the database
"""

from mkts_backend.cli_tools.fit_check import fit_check_command
from mkts_backend.cli_tools.fit_update import (
    fit_update_command,
    collect_fit_metadata_interactive,
)
from mkts_backend.cli_tools.cli_help import (
    display_cli_help,
    display_update_fit_help,
    display_update_target_help,
)
from mkts_backend.cli_tools.add_watchlist import add_watchlist
from mkts_backend.cli_tools.args_parser import parse_args
from mkts_backend.cli_tools.cli_db_commands import check_tables
from mkts_backend.cli_tools.prompter import get_multiline_input

__all__ = [
    "fit_check_command",
    "fit_update_command",
    "collect_fit_metadata_interactive",
    "add_watchlist",
    "parse_args",
    "display_cli_help",
    "display_update_fit_help",
    "display_update_target_help",
    "check_tables",
    "get_multiline_input",
]
