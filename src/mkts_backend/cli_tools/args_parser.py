from mkts_backend.cli_tools.add_watchlist import add_watchlist
from mkts_backend.cli_tools.cli_help import (
    display_cli_help,
    display_update_fit_help,
    display_fit_check_help,
    display_fit_update_help,
    display_update_target_help,
)
from mkts_backend.cli_tools.equiv_manager import equiv_command

from mkts_backend.config.market_context import MarketContext
from mkts_backend.config.config import DatabaseConfig
from mkts_backend.utils.validation import validate_all
from mkts_backend.utils.parse_items import parse_items
from mkts_backend.utils.parse_fits import parse_fit_metadata
from mkts_backend.cli_tools.fit_update import (
    fit_update_command,
    update_fit_workflow,
    update_target_command,
)
from mkts_backend.cli_tools.fit_check import fit_check_command
from mkts_backend.cli_tools.cli_db_commands import check_tables
from mkts_backend.config.logging_config import configure_logging
import os

logger = configure_logging(__name__)

_VALID_ENVIRONMENTS = ("production", "development")


def parse_args(args: list[str]) -> dict | None:
    return_args = {}

    if len(args) == 0:
        return None

    # ── Parse --env flag early so the override is visible to all downstream
    #    settings loaders (MarketContext, DatabaseConfig, etc.).  This sets the
    #    MKTS_ENVIRONMENT env-var for the duration of this process only.
    for arg in args:
        if arg.startswith("--env="):
            env_value = arg.split("=", 1)[1].lower()
            if env_value not in _VALID_ENVIRONMENTS:
                print(f"Error: --env must be one of: {', '.join(_VALID_ENVIRONMENTS)}")
                exit(1)
            os.environ["MKTS_ENVIRONMENT"] = env_value
            print(f"Environment override: {env_value}")
            break

    # Handle --help: check for subcommand-specific help first
    if "--help" in args or "-h" in args:
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
                f"  {alias}: {ctx.name} (region={ctx.region_id}, db={
                    ctx.database_alias
                })"
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
        if "--help" in args or "-h" in args:
            display_update_target_help()
            exit(0)
        fit_id = None
        target = None
        market_alias = "primary"
        remote = False
        target_alias = "wcmkt"
        for arg in args:
            if arg.startswith("--fit-id=") or arg.startswith("--fit="):
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
        if "--help" in args or "-h" in args:
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
            if arg.startswith("--fit-file=") or arg.startswith("--file="):
                fit_file = arg.split("=", 1)[1]
            elif arg.startswith("--meta-file="):
                meta_file = arg.split("=", 1)[1]
            elif arg.startswith("--fit-id=") or arg.startswith("--fit_id=") or arg.startswith("fit=") or arg.startswith("id="):
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
                elif market_val.lower() == "north":
                    target_markets = ["deployment"]
                else:
                    print("Error: --market must be one of: primary, deployment, both")
                    return None
            elif arg == "--both":
                target_markets = ["primary", "deployment"]
            elif arg.lower() == "--deployment" or arg.lower() == "--north":
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

        remote = "--remote" in args or any(arg.startswith("--remote=") for arg in args)
        clear_existing = "--no-clear" not in args
        dry_run = "--dry-run" in args

        try:
            # Get metadata from file or interactive prompt
            if meta_file:
                metadata = parse_fit_metadata(meta_file)
                if fit_id is not None and metadata.fit_id != fit_id:
                    print(
                        f"Warning: --fit-id={fit_id} overrides fit_id={
                            metadata.fit_id
                        } from metadata file"
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
                from fit_update import collect_fit_metadata_interactive

                # Interactive mode - collect metadata from user
                metadata_dict = collect_fit_metadata_interactive(
                    fit_id, fit_file)

            # Map market aliases to database aliases
            market_to_db = {
                "primary": "wcmkt",
                "deployment": "wcmktnorth",
            }

            # Process for each target market
            for target_market in target_markets:
                target_alias = market_to_db[target_market]
                print(
                    f"\n--- Processing for {
                        target_market} market ({target_alias}) ---"
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
                    print(f"Ship: {result['ship_name']
                                   } ({result['ship_type_id']})")
                    print(f"Items parsed: {len(result['items'])}")
                    if result["missing_items"]:
                        print(f"Missing type_ids for: {
                              result['missing_items']}")
                else:
                    print(
                        f"Fit update completed for fit_id {metadata_obj.fit_id} -> {
                            target_alias
                        } (remote={remote})"
                    )
                    if update_targets:
                        print("  ship_targets updated")

            exit(0)
        except Exception as e:
            logger.error(f"update-fit failed: {e}")
            print(f"Error running update-fit: {e}")
            exit(1)

    # Handle fit-check command
    if "fit-check" in args:
        # Check for subcommand help
        if "--help" in args or "-h" in args:
            display_fit_check_help()
            exit(0)

        file_path = None
        paste_mode = "--paste" in args
        no_jita = "--no-jita" in args
        target = None
        output_format = None
        fit_id = None

        for arg in args:
            if arg.startswith("--file=") or arg.startswith("--fit-file"):
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
                    print("Error: --output must be one of: csv, multibuy, markdown")
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
        if "--help" in args or "-h" in args:
            display_fit_update_help()
            exit(0)

        # Determine subcommand (first positional arg after fit-update)
        fit_update_idx = args.index("fit-update")
        subcommand = None
        for arg in args[fit_update_idx + 1:]:
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
        remote = "--remote" in args or any(arg.startswith("--remote=") for arg in args)
        local_only = "--local-only" in args
        dry_run = "--dry-run" in args
        skip_targets = "--skip-targets" in args
        paste_mode = "--paste" in args

        if paste_mode:
            file_path = None
            print("***PASTE MODE***")
        else:
            paste_mode = False

        friendly_name = None
        doctrine_id = None
        fit_ids_str = None
        for arg in args:
            if arg.startswith("--file=") or arg.startswith("--fit-file"):
                file_path = arg.split("=", 1)[1]
            elif arg.startswith("--meta-file="):
                meta_file = arg.split("=", 1)[1]
            elif arg.startswith("--fit-id=") or arg.startswith("--fit_id=") or arg.startswith("--id="):
                fit_ids_str = arg.split("=", 1)[1]
            elif arg.startswith("--name="):
                friendly_name = arg.split("=", 1)[1]
            elif arg.startswith("--doctrine-id="):
                doctrine_id = int(arg.split("=", 1)[1])
            elif arg.startswith("--target="):
                # Target quantity for doctrine-add-fit
                target_qty = int(arg.split("=", 1)[1])
            elif arg.startswith("--db-alias="):
                db_alias = arg.split("=", 1)[1]
            elif arg == "--north" or arg == "deployment":
                db_alias = "wcmktnorth"
                market_alias = "deployment"
            elif arg.startswith("--market="):
                market_val = arg.split("=", 1)[1]
                market_alias = market_val
                if market_val == "deployment":
                    db_alias = "wcmktnorth"

        # Parse fit_id(s) - supports comma-separated for doctrine-add-fit
        if fit_ids_str:
            if "," in fit_ids_str:
                # Multiple fit IDs for doctrine-add-fit
                fit_id = None  # Will use fit_ids list instead
                fit_ids = [int(f.strip())
                           for f in fit_ids_str.split(",") if f.strip()]
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
            paste_mode=paste_mode,
            friendly_name=friendly_name,
            doctrine_id=doctrine_id,
        )
        exit(0 if success else 1)

    if "equiv" in args:
        # Check for subcommand help
        if "--help" in args or "-h" in args:
            from mkts_backend.cli_tools.equiv_manager import _display_equiv_help
            _display_equiv_help()
            exit(0)
        success = equiv_command(args, market_alias)
        exit(0 if success else 1)

    if "assets" in args:
        from mkts_backend.cli_tools.asset_check import asset_check_command

        asset_type_id = None
        asset_type_name = None
        force_refresh = "--refresh" in args
        for arg in args:
            if arg.startswith("--id="):
                try:
                    asset_type_id = int(arg.split("=", 1)[1])
                except ValueError:
                    print("Error: --id must be an integer")
                    return None
            elif arg.startswith("--name="):
                asset_type_name = arg.split("=", 1)[1]

        if asset_type_id is None and asset_type_name is None:
            print("Error: --id=<type_id> or --name=<type_name> is required")
            print("Usage: mkts-backend assets --id=11379")
            print("       mkts-backend assets --name='Damage Control'")
            return None

        success = asset_check_command(
            type_id=asset_type_id,
            type_name=asset_type_name,
            force_refresh=force_refresh,
        )
        exit(0 if success else 1)

    if "esi-auth" in args:
        from mkts_backend.esi.esi_auth import authorize_character, REQUIRED_SCOPES
        from mkts_backend.config.character_config import load_characters

        char_key = None
        for arg in args:
            if arg.startswith("--char="):
                char_key = arg.split("=", 1)[1]

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
                    exit(1)
            else:
                char_key = choice
            authorize_character(char_key, REQUIRED_SCOPES)
        exit(0)

    if "sync" in args:
        # Determine which markets to sync
        if "--both" in args or ("--market=both" in args):
            sync_markets = ["primary", "deployment"]
        else:
            sync_markets = [market_alias]

        for mkt in sync_markets:
            market_ctx = MarketContext.from_settings(mkt)
            db = DatabaseConfig(market_context=market_ctx)
            print(f"Syncing database for market: {
                  market_ctx.name} ({market_ctx.alias})")
            db.sync()
            logger.info(f"Database synced: {db.alias}")
            print(f"Database synced: {db.alias} ({db.path})")

        exit()
        return None

    if "validate" in args:
        # Use market_alias parsed from --market/--deployment/--primary flags
        market_ctx = MarketContext.from_settings(market_alias)
        db = DatabaseConfig(market_context=market_ctx)
        print(f"Validating database for market: {
              market_ctx.name} ({market_ctx.alias})")
        validation_test = db.validate_sync()
        if validation_test:
            print(f"Database validated: {db.alias}")
        else:
            print(
                f"Database {
                    db.alias} is out of date. Run 'sync' to sync the database."
            )
        exit()

    if "--validate-env" in args:
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

    # Handle add_watchlist command
    if "add_watchlist" in args:
        add_watchlist(args)
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
