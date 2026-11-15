"""Centralized command registry for CLI subcommand dispatch.

Both ``mkts-backend`` and ``fitcheck`` entry points resolve subcommands
through this registry, enabling "no-wrong-door" routing — any registered
command works from either entry point.

Handler signature::

    def handler(args: list[str], market_alias: str) -> bool:
        ...

Handlers return ``True`` on success, ``False`` on failure.  They should
**not** call ``sys.exit()`` — the entry point handles exit codes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

HandlerFn = Callable[[list[str], str], bool]

# Commands that fit-manager replaced, mapped to what to run instead.
REMOVED_COMMANDS: dict[str, str] = {
    "fit-update": "fit-manager <subcommand> (the subcommand names are unchanged)",
    "update-target": "fit-manager update-target",
    "update-fit": (
        "fit-manager update (existing fit) or fit-manager add (new fit), "
        "with --file in place of --fit-file"
    ),
}


def removed_command_message(token: str) -> str | None:
    """Return the replacement notice for a removed command, else ``None``."""
    replacement = REMOVED_COMMANDS.get(token)
    if replacement is None:
        return None
    return (
        f"'{token}' was removed. Use: {replacement}\n"
        "Run 'mkts-backend fit-manager --help' for details."
    )


@dataclass
class CommandEntry:
    """A single registered CLI subcommand."""

    name: str
    handler: HandlerFn
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    default_market: str = "primary"

    @property
    def all_names(self) -> set[str]:
        return {self.name} | set(self.aliases)


class CommandRegistry:
    """Lookup table mapping command names/aliases to handlers."""

    def __init__(self) -> None:
        self._commands: list[CommandEntry] = []
        self._index: dict[str, CommandEntry] = {}

    def register(
        self,
        name: str,
        handler: HandlerFn,
        *,
        aliases: list[str] | None = None,
        description: str = "",
        default_market: str = "primary",
    ) -> None:
        entry = CommandEntry(
            name=name,
            handler=handler,
            aliases=aliases or [],
            description=description,
            default_market=default_market,
        )
        self._commands.append(entry)
        for n in entry.all_names:
            self._index[n] = entry

    def resolve(self, token: str) -> CommandEntry | None:
        """Look up a command by name or alias."""
        return self._index.get(token)

    def all_names(self) -> set[str]:
        """Return every registered name and alias (for fuzzy matching)."""
        return set(self._index.keys())

    def all_commands(self) -> list[CommandEntry]:
        """Return all registered commands (no duplicates from aliases)."""
        return list(self._commands)


# ── Singleton registry ──────────────────────────────────────────

_registry: CommandRegistry | None = None


def get_registry() -> CommandRegistry:
    """Return the global command registry, creating it on first call."""
    global _registry
    if _registry is None:
        _registry = CommandRegistry()
        _register_all(_registry)
    return _registry


def _register_all(reg: CommandRegistry) -> None:
    """Register every subcommand.  Uses lazy imports to avoid circular deps."""

    # ── fit-check ───────────────────────────────────────────────
    def _handle_fit_check(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.arg_utils import ParsedArgs, ArgError
        from mkts_backend.cli_tools.fit_check import fit_check_command

        p = ParsedArgs(args)

        if p.has_help():
            from mkts_backend.cli_tools.cli_help import display_fit_check_help
            display_fit_check_help()
            return True

        file_path = p.get_string("file", "fit-file")
        paste_mode = p.has_flag("paste")
        no_jita = p.has_flag("no-jita")
        refresh = p.has_flag("refresh")

        try:
            fit_id = p.get_int("fit-id", "fit_id", "fit", "id")
            target = p.get_int("target")
            output_format = p.get_choice("output", choices={"csv", "multibuy", "markdown"})
        except ArgError as e:
            print(f"Error: {e}")
            return False

        if not file_path and not paste_mode and fit_id is None:
            print("Error: --file=<path>, --paste, or --fit-id=<id> is required for fit-check command")
            print("Use 'fit-check --help' for usage information.")
            return False

        eft_text = None
        if paste_mode:
            import sys
            print("Paste your EFT fit below (Ctrl+D or blank line to finish):")
            lines: list[str] = []
            try:
                for line in sys.stdin:
                    if line.strip() == "":
                        if lines and lines[-1] == "":
                            break
                        lines.append("")
                    else:
                        lines.append(line.rstrip())
            except EOFError:
                pass
            eft_text = "\n".join(lines)

        return fit_check_command(
            file_path=file_path,
            eft_text=eft_text,
            fit_id=fit_id,
            market_alias=market_alias,
            show_legend=True,
            target=target,
            output_format=output_format,
            show_jita=not no_jita,
            refresh=refresh,
        )

    reg.register(
        "fit-check",
        _handle_fit_check,
        aliases=["fc"],
        description="Display market availability for an EFT fit",
    )

    # ── fit-manager ─────────────────────────────────────────────
    def _handle_fit_manager(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.fit_manager import handle_fit_manager
        return handle_fit_manager(args, market_alias)

    reg.register(
        "fit-manager",
        _handle_fit_manager,
        description="Manage fits, doctrines, targets, and market assignment",
    )

    # ── assets ──────────────────────────────────────────────────
    def _handle_assets(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.arg_utils import ParsedArgs, ArgError
        from mkts_backend.cli_tools.asset_check import asset_check_command

        p = ParsedArgs(args)
        force_refresh = p.has_flag("refresh")

        try:
            type_id = p.get_int("id")
        except ArgError as e:
            print(f"Error: {e}")
            return False

        type_name = p.get_string("name")

        if type_id is None and type_name is None:
            print("Error: --id=<type_id> or --name=<type_name> is required")
            print("Usage: assets --id=11379")
            print("       assets --name='Damage Control'")
            return False

        return asset_check_command(
            type_id=type_id,
            type_name=type_name,
            force_refresh=force_refresh,
        )

    reg.register(
        "assets",
        _handle_assets,
        description="Look up character assets by type ID or name",
    )

    # ── equiv ───────────────────────────────────────────────────
    def _handle_equiv(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.arg_utils import ParsedArgs

        p = ParsedArgs(args)
        if p.has_help():
            from mkts_backend.cli_tools.equiv_manager import _display_equiv_help
            _display_equiv_help()
            return True

        from mkts_backend.cli_tools.equiv_manager import equiv_command
        return equiv_command(args, market_alias)

    reg.register(
        "equiv",
        _handle_equiv,
        description="Manage module equivalence groups",
    )

    # ── sync ────────────────────────────────────────────────────
    def _handle_sync(args: list[str], market_alias: str) -> bool:
        """Pull every configured replica from Turso.

        Markets come from the market selector; shared databases come from
        database_routing(), so a new [shared.*] block in settings.toml is
        picked up with no code change. This is the whole of the old
        dbrefresh*.sh procedure: the scripts used `turso db export`, which
        cannot produce pyturso metadata.
        """
        from mkts_backend.cli_tools.arg_utils import ParsedArgs
        from mkts_backend.cli_tools.market_args import expand_market_alias
        from mkts_backend.config import db_config
        from mkts_backend.config.logging_config import configure_logging
        from mkts_backend.config.market_context import MarketContext
        from mkts_backend.config.settings_service import (
            SettingsService,
            get_all_market_contexts,
        )

        logger = configure_logging(__name__)
        p = ParsedArgs(args)
        skip_buildcost = p.has_flag("no-buildcost")
        markets_only = p.has_flag("markets-only")
        include_testing = p.has_flag("include-testing")

        routing = SettingsService().database_routing()
        market_db_aliases = {
            ctx.database_alias for ctx in get_all_market_contexts().values()
        }
        testing_alias = SettingsService().shared_testing["database_alias"]

        ok = True

        def credentials_present(cfg: dict) -> bool:
            return bool(
                cfg["turso_url_env"]
                and cfg["turso_token_env"]
                and os.getenv(cfg["turso_url_env"])
                and os.getenv(cfg["turso_token_env"])
            )

        def pull_one(db, label: str, required: bool) -> bool:
            print(f"Syncing database: {label}")
            try:
                db.assert_remote_compatible()
                if not db.heal_metadata():
                    raise RuntimeError("replica metadata could not be repaired")
                db.sync()
            except Exception as exc:
                logger.warning(f"{label} sync failed: {exc}")
                print(f"{'Error' if required else 'Warning'}: {label} sync failed: {exc}")
                return False
            logger.info(f"Database synced: {db.alias}")
            print(f"Database synced: {db.alias} ({db.path})")
            return True

        for mkt in expand_market_alias(market_alias):
            market_ctx = MarketContext.from_settings(mkt)
            cfg = routing[market_ctx.database_alias]
            if not credentials_present(cfg):
                print(f"Error: required credentials are incomplete for {mkt}")
                ok = False
                continue
            db = db_config.DatabaseConfig(market_context=market_ctx)
            ok &= pull_one(db, f"{market_ctx.name} ({market_ctx.alias})", required=True)

        if markets_only:
            return ok

        for alias, cfg in routing.items():
            if alias in market_db_aliases:
                continue
            if alias == testing_alias and not include_testing:
                continue
            if skip_buildcost and "buildcost" in alias:
                continue
            if not credentials_present(cfg):
                level = "Warning" if cfg["optional"] else "Error"
                print(f"{level}: credentials are incomplete for {alias}")
                ok &= bool(cfg["optional"])
                continue
            db = db_config.DatabaseConfig(alias)
            ok &= pull_one(db, alias, required=not cfg["optional"])

        return ok

    reg.register(
        "sync",
        _handle_sync,
        description=(
            "Pull every configured replica from Turso "
            "(--markets-only, --no-buildcost, --include-testing to narrow)"
        ),
        default_market="all",
    )

    # ── update-builder-costs ───────────────────────────────────
    def _handle_update_builder_costs(args: list[str], market_alias: str) -> bool:
        del market_alias  # buildcost data is market-agnostic
        from mkts_backend.cli_tools.arg_utils import ParsedArgs
        from mkts_backend.builder_costs.runner import run

        p = ParsedArgs(args)
        if p.has_help():
            from mkts_backend.cli_tools.cli_help import display_builder_cost_help
            display_builder_cost_help()
            return True
        result = run()
        if not result.success:
            return False
        if result.missing > 0:
            # Partial EverRef failures must surface as non-zero exit so the
            # daily CI job catches stale rows instead of silently persisting.
            print(
                f"update-builder-costs: {result.missing} of "
                f"{result.watchlist_size} items missing fresh cost data"
            )
            return False
        return True

    reg.register(
        "update-builder-costs",
        _handle_update_builder_costs,
        aliases=["builder-costs", "ubc", "update-build-costs"],
        description="Refresh manufacturing costs in buildcost.db",
    )

    # ── update-markets ──────────────────────────────────────────
    def _handle_update_markets(args: list[str], market_alias: str) -> bool:
        import sys
        from mkts_backend.cli_tools.arg_utils import ParsedArgs
        from mkts_backend.cli import run_market_update

        # Honor --history regardless of flag position; market is already
        # resolved from full argv by the dispatcher, so do the same here.
        history = ParsedArgs(sys.argv[1:]).has_flag("history", "include-history")
        return run_market_update(history=history, market_alias=market_alias)

    reg.register(
        "update-markets",
        _handle_update_markets,
        aliases=["update"],
        description="Run full market-data update pipeline (all markets by default)",
        default_market="all",
    )

    # ── parse-items ─────────────────────────────────────────────
    def _handle_parse_items(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.arg_utils import ParsedArgs
        from mkts_backend.utils.parse_items import parse_items

        p = ParsedArgs(args)
        input_file = p.get_string("input")
        output_file = p.get_string("output")

        if not input_file or not output_file:
            print("Error: Both --input and --output parameters are required for parse-items command")
            print("Usage: parse-items --input=structure_data.txt --output=market_prices.csv")
            return False

        success = parse_items(input_file, output_file)
        print("Parse items command completed successfully" if success else "Parse items command failed")
        return success

    reg.register("parse-items", _handle_parse_items, description="Parse Eve structure data and create CSV with pricing")

    # ── esi-auth ────────────────────────────────────────────────
    def _handle_esi_auth(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.esi_auth_cli import handle_esi_auth
        return handle_esi_auth(args, market_alias)

    reg.register("esi-auth", _handle_esi_auth, description="Manage ESI credentials and browser authorization")

    # ── build-watchlist ─────────────────────────────────────────
    def _handle_build_watchlist(args: list[str], market_alias: str) -> bool:
        del market_alias  # buildcost data is market-agnostic
        from mkts_backend.cli_tools.build_watchlist_cli import (
            handle_build_watchlist,
        )
        return handle_build_watchlist(args)

    reg.register(
        "build-watchlist",
        _handle_build_watchlist,
        aliases=["build_watchlist"],
        description="Manage build_watchlist (add | remove | mirror | sync)",
    )

    # ── add_watchlist ───────────────────────────────────────────
    def _handle_add_watchlist(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.add_watchlist import add_watchlist
        return add_watchlist(args, market_alias=market_alias)

    reg.register(
        "add_watchlist",
        _handle_add_watchlist,
        aliases=["add-watchlist"],
        description="Add items to watchlist by type IDs",
    )

    # ── add_structure ───────────────────────────────────────────
    def _handle_add_structure(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.add_structure import add_structure
        return add_structure(args, market_alias=market_alias)

    reg.register(
        "add_structure",
        _handle_add_structure,
        aliases=["add-structure", "add_structures"],
        description="Import structures from a Google Sheet into buildcost.db",
    )

    # ── list-fits (fitcheck subcommand) ─────────────────────────
    def _handle_list_fits(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.fit_check import _handle_list_fits as _lf
        _lf(args)
        return True

    reg.register(
        "list-fits",
        _handle_list_fits,
        aliases=["lf"],
        description="List all tracked doctrine fits",
    )

    # ── needed (fitcheck subcommand) ────────────────────────────
    def _handle_needed(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.fit_check import _handle_needed as _n
        _n(args)
        return True

    reg.register("needed", _handle_needed, description="Show all items needed to reach ship targets")

    # ── module (fitcheck subcommand) ────────────────────────────
    def _handle_module(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.fit_check import _handle_module as _m
        _m(args)
        return True

    reg.register("module", _handle_module, description="Show which fits use a given module")
