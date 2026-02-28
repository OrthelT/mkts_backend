from mkts_backend.cli_tools.cli_help import display_cli_help
from mkts_backend.cli_tools.market_args import parse_market_args
from mkts_backend.cli_tools.arg_utils import ParsedArgs, suggest_command, check_bare_args
from mkts_backend.cli_tools.command_registry import get_registry

from mkts_backend.config.market_context import MarketContext
from mkts_backend.utils.validation import validate_all
from mkts_backend.cli_tools.cli_db_commands import check_tables
from mkts_backend.config.logging_config import configure_logging
import os

logger = configure_logging(__name__)

_VALID_ENVIRONMENTS = ("production", "development")

# Subcommands that have their own --help handling
_SUBCOMMANDS_WITH_HELP = {"fit-check", "fit-update", "update-fit", "update-target"}


def parse_args(args: list[str]) -> dict | None:
    return_args = {}

    if len(args) == 0:
        return None

    p = ParsedArgs(args)

    # ── Parse --env flag early so the override is visible to all downstream
    #    settings loaders (MarketContext, DatabaseConfig, etc.).
    env_value = p.get_string("env")
    if env_value is not None:
        env_value = env_value.lower()
        if env_value not in _VALID_ENVIRONMENTS:
            print(f"Error: --env must be one of: {', '.join(_VALID_ENVIRONMENTS)}")
            exit(1)
        os.environ["MKTS_ENVIRONMENT"] = env_value
        print(f"Environment override: {env_value}")

    # Handle --help: check for subcommand-specific help first
    if p.has_help():
        for subcmd in _SUBCOMMANDS_WITH_HELP:
            if subcmd in args:
                break
        else:
            display_cli_help()
            exit()

    market_alias = parse_market_args(args)
    return_args["market"] = market_alias

    if p.has_flag("list-markets"):
        available = MarketContext.list_available()
        print(f"Available markets: {', '.join(available)}")
        for alias in available:
            ctx = MarketContext.from_settings(alias)
            print(
                f"  {alias}: {ctx.name} (region={ctx.region_id}, db={
                    ctx.database_alias
                })"
            )
        exit()

    if p.has_flag("check_tables"):
        check_tables(market_alias)
        exit()

    if p.has_flag("validate-env"):
        result = validate_all()
        if result["is_valid"]:
            print(result["message"])
            print(
                f"Required credentials present: {
                    ', '.join(result['present_required'])}"
            )
            if result["present_optional"]:
                print(
                    f"Optional credentials present: {
                        ', '.join(result['present_optional'])
                    }"
                )
        else:
            print(result["message"])
            if result["missing_required"]:
                print(f"Missing required: {
                      ', '.join(result['missing_required'])}")
        exit(0 if result["is_valid"] else 1)

    # ── Registry-based subcommand dispatch ──────────────────────
    registry = get_registry()
    for i, arg in enumerate(args):
        if arg.startswith("--"):
            continue
        entry = registry.resolve(arg)
        if entry:
            sub_args = args[i + 1:]
            # Check for bare key=value args (missing --) and suggest fixes
            bare = check_bare_args(sub_args, registry.all_names())
            if bare:
                corrected = " ".join(
                    (f"--{a}" if not a.startswith("--") and "=" in a and a not in registry.all_names() else a)
                    for a in sub_args
                )
                print(f"\033[93mDid you mean?\033[0m mkts-backend {arg} {corrected}")
                exit(1)
            success = entry.handler(sub_args, market_alias)
            exit(0 if success else 1)

    # ── Unknown positional? Suggest closest command ─────────────
    for i, arg in enumerate(args):
        if arg.startswith("--"):
            continue
        suggestion = suggest_command(arg, registry.all_names())
        if suggestion:
            rest = " ".join(args[i + 1:])
            hint = f"mkts-backend {suggestion}"
            if rest:
                hint += f" {rest}"
            print(f"Unknown command: '{arg}'")
            print(f"\033[93mDid you mean?\033[0m {hint}")
            exit(1)

    # ── Flags that don't exit ───────────────────────────────────
    if p.has_flag("history", "include-history"):
        return_args["history"] = True
    else:
        return_args["history"] = False

    # If we have a market specified but no other command, run the main workflow
    if return_args.get("market"):
        return return_args

    display_cli_help()
    exit()
