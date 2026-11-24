# Evaluation of Review Comments (Commit `dbef147`)

All **10 findings are real and verified against the current code**.

One caveat on the reviewer's overarching recommendation: changing `push_touched()` fixes Finding 2, **not Finding 5**, because Finding 5 involves a clean `False` return followed by `uow.touched.discard(...)` rather than a rolled-back transaction. Several closely related call sites were also identified during code inspection and are included in the remediation below.

---

## Bugs Introduced by the Refactor

### 1. Circular import — `src/mkts_backend/db/unit_of_work.py:40`
- **Assessment:** **Agree (reproduced).** `unit_of_work.py` imports `push_db` from `cli_tools.push` at module level, executing `cli_tools/__init__.py` → `fit_manager.py` → `fit_update.py` → `ReplicaUnitOfWork` from the partially initialized module. Running `pytest tests/test_unit_of_work.py` alone fails at collection with `ImportError`. Architecturally, `db/` is a lower layer than `cli_tools/` and should not import from it.
- **Remediation:**
  - Move `push_db` to `src/mkts_backend/db/push.py` and re-export it from `src/mkts_backend/cli_tools/push.py` (preserving backwards compatibility for existing imports and monkeypatches).
  - Import `push_db` from `mkts_backend.db.push` in `unit_of_work.py`.
  - Add a standalone import regression test.

### 2. Rolled-back transaction blocks pushing earlier commits on the same alias — `fit_update.py:2036`, `unit_of_work.py:169`
- **Assessment:** **Agree.** `ReplicaUnitOfWork.tx()` adds an alias to `self.touched` only in the `else:` branch (after `engine.begin()` commits cleanly), while `push_touched()` subtracts `self.failed`. Under pyturso, rolling back tx 2 does not undo tx 1's committed writes in the local CDC queue; skipping the push strands tx 1 locally (fatal for `fittings`, which has no scheduled push). This affects `doctrine_remove_fit_command` (which also checks `not uow.push_failed` instead of `uow.ok` and ignores `fail_count`), `doctrine_add_fit_command` Stage 1, and `_execute_market_plan` Phase 3→Phase 4.
- **Key insight:** Because `self.touched` only records aliases with ≥1 committed transaction, pushing `sorted(self.touched)` still skips any alias whose sole transaction rolled back (never added to `touched`), while ensuring committed earlier transactions converge.
- **Remediation:**
  - Change `ReplicaUnitOfWork` so `skip_failed_on_push` defaults to `False` (pushing every alias in `self.touched`).
  - Update every UoW handler to check `uow.ok` (plus `fail_count == 0` where applicable) instead of `not uow.push_failed`.
  - Update `tests/test_unit_of_work.py` to test both cases (sole tx rolled back → not pushed; tx 1 committed + tx 2 rolled back → pushed, `uow.ok is False`).

### 3. Uncaught exception on a later market skips every push — `equiv_manager.py:143, :174, :281`; `fit_update.py:2110, :2146`
- **Assessment:** **Agree (plus additional call sites).** All five loops omit per-alias `try/except`, so an exception on a later market exits `with ReplicaUnitOfWork() as uow:` abnormally and `__exit__` skips all pushes, stranding earlier committed markets in local CDC.
- **Additional related spots found:**
  - `update_fit_workflow` (`utils/parse_fits.py:652-674`) and `interactive_add_fit` (`fit_update.py:498-517`): if `fittings` commits and a subsequent market write raises, the exception escapes the UoW and strands `fittings` locally.
  - `remove_fit_command` (`fit_update.py:1757-1788`): if market deletes commit and the `fittings` tx raises, the market write is stranded.
- **Remediation:**
  - Wrap each per-market iteration in `equiv_manager.py` (`_equiv_add_all`, `_equiv_remove_all`, `_equiv_find`) and `fit_update.py` (`update_friendly_name_command`, `populate_friendly_names_command`) in `try/except Exception` and return `uow.ok` (alongside command-level failure flags).
  - Isolate per-market failures in `update_fit_workflow` / `interactive_add_fit` and the `fittings` link cleanup in `remove_fit_command` so already-committed replicas (especially `fittings`) still push while the command returns `False` (or re-raises after pushing for standalone `update_fit_workflow` calls).

### 4. Failed watchlist insert commits a partial write — `utils/db_utils.py:112`
- **Assessment:** **Agree (plus a second swallow in `process_add_watchlist`).** When `conn is not None`, `add_missing_items_to_watchlist` catches exceptions and returns `False`, causing `uow.tx()` to exit cleanly and commit rows inserted before the error; `add_watchlist.py:98` then discards the alias from `uow.touched`, stranding the partial commit. Furthermore, `process_add_watchlist` (`add_watchlist.py:146-168`) **also** catches `ValueError`/`Exception` and returns `False`, which would defeat a fix applied only to `db_utils.py`.
- **Remediation:**
  - Let exceptions propagate when `conn is not None` in both `add_missing_items_to_watchlist` (`utils/db_utils.py`) and `process_add_watchlist` (`cli_tools/add_watchlist.py`), so `uow.tx()` rolls back cleanly and `add_watchlist`'s outer `except Exception` reports the failure.

### 5. Discarding an alias from `uow.touched` drops earlier committed work — `fit_update.py:2011`
- **Assessment:** **Agree (and affects all `uow.touched.discard` sites).** In `doctrine_remove_fit_command`, a second fit returning `link_removed = False` (e.g., `--fit-id=5,5`) removes `"fittings"` from `uow.touched`, dropping an earlier fit's committed delete. The same `.discard()` anti-pattern exists in `add_watchlist.py:98`, `fit_update.py:1782`, `fit_update.py:2114`, and `fit_update.py:2151` (which can collide on canonical aliases when `"wcmkt"` and `"wcmktnewkeep"` are both in `target_aliases`, or under `development` where all aliases map to `testing`).
- **Remediation:**
  - Add `auto_touch: bool = True` to `ReplicaUnitOfWork.tx(alias, *, auto_touch: bool = True)`.
  - Replace every `uow.touched.discard(...)` site with `with uow.tx(alias, auto_touch=False) as conn:` followed by `if changed: uow.mark_touched(alias)` after commit. A no-op transaction simply refrains from adding to `touched` and can never erase an earlier commit.

### 6. Wrong doctrine name written to `lead_ships` — `fit_update.py:313, :405`
- **Assessment:** **Agree.** When choosing `"new"` with an existing doctrine ID in `fit-manager add --interactive`, `_prompt_create_doctrine()` returns the user-typed `new_doctrine_name` instead of the existing doctrine's stored name. The caller overwrites `doctrine_name_map[new_id]` and writes that wrong name to `lead_ships`.
- **Remediation:**
  - In `_prompt_create_doctrine()`, query `SELECT name FROM fittings_doctrine WHERE id = :doctrine_id` inside `uow.read("fittings")` and return `(new_doctrine_id, existing_name)` when the doctrine already exists (including if `create_doctrine()` returns `False`).

### 7. Possible idle connection on `fittings` at push time — `fit_update.py:1626`
- **Assessment:** **Agree.** `DoctrineFit(...)` in `doctrine_add_fit_command` Stage 1 opens standalone `DatabaseConfig("fittings")` and `DatabaseConfig("sde")` engines in `__post_init__` without calling `.dispose()`. Under pyturso, pooled `ConnectionSync` handles waiting on GC can block WAL checkpointing during `fittings.push()`.
- **Remediation:**
  - Use `DoctrineFit.from_connections(fconn, sconn, doctrine_id, fit_id, fit_target)` inside `uow.read("sde")` and `uow.tx("fittings")` in `doctrine_add_fit_command` Stage 1.
  - Add `try ... finally: fittings_engine.dispose(); sde_engine.dispose()` to `DoctrineFit.__post_init__` (`utils/doctrine_update.py`) for any standalone callers.
  - Update `_FakeDoctrineFit` in `tests/test_management_push.py` to implement `from_connections`.

---

## Pre-Existing Bugs

### 8. `fit-manager update` resets `market_flag` to `primary` — `utils/parse_fits.py:655`
- **Assessment:** **Agree.** `upsert_doctrine_fits` defaults `market_flag="primary"` and unconditionally writes `market_flag = :market_flag` in its `UPDATE` branch, overwriting `"deployment"` or `"both"` during `fit-manager update`. (Non-interactive `fit-manager add --meta-file --market=deployment` also fails to pass `market_flag`.)
- **Remediation:**
  - Change `upsert_doctrine_fits(..., market_flag: str | None = None)` so `UPDATE` preserves the existing `market_flag` when `market_flag is None`, while `INSERT` defaults `None` to `"primary"`.
  - Accept optional `market_flag: Optional[str] = None` in `update_fit_workflow` / `_write_fit`, passing `None` from `_run_update` (preserve) and `inv.market` from `_run_add` (set on insert).

### 9. Heal work is never counted — `fit_update.py:1120`
- **Assessment:** **Agree.** `_plan_market_action` only emits `"update"`, `"remove"`, or `"skip"`. Phase 1 queues `"heal_if_needed"` for `"skip"` plans, but line 1120 checks `if p["action"] == "heal" and did_work:`, which is unreachable. Healed fits are counted as `skipped`, causing `assign-market` to exit `False` after writing and pushing.
- **Remediation:**
  - Change line 1120 to `if step_type == "heal_if_needed" and did_work:`.

---

## Rule Violation

### 10. Watchlist prep writes outside `ReplicaUnitOfWork` — `fit_update.py:1085, :1640`
- **Assessment:** **Agree.** Neither `_execute_market_plan` nor `doctrine_add_fit_command` passes `conn=` or `fittings_conn=` to `_prepare_watchlist_for_fit`, so `add_missing_items_to_watchlist` commits via a standalone `DatabaseConfig(alias)` engine outside `uow`. If Phase 3 / Stage 3 later rolls back, those watchlist rows are stranded unpushed, violating the rule that every write path coordinates through `ReplicaUnitOfWork` and ends with a `push()`.
- **Remediation:**
  - Run `_prepare_watchlist_for_fit(..., conn=conn, fittings_conn=fconn)` inside the active `uow.read("fittings")` and `uow.tx(alias)` in both `_execute_market_plan` (Phase 3) and `doctrine_add_fit_command` (Stage 3), letting `_DB_ERRORS` roll back the bucket atomically so watchlist rows are never committed outside `uow` or stranded unpushed.

---

## Assessment of the Reviewer's Overarching Recommendation

- **Agree on Finding 2:** Pushing all aliases in `self.touched` and checking `uow.ok` across all handlers cleanly fixes Finding 2 without pushing aliases whose sole transaction rolled back.
- **Correction on Finding 5:** Changing `push_touched()` does **not** fix Finding 5, because `fit_update.py:2011` removes `"fittings"` from `uow.touched` on a clean `False` return (nothing fails). Eliminating `uow.touched.discard(...)` via `tx(..., auto_touch=False)` + `uow.mark_touched(...)` is still required.
- **Agree on Finding 3:** Each multi-market loop still needs per-alias `try/except` error isolation (plus guarding committed `fittings` writes in `update_fit_workflow`, `interactive_add_fit`, and `remove_fit_command`).
