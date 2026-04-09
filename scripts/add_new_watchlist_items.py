"""
Add new watchlist items to both primary and deployment databases.

Items requested by users (April 2026 batch).
Run with: uv run python scripts/add_new_watchlist_items.py

Requires database credentials in .env or environment variables.
"""

from mkts_backend.cli_tools.add_watchlist import process_add_watchlist
from mkts_backend.cli_tools.market_args import MARKET_DB_MAP

# Type IDs resolved from ESI API for the requested item names.
# fmt: off
NEW_ITEMS = {
    17938: "Core Probe Launcher I",
    31300: "Medium Particle Dispersion Projector I",
    31288: "Medium Particle Dispersion Augmentor I",
    31294: "Medium Particle Dispersion Augmentor II",
    31029: "Medium Kinetic Armor Reinforcer II",
    31546: "Medium Hybrid Collision Accelerator II",
    31694: "Medium Projectile Locus Coordinator I",
    31682: "Medium Projectile Collision Accelerator I",
    31670: "Medium Projectile Burst Aerator I",
    31480: "Medium Energy Locus Coordinator I",
    31444: "Medium Energy Burst Aerator I",
    31450: "Medium Energy Burst Aerator II",
    31442: "Small Energy Burst Aerator I",
    31526: "Small Hybrid Burst Aerator I",
    31538: "Small Hybrid Collision Accelerator I",
    31544: "Small Hybrid Collision Accelerator II",
    31680: "Small Projectile Collision Accelerator I",
    31668: "Small Projectile Burst Aerator I",
    3979:  "Large Proton Smartbomb II",
    33671: "Heavy Hull Maintenance Bot I",
    23709: "Medium Armor Maintenance Bot I",
    28203: "Light Shield Maintenance Bot II",
    23717: "Medium Shield Maintenance Bot I",
    22765: "Heavy Shield Maintenance Bot I",
    34595: "Entosis Link II",
    18684: "Core C-Type 100MN Afterburner",
    89617: "Expedition Command Mindlink",
    15711: "Imperial Navy EM Armor Hardener",
    89618: "Sisters Expedition Command Mindlink",
    83642: "Guri Malakim Command Mindlink",
    33406: "Caldari Navy Command Mindlink",
    89608: "Expedition Command Burst I",
    89615: "Expedition Command Burst II",
    89616: "Sisters Expedition Command Burst",
    89614: "Expedition Pinpointing Charge",
    89613: "Expedition Reach Charge",
    89612: "Expedition Strength Charge",
    3195:  "Eifyr and Co. 'Gunslinger' Surgical Strike SS-906",
    19691: "Inherent Implants 'Lancer' Small Energy Turret SE-605",
    27078: "Zainou 'Deadeye' Trajectory Analysis TA-701",
    3092:  "Zainou 'Gnome' Shield Operation SP-906",
    3239:  "Inherent Implants 'Squire' Capacitor Management EM-806",
    13878: "Shadow Serpentis 425mm Railgun",
    14126: "Domination Overdrive Injector",
    31500: "Capital Energy Metastasis Adjuster II",
    31584: "Capital Hybrid Metastasis Adjuster II",
    31702: "Capital Projectile Locus Coordinator II",
    31714: "Capital Projectile Metastasis Adjuster II",
    27483: "Legion Mjolnir Auto-Targeting Heavy Missile",
    90475: "Integrated Sensor Array",
}
# fmt: on


def main():
    type_ids_str = ",".join(str(tid) for tid in NEW_ITEMS)
    print(f"Adding {len(NEW_ITEMS)} items to watchlist (both primary and deployment):\n")
    for tid, name in NEW_ITEMS.items():
        print(f"  {tid:>6}  {name}")
    print()

    for market in ("primary", "deployment"):
        db_alias = MARKET_DB_MAP[market]
        print(f"--- Adding to {market} ({db_alias}) ---")
        success = process_add_watchlist(type_ids_str, remote=True, db_alias=db_alias)
        if success:
            print(f"Successfully added items to {market} watchlist\n")
        else:
            print(f"Failed to add items to {market} watchlist\n")


if __name__ == "__main__":
    main()
