"""
Replace the microwarpdrives on selected doctrine fits (Oct 2026 MWD rebalance).

Quad LiF Restrained and Y-T8 Compact MWDs move to Cold-Gas Enduring or Tech II
on the hulls listed in TARGETS. Every MWD of the matching size on a listed
market fit is replaced, including cargo spares on AB refits and Cold-Gas MWDs
whose target is now Tech II. Fits that are in no market's doctrine_fits are
left alone, as are afterburners and unlisted hulls.

Writes:
  - fittings_fittingitem in the shared fittings DB (type_id and type_fk_id,
    updated in place so item ids are kept)
  - doctrines in every market DB: the MWD row is renamed to the new type with
    that market's marketstats figures; rows are merged when a fit ends up with
    the new type twice

Usage:
  uv run python scripts/swap_mwds.py                 # dry run on local replicas
  uv run python scripts/swap_mwds.py --apply         # pull, write, push
  uv run python scripts/swap_mwds.py --revert=FILE   # undo an --apply

--apply saves a manifest of every item it changes before writing anything.
--revert reads that manifest, swaps each item back, and rebuilds the same
doctrines rows from the restored fits, so it still works after later hourly
market runs. Items edited since the apply are skipped and reported.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

COLD_GAS = {"5MN": 5971, "50MN": 5975, "500MN": 5945}
MWD_II = {"5MN": 440, "50MN": 12076, "500MN": 12084}

# Every MWD a listed fit may currently carry, by size:
# Quad LiF Restrained, Y-T8 Compact, Cold-Gas Enduring, Tech II.
REPLACEABLE = {
    "5MN": {35658, 5973, 5971, 440},
    "50MN": {35660, 35659, 5975, 12076},
    "500MN": {35662, 35661, 5945, 12084},
}
SIZE_OF = {tid: size for size, tids in REPLACEABLE.items() for tid in tids}

# Hull name (matches doctrine_fits.ship_name) or a specific fit_id when only
# some fits of a hull are meant.
TARGETS = {
    "500MN": {
        COLD_GAS["500MN"]: ["Apocalypse Navy Issue", "Bhaalgorn", "Vindicator", "Rokh",
                            "Raven Navy Issue", "Raven", "Scorpion"],
        MWD_II["500MN"]: ["Armageddon"],
    },
    "50MN": {
        COLD_GAS["50MN"]: ["Claymore", "Damnation", "Moa", "Huginn",
                           "Exequror Navy Issue", "Scimitar"],
        MWD_II["50MN"]: ["Broadsword", "Muninn", 463, 271, "Lachesis",
                         "Hurricane Fleet Issue", "Onyx", "Nighthawk", "Vulture", "Osprey"],
    },
    "5MN": {
        COLD_GAS["5MN"]: ["Bifrost", "Kitsune", 313, "Hyena", "Flycatcher", "Eris",
                          333, "Stork", 242, 323, "Punisher"],
        MWD_II["5MN"]: ["Deacon", "Magus", "Keres", "Jaguar", "Pontifex", "Thalia",
                        356, "Sabre", 331, 950],
    },
}


def target_for(fit_id: int, ship_name: str, size: str) -> int | None:
    """Return the new MWD type for this fit and MWD size, or None if unlisted."""
    for new_type, entries in TARGETS[size].items():
        if fit_id in entries or ship_name in entries:
            return new_type
    return None


def build_plan(fit_ships: dict[int, str], items: list[tuple]) -> list[dict]:
    """Return one change per fitting item that must be swapped.

    fit_ships maps each market fit_id to its hull name. items are
    (item_id, fit_id, flag, quantity, type_id) rows.
    """
    plan = []
    for item_id, fit_id, flag, quantity, type_id in items:
        size = SIZE_OF.get(type_id)
        if size is None or fit_id not in fit_ships:
            continue
        new_type = target_for(fit_id, fit_ships[fit_id], size)
        if new_type is None or new_type == type_id:
            continue
        plan.append({
            "item_id": item_id, "fit_id": fit_id, "ship_name": fit_ships[fit_id],
            "flag": flag, "quantity": quantity,
            "old_type_id": type_id, "new_type_id": new_type,
        })
    return sorted(plan, key=lambda c: (c["ship_name"], c["fit_id"], c["item_id"]))


def unmatched_targets(fit_ships: dict[int, str], items: list[tuple]) -> list[str]:
    """Return TARGETS entries that match no market fit carrying an MWD of that size."""
    present = {(fit_id, SIZE_OF[t]) for _, fit_id, _, _, t in items
               if t in SIZE_OF and fit_id in fit_ships}
    missing = []
    for size, by_type in TARGETS.items():
        for entries in by_type.values():
            for entry in entries:
                hit = any(s == size and (f == entry or fit_ships[f] == entry)
                          for f, s in present)
                if not hit:
                    missing.append(f"{size} {entry}")
    return missing


def read_market_fits(market_dbs: dict, fittings_db, sde_db) -> dict[int, str]:
    """Return {fit_id: hull name} for every fit in any market's doctrine_fits.

    The hull comes from fittings_fitting, not doctrine_fits.ship_name: some
    doctrine_fits rows name the wrong hull for their fit_id.
    """
    fit_ids = set()
    for db in market_dbs.values():
        with db.engine.connect() as conn:
            fit_ids.update(int(r[0]) for r in conn.execute(
                text("SELECT DISTINCT fit_id FROM doctrine_fits")))
    hulls = fit_hulls(fittings_db, fit_ids)
    names = {t: info["type_name"] for t, info in read_type_info(sde_db, set(hulls.values())).items()}
    return {fit_id: names[ship] for fit_id, ship in hulls.items()}


def read_mwd_items(fittings_db) -> list[tuple]:
    ids = ", ".join(str(t) for t in SIZE_OF)
    with fittings_db.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, fit_id, flag, quantity, type_id FROM fittings_fittingitem "
            f"WHERE type_id IN ({ids})"
        )).all()
    return [tuple(r) for r in rows]


def read_type_info(sde_db, type_ids) -> dict[int, dict]:
    ids = ", ".join(str(int(t)) for t in type_ids)
    with sde_db.engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT typeID, typeName, groupID, groupName, categoryID, categoryName "
            f"FROM sdetypes WHERE typeID IN ({ids})"
        )).all()
    info = {
        int(r[0]): {"type_name": r[1], "group_id": int(r[2]), "group_name": r[3],
                    "category_id": int(r[4]), "category_name": r[5]}
        for r in rows
    }
    missing = set(int(t) for t in type_ids) - set(info)
    if missing:
        raise RuntimeError(f"type_ids missing from SDE: {sorted(missing)}")
    return info


def swap_items(fittings_db, swaps: list[tuple[int, int, int]]) -> list[int]:
    """Set each (item_id, from_type, to_type) item to to_type in one transaction.

    An item whose type is no longer from_type is left alone; its id is returned.
    """
    skipped = []
    stmt = text(
        "UPDATE fittings_fittingitem SET type_id = :to_type, type_fk_id = :to_type "
        "WHERE id = :item_id AND type_id = :from_type"
    )
    with fittings_db.engine.begin() as conn:
        for item_id, from_type, to_type in swaps:
            result = conn.execute(
                stmt, {"item_id": item_id, "from_type": from_type, "to_type": to_type}
            )
            if result.rowcount != 1:
                skipped.append(item_id)
    return skipped


def fit_hulls(fittings_db, fit_ids) -> dict[int, int]:
    """Return {fit_id: hull type_id} from fittings_fitting."""
    with fittings_db.engine.connect() as conn:
        rows = conn.execute(text("SELECT id, ship_type_type_id FROM fittings_fitting")).all()
    return {int(f): int(s) for f, s in rows if f in fit_ids}


def fit_quantities(fittings_db, involved: dict[int, set[int]]) -> dict[int, dict[int, int]]:
    """Return {fit_id: {type_id: total quantity}} for each fit's involved MWD types."""
    qtys = {fit_id: dict.fromkeys(types, 0) for fit_id, types in involved.items()}
    stmt = text(
        "SELECT type_id, SUM(quantity) FROM fittings_fittingitem "
        "WHERE fit_id = :fit_id GROUP BY type_id"
    )
    with fittings_db.engine.connect() as conn:
        for fit_id, types in involved.items():
            for type_id, qty in conn.execute(stmt, {"fit_id": fit_id}):
                if type_id in types:
                    qtys[fit_id][int(type_id)] = int(qty)
    return qtys


def refresh_doctrines(market_db, qtys: dict[int, dict[int, int]],
                      type_info: dict[int, dict], hulls: dict[int, int]) -> int:
    """Make doctrines rows for the given fits and MWD types match qtys.

    A row whose type is no longer fitted is renamed to a newly fitted type
    (keeping its id) or deleted; a newly fitted type with no row to rename
    gets a new row. Stock and price figures come from marketstats, as in
    calculate_doctrine_stats. Only rows whose ship_id matches the fit's hull
    (hulls) are touched; a market can hold stale rows for a reused fit_id.
    Fits with no matching rows are skipped. Returns the number of fits changed.
    """
    now = datetime.now(timezone.utc).isoformat()
    changed = 0
    with market_db.engine.begin() as conn:
        for fit_id, by_type in qtys.items():
            match = {"f": fit_id, "ship": hulls[fit_id]}
            base = conn.execute(text(
                "SELECT ship_id, ship_name, hulls FROM doctrines "
                "WHERE fit_id = :f AND ship_id = :ship LIMIT 1"
            ), match).first()
            if base is None:
                continue
            rows = conn.execute(text(
                "SELECT id, type_id FROM doctrines WHERE fit_id = :f AND ship_id = :ship"
            ), match).all()
            have = {int(r.type_id): int(r.id) for r in rows if int(r.type_id) in by_type}
            stale = [have[t] for t, q in by_type.items() if q == 0 and t in have]
            new = [t for t, q in by_type.items() if q > 0 and t not in have]

            for row_id, type_id in zip(stale, new):
                conn.execute(text("UPDATE doctrines SET type_id = :t WHERE id = :id"),
                             {"t": type_id, "id": row_id})
            for row_id in stale[len(new):]:
                conn.execute(text("DELETE FROM doctrines WHERE id = :id"), {"id": row_id})
            for type_id in new[len(stale):]:
                conn.execute(text(
                    "INSERT INTO doctrines (fit_id, ship_id, ship_name, hulls, type_id, fit_qty) "
                    "VALUES (:f, :ship_id, :ship_name, :hulls, :t, :qty)"
                ), {"f": fit_id, "ship_id": base.ship_id, "ship_name": base.ship_name,
                    "hulls": base.hulls, "t": type_id, "qty": by_type[type_id]})

            for type_id, qty in by_type.items():
                if qty == 0:
                    continue
                stats = conn.execute(text(
                    "SELECT total_volume_remain, price, avg_volume, days_remaining, "
                    "last_update FROM marketstats WHERE type_id = :t"
                ), {"t": type_id}).first()
                stock = int(stats.total_volume_remain or 0) if stats else 0
                conn.execute(text(
                    "UPDATE doctrines SET type_name = :type_name, fit_qty = :qty, "
                    "total_stock = :stock, fits_on_mkt = :fits, price = :price, "
                    "avg_vol = :avg_vol, days = :days, group_id = :group_id, "
                    "group_name = :group_name, category_id = :category_id, "
                    "category_name = :category_name, timestamp = :ts "
                    "WHERE fit_id = :f AND ship_id = :ship AND type_id = :t"
                ), {
                    **type_info[type_id], **match, "t": type_id, "qty": qty,
                    "stock": stock, "fits": round(stock / qty, 1),
                    "price": float(stats.price or 0) if stats else 0.0,
                    "avg_vol": float(stats.avg_volume or 0) if stats else 0.0,
                    "days": float(stats.days_remaining or 0) if stats else 0.0,
                    "ts": (stats.last_update if stats and stats.last_update else now),
                })
            if stale or new:
                changed += 1
    return changed


def run_swaps(changes: list[dict], fittings_db, market_dbs: dict, sde_db,
              reverse: bool = False) -> bool:
    """Swap fitting items (old -> new, or back with reverse) and update doctrines.

    Pushes the fittings DB and each market DB after its write. Returns True
    when every item swapped and every push succeeded.
    """
    ok = True
    src, dst = ("new_type_id", "old_type_id") if reverse else ("old_type_id", "new_type_id")
    swaps = [(c["item_id"], c[src], c[dst]) for c in changes]
    skipped = swap_items(fittings_db, swaps)
    if skipped:
        ok = False
        print(f"WARNING: {len(skipped)} items no longer hold the expected type "
              f"and were left unchanged: {skipped}")
    print(f"fittings: swapped {len(swaps) - len(skipped)} items")
    ok = _push(fittings_db) and ok

    involved = defaultdict(set)
    for c in changes:
        involved[c["fit_id"]].update((c["old_type_id"], c["new_type_id"]))
    qtys = fit_quantities(fittings_db, involved)
    hulls = fit_hulls(fittings_db, involved)
    type_info = read_type_info(sde_db, {t for ts in involved.values() for t in ts})

    for alias, db in market_dbs.items():
        try:
            changed = refresh_doctrines(db, qtys, type_info, hulls)
        except Exception as exc:  # report and continue with the other markets
            ok = False
            print(f"ERROR: {alias}: doctrines not updated: {exc!r}")
            continue
        print(f"{alias}: updated doctrines for {changed} fits")
        ok = _push(db) and ok
    return ok


def _push(db) -> bool:
    """Push one replica; on failure the write stays queued locally."""
    try:
        db.push()
    except Exception as exc:
        print(f"ERROR: {db.alias}: push failed, write is committed locally only: {exc!r}\n"
              f"  Retry: uv run python -c \"from mkts_backend.config.db_config import "
              f"DatabaseConfig; DatabaseConfig('{db.alias}').push()\"")
        return False
    print(f"{db.alias}: pushed")
    return True


def print_plan(plan: list[dict], type_info: dict[int, dict], missing: list[str]) -> None:
    for c in plan:
        print(f"{c['fit_id']:>4}  {c['ship_name'][:22]:<22} {c['flag']:<9} "
              f"{type_info[c['old_type_id']]['type_name']:<42} -> "
              f"{type_info[c['new_type_id']]['type_name']}")
    print(f"\n{len(plan)} items on {len({c['fit_id'] for c in plan})} fits")
    if missing:
        print(f"\nNo market fit matched these targets: {', '.join(missing)}")


def open_databases():
    from mkts_backend.config.db_config import DatabaseConfig
    from mkts_backend.config.settings_service import get_all_market_contexts

    markets = {ctx.database_alias: DatabaseConfig(ctx.database_alias)
               for ctx in get_all_market_contexts().values()}
    return DatabaseConfig("fittings"), markets, DatabaseConfig("sde")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="pull, write and push")
    mode.add_argument("--revert", metavar="FILE", help="undo using an --apply manifest")
    args = parser.parse_args(argv)

    fittings_db, market_dbs, sde_db = open_databases()
    if args.apply or args.revert:
        for db in (fittings_db, *market_dbs.values()):
            db.pull()

    if args.revert:
        manifest = json.loads(Path(args.revert).read_text())
        changes = manifest["changes"]
        print(f"Reverting {len(changes)} items from {args.revert}")
        return 0 if run_swaps(changes, fittings_db, market_dbs, sde_db, reverse=True) else 1

    fit_ships = read_market_fits(market_dbs, fittings_db, sde_db)
    items = read_mwd_items(fittings_db)
    plan = build_plan(fit_ships, items)
    type_info = read_type_info(sde_db, SIZE_OF)
    print_plan(plan, type_info, unmatched_targets(fit_ships, items))

    if not args.apply:
        print("\nDry run against local replicas; --apply pulls first and re-plans.")
        return 0
    if not plan:
        print("Nothing to change.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = Path(f"mwd_swap_manifest_{stamp}.json")
    manifest_path.write_text(json.dumps(
        {"created": stamp, "markets": list(market_dbs), "changes": plan}, indent=2
    ))
    print(f"\nManifest saved to {manifest_path.resolve()}")
    print(f"Undo with: uv run python scripts/swap_mwds.py --revert={manifest_path}")
    return 0 if run_swaps(plan, fittings_db, market_dbs, sde_db) else 1


if __name__ == "__main__":
    sys.exit(main())
