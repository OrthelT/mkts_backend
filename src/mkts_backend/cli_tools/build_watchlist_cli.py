"""CLI dispatch for ``build-watchlist [add|remove|mirror|sync]``.

Verbs:
    add    — write items to build_watchlist after SDE lookup
    remove — delete items from build_watchlist
    mirror — pull missing items from wcmktprod.watchlist (reconciliation)
    sync   — refresh local buildcost mirror from remote (db.sync())

``sync`` is the standard libsql remote→local pull. ``mirror`` is the
wcmktprod-into-buildcost reconciliation. They are deliberately separate
verbs so they don't collide semantically with the rest of the CLI, where
``sync`` always means ``DatabaseConfig.sync()``.
"""

from __future__ import annotations

import csv

from mkts_backend.builder_costs.sde_lookup import lookup_type_ids_by_name
from mkts_backend.builder_costs.watchlist_sync import (
    AddResult,
    RemoveResult,
    SyncResult,
    add_to_build_watchlist,
    remove_from_build_watchlist,
    sync_from_market,
)
from mkts_backend.cli_tools.arg_utils import ParsedArgs
from mkts_backend.cli_tools.cli_help import (
    display_build_watchlist_add_help,
    display_build_watchlist_help,
    display_build_watchlist_mirror_help,
    display_build_watchlist_remove_help,
    display_build_watchlist_sync_help,
)
from mkts_backend.cli_tools.prompter import get_multiline_input
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.logging_config import configure_logging

logger = configure_logging(__name__)


def handle_build_watchlist(args: list[str]) -> bool:
    p = ParsedArgs(args)

    subcommand = next(
        (a for a in p.positionals() if a != "build-watchlist"),
        None,
    )

    if p.has_help() and not subcommand:
        display_build_watchlist_help()
        return True

    if not subcommand:
        print(
            "Error: build-watchlist requires a subcommand (add | remove | mirror | sync)"
        )
        display_build_watchlist_help()
        return False

    handlers = {
        "add": _handle_add,
        "remove": _handle_remove,
        "mirror": _handle_mirror,
        "sync": _handle_sync,
    }
    handler = handlers.get(subcommand)
    if handler is None:
        suggestion = _suggest(subcommand, handlers.keys())
        print(f"Error: unknown subcommand '{subcommand}'.{suggestion}")
        display_build_watchlist_help()
        return False

    return handler(p)


def _handle_add(p: ParsedArgs) -> bool:
    if p.has_help():
        display_build_watchlist_add_help()
        return True

    type_ids = _resolve_type_ids(p)
    if type_ids is None:
        return False

    force = p.has_flag("force")
    buildcost_db = DatabaseConfig("buildcost")
    sde_db = DatabaseConfig("sde")
    result = add_to_build_watchlist(buildcost_db, sde_db, type_ids, force=force)
    _print_add_summary(result)
    if result.added > 0 and not p.has_flag("no-sync"):
        _push_buildcost_changes(buildcost_db)
    return True


def _handle_remove(p: ParsedArgs) -> bool:
    if p.has_help():
        display_build_watchlist_remove_help()
        return True

    type_ids = _resolve_type_ids(p)
    if type_ids is None:
        return False

    buildcost_db = DatabaseConfig("buildcost")
    result = remove_from_build_watchlist(buildcost_db, type_ids)
    _print_remove_summary(result)
    if result.removed > 0 and not p.has_flag("no-sync"):
        _push_buildcost_changes(buildcost_db)
    return True


def _push_buildcost_changes(buildcost_db: DatabaseConfig) -> None:
    """Push the local buildcost write up to Turso. Best-effort.

    pyturso writes land locally first, so without this the change stays on
    this machine until some later run pushes.
    """
    try:
        buildcost_db.push()
        print("Pushed buildcost changes to Turso")
    except Exception as exc:
        logger.warning(f"buildcost push failed (local write succeeded): {exc}")
        print(f"Warning: buildcost push failed: {exc}")


def _handle_mirror(p: ParsedArgs) -> bool:
    if p.has_help():
        display_build_watchlist_mirror_help()
        return True

    if (
        p.get_string("type_id", "type-id")
        or p.get_string("file")
        or p.has_flag("paste")
    ):
        print("Error: 'mirror' takes no item flags; it reads from the primary market.")
        return False

    buildcost_db = DatabaseConfig("buildcost")
    sde_db = DatabaseConfig("sde")
    primary_db = DatabaseConfig("primary")

    try:
        primary_db.sync()
    except Exception as exc:
        # Reconciling against a stale mirror would silently miss recent
        # wcmktprod additions; abort so the user knows the run was a no-op.
        logger.error(f"Pre-sync of {primary_db.alias} failed: {exc}")
        print(
            f"Error: could not sync {primary_db.alias} local mirror; aborting mirror."
        )
        return False

    try:
        buildcost_db.sync()
    except Exception as exc:
        logger.warning(f"Pre-sync of {buildcost_db.alias} failed: {exc}")

    result = sync_from_market(buildcost_db, sde_db, primary_db)
    _print_mirror_summary(result)
    if result.added > 0 and not p.has_flag("no-sync"):
        _push_buildcost_changes(buildcost_db)
    return True


def _handle_sync(p: ParsedArgs) -> bool:
    """Pull the local buildcost mirror from the remote (libsql sync).

    No reconciliation, no remote writes — just ``DatabaseConfig.sync()``.
    Use this when another process (or another machine) has written to the
    buildcost remote and the local mirror needs to catch up.
    """
    if p.has_help():
        display_build_watchlist_sync_help()
        return True

    buildcost_db = DatabaseConfig("buildcost")
    print("Syncing buildcost local mirror from remote…")
    try:
        buildcost_db.sync()
    except Exception as exc:
        logger.error(f"buildcost sync failed: {exc}")
        print(f"Error: buildcost sync failed: {exc}")
        return False
    print(f"Synced buildcost local mirror ({buildcost_db.path})")
    return True


def _resolve_type_ids(p: ParsedArgs) -> list[int] | None:
    """Resolve the input type_ids from --type_id / --file / --paste.

    Returns None if the inputs are invalid or empty (already printed an error).
    """
    type_ids_str = p.get_string("type_id", "type-id")
    file_path = p.get_string("file")
    paste = p.has_flag("paste")

    sources = sum(1 for x in (type_ids_str, file_path, paste) if x)
    if sources > 1:
        print("Error: --type_id, --file, and --paste are mutually exclusive")
        return None
    if sources == 0:
        print("Error: provide --type_id=…, --file=…, or --paste")
        return None

    if type_ids_str:
        return _parse_csv_ids(type_ids_str)

    if file_path:
        return _read_type_ids_from_csv(file_path)

    pasted = get_multiline_input()
    if not pasted:
        print("Error: no paste input provided")
        return None
    return _parse_pasted_lines(pasted.splitlines())


def _parse_csv_ids(value: str) -> list[int] | None:
    try:
        return [int(s.strip()) for s in value.split(",") if s.strip()]
    except ValueError as exc:
        print(f"Error: invalid type_id value: {exc}")
        return None


def _read_type_ids_from_csv(path: str) -> list[int] | None:
    try:
        type_ids: list[int] = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                val = (row.get("type_ids") or row.get("type_id") or "").strip()
                if val:
                    type_ids.append(int(val))
        if not type_ids:
            print(f"Error: no type_ids found in {path}")
            return None
        return type_ids
    except (OSError, ValueError) as exc:
        print(f"Error reading {path}: {exc}")
        return None


def _parse_pasted_lines(lines: list[str]) -> list[int] | None:
    cleaned = [line.strip() for line in lines if line.strip()]
    if not cleaned:
        print("Error: paste input was empty")
        return None
    sde_db = DatabaseConfig("sde")
    type_ids, unresolved = lookup_type_ids_by_name(cleaned, sde_db)
    if unresolved:
        print(
            f"Warning: {len(unresolved)} name(s) not found in SDE: "
            f"{unresolved[:10]}{'…' if len(unresolved) > 10 else ''}"
        )
    if not type_ids:
        print("Error: no valid type names found in paste input")
        return None
    return type_ids


def _suggest(name: str, choices) -> str:
    matches = [c for c in choices if c.startswith(name[:1])]
    if not matches:
        return ""
    return f" Did you mean: {', '.join(sorted(matches))}?"


def _print_add_summary(result: AddResult) -> None:
    print(f"build-watchlist add: {result.added} added")
    if result.skipped:
        print(f"  {len(result.skipped)} skipped (no blueprint): {result.skipped[:10]}")
    if result.invalid:
        print(f"  {len(result.invalid)} invalid (not in SDE): {result.invalid[:10]}")


def _print_remove_summary(result: RemoveResult) -> None:
    print(f"build-watchlist remove: {result.removed} removed")
    if result.not_present:
        print(
            f"  {len(result.not_present)} not in build_watchlist: "
            f"{result.not_present[:10]}"
        )


def _print_mirror_summary(result: SyncResult) -> None:
    print(
        f"build-watchlist mirror: {result.market_size} in market, "
        f"{result.already_present} already present, {result.added} added"
    )
    if result.skipped:
        print(f"  {len(result.skipped)} skipped (no blueprint): {result.skipped[:10]}")
