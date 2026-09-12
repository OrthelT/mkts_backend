"""Swap Caldari Navy hybrid charges for Federation Navy charges in all fits.

CCP is removing Caldari Navy hybrid charges and replacing them with the
Federation Navy equivalents. The Caldari -> Federation pairs are read from
``module_equivalents`` groups 8-22 in the primary market database, so that
table stays the single source of truth for the mapping.

Three databases-worth of changes, in this order:

1. ``wcfitting.db`` / ``fittings_type`` - the Federation charges are mostly
   absent from this catalog, and ``fittings_fittingitem.type_fk_id`` points
   into it. Each missing row is copied from its Caldari counterpart (same
   size class, so mass/volume/market group all match) with the type_id and
   type_name replaced.
2. ``wcfitting.db`` / ``fittings_fittingitem`` - the swap itself. type_id and
   type_fk_id both move to the Federation charge.
3. Each market database / ``doctrines`` - type_id and type_name move to the
   Federation charge, and the market columns are refreshed from that market's
   own ``marketstats``. This table is not rebuilt from wcfitting.db on a normal
   collection run, so without this step the swap never reaches the frontend.

Dry run by default: every statement runs inside a transaction that is rolled
back unless --apply is passed, so the preview counts are real.

Examples:
  # preview against the local .db files
  uv run python scripts/swap_navy_hybrids.py --local

  # apply to the local .db files
  uv run python scripts/swap_navy_hybrids.py --local --apply

  # apply to Turso cloud
  uv run python scripts/swap_navy_hybrids.py --remote --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from sqlalchemy import text

from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.settings_service import get_all_market_contexts

EQUIV_GROUP_LO, EQUIV_GROUP_HI = 8, 22
CALDARI, FEDERATION = "Caldari Navy", "Federation Navy"


def engine_for(alias: str, remote: bool):
    cfg = DatabaseConfig(alias)
    return cfg.remote_engine if remote else cfg.engine


def load_swap(conn) -> list[tuple[int, str, int, str]]:
    """Return [(caldari_id, caldari_name, federation_id, federation_name), ...]."""
    rows = conn.execute(
        text(
            "SELECT equiv_group_id, type_id, type_name FROM module_equivalents "
            "WHERE equiv_group_id BETWEEN :lo AND :hi"
        ),
        {"lo": EQUIV_GROUP_LO, "hi": EQUIV_GROUP_HI},
    ).fetchall()

    groups = defaultdict(list)
    for row in rows:
        groups[row.equiv_group_id].append(row)

    swap = []
    for group_id, members in sorted(groups.items()):
        caldari = [m for m in members if m.type_name.startswith(CALDARI)]
        federation = [m for m in members if m.type_name.startswith(FEDERATION)]
        if len(caldari) != 1 or len(federation) != 1:
            raise SystemExit(
                f"equiv group {group_id}: expected one {CALDARI} and one {FEDERATION} "
                f"charge, got {[m.type_name for m in members]}"
            )
        swap.append(
            (caldari[0].type_id, caldari[0].type_name,
             federation[0].type_id, federation[0].type_name)
        )
    if not swap:
        raise SystemExit(
            f"no rows in module_equivalents groups {EQUIV_GROUP_LO}-{EQUIV_GROUP_HI}"
        )
    return swap


def swap_fits(conn, swap) -> tuple[int, int, int]:
    """Update wcfitting.db. Returns (catalog rows added, items swapped, fits touched)."""
    added = swapped = 0
    fits: set[int] = set()

    for caldari_id, _, federation_id, federation_name in swap:
        item_fits = [
            r.fit_id for r in conn.execute(
                text("SELECT DISTINCT fit_id FROM fittings_fittingitem WHERE type_id = :caldari"),
                {"caldari": caldari_id},
            )
        ]
        if not item_fits:
            continue

        clashes = conn.execute(
            text(
                "SELECT DISTINCT fit_id FROM fittings_fittingitem "
                "WHERE type_id = :federation AND fit_id IN "
                "(SELECT fit_id FROM fittings_fittingitem WHERE type_id = :caldari)"
            ),
            {"caldari": caldari_id, "federation": federation_id},
        ).fetchall()
        if clashes:
            raise SystemExit(
                f"fits {[r.fit_id for r in clashes]} already contain {federation_name} "
                f"({federation_id}); swapping {caldari_id} would duplicate it"
            )

        # The Federation charge shares its size class with the Caldari charge it
        # replaces, so every physical column copies across unchanged.
        added += conn.execute(
            text(
                "INSERT INTO fittings_type ("
                "  type_id, type_name, published, mass, capacity, description, volume,"
                "  packaged_volume, portion_size, radius, graphic_id, icon_id,"
                "  market_group_id, group_id"
                ") SELECT :federation, :federation_name, published, mass, capacity,"
                "  description, volume, packaged_volume, portion_size, radius,"
                "  graphic_id, icon_id, market_group_id, group_id"
                " FROM fittings_type WHERE type_id = :caldari"
                "   AND NOT EXISTS (SELECT 1 FROM fittings_type WHERE type_id = :federation)"
            ),
            {"caldari": caldari_id, "federation": federation_id,
             "federation_name": federation_name},
        ).rowcount

        swapped += conn.execute(
            text(
                "UPDATE fittings_fittingitem SET type_id = :federation, "
                "type_fk_id = :federation WHERE type_id = :caldari"
            ),
            {"caldari": caldari_id, "federation": federation_id},
        ).rowcount
        fits.update(item_fits)

    return added, swapped, len(fits)


def swap_doctrines(conn, swap) -> int:
    """Update one market database's doctrines table. Returns rows swapped."""
    swapped = 0
    for caldari_id, _, federation_id, federation_name in swap:
        stats = conn.execute(
            text(
                "SELECT price, avg_volume, days_remaining, total_volume_remain "
                "FROM marketstats WHERE type_id = :federation"
            ),
            {"federation": federation_id},
        ).fetchone()
        stock = int(stats.total_volume_remain or 0) if stats else 0

        rows = conn.execute(
            text(
                "UPDATE doctrines SET"
                "  type_id = :federation,"
                "  type_name = :federation_name,"
                "  total_stock = :stock,"
                "  price = :price,"
                "  avg_vol = :avg_vol,"
                "  days = :days,"
                "  fits_on_mkt = CASE WHEN fit_qty > 0"
                "    THEN ROUND(:stock * 1.0 / fit_qty, 1) ELSE 0 END"
                " WHERE type_id = :caldari"
            ),
            {
                "caldari": caldari_id,
                "federation": federation_id,
                "federation_name": federation_name,
                "stock": stock,
                "price": float(stats.price or 0) if stats else 0.0,
                "avg_vol": float(stats.avg_volume or 0) if stats else 0.0,
                "days": float(stats.days_remaining or 0) if stats else 0.0,
            },
        ).rowcount

        if rows and not stats:
            print(f"  warning: no marketstats for {federation_name} ({federation_id}); "
                  f"stock and price set to 0")
        swapped += rows
    return swapped


def remaining_caldari(conn, table: str, swap) -> int:
    ids = [caldari_id for caldari_id, _, _, _ in swap]
    placeholders = ",".join(str(i) for i in ids)
    return conn.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE type_id IN ({placeholders})")
    ).scalar()


def run(alias: str, remote: bool, apply: bool, work, verify_table: str, swap):
    """Run `work` on `alias` inside a transaction, committing only when applying."""
    engine = engine_for(alias, remote)
    with engine.connect() as conn:
        transaction = conn.begin()
        result = work(conn, swap)
        left = remaining_caldari(conn, verify_table, swap)
        if left:
            transaction.rollback()
            raise SystemExit(
                f"{alias}: {left} {CALDARI} rows still in {verify_table} after the swap; "
                f"rolled back"
            )
        if apply:
            transaction.commit()
        else:
            transaction.rollback()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--local", action="store_true", help="use the local .db files")
    target.add_argument("--remote", action="store_true", help="use the Turso cloud databases")
    parser.add_argument("--apply", action="store_true",
                        help="commit the changes (default: dry run, rolled back)")
    args = parser.parse_args()

    remote = args.remote
    where = "remote (Turso)" if remote else "local"
    mode = "APPLY" if args.apply else "DRY RUN"
    markets = get_all_market_contexts()
    primary = markets["primary"].database_alias

    print(f"{mode} against {where} databases\n")

    with engine_for(primary, remote).connect() as conn:
        swap = load_swap(conn)
    print(f"{len(swap)} charge pairs from {primary}.module_equivalents "
          f"groups {EQUIV_GROUP_LO}-{EQUIV_GROUP_HI}:")
    for caldari_id, caldari_name, federation_id, federation_name in swap:
        print(f"  {caldari_name} ({caldari_id}) -> {federation_name} ({federation_id})")

    print("\nwcfitting.db")
    added, swapped, fits = run("fittings", remote, args.apply, swap_fits,
                               "fittings_fittingitem", swap)
    print(f"  fittings_type: {added} Federation charges added")
    print(f"  fittings_fittingitem: {swapped} rows swapped across {fits} fits")

    for market in markets.values():
        print(f"\n{market.database_file}  ({market.alias} - {market.name})")
        rows = run(market.database_alias, remote, args.apply, swap_doctrines,
                   "doctrines", swap)
        print(f"  doctrines: {rows} rows swapped")

    if not args.apply:
        print("\nDry run - everything above was rolled back. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
