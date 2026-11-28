"""fit-manager: the one CLI command for managing fits and doctrines.

This module owns the command's surface: the subcommand table, per-subcommand
help, option validation, market resolution, and dispatch. The database work
lives in ``fit_update.py``.

Each subcommand is one ``Subcommand`` entry. Its ``options`` both document
the subcommand and define what it accepts: an option that is not listed is
rejected, so help text cannot drift from the parser.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from typing import Callable

from mkts_backend.cli_tools import fit_update
from mkts_backend.cli_tools.arg_utils import ArgError, ParsedArgs, suggest_command
from mkts_backend.cli_tools.market_args import (
    MARKET_DB_MAP,
    resolve_market_alias,
    resolve_market_alias_interactive,
)
from mkts_backend.cli_tools.prompter import get_multiline_input
from mkts_backend.config.logging_config import configure_logging
from mkts_backend.db.unit_of_work import ReplicaUnitOfWork
from mkts_backend.utils.parse_fits import parse_fit_metadata, update_fit_workflow
from rich.prompt import Confirm, IntPrompt

logger = configure_logging(__name__)

PROG = "mkts-backend fit-manager"

# Market values shown in help. MARKETS_ANY follows settings.toml, so a new
# [markets.*] block appears with no edit here. The assignment model
# (fit_update._flag_to_aliases) accepts only the three in MARKETS_ASSIGNMENT.
MARKETS_ANY = ", ".join([*MARKET_DB_MAP, "all"])
MARKETS_ASSIGNMENT = "primary, deployment, all"

_MARKET_FLAGS = {"market", "primary", "deployment", "north", "all", "both"}
_GLOBAL_FLAGS = {"help", "env"}

_META_FILE_FORMAT = """\
Metadata file (JSON):
  {
    "fit_id": 313,
    "name": "Hurricane Fleet Issue - Arty",
    "description": "Standard doctrine fit",
    "doctrine_id": 42,
    "target": 300
  }
"doctrine_id" may be a list, such as [42, 43]. A doctrine that does not
exist yet is created with the fit's name."""

_OPT_FILE = ("--file=<path>", "EFT fit file.")
_OPT_PASTE = (
    "--paste",
    "Paste the EFT fit into the multiline editor. Press Esc, then Enter, "
    "to submit.",
)
_OPT_DRY_RUN = (
    "--dry-run",
    "Parse the fit and report the ship and item count. Nothing is saved.",
)
_OPT_DB_ALIAS = (
    "--db-alias=<alias>",
    "Use this database_alias from settings.toml instead of the selected "
    "market's database.",
)


@dataclass(frozen=True)
class Invocation:
    """One parsed ``fit-manager <subcommand>`` call."""

    args: ParsedArgs
    market: str  # a configured market name, or "all"
    db_alias: str  # database of `market`; the primary market's for "all"


@dataclass(frozen=True)
class Subcommand:
    name: str
    group: str
    summary: str
    usage: tuple[str, ...]
    options: tuple[tuple[str, str], ...]
    scope: str
    examples: tuple[tuple[str, str], ...]
    run: Callable[[Invocation], bool]
    markets: str | None = None  # accepted --market values; None = no market
    notes: str = ""

    @property
    def option_names(self) -> set[str]:
        names = {flag[2:].split("=")[0] for flag, _ in self.options}
        if self.markets:
            names |= _MARKET_FLAGS
        return names | _GLOBAL_FLAGS


def _fail(message: str) -> bool:
    print(f"Error: {message}")
    return False


def _all_db_aliases() -> list[str]:
    """Database alias of every configured market, in settings.toml order."""
    return list(dict.fromkeys(MARKET_DB_MAP.values()))


def _selected_db_aliases(inv: Invocation) -> list[str]:
    return _all_db_aliases() if inv.market == "all" else [inv.db_alias]


def _fit_ids(inv: Invocation) -> list[int] | None:
    return inv.args.get_int_list("fit-id") or None


def _single_fit_id(inv: Invocation) -> int | None:
    return inv.args.get_int("fit-id")


def _fit_file(inv: Invocation) -> str | None:
    """Return the EFT file to read: ``--file``, or pasted text saved to a file."""
    if inv.args.has_flag("paste"):
        eft_text = get_multiline_input()
        return fit_update.eft_text_to_file(eft_text) if eft_text.strip() else None
    return inv.args.get_string("file")


def _write_fit(
    fit_id: int,
    fit_file: str,
    db_aliases: list[str],
    *,
    meta_file: str | None = None,
    metadata_override: dict | None = None,
    clear_existing: bool = True,
    dry_run: bool = False,
    market_flag: str | None = None,
) -> bool:
    """Run ``update_fit_workflow`` across ``db_aliases`` and push each replica once."""
    if dry_run:
        result = update_fit_workflow(
            fit_id=fit_id,
            fit_file=fit_file,
            fit_metadata_file=meta_file,
            clear_existing=clear_existing,
            dry_run=True,
            target_aliases=db_aliases,
            metadata_override=metadata_override,
        )
        print("Dry run: nothing was saved.")
        print(f"Ship: {result['ship_name']} ({result['ship_type_id']})")
        print(f"Items: {len(result['items'])}")
        if result["missing_items"]:
            print(f"Items with no type ID: {result['missing_items']}")
        print(f"Markets: {', '.join(db_aliases)}")
        return True

    with ReplicaUnitOfWork() as uow:
        update_fit_workflow(
            fit_id=fit_id,
            fit_file=fit_file,
            fit_metadata_file=meta_file,
            clear_existing=clear_existing,
            dry_run=False,
            target_aliases=db_aliases,
            metadata_override=metadata_override,
            uow=uow,
            market_flag=market_flag,
        )
    if not uow.ok:
        return False
    print(f"Saved fit {fit_id} to {', '.join(db_aliases)}")
    return True


# ── Subcommand implementations ──────────────────────────────────


def _run_add(inv: Invocation) -> bool:
    interactive = inv.args.has_flag("interactive")
    meta_file = inv.args.get_string("meta-file")
    if interactive == bool(meta_file):
        return _fail("add requires one of --interactive or --meta-file=<path>")
    fit_file = _fit_file(inv)
    if not fit_file:
        return _fail("add requires --file=<path> or --paste")
    dry_run = inv.args.has_flag("dry-run")

    if interactive:
        return fit_update.interactive_add_fit(
            fit_file=fit_file,
            dry_run=dry_run,
            target_alias=inv.db_alias,
            market_flag=inv.market,
        )
    return _write_fit(
        parse_fit_metadata(meta_file).fit_id,
        fit_file,
        _selected_db_aliases(inv),
        meta_file=meta_file,
        dry_run=dry_run,
        # doctrine_fits.market_flag stores assignment values only. Any other
        # market passes None: a new row gets "primary", an existing row keeps
        # its flag.
        market_flag=inv.market if inv.market in MARKETS_ASSIGNMENT.split(", ") else None,
    )


def _run_update(inv: Invocation) -> bool:
    fit_id = _single_fit_id(inv)
    meta_file = inv.args.get_string("meta-file")
    if fit_id is None and not meta_file:
        return _fail("update requires --fit-id=<id> or --meta-file=<path>")
    fit_file = _fit_file(inv)
    if not fit_file:
        return _fail("update requires --file=<path> or --paste")

    metadata_override = None
    if meta_file:
        metadata = parse_fit_metadata(meta_file)
        if fit_id is None:
            fit_id = metadata.fit_id
        elif metadata.fit_id != fit_id:
            print(
                f"Warning: --fit-id={fit_id} overrides fit_id={metadata.fit_id} "
                f"from {meta_file}"
            )
            metadata_override = {
                "fit_id": fit_id,
                "name": metadata.name,
                "description": metadata.description,
                "doctrine_id": metadata.doctrine_ids,
                "target": metadata.target,
            }

    db_aliases, tracked_metadata = fit_update.find_tracked_fit(fit_id)
    if not db_aliases:
        return _fail(
            f"fit {fit_id} is not tracked in any market. "
            "Use 'fit-manager add' for a new fit."
        )
    if not meta_file:
        metadata_override = tracked_metadata

    return _write_fit(
        fit_id,
        fit_file,
        db_aliases,
        meta_file=meta_file,
        metadata_override=metadata_override,
        clear_existing=not inv.args.has_flag("no-clear"),
        dry_run=inv.args.has_flag("dry-run"),
    )


def _run_remove(inv: Invocation) -> bool:
    fit_id = _single_fit_id(inv)
    if fit_id is None:
        return _fail("remove requires --fit-id=<id>")
    db_aliases = _selected_db_aliases(inv)
    # The doctrine links live in the shared fittings database. Deleting them
    # while another market still tracks the fit would orphan that market's rows.
    tracked, _ = fit_update.find_tracked_fit(fit_id)
    keep_links = bool(set(tracked) - set(db_aliases))
    ok = True
    for alias in db_aliases:
        if not fit_update.remove_fit_command(
            fit_id=fit_id, db_alias=alias, keep_doctrine_links=keep_links
        ):
            ok = False
    return ok


def _run_list_fits(inv: Invocation) -> bool:
    fit_update.list_fits_command(db_alias=inv.db_alias)
    return True


def _run_list_doctrines(inv: Invocation) -> bool:
    fit_update.list_doctrines_command(db_alias=inv.db_alias)
    return True


def _run_create_doctrine(inv: Invocation) -> bool:
    return fit_update.create_doctrine_command()


def _run_doctrine_add_fit(inv: Invocation) -> bool:
    return fit_update.doctrine_add_fit_command(
        fit_ids=_fit_ids(inv),
        target=inv.args.get_int("target", default=100),
        market_flag=inv.market,
        db_alias=inv.db_alias,
        skip_targets=inv.args.has_flag("skip-targets"),
    )


def _run_doctrine_remove_fit(inv: Invocation) -> bool:
    return fit_update.doctrine_remove_fit_command(
        doctrine_id=inv.args.get_int("doctrine-id"),
        fit_ids=_fit_ids(inv),
        db_alias=inv.db_alias,
    )


def _plan_succeeded(result: dict, *changed_keys: str) -> bool:
    """A market plan succeeded only if it changed a row and nothing failed.

    A later bucket, its push, or one step inside an otherwise good bucket can
    fail after rows were already updated, so a non-zero count is not enough.
    """
    return (
        any(result.get(key, 0) for key in changed_keys)
        and not result.get("push_failed")
        and not result.get("bucket_failures")
        and not result.get("step_failures")
    )


def _fit_or_doctrine(inv: Invocation, name: str) -> tuple[int | None, int | None] | None:
    fit_id = _single_fit_id(inv)
    doctrine_id = inv.args.get_int("doctrine-id")
    if (fit_id is None) == (doctrine_id is None):
        _fail(f"{name} requires one of --fit-id=<id> or --doctrine-id=<id>")
        return None
    return fit_id, doctrine_id


def _run_assign_market(inv: Invocation) -> bool:
    target = _fit_or_doctrine(inv, "assign-market")
    if target is None:
        return False
    fit_id, doctrine_id = target
    if doctrine_id is not None:
        return fit_update.assign_doctrine_market(
            doctrine_id, inv.market, db_alias=inv.db_alias
        )
    result = fit_update.assign_market_command(
        fit_id, inv.market, db_alias=inv.db_alias
    )
    return _plan_succeeded(result, "updated")


def _run_unassign_market(inv: Invocation) -> bool:
    target = _fit_or_doctrine(inv, "unassign-market")
    if target is None:
        return False
    fit_id, doctrine_id = target
    if doctrine_id is not None:
        return fit_update.unassign_doctrine_market(
            doctrine_id, inv.market, db_alias=inv.db_alias
        )
    result = fit_update.unassign_market_command(
        fit_id, inv.market, db_alias=inv.db_alias
    )
    return _plan_succeeded(result, "updated", "deleted")


def _run_update_target(inv: Invocation) -> bool:
    fit_id = _single_fit_id(inv)
    if fit_id is None:
        return _fail("update-target requires --fit-id=<id>")
    # No default: a missing --target must prompt or fail, never write a guess.
    target = inv.args.get_int("target")
    interactive_tty = sys.stdin.isatty()
    if target is None:
        if not interactive_tty:
            return _fail("update-target requires --target=<qty>")
        target = IntPrompt.ask("[bold]Target quantity[/bold]")
    if interactive_tty and not Confirm.ask(
        f"Update target for fit {fit_id} to [yellow]{target}[/yellow]?"
    ):
        print("Aborted: target unchanged")
        return False
    return fit_update.update_target_command(
        fit_id, target, market_flag=inv.market, db_alias=inv.db_alias
    )


def _run_update_lead_ship(inv: Invocation) -> bool:
    doctrine_id = inv.args.get_int("doctrine-id")
    fit_id = _single_fit_id(inv)
    if doctrine_id is None or fit_id is None:
        return _fail("update-lead-ship requires --doctrine-id=<id> and --fit-id=<id>")
    return fit_update.update_lead_ship_command(
        doctrine_id=doctrine_id, fit_id=fit_id, market_flag=inv.market
    )


def _run_update_friendly_name(inv: Invocation) -> bool:
    doctrine_id = inv.args.get_int("doctrine-id")
    name = inv.args.get_string("name")
    if doctrine_id is None or not name:
        return _fail(
            "update-friendly-name requires --doctrine-id=<id> and --name=<name>"
        )
    return fit_update.update_friendly_name_command(
        doctrine_id, name, db_alias=inv.db_alias
    )


def _run_populate_friendly_names(inv: Invocation) -> bool:
    return fit_update.populate_friendly_names_command(
        json_path="doctrine_names.json", db_alias=inv.db_alias
    )


# ── Subcommand table ────────────────────────────────────────────

_GROUP_FITS = "Fits"
_GROUP_DOCTRINES = "Doctrines"
_GROUP_MARKETS = "Market assignment"
_GROUP_TARGETS = "Targets and labels"

_SUBCOMMAND_LIST: tuple[Subcommand, ...] = (
    Subcommand(
        name="add",
        group=_GROUP_FITS,
        summary="Add a new fit from EFT text and link it to doctrines",
        usage=(
            "--file=<path> --interactive [options]",
            "--file=<path> --meta-file=<path> [options]",
        ),
        options=(
            _OPT_FILE,
            _OPT_PASTE,
            (
                "--interactive",
                "Prompt for the description, doctrines, target, fit ID, and "
                "market assignment.",
            ),
            (
                "--meta-file=<path>",
                "Read the fit ID, name, description, doctrines, and target "
                "from a JSON file. Use instead of --interactive.",
            ),
            _OPT_DRY_RUN,
            _OPT_DB_ALIAS,
        ),
        markets=MARKETS_ANY,
        scope=(
            "Writes the shared fittings database and the selected market. "
            "--market=all writes every configured market."
        ),
        notes=(
            "Give the fit with --file or --paste, and the metadata with\n"
            "--interactive or --meta-file.\n\n"
            "With --interactive, the 'Market assignment' prompt defaults to\n"
            "the --market value. Keep that default: the fit is written to the\n"
            "--market database, or to every market when the answer is 'all'.\n\n"
            + _META_FILE_FORMAT
        ),
        examples=(
            ("Add a fit from a file and answer prompts",
             "add --file=fits/hfi.txt --interactive --market=primary"),
            ("Paste the fit instead of using a file",
             "add --paste --interactive --market=primary"),
            ("Add a fit to every market from a metadata file",
             "add --file=fits/hfi.txt --meta-file=fits/hfi.json --market=all"),
            ("Check that a fit parses before saving it",
             "add --file=fits/hfi.txt --meta-file=fits/hfi.json --dry-run"),
        ),
        run=_run_add,
    ),
    Subcommand(
        name="update",
        group=_GROUP_FITS,
        summary="Replace the items of an existing fit from EFT text",
        usage=(
            "--fit-id=<id> --file=<path> [options]",
            "--fit-id=<id> --paste [options]",
        ),
        options=(
            (
                "--fit-id=<id>",
                "Fit to update. Optional when --meta-file names the fit_id; "
                "when both are given, --fit-id wins.",
            ),
            _OPT_FILE,
            _OPT_PASTE,
            (
                "--meta-file=<path>",
                "Also replace the fit's name, description, doctrines, and "
                "target from a JSON file. Without it, they are kept as "
                "stored in the market's doctrine_fits table.",
            ),
            (
                "--no-clear",
                "Keep items that the new EFT text no longer lists. By "
                "default they are deleted.",
            ),
            _OPT_DRY_RUN,
        ),
        scope=(
            "Writes the shared fittings database and every market that "
            "already tracks the fit. It takes no market option. To put a fit "
            "into another market, use 'add' or 'assign-market'."
        ),
        notes=_META_FILE_FORMAT,
        examples=(
            ("Preview new items for fit 313",
             "update --fit-id=313 --file=fits/hfi.txt --dry-run"),
            ("Replace the items of fit 313",
             "update --fit-id=313 --file=fits/hfi.txt"),
            ("Paste the new fit instead of using a file",
             "update --fit-id=313 --paste"),
            ("Replace the items and the metadata",
             "update --file=fits/hfi.txt --meta-file=fits/hfi.json"),
            ("Add items without deleting the ones already stored",
             "update --fit-id=313 --file=fits/extra.txt --no-clear"),
        ),
        run=_run_update,
    ),
    Subcommand(
        name="remove",
        group=_GROUP_FITS,
        summary="Stop tracking a fit in a market",
        usage=("--fit-id=<id> --market=<name>",),
        options=(
            ("--fit-id=<id>", "Fit to remove. Required."),
            _OPT_DB_ALIAS,
        ),
        markets=MARKETS_ANY,
        scope=(
            "Removes the fit from the selected market only. --market=all "
            "removes it from every configured market. Doctrine links in the "
            "shared fittings database are deleted once no market tracks the "
            "fit."
        ),
        notes=(
            "Asks for confirmation for each market. A removal cannot be undone.\n"
            "The fit record and its items stay in the fittings database.\n"
            "To unlink a fit from one doctrine, use doctrine-remove-fit."
        ),
        examples=(
            ("Stop tracking fit 313 in the deployment market",
             "remove --fit-id=313 --market=deployment"),
            ("Stop tracking fit 313 everywhere",
             "remove --fit-id=313 --market=all"),
        ),
        run=_run_remove,
    ),
    Subcommand(
        name="list-fits",
        group=_GROUP_FITS,
        summary="List the fits tracked in a market",
        usage=("[options]",),
        options=(_OPT_DB_ALIAS,),
        markets=MARKETS_ANY,
        scope="Reads one market. --market=all is read as primary.",
        examples=(
            ("List the primary market's fits", "list-fits --market=primary"),
            ("List the deployment market's fits", "list-fits --market=deployment"),
        ),
        run=_run_list_fits,
    ),
    Subcommand(
        name="create-doctrine",
        group=_GROUP_DOCTRINES,
        summary="Create a new doctrine (a named group of fits)",
        usage=("",),
        options=(),
        scope=(
            "Writes the shared fittings database only. It takes no market "
            "option."
        ),
        notes=(
            "Prompts for the doctrine ID, name, and description, then asks\n"
            "for confirmation. Link fits to the new doctrine with\n"
            "doctrine-add-fit, or while adding a fit with add --interactive."
        ),
        examples=(("Create a doctrine", "create-doctrine"),),
        run=_run_create_doctrine,
    ),
    Subcommand(
        name="list-doctrines",
        group=_GROUP_DOCTRINES,
        summary="List the doctrines assigned to a market",
        usage=("[options]",),
        options=(_OPT_DB_ALIAS,),
        markets=MARKETS_ANY,
        scope="Reads one market. --market=all is read as primary.",
        notes="Doctrines whose name starts with 'zz' are hidden as retired.",
        examples=(
            ("List the primary market's doctrines",
             "list-doctrines --market=primary"),
        ),
        run=_run_list_doctrines,
    ),
    Subcommand(
        name="doctrine-add-fit",
        group=_GROUP_DOCTRINES,
        summary="Link existing fits to a doctrine",
        usage=("[options]",),
        options=(
            (
                "--fit-id=<id>[,<id>...]",
                "Fits to link, comma-separated. Omit to be prompted.",
            ),
            (
                "--target=<qty>",
                "Target offered for a fit that has no saved target. "
                "Default: 100.",
            ),
            (
                "--skip-targets",
                "Keep each fit's saved target and skip the target prompts.",
            ),
            _OPT_DB_ALIAS,
        ),
        markets=MARKETS_ANY,
        scope=(
            "Writes the shared fittings database and the selected market. "
            "--market=all writes every configured market."
        ),
        notes=(
            "The doctrine is always chosen at a prompt; there is no\n"
            "--doctrine-id option. Fits already in the doctrine are skipped.\n"
            "The 'Market assignment' prompt defaults to the --market value;\n"
            "keep that default."
        ),
        examples=(
            ("Choose the doctrine and the fits at the prompts",
             "doctrine-add-fit --market=primary"),
            ("Link three fits and keep their saved targets",
             "doctrine-add-fit --fit-id=313,314,315 --skip-targets --market=primary"),
            ("Link one fit with a suggested target of 300",
             "doctrine-add-fit --fit-id=313 --target=300 --market=deployment"),
        ),
        run=_run_doctrine_add_fit,
    ),
    Subcommand(
        name="doctrine-remove-fit",
        group=_GROUP_DOCTRINES,
        summary="Unlink fits from a doctrine",
        usage=("[options]",),
        options=(
            ("--doctrine-id=<id>", "Doctrine to unlink from. Omit to be prompted."),
            (
                "--fit-id=<id>[,<id>...]",
                "Fits to unlink, comma-separated. Omit to be prompted.",
            ),
            _OPT_DB_ALIAS,
        ),
        markets=MARKETS_ANY,
        scope=(
            "Writes one market. --market=all is read as primary. The link in "
            "the shared fittings database is deleted once no market has the "
            "fit in that doctrine."
        ),
        notes=(
            "Asks for confirmation. The fit stays in its other doctrines.\n"
            "To stop tracking a fit in a market entirely, use remove."
        ),
        examples=(
            ("Choose the doctrine and the fits at the prompts",
             "doctrine-remove-fit --market=primary"),
            ("Unlink two fits from doctrine 21 in the deployment market",
             "doctrine-remove-fit --doctrine-id=21 --fit-id=313,314 --market=deployment"),
        ),
        run=_run_doctrine_remove_fit,
    ),
    Subcommand(
        name="assign-market",
        group=_GROUP_MARKETS,
        summary="Set which markets stock a fit or a whole doctrine",
        usage=(
            "--fit-id=<id> --market=<name>",
            "--doctrine-id=<id> --market=<name>",
        ),
        options=(
            ("--fit-id=<id>", "Assign one fit."),
            ("--doctrine-id=<id>", "Assign every fit in a doctrine."),
        ),
        markets=MARKETS_ASSIGNMENT,
        scope=(
            "primary means the primary and market3 markets. deployment is "
            "separate. all means every configured market. Assignment "
            "replaces the current membership, so assigning deployment "
            "removes a primary assignment."
        ),
        notes=(
            "Give one of --fit-id or --doctrine-id. Shows a preview of every\n"
            "change and asks for confirmation before writing."
        ),
        examples=(
            ("Stock fit 313 in the deployment market only",
             "assign-market --fit-id=313 --market=deployment"),
            ("Stock every fit of doctrine 21 in every market",
             "assign-market --doctrine-id=21 --market=all"),
        ),
        run=_run_assign_market,
    ),
    Subcommand(
        name="unassign-market",
        group=_GROUP_MARKETS,
        summary="Remove a fit or a whole doctrine from a market",
        usage=(
            "--fit-id=<id> --market=<name>",
            "--doctrine-id=<id> --market=<name>",
        ),
        options=(
            ("--fit-id=<id>", "Unassign one fit."),
            ("--doctrine-id=<id>", "Unassign every fit in a doctrine."),
        ),
        markets=MARKETS_ASSIGNMENT,
        scope=(
            "primary means the primary and market3 markets. deployment is "
            "separate. all means every configured market. Other markets "
            "keep the fit."
        ),
        notes=(
            "Give one of --fit-id or --doctrine-id. Shows a preview of every\n"
            "change and asks for confirmation before writing."
        ),
        examples=(
            ("Stop stocking fit 313 in the deployment market",
             "unassign-market --fit-id=313 --market=deployment"),
            ("Stop stocking doctrine 21 in every market",
             "unassign-market --doctrine-id=21 --market=all"),
        ),
        run=_run_unassign_market,
    ),
    Subcommand(
        name="update-target",
        group=_GROUP_TARGETS,
        summary="Set the target quantity for a fit",
        usage=("--fit-id=<id> --target=<qty> [options]",),
        options=(
            ("--fit-id=<id>", "Fit to change. Required."),
            (
                "--target=<qty>",
                "New target quantity. In a terminal, omit it to be prompted. "
                "Required in scripts.",
            ),
        ),
        markets=MARKETS_ANY,
        scope=(
            "Writes the selected market. primary also writes market3. "
            "--market=all writes every configured market."
        ),
        notes=(
            "In a terminal, asks for confirmation before writing.\n"
            "A script (no terminal) writes without confirmation."
        ),
        examples=(
            ("Set fit 313's target to 300 in the deployment market",
             "update-target --fit-id=313 --target=300 --market=deployment"),
            ("Enter the target at a prompt",
             "update-target --fit-id=313 --market=primary"),
        ),
        run=_run_update_target,
    ),
    Subcommand(
        name="update-lead-ship",
        group=_GROUP_TARGETS,
        summary="Set the lead ship shown for a doctrine",
        usage=("--doctrine-id=<id> --fit-id=<id>",),
        options=(
            ("--doctrine-id=<id>", "Doctrine to change. Required."),
            ("--fit-id=<id>", "Fit whose ship becomes the lead ship. Required."),
        ),
        markets=MARKETS_ANY,
        scope=(
            "Writes the selected market only; primary does not include "
            "market3 here. --market=all writes every configured market."
        ),
        notes="Asks for confirmation before writing.",
        examples=(
            ("Make fit 313's ship the lead ship of doctrine 21",
             "update-lead-ship --doctrine-id=21 --fit-id=313 --market=all"),
        ),
        run=_run_update_lead_ship,
    ),
    Subcommand(
        name="update-friendly-name",
        group=_GROUP_TARGETS,
        summary="Set the short display name for a doctrine",
        usage=("--doctrine-id=<id> --name=<name>",),
        options=(
            ("--doctrine-id=<id>", "Doctrine to rename. Required."),
            ("--name=<name>", "Display name. Quote a name that has spaces."),
        ),
        scope="Writes every configured market. It takes no market option.",
        examples=(
            ("Show doctrine 21 as 'Hurricane'",
             'update-friendly-name --doctrine-id=21 --name="Hurricane"'),
        ),
        run=_run_update_friendly_name,
    ),
    Subcommand(
        name="populate-friendly-names",
        group=_GROUP_TARGETS,
        summary="Set display names in bulk from a JSON file",
        usage=("",),
        options=(),
        scope="Writes every configured market. It takes no market option.",
        notes=(
            "Reads doctrine_names.json from the current directory:\n"
            "  [\n"
            '    {"fit_id": 313, "doctrine_id": 21, "friendly_name": "Hurricane"},\n'
            '    {"fit_id": 550, "doctrine_id": 34, "friendly_name": "Muninn"}\n'
            "  ]\n"
            "An entry with no friendly_name is skipped."
        ),
        examples=(("Load the names", "populate-friendly-names"),),
        run=_run_populate_friendly_names,
    ),
)

SUBCOMMANDS: dict[str, Subcommand] = {s.name: s for s in _SUBCOMMAND_LIST}


# ── Help ────────────────────────────────────────────────────────

_WIDTH = 78


def _wrap(text: str, indent: str = "  ") -> list[str]:
    return textwrap.wrap(
        text, width=_WIDTH, initial_indent=indent, subsequent_indent=indent
    )


def _option_lines(options: list[tuple[str, str]]) -> list[str]:
    pad = max(len(flag) for flag, _ in options) + 2
    lines: list[str] = []
    for flag, description in options:
        lines += textwrap.wrap(
            description,
            width=_WIDTH,
            initial_indent=f"  {flag:<{pad}}",
            subsequent_indent=" " * (pad + 2),
        )
    return lines


def _command_lines(command: str) -> list[str]:
    """Wrap a long example command with shell line continuations."""
    parts = textwrap.wrap(
        command,
        width=_WIDTH - 2,
        initial_indent="  ",
        subsequent_indent="      ",
        break_on_hyphens=False,
        break_long_words=False,
    )
    return [f"{part} \\" for part in parts[:-1]] + parts[-1:]


def format_subcommand_help(spec: Subcommand) -> str:
    options = list(spec.options)
    scope = spec.scope
    if spec.markets:
        options.append((
            "--market=<name>",
            f"One of: {spec.markets}. Shorthand: --primary, --deployment, --all.",
        ))
        scope += (
            " Without a market option, a terminal session asks which market "
            "to use and a script uses primary."
        )
    options.append(("--help", "Show this help."))

    lines = [f"fit-manager {spec.name} - {spec.summary}", "", "Usage:"]
    lines += [f"  {PROG} {spec.name} {usage}".rstrip() for usage in spec.usage]
    lines += ["", "Options:", *_option_lines(options)]
    lines += ["", "Market scope:", *_wrap(scope)]
    if spec.notes:
        lines += ["", "Notes:", *(f"  {line}".rstrip() for line in spec.notes.split("\n"))]
    lines += ["", "Examples:"]
    for comment, command in spec.examples:
        lines += [f"  # {comment}", *_command_lines(f"{PROG} {command}"), ""]
    return "\n".join(lines).rstrip() + "\n"


def format_overview_help() -> str:
    lines = [
        "fit-manager - Manage doctrine fits, doctrines, targets, and market",
        "              assignment",
        "",
        "Usage:",
        f"  {PROG} <subcommand> [options]",
        f"  {PROG} <subcommand> --help",
    ]
    width = max(len(name) for name in SUBCOMMANDS) + 2
    group = None
    for spec in _SUBCOMMAND_LIST:
        if spec.group != group:
            group = spec.group
            lines += ["", f"{group}:"]
        lines.append(f"  {spec.name:<{width}}{spec.summary}")
    lines += [
        "",
        "Each subcommand has its own help page with its options, market scope,",
        "and examples:",
        f"  {PROG} add --help",
        "",
        "Every change is saved to the local replica and pushed to Turso. Run",
        "'mkts-backend sync' first so the local replica is current.",
        "",
        "Typical workflow:",
        f"  1. {PROG} create-doctrine",
        f"  2. {PROG} add --file=<path> --interactive",
        f"  3. {PROG} doctrine-add-fit",
        f"  4. {PROG} update-target --fit-id=<id> --target=<qty>",
    ]
    return "\n".join(lines) + "\n"


# ── Dispatch ────────────────────────────────────────────────────


def _rejected_option(spec: Subcommand, args: list[str]) -> str | None:
    """Return the first ``--option`` that ``spec`` does not accept, if any."""
    allowed = spec.option_names
    for arg in args:
        if arg.startswith("--") and arg[2:].split("=")[0] not in allowed:
            return arg
    return None


def _resolve_invocation(spec: Subcommand, args: list[str], market_alias: str) -> Invocation:
    """Resolve the market and its database for one call.

    Priority: ``--db-alias``, then an explicit market option, then a prompt
    (terminal) or ``primary`` (script). ``all`` is kept as given so that
    subcommands which write several markets can expand it; ``db_alias`` is
    then the primary market's database, the single-database fallback.
    """
    p = ParsedArgs(args)
    primary_db = MARKET_DB_MAP["primary"]
    if not spec.markets:
        return Invocation(p, "primary", primary_db)

    db_alias = p.get_string("db-alias")
    if db_alias:
        return Invocation(p, market_alias, db_alias)

    market = resolve_market_alias(args)
    if market is None:
        if sys.stdin.isatty():
            market = resolve_market_alias_interactive(default="primary")
        else:
            logger.warning(
                "fit-manager %s: no --market specified in non-TTY context; "
                "defaulting to 'primary'. Pass --market explicitly to silence "
                "this warning.",
                spec.name,
            )
            market = "primary"
    return Invocation(p, market, MARKET_DB_MAP.get(market, primary_db))


def handle_fit_manager(args: list[str], market_alias: str) -> bool:
    """Registry handler for ``fit-manager``. Returns ``True`` on success."""
    p = ParsedArgs(args)
    name = next((a for a in p.positionals() if a != "fit-manager"), None)

    if name is None:
        print(format_overview_help(), end="")
        if p.has_help():
            return True
        return _fail("fit-manager requires a subcommand")

    spec = SUBCOMMANDS.get(name)
    if spec is None:
        print(f"Error: unknown fit-manager subcommand '{name}'")
        suggestion = suggest_command(name, set(SUBCOMMANDS))
        if suggestion:
            print(f"Did you mean? {PROG} {suggestion}")
        print(f"Run '{PROG} --help' to list subcommands.")
        return False

    if p.has_help():
        print(format_subcommand_help(spec), end="")
        return True

    rejected = _rejected_option(spec, args)
    if rejected:
        print(f"Error: 'fit-manager {name}' does not accept {rejected}")
        if rejected[2:].split("=")[0] in _MARKET_FLAGS:
            print("\n".join(_wrap(spec.scope, indent="")))
        print(f"Run '{PROG} {name} --help' for its options.")
        return False

    try:
        return spec.run(_resolve_invocation(spec, args, market_alias))
    except ArgError as e:
        return _fail(str(e))
    except Exception as e:
        logger.exception("fit-manager %s failed", name)
        return _fail(str(e))
