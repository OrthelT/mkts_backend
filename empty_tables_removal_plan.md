# Unified Empty Tables Removal Plan

**Date:** 2026-02-15
**Database:** `wcmktprod.db` (shared by both repos via Turso sync)
**Repos affected:**
- **Backend:** `mkts_backend` (`~/workspace/github/mkts_backend/`)
- **Frontend:** `wcmkts_new` (`~/workspace/github/wcmkts_new/`)

---

## Summary

9 empty tables to remove, 1 to keep. Both repos share `wcmktprod.db` — the backend is the authoritative writer, and the frontend syncs from Turso. Code cleanup is needed in **both** repos, and the SQL drops must be executed on the **Turso remote** (via the backend) so the frontend picks up the changes on next sync.

| Table | Backend models.py | Frontend models.py | Active Code | Verdict |
|-------|-------------------|-------------------|-------------|---------|
| `check_history` | — | — | None | **Remove** |
| `deployment_watchlist` | — | — | Archive only (backend) | **Remove** |
| `doctrine_info` | `DoctrineInfo` :152 | `DoctrineInfo` :175 | Model only, never queried | **Remove** |
| `invtypes` | — | — (sdemodels.py for sdelite.db) | sdelite.db only | **Remove from wcmktprod.db** |
| `jita_history` | — | — | Deprecated code + disabled comment (frontend) | **Remove** |
| `module_equivalents` | `ModuleEquivalents` :196 | `ModuleEquivalents` :273 | Recently added (WIP) | **KEEP** |
| `nakah_watchlist` | — | `NakahWatchlist` :159 | Model only, never queried | **Remove** |
| `region_history` | — | `RegionHistory` :230 + event listener | Broken backend test + dead frontend model | **Remove** |
| `region_orders` | — | `RegionOrders` :202 + property | AGENTS.md docs only | **Remove** |
| `region_stats` | — | — | Archive docs only | **Remove** |

---

## Cross-Repo Comparison

The frontend plan (`docs/frontend_empty_tables_plan.md`) found **4 ORM models** for empty tables in the frontend `models.py` that the backend doesn't have:

| Model Class | Frontend `models.py` | Backend `models.py` | Note |
|-------------|---------------------|---------------------|------|
| `DoctrineInfo` | Line 175 | Line 152 | Both repos — dead in both |
| `NakahWatchlist` | Line 159 | Not present | Frontend only |
| `RegionOrders` | Line 202 (+ `resolved_type_name` property) | Not present | Frontend only |
| `RegionHistory` | Line 230 (+ event listener line 255) | Not present | Frontend only; backend has broken test |

The frontend also flagged that `invtypes` has a model in `sdemodels.py` targeting `sdelite.db` — that model must **NOT** be touched.

---

## Execution Plan

### Step 1: Backend Code Cleanup (`mkts_backend`)

**File: `src/mkts_backend/db/models.py`**
- Delete `DoctrineInfo` class (lines 152-156)

**File: `tests/test_region_history.py`**
- Delete entire file (180 lines — imports nonexistent `RegionHistory` model, test is broken)

**File: `tests/test_utils.py`**
- Remove line 6: `from models import RegionHistory` (also broken import)

**File: `AGENTS.md`**
- Line 73: Remove `fetch_jita_history()` reference
- Line 105: Remove `get_region_orders()` reference
- Line 740: Remove `region_orders: Regional market data`
- Line 743: Remove `doctrine_info: Doctrine metadata`

**Do NOT modify:**
- `archive/` directory (already archived, low priority)
- `esi/esi_requests.py` `fetch_region_orders()` function (it fetches ESI data; keep for potential future use)
- `ModuleEquivalents` model (recently added, WIP)

### Step 2: Frontend Code Cleanup (`wcmkts_new`)

**File: `models.py`**
- Delete `NakahWatchlist` class (lines 159-172)
- Delete `DoctrineInfo` class (lines 175-179)
- Delete `RegionOrders` class (lines 202-227, includes `resolved_type_name` property)
- Delete `RegionHistory` class (lines 230-252, includes `resolved_type_name` property)
- Delete `populate_region_history_type_name` event listener (lines 255-261)
- Remove `event` from the `sqlalchemy` import on line 1 (if no other event listeners remain)
- Remove `get_type_name` import from line 3 (if no other usage remains)

**File: `AGENTS.md`**
- Line 147: Remove `region_orders: Regional market orders`
- Line 148: Remove `region_history: Regional historical data`
- Line 151: Remove `doctrine_info: Additional doctrine metadata`
- Line 155: Remove `nakah_watchlist: Nakah-specific watchlist`

**Do NOT modify:**
- `sdemodels.py` (`InvTypes` targets `sdelite.db`, not `wcmktprod.db`)
- `services/pricer_service.py` (`get_doctrine_info()` queries `doctrines` table, not `doctrine_info`)
- `ModuleEquivalents` model (recently added, WIP)

### Step 3: Database Table Drops (Turso Remote)

Execute on the **Turso remote database** so both local copies sync the changes:

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

**Tables removed:** 9
**Tables kept:** `module_equivalents` (WIP feature)
**Post-drop table count:** 13 (down from 22)

### Step 4: Verification

**Backend (`mkts_backend`):**
1. `uv run pytest -q` — confirm no test regressions
2. `uv run mkts-backend` — verify CLI still functions
3. Confirm `wcmktprod.db` has 13 tables

**Frontend (`wcmkts_new`):**
1. `uv run pytest -q` — confirm no test regressions
2. `uv run streamlit run app.py` — verify all pages load
3. Confirm no import errors from removed model classes

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Removing a table the backend writes to | Very Low | Medium | Neither repo populates these tables today |
| Breaking an import chain | Low | Low | Models are defined but never imported outside their definition files |
| Losing future-planned functionality | Low | Low | Tables are trivially recreatable; schemas are preserved in this document |
| Frontend sync issues | Very Low | Low | Drops executed on Turso remote; frontend syncs automatically |
| `fetch_region_orders()` left without a table | None | None | Function fetches ESI data and returns it — doesn't depend on the table |

**Overall risk: Low.** All 9 tables are empty with no evidence of ever having contained data. The ORM models are dead code with zero active query usage.

---

## Revert Procedure

If something goes wrong after the drops, restore from Turso cloud backups:

```bash
# 1. Restore wcmktprod from the backup database
turso db shell wcmktprodbak ".dump" > /tmp/wcmktprod_backup.sql
turso db shell wcmktprod < /tmp/wcmktprod_backup.sql

# 2. Re-sync local copies in both repos
#    Backend:
cd ~/workspace/github/mkts_backend
uv run python -c "from mkts_backend.config.config import DatabaseConfig; db = DatabaseConfig('wcmktprod'); db.sync()"

#    Frontend (syncs automatically on next Streamlit run, or manually):
cd ~/workspace/github/wcmkts_new
uv run python -c "from config import get_engine; get_engine()"

# 3. Revert code changes via git
cd ~/workspace/github/mkts_backend && git checkout -- src/mkts_backend/db/models.py AGENTS.md
cd ~/workspace/github/wcmkts_new && git checkout -- models.py AGENTS.md
# Note: deleted test files would need to be restored from git as well:
#   cd ~/workspace/github/mkts_backend && git checkout -- tests/test_region_history.py tests/test_utils.py
```

**Turso backup databases available:** `wcmktprodbak`, `wcmktnorth2bak`

---

## Additional Finding: wcmktnorth2.db

- Backend `settings.toml` configures `wcmktnorth2.db` for the deployment market (B-9C24 Keepstar)
- **This file does not exist** in the backend repo
- Frontend repo has `wcmktnorth2.db` (with data, no empty tables per frontend analysis)
- A 0-byte `wcnorth2.db` exists in the backend — likely a stale/misconfigured artifact
- **Recommendation:** Investigate separately whether the backend deployment market config needs updating
