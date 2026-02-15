# Empty Tables Analysis & Removal Plan

**Database:** `wcmktprod.db`
**Date:** 2026-02-15
**Note:** `wcmktnorth2.db` has no empty tables.

---

## Summary

9 tables in `wcmktprod.db` contain zero rows. Analysis of the codebase shows most are completely unused and safe to remove. A few have ORM models defined but never referenced in queries, services, or pages.

| Table | ORM Model | Code References | Verdict |
|-------|-----------|-----------------|---------|
| `check_history` | None | None | **Remove** |
| `deployment_watchlist` | None | None | **Remove** |
| `doctrine_info` | `models.py:175` | Model only, never queried | **Remove** |
| `invtypes` | `sdemodels.py` (for sdelite.db) | All usage targets sdelite.db | **Remove from wcmktprod.db** |
| `jita_history` | None | Disabled feature comment in doctrine_status.py | **Remove** (recreate if needed) |
| `nakah_watchlist` | `models.py:159` | Model only, never queried | **Remove** |
| `region_history` | `models.py:230` + event listener | Model only, never queried | **Remove** |
| `region_orders` | `models.py:202` + property | Model only, never queried | **Remove** |
| `region_stats` | None | None | **Remove** |

---

## Detailed Analysis

### Tier 1: No Model, No References (Safe to Drop Immediately)

#### `check_history`
- **Schema:** id, type_id, last_modified, expires, etag, timestamp
- **Purpose:** Appears to be legacy ESI HTTP caching. Superseded by `esi_request_cache` table.
- **Risk:** None. Zero code references.

#### `deployment_watchlist`
- **Purpose:** Unknown. No model, no code, no documentation beyond AGENTS.md listing.
- **Risk:** None. Zero code references.

#### `region_stats`
- **Schema:** Mirrors `marketstats` structure.
- **Purpose:** Regional equivalent of `marketstats`, never implemented.
- **Risk:** None. Zero code references.

#### `jita_history`
- **Schema:** Mirrors `market_history` structure.
- **Purpose:** Intended for Jita price history caching. A disabled comment in `pages/doctrine_status.py` references this feature: "DISABLED: Jita prices - restore when backend caching implemented."
- **Risk:** Minimal. If this feature is eventually implemented, the table can be recreated by the backend (mkts_backend). No ORM model exists to preserve.

### Tier 2: ORM Model Exists but Never Used

#### `doctrine_info` (model at `models.py:175-180`)
- **Model:** `DoctrineInfo` with columns: id, doctrine_id, doctrine_name
- **Note:** `pricer_service.py` has a method named `get_doctrine_info()` but it queries the `doctrines` table, NOT `doctrine_info`. The name is misleading.
- **Risk:** None. Model is dead code.
- **Removal steps:** Drop table + delete `DoctrineInfo` class from `models.py`.

#### `nakah_watchlist` (model at `models.py:159-172`)
- **Model:** `NakahWatchlist` with same schema as `watchlist`.
- **Purpose:** Intended for Nakah market hub. Never populated or referenced.
- **Risk:** None. The sister app (Northern Supply) uses a separate database (`wcmktnorth2.db`) and does not reference this table either.
- **Removal steps:** Drop table + delete `NakahWatchlist` class from `models.py`.

#### `region_history` (model at `models.py:230-252`)
- **Model:** `RegionHistory` with event listener `populate_region_history_type_name`.
- **Purpose:** Multi-region market history. Never populated.
- **Risk:** None. Event listener never fires.
- **Removal steps:** Drop table + delete `RegionHistory` class and event listener from `models.py`.

#### `region_orders` (model at `models.py:202-227`)
- **Model:** `RegionOrders` with `resolved_type_name` property.
- **Purpose:** Multi-region market orders. Never populated.
- **Risk:** None. Property method is dead code.
- **Removal steps:** Drop table + delete `RegionOrders` class from `models.py`.

### Tier 3: Special Case

#### `invtypes` (in wcmktprod.db only)
- **Model:** `InvTypes` in `sdemodels.py` targets `sdelite.db` (51,134 rows).
- **Purpose:** Duplicate of the SDE table. All code queries `sdelite.db` via `sde_engine`.
- **Risk:** None to wcmktprod.db. The `sdemodels.py` model and `sde_repo.py` usage must NOT be touched - they serve the populated sdelite.db copy.
- **Removal steps:** Drop table from wcmktprod.db only. No code changes needed.

---

## Removal Plan

### Phase 1: Code Cleanup (models.py)

Remove the following dead ORM classes from `models.py`:

1. **Delete `DoctrineInfo`** class (lines 175-180)
2. **Delete `NakahWatchlist`** class (lines 159-172)
3. **Delete `RegionOrders`** class (lines 202-227)
4. **Delete `RegionHistory`** class (lines 230-252) and its event listener `populate_region_history_type_name` (lines 255-261)

**Do NOT modify:**
- `sdemodels.py` (`InvTypes` is used for sdelite.db)
- `services/pricer_service.py` (`get_doctrine_info()` queries `doctrines` table, not `doctrine_info`)

### Phase 2: Documentation Update

Update `CLAUDE.md` (AGENTS.md section) to remove references to dropped tables from:
- The `wcmktprod.db tables` listing
- Any mentions of `nakah_watchlist`, `doctrine_info`, `region_orders`, `region_history`, `region_stats`

### Phase 3: Database Table Drops

Tables to drop from **wcmktprod.db** (all 9):

```sql
DROP TABLE IF EXISTS check_history;
DROP TABLE IF EXISTS deployment_watchlist;
DROP TABLE IF EXISTS doctrine_info;
DROP TABLE IF EXISTS invtypes;
DROP TABLE IF EXISTS jita_history;
DROP TABLE IF EXISTS nakah_watchlist;
DROP TABLE IF EXISTS region_history;
DROP TABLE IF EXISTS region_orders;
DROP TABLE IF EXISTS region_stats;
```

**Important:** These drops must be executed on the **Turso remote database** (via the mkts_backend repo or Turso CLI), since this frontend only syncs from remote. Dropping locally would be overwritten on next sync.

### Phase 4: Verification

1. Run `uv run pytest -q` to confirm no tests break
2. Run `uv run streamlit run app.py` and verify all pages load
3. Confirm no import errors related to removed model classes
4. Verify `wcmktnorth2.db` is unaffected (it has no empty tables)

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Removing a table the backend writes to | Low | Medium | Backend (mkts_backend) doesn't populate these tables today |
| Breaking an import chain | Low | Low | Models are defined but never imported elsewhere |
| Losing future-planned functionality | Low | Low | Tables can be trivially recreated; schemas are documented here |
| Sister app dependency | Very Low | Low | Northern Supply uses wcmktnorth2.db, not wcmktprod.db tables |

**Overall risk: Low.** All 9 tables are empty and have been empty (no evidence of prior data). The 4 ORM models are dead code with zero imports outside their definition file.
