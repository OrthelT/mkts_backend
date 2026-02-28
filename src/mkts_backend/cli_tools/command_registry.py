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

from dataclasses import dataclass, field
from typing import Callable

HandlerFn = Callable[[list[str], str], bool]


@dataclass
class CommandEntry:
    """A single registered CLI subcommand."""

    name: str
    handler: HandlerFn
    aliases: list[str] = field(default_factory=list)
    description: str = ""

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
    ) -> None:
        entry = CommandEntry(
            name=name,
            handler=handler,
            aliases=aliases or [],
            description=description,
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
        )

    reg.register(
        "fit-check",
        _handle_fit_check,
        aliases=["fc"],
        description="Display market availability for an EFT fit",
    )

    # ── fit-update ──────────────────────────────────────────────
    def _handle_fit_update(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.arg_utils import ParsedArgs, ArgError
        from mkts_backend.cli_tools.market_args import MARKET_DB_MAP
        from mkts_backend.cli_tools.fit_update import fit_update_command

        p = ParsedArgs(args)

        if p.has_help():
            from mkts_backend.cli_tools.cli_help import display_fit_update_help
            display_fit_update_help()
            return True

        # Determine subcommand (first positional arg)
        subcommand = None
        for arg in args:
            if not arg.startswith("--"):
                if arg == "fit-update":
                    continue
                subcommand = arg
                break

        if not subcommand:
            print("Error: fit-update requires a subcommand")
            print("Use 'fit-update --help' for usage information.")
            return False

        db_alias = p.get_string("db-alias", default=MARKET_DB_MAP.get(market_alias, "wcmkt"))
        paste_mode = p.has_flag("paste")
        file_path = None if paste_mode else p.get_string("file", "fit-file")
        meta_file = p.get_string("meta-file")
        friendly_name = p.get_string("name")

        if paste_mode:
            print("***PASTE MODE***")

        try:
            fit_ids_str = p.get_string("fit-id", "fit_id", "id")
            doctrine_id = p.get_int("doctrine-id")
            target_qty = p.get_int("target", default=100)
        except ArgError as e:
            print(f"Error: {e}")
            return False

        fit_id = None
        fit_ids = None
        if fit_ids_str:
            if "," in fit_ids_str:
                fit_ids = [int(f.strip()) for f in fit_ids_str.split(",") if f.strip()]
            else:
                fit_id = int(fit_ids_str)

        return fit_update_command(
            subcommand=subcommand,
            fit_id=fit_id,
            fit_ids=fit_ids,
            file_path=file_path,
            meta_file=meta_file,
            market_flag=market_alias,
            remote=p.has_flag("remote"),
            local_only=p.has_flag("local-only"),
            dry_run=p.has_flag("dry-run"),
            interactive=p.has_flag("interactive"),
            target_alias=db_alias,
            target=target_qty,
            skip_targets=p.has_flag("skip-targets"),
            paste_mode=paste_mode,
            friendly_name=friendly_name,
            doctrine_id=doctrine_id,
        )

    reg.register(
        "fit-update",
        _handle_fit_update,
        description="Interactive tool for managing fits and doctrines",
    )

    # ── update-fit ──────────────────────────────────────────────
    def _handle_update_fit(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.arg_utils import ParsedArgs, ArgError
        from mkts_backend.cli_tools.market_args import MARKET_DB_MAP
        from mkts_backend.utils.parse_fits import parse_fit_metadata
        from mkts_backend.cli_tools.fit_update import update_fit_workflow

        p = ParsedArgs(args)

        if p.has_help():
            from mkts_backend.cli_tools.cli_help import display_update_fit_help
            display_update_fit_help()
            return True

        try:
            fit_id = p.get_int("fit-id", "fit_id")
        except ArgError as e:
            print(f"Error: {e}")
            return False

        fit_file = p.get_string("fit-file", "file")
        meta_file = p.get_string("meta-file")
        interactive = p.has_flag("interactive")
        update_targets = p.has_flag("update-targets")

        # Legacy bare arg compat (fit=, id=)
        if fit_id is None:
            for arg in args:
                if arg.startswith("fit=") or arg.startswith("id="):
                    try:
                        fit_id = int(arg.split("=", 1)[1])
                    except ValueError:
                        print("Error: --fit-id must be an integer")
                        return False

        if not fit_file:
            print("Error: --fit-file is required for update-fit")
            print("Use 'update-fit --help' for usage information.")
            return False

        if not meta_file and fit_id is None:
            print("Error: Either --meta-file or --fit-id is required")
            print("Use 'update-fit --help' for usage information.")
            return False

        if fit_id is not None and not meta_file and not interactive:
            print("Error: --fit-id requires either --meta-file or --interactive")
            print("Use 'update-fit --help' for usage information.")
            return False

        if market_alias == "both":
            target_markets = ["primary", "deployment"]
        else:
            target_markets = [market_alias]

        remote = p.has_flag("remote")
        clear_existing = not p.has_flag("no-clear")
        dry_run = p.has_flag("dry-run")

        try:
            if meta_file:
                metadata = parse_fit_metadata(meta_file)
                if fit_id is not None and metadata.fit_id != fit_id:
                    print(
                        f"Warning: --fit-id={fit_id} overrides fit_id={
                            metadata.fit_id} from metadata file"
                    )
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
                    metadata_dict = None
            else:
                from fit_update import collect_fit_metadata_interactive
                metadata_dict = collect_fit_metadata_interactive(fit_id, fit_file)

            for target_market in target_markets:
                target_alias = MARKET_DB_MAP[target_market]
                print(f"\n--- Processing for {target_market} market ({target_alias}) ---")

                if metadata_dict:
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
                        f"Fit update completed for fit_id {metadata_obj.fit_id} -> "
                        f"{target_alias} (remote={remote})"
                    )
                    if update_targets:
                        print("  ship_targets updated")

            return True
        except Exception as e:
            from mkts_backend.config.logging_config import configure_logging
            logger = configure_logging(__name__)
            logger.error(f"update-fit failed: {e}")
            print(f"Error running update-fit: {e}")
            return False

    reg.register(
        "update-fit",
        _handle_update_fit,
        description="Process an EFT fit file and update doctrine tables",
    )

    # ── update-target ───────────────────────────────────────────
    def _handle_update_target(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.arg_utils import ParsedArgs, ArgError
        from mkts_backend.cli_tools.market_args import MARKET_DB_MAP
        from mkts_backend.cli_tools.fit_update import update_target_command

        p = ParsedArgs(args)

        if p.has_help():
            from mkts_backend.cli_tools.cli_help import display_update_target_help
            display_update_target_help()
            return True

        try:
            fit_id = p.get_int("fit-id", "fit")
            target = p.get_int("target")
        except ArgError as e:
            print(f"Error: {e}")
            return False

        remote = p.has_flag("remote")
        if p.has_flag("local-only"):
            remote = False
        target_alias = p.get_string("db-alias", default=MARKET_DB_MAP.get(market_alias, "wcmkt"))

        if not fit_id or not target:
            print("Error: --fit-id and --target are required for update-target command")
            print("Use 'update-target --help' for usage information.")
            return False

        success = update_target_command(
            fit_id, target,
            market_flag=market_alias,
            remote=remote,
            db_alias=target_alias,
        )
        if success:
            print("Update target command completed successfully")
        else:
            print("Update target command failed")
        return success

    reg.register(
        "update-target",
        _handle_update_target,
        description="Update the target quantity for a fit",
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
        from mkts_backend.config.market_context import MarketContext
        from mkts_backend.config.config import DatabaseConfig
        from mkts_backend.config.logging_config import configure_logging

        logger = configure_logging(__name__)

        if market_alias == "both":
            sync_markets = ["primary", "deployment"]
        else:
            sync_markets = [market_alias]

        for mkt in sync_markets:
            market_ctx = MarketContext.from_settings(mkt)
            db = DatabaseConfig(market_context=market_ctx)
            print(f"Syncing database for market: {market_ctx.name} ({market_ctx.alias})")
            db.sync()
            logger.info(f"Database synced: {db.alias}")
            print(f"Database synced: {db.alias} ({db.path})")

        return True

    reg.register("sync", _handle_sync, description="Sync the database")

    # ── validate ────────────────────────────────────────────────
    def _handle_validate(args: list[str], market_alias: str) -> bool:
        from mkts_backend.config.market_context import MarketContext
        from mkts_backend.config.config import DatabaseConfig

        market_ctx = MarketContext.from_settings(market_alias)
        db = DatabaseConfig(market_context=market_ctx)
        print(f"Validating database for market: {market_ctx.name} ({market_ctx.alias})")
        valid = db.validate_sync()
        if valid:
            print(f"Database validated: {db.alias}")
        else:
            print(f"Database {db.alias} is out of date. Run 'sync' to sync the database.")
        return valid

    reg.register("validate", _handle_validate, description="Validate the database sync status")

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
        from mkts_backend.cli_tools.arg_utils import ParsedArgs
        from mkts_backend.esi.esi_auth import authorize_character, REQUIRED_SCOPES
        from mkts_backend.config.character_config import load_characters

        p = ParsedArgs(args)
        char_key = p.get_string("char")

        if char_key:
            authorize_character(char_key, REQUIRED_SCOPES)
        else:
            characters = load_characters()
            print("Available characters:")
            for i, char in enumerate(characters, 1):
                print(f"  {i}. {char.name} (key: {char.key})")
            choice = input("\nEnter character key (or number): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(characters):
                    char_key = characters[idx].key
                else:
                    print("Invalid selection.")
                    return False
            else:
                char_key = choice
            authorize_character(char_key, REQUIRED_SCOPES)
        return True

    reg.register("esi-auth", _handle_esi_auth, description="Re-authorize ESI tokens with expanded scopes")

    # ── add_watchlist ───────────────────────────────────────────
    def _handle_add_watchlist(args: list[str], market_alias: str) -> bool:
        from mkts_backend.cli_tools.add_watchlist import add_watchlist
        add_watchlist(args, market_alias=market_alias)
        return True

    reg.register(
        "add_watchlist",
        _handle_add_watchlist,
        aliases=["add-watchlist"],
        description="Add items to watchlist by type IDs",
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
