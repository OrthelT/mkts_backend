"""
CLI subpackage for mkts-backend doctrine tools.

This package contains CLI commands for managing EVE Online fits and doctrines:
- fit-check: Display market availability for EFT fits
- fit-manager: Manage fits, doctrines, targets, and market assignment
- add_watchlist: Add items to watchlist by type IDs
- args_parser: Parse command line arguments
- prompter: Multiline input prompter for pasting EFT fit text
- display_cli_help: Display general CLI help
- check_tables: Check tables in the database
"""

from mkts_backend.cli_tools.fit_check import fit_check_command
from mkts_backend.cli_tools.fit_manager import handle_fit_manager
from mkts_backend.cli_tools.cli_help import display_cli_help
from mkts_backend.cli_tools.add_watchlist import add_watchlist
from mkts_backend.cli_tools.args_parser import parse_args
from mkts_backend.cli_tools.cli_db_commands import check_tables
from mkts_backend.cli_tools.prompter import get_multiline_input

__all__ = [
    "fit_check_command",
    "handle_fit_manager",
    "add_watchlist",
    "parse_args",
    "display_cli_help",
    "check_tables",
    "get_multiline_input",
]
