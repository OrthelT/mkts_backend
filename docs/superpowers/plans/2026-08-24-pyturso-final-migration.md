# pyturso Final Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining cutover-safety gaps in both pyturso worktrees so the backend and frontend can be switched from test remotes to production remotes through one controlled settings-and-secrets cutover, followed by a mandatory cold bootstrap.

**Architecture:** Three independent workstreams that converge on one cutover runbook. (1) Replica-metadata handling in both repos gains a shape classifier, a tested repair path for invalid metadata, and a fail-closed remote-identity guard. (2) Every backend management command that writes reports the database aliases it touched to an explicit command-level `push()` boundary, so a command that reports success has actually reached Turso. (3) CI database paths stop being hardcoded and are derived from `SettingsService`, so filename changes flow from settings while remote targets remain controlled by secrets.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0.x, pyturso 0.7.2 (`sqlite+turso_sync` dialect), pytest, Streamlit (frontend), GitHub Actions.

**Spec:** `docs/migration-review.md` (Codex-verified findings + owner decisions marked `UC:`), `docs/migration-prep.md` (original brief).

## Codex plan review — 2026-08-25

This review found the plan directionally sound but **not execution-ready as originally written**. The amendments below are normative and supersede conflicting examples later in the document.

### Blocking corrections incorporated here

1. **Remote identity must be enforced, not merely printable.** The original Task 3 added `remote_matches_metadata()` but never called it from `engine`, `push()`, `pull()`, `sync`, or `verify_db_exists()`. A valid test replica could therefore still be opened and pushed while production credentials were configured. A mismatch now fails closed before any connection. It is never repaired with the `-info`-only strategy.
2. **Phase 4 needs real command boundaries.** Several proposed edits referenced `db`, `fittings_db`, or `market_db` variables that do not exist at those call sites. The writers construct fresh `DatabaseConfig` instances internally, and `update_fit_workflow()` can run repeatedly inside one CLI invocation. Each management command must explicitly collect the aliases it touched and push each distinct alias once after its final write. Tests must invoke the real public function signatures; placeholder or invented `main()` calls are not acceptable.
3. **`[shared.testing]` is never part of a production-wide sync.** It remains test-only, keeps `wcmkttest.db`, and is excluded from default `sync`, CI caches, remote-host production assertions, and cutover filename edits. Add an explicit `--include-testing` flag for deliberate sandbox use.
4. **Required credentials cannot be silently skipped.** For every required route, a missing URL *or* token makes `sync` fail. Optional routes warn and skip. Market routes follow the same rule.
5. **The cutover needs control-plane and rollback gates.** Updating local `.env` and `.streamlit/secrets.toml` does not update GitHub Actions secrets or a hosted Streamlit deployment. The production control planes and PR landing sequence are now recorded below; the remaining rollback distinction is code state versus already-pushed Turso data.
6. **Stale starting-state claims are corrected.** The backend already declares `pyturso>=0.7.1` and `sqlalchemy>=2.0.45`; Task 16 must bump pyturso to `>=0.7.2`, not add a supposedly missing dependency. `remote_engine` still has many read and compatibility consumers, so its removal is deferred to a separately scoped audit rather than hidden inside Task 12.
7. **"Do not push the failed alias" is not rollback.** Many helpers commit independently. If a later step fails, leaving the alias unpushed only leaves partial changes in CDC for the next unrelated `push()` to publish. Each logical command must use one local transaction per database (helpers accept a caller-owned `Connection`/`Session`) or explicitly compensate and prove the CDC queue is clean. Only then is it safe to push successful aliases.

### Owner decisions — 2026-08-25

- **Legacy metadata repair:** deleting only `-info` and pulling is approved as safe; no cold bootstrap is required for libsql/corrupt metadata. The owner has confirmed this from experience, so the characterization spike in the earlier draft is removed. `heal_metadata()` implements the `-info`-only strategy directly, and its unit tests are the regression guard.
- **Backend automation:** the active GitHub Actions workflows and their secrets live in the production `OrthelT/mkts_backend` repository; scheduled workflows run from its `main` branch.
- **Frontend secrets:** deployed secrets are updated through the Streamlit Cloud web interface at streamlit.io. The local `.streamlit/secrets.toml` is only for local verification.
- **Landing sequence:** after implementation and test verification are complete, push new PR branches to the two production repositories (`OrthelT/mkts_backend` and `OrthelT/wcmkts_new`), open PRs against `main`, and merge those PRs to go live. Never push implementation commits directly to either `main` branch.
- **Rollback procedure:** record each production repository's pre-merge `main` SHA, move the deployed code back to that SHA if necessary, then delete the deployed replica caches through the relevant web UI so they rebuild from Turso.
- **Authoritative data behavior:** rebuilding a cache discards the local replica and downloads the current Turso database again. It fixes stale or incompatible cached files. It does **not** change or rewind the data stored in Turso; this migration therefore retains any normal production updates already pushed before a rollback.

## Global Constraints

- Backend worktree: `/home/orthel/workspace/github/mkts-turso`, branch `final-migration`. **This branch has no upstream tracking branch** (`git branch -vv` shows no bracket). Set one before any push.
- Frontend worktree: `/home/orthel/workspace/github/wcmkts-pyturso-migration`, branch `pyturso-migration-main`, tracks `staging/main`.
- During implementation, do not push incomplete migration work. Once the full plan is complete and both suites are green, push new PR branches to each `origin` production repository and open PRs against `main`. Never push commits directly to `origin/main`; merging the reviewed PRs is the go-live action.
- All `settings.toml` reads go through `mkts_backend.config.settings_service.SettingsService`. Never parse the TOML directly.
- Sync-managed replicas must be opened through `sqlite+turso_sync`. A plain `sqlite+turso` connection auto-checkpoints the WAL at 1000 frames and destroys the baseline `pull()` needs.
- A write reaches Turso only after `DatabaseConfig.push()`. `remote_engine` is an alias of `engine`; both are local.
- Do not use `delete`+`insert` on a table with a secondary `UNIQUE` constraint — it churns primary keys and breaks the next `push()`. Upsert in place.
- Migrate schema by drop → create-with-final-name → reinsert. `ALTER … RENAME` emits no CDC.
- Tests are derived from config, never frozen TOML literals: use `database_routing()` / the `market_aliases` pattern in `tests/conftest.py`.
- Backend suite baseline: **411 passed**. Frontend suite baseline: **644 passed, 22 subtests passed**. Neither may regress.

---

## Corrections to `docs/migration-review.md`

Verified during planning; the review is wrong or incomplete on these points. Fix the review as part of Task 23.

| Review claim | Actual |
|---|---|
| "The replica bundle is the `.db` plus five sidecars" (`:76`) | On disk it is **4–5 files**. No `-shm` exists for any of the six backend replicas. `sdelitetest.db` and `wcfittingtest.db` have no `-wal-revert` (pull-only). `DB_FILE_SUFFIXES` lists six because it includes `""` for the `.db` itself. |
| `validate` can be fixed by comparing against `last_pushed_change_id_hint` (`:162-164`) | **`last_pushed_change_id_hint` is not on `conn.stats()`.** `PyTursoSyncDatabaseStats` (`turso-dev/bindings/python/src/turso_sync.rs:257-275`) exposes only `cdc_operations`, `main_wal_size`, `revert_wal_size`, `last_pull_unix_time`, `last_push_unix_time`, `revision`, `network_sent_bytes`, `network_received_bytes`. The hint lives in `DatabaseMetadata` (the `-info` sidecar). The proposed fix has no Python API behind it. |
| `validate` has a false positive | True, **and** it crashes. `db_config.py:195-196` calls `datetime.fromtimestamp()` on `last_pull_unix_time` / `last_push_unix_time`, which are `Option<i64>`. `sdelitetest.db-info` and `buildcosttest.db-info` have `last_push_unix_time: null`, so `validate` raises `TypeError` on those replicas. |
| Frontend "admin **write** path remains disabled" (`:33`, `:223-228`) | `_get_write_engine()` (`admin_repo.py:761-770`) is called by 15 sites including read methods, so **the entire admin surface raises**, reads included. Both admin pages are dead, not degraded. |
| Frontend plain dialect "is not a complete read-only guard" (`:230-246`) | Understates it. The frontend uses `sqlite+turso` for **every** database (`config.py:141`, no call site overrides `dialect`) while pulling through raw `turso.sync.connect()` (`config.py:259-264`). This is the exact WAL-checkpoint hazard the backend eliminated. It is a cutover blocker in its own right. |
| Backend `sync` "covers configured markets plus buildcost only" (`:92-94`) | Confirmed (`command_registry.py:506-535`). Also confirmed: `database_routing()` (`settings_service.py:228-270`) already returns every market **and** shared entry, so extending `sync` needs no new config plumbing. |
| Refresh scripts "the untracked backend `dbrefreshtest.sh`" (`:86`) | Backend `dbrefreshtest.sh` and `dbdeltest.sh` are **tracked**. The **frontend**'s scripts are untracked (`.gitignore:51` = `*.sh`), so removing those is a local file operation, not a commit. |
| `sync_equiv_to_remote()` risks `UNIQUE constraint failed` (`:128-132`) | Correctly downgraded in the review. Confirmed: `module_equivalents` (`db/models.py:214-235`) has no secondary UNIQUE and no indexes in the live replica. The delete+insert churns AUTOINCREMENT rowids only. |
| Frontend needs SQLAlchemy floor raised, "do not present 2.0.42 as a confirmed package requirement" (`:276-280`) | The **package metadata** says `>=2.0`; the **dialect's real floor is 2.0.42** (the dialect is first-party — see `~/workspace/turso-dev`). Declare `>=2.0.42` in both repos and note that package metadata under-declares it. |
| "twelve"/"eleven" replicas | Backend 6, frontend 5 = 11. Confirmed. |

New findings the review does not mention:

- **`-info` records the remote it was bootstrapped against.** `saved_configuration.remote_url` in every pyturso `-info` names the exact Turso database. This gives the cutover a real isolation check that does not depend on trusting `settings.toml` — see Task 3.
- **`_mirror_to_build_watchlist` is not broken the way the review says.** `add_watchlist.py:136` does call `buildcost_db.sync()` (a pull) where a push is meant, but the data still reaches Turso because `builder_costs/repository.py:upsert_build_watchlist` pushes at `:161`. The trailing `sync()` is a wrong-direction no-op that pulls back over freshly pushed state. Still wrong; not data loss.
- **`add_structure.py` writes the same rows twice** (`:154` via `remote_engine`, `:165` via `engine`) into the same replica. `--remote-only` / `--local` are now meaningless, and the "partial success" warnings at `:176-185` cannot fire.
- **`init_buildcost_tables` (`builder_costs/repository.py:29`) creates tables with no push** at `:39`. Early exits at `runner.py:59/67/101/108` leave the DDL local.
- **Neither GitHub Actions workflow runs the test suite.** There is no `pytest` invocation anywhere in `.github/`.
- **`builder-costs-collection.yml` caches on `github.run_id`**, so its primary key never hits and every run falls through to the `restore-keys` prefix.
- **`scripts/wipe_gha_db_cache.sh` cannot be made to cover `builder-cost-dbs-v4-*`** by env override: it always appends `-shared-` or `-mkt-<leg>-` to the prefix, and the builder key is `builder-cost-dbs-v4-<run_id>`.
- **`DB_FILE_SUFFIXES` is duplicated three times inside `dbdeltest.sh`** (`:43-48`, `:57-63`, `:97-104`), maintained by hand.
- **`libsql 0.1.11` and `libsql_experimental 0.0.55` are still installed** in the frontend `.venv` despite being absent from `pyproject.toml`.

---

## Decisions — settled 2026-08-24

All three were answered by the owner. They are recorded here as the rationale behind the tasks; the rejected alternatives have been removed from the plan. Do not reopen them without saying so.

### Decision A — `validate` (review `:29-31`, "Do we really need it?")

Evidence:

- Zero dependents. Not referenced in `.github/workflows/`, `scripts/`, or any `.sh`. Its exit code (`args_parser.py:115`) is never consumed.
- Cannot be fixed as the review proposes: `last_pushed_change_id_hint` is not exposed through `stats()`.
- Crashes on never-pushed replicas (`sde`, `buildcost`) via `datetime.fromtimestamp(None)`.
- The guarantee it was meant to provide — "did my write reach Turso?" — is delivered better and earlier by Phase 4: `push()` raising and failing the command.

A post-hoc CDC-depth probe is a weak proxy for a push that is now checked at the source.

**Decision: delete it.** Task 6. The alternative — keeping it and reimplementing against `turso_cdc` plus the `-info` hint — was rejected: it reverse-engineers pyturso internals for a command nothing consumes, and would need revisiting on every pyturso upgrade.

### Decision B — the `push()` gaps (review `:26-27`, "Identify them and let me choose")

The original audit identified 18 writer sites grouped under management CLIs; the hourly pipeline and the builder-costs data writers are already correct. Treat 18 as the starting inventory, not a frozen acceptance count: the implementation must re-run the commit/begin/push audit and map the current call graph before editing.

| # | Task | CLI commands affected | Writers | Target DB | Blast radius if left unfixed |
|---|---|---|---|---|---|
| 1 Watchlist | 8 | `add_watchlist` / `add-watchlist`, and the watchlist step inside `fit-update add\|update` and `update-fit` | `utils/db_utils.py:add_missing_items_to_watchlist:16` (commit `:89`) | market DBs | New watchlist items never tracked by the next pipeline run on any other host. |
| 2 Module equivalents | 9 | `equiv add`, `equiv remove` | `db/equiv_handlers.py:add_equiv_group:187`, `remove_equiv_group:242`, `ensure_equiv_table:317`, `sync_equiv_to_remote:269` | every market DB | Frontend never sees equivalence groups. `sync_equiv_to_remote` is pure local churn. |
| 3 Fittings DB | 10 | `fit-update add\|update`, `update-fit`, `fit-update create-doctrine` | `utils/parse_fits.py:upsert_fittings_fitting:517`, `insert_fit_items_to_db:446`, `create_doctrine:296`, `add_doctrine_to_watch:385`, `ensure_doctrine_link:568`, `remove_doctrine_link:613`, `remove_all_doctrine_links_for_fit:651` | shared `fittings` | New/edited fits invisible to every other consumer of `wcfittingtest`. |
| 4 Market doctrine writers | 11 | `fit-update add\|update\|remove\|doctrine-remove-fit`, `update-fit`, `update-target`, `fit-update update-target\|update-friendly-name\|populate-friendly-names` | `utils/doctrine_update.py` (`upsert_doctrine_fits:170`, `upsert_doctrine_map:373`, `upsert_ship_target:676`, `refresh_doctrines_for_fit:1077`, `remove_*`, `set_lead_ship:910`, `update_doctrine_friendly_name:1314`, `populate_friendly_names_from_json:1346`, `sync_friendly_names_to_remote:1388`), `cli_tools/fit_update.py:2806-2813` | market DBs | Targets, friendly names and doctrine rows silently diverge from the frontend. |
| 5 Multi-alias loop commands | 12 | `fit-update assign-market\|unassign-market\|doctrine-add-fit\|update-lead-ship` | `fit_update.py:_execute_market_plan:1244` (`:1368`, `:1415`), `:2146`, `:2419`, `_provision_fit_in_market:1089`, `_cleanup_fit_in_market:1152`, `_provision_market_db:1130` | several market DBs per invocation | Market assignment changes never propagate. Needs per-alias push after the loop. |
| 6 Structure + buildcost seams | 13 | `add_structure` / `add-structure`, `add_watchlist` buildcost mirror, `update-builder-costs` schema step | `utils/build_cost_utils.py:upsert_structures:433` (called twice at `add_structure.py:154`/`:165`), `add_watchlist.py:_mirror_to_build_watchlist:106` (`:136` wrong direction), `builder_costs/repository.py:init_buildcost_tables:29` | `buildcost` | Structures never reach Turso; duplicate local writes; DDL stranded on early exit. |

**Decision: fix all six groups.** Tasks 7–12, none skipped. Some are simple push additions, but the fit/doctrine commands require caller-owned transactions and touched-alias accumulation; they are not safe three-line edits. `FakeDatabaseConfig` already counts pushes, but rollback and multi-alias failure behavior need additional assertions.

### Decision C — frontend `industry_index` (review `:230-246`)

`repositories/build_cost_repo.py:_write_industry_index_impl:105-108` does `to_sql(if_exists="replace")` — DROP + CREATE + INSERT — into the sync-managed `buildcost.db`, on the Build Costs **page-load** path (`pages/build_costs.py:244` → `:249-259`), with no push. pyturso replays DDL from `sqlite_schema` text, so this queues schema DDL in CDC on a replica that is otherwise pull-only.

**Decision: move it to a local-only cache database.** Task 15. This mirrors the backend's `cli_cache.db` pattern and keeps `buildcost.db` a clean pull-only replica. The alternatives — moving ownership to the backend, or keeping the local overlay and documenting it — were rejected: the first changes refresh timing for no gain, the second leaves DDL in a shared replica's CDC queue.

---

## File Structure

**Backend** (`/home/orthel/workspace/github/mkts-turso`)

| File | Change | Responsibility |
|---|---|---|
| `src/mkts_backend/config/replica_metadata.py` | **create** | Pure functions over the `-info` sidecar: classify its shape, read its recorded remote. No I/O beyond reading one file. Kept out of `db_config.py` so it is importable by tests and by the frontend port without dragging in engine construction. |
| `src/mkts_backend/config/db_config.py` | modify | `heal_metadata()` plus enforced fail-closed remote-mismatch guards on engine/sync paths; `validate_sync()` removed. |
| `src/mkts_backend/cli_tools/command_registry.py` | modify `:506-535`, `:546-567` | `sync` covers every routed replica; `validate` handler removed or fixed. |
| `src/mkts_backend/cli_tools/args_parser.py` | modify | New `--list-db-paths` flag for CI path derivation. |
| `src/mkts_backend/utils/db_utils.py`, `utils/doctrine_update.py`, `utils/parse_fits.py`, `utils/build_cost_utils.py`, `db/equiv_handlers.py`, `cli_tools/fit_update.py`, `cli_tools/add_watchlist.py`, `cli_tools/add_structure.py`, `builder_costs/repository.py` | modify | One `push()` per logical command transaction. |
| `tests/test_replica_metadata.py` | **create** | Classifier fixtures: pyturso, libsql, corrupt, missing, orphaned. |
| `tests/test_verify_db_exists.py` | modify | Heal-path cases added to the existing 4-case matrix. |
| `tests/test_management_push.py` | **create** | One test per fixed write path asserting local change **and** `push()` call. |
| `.github/workflows/*.yml` | modify | Paths derived from `--list-db-paths`; new test job. |
| `scripts/wipe_gha_db_cache.sh` | modify | Covers `builder-cost-dbs-v4-*`; ref default corrected. |
| `dbrefreshtest.sh` | **delete** | Superseded by `mkts-backend sync`. |
| `dbdeltest.sh` | modify | Reads suffixes from the CLI instead of three hand-maintained copies. |

**Frontend** (`/home/orthel/workspace/github/wcmkts-pyturso-migration`)

| File | Change | Responsibility |
|---|---|---|
| `replica_metadata.py` | **create** | Verbatim port of the backend module. Two copies is correct here — the repos share no package. |
| `config.py` | modify `:141`, `:198-211`, `:232-250` | Sync dialect by default; classifier-backed metadata check. |
| `init_db.py` | modify `:28-69` | `verify_db_content` uses the classifier. |
| `repositories/build_cost_repo.py` | modify `:105-108`, `:214-216` | `industry_index` moves to a local-only `streamlit_cache.db`. |
| `pyproject.toml` | modify | pyturso 0.7.2, SQLAlchemy `>=2.0.42`, drop `asyncio`/`sql`/`typing`. |
| `tests/test_replica_metadata.py` | **create** | Same fixtures as backend. |

---

# Phase 1 — Replica metadata: detect and heal

Owner decision (review `:50`, `:66`, `:70`, `:74`, `:78`; reaffirmed 2026-08-25): on encountering libsql/corrupt metadata, delete only the metadata file and `pull()` to repopulate it — **not** a full bundle nuke. The owner has confirmed from experience that this heals; it is not re-derived here. `heal_metadata()` implements exactly that and nothing else.

### Task 1: Metadata classifier

**Files:**
- Create: `src/mkts_backend/config/replica_metadata.py`
- Test: `tests/test_replica_metadata.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MetadataKind = Literal["pyturso", "libsql", "corrupt", "missing"]`
  - `classify_metadata(db_path: str | Path) -> MetadataKind`
  - `metadata_remote_url(db_path: str | Path) -> str | None`
  - `METADATA_SUFFIX: str = "-info"`

  Used by Tasks 2, 3, 5 and ported to the frontend in Task 14.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_replica_metadata.py
"""Shape checks for the pyturso -info sidecar.

A libsql-era -info is valid JSON, so an existence check or a bare
json.loads() accepts it and every later engine call raises
turso.lib.DatabaseError. These fixtures pin the discriminators.
"""
import json

import pytest

from mkts_backend.config.replica_metadata import (
    classify_metadata,
    metadata_remote_url,
)

PYTURSO_INFO = {
    "version": "v1",
    "client_unique_id": "turso-sync-py-2d5e3bef-a5f3-407c-a807-e386e2ee1c0e",
    "synced_revision": {"type": "v1", "revision": "{}"},
    "last_pull_unix_time": 1787620313,
    "last_push_unix_time": 1787620467,
    "saved_configuration": {
        "remote_url": "https://wcmktnewkeeptest-orthelt.aws-us-east-1.turso.io"
    },
}
LIBSQL_INFO = {"hash": "0" * 64, "version": 0, "generation": 1}


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "sample.db"
    p.write_bytes(b"")
    return p


def write_info(db_path, payload):
    (db_path.parent / f"{db_path.name}-info").write_text(
        payload if isinstance(payload, str) else json.dumps(payload)
    )


def test_pyturso_metadata_classified(db_path):
    write_info(db_path, PYTURSO_INFO)
    assert classify_metadata(db_path) == "pyturso"


def test_libsql_metadata_classified(db_path):
    write_info(db_path, LIBSQL_INFO)
    assert classify_metadata(db_path) == "libsql"


def test_non_json_is_corrupt(db_path):
    write_info(db_path, "not json at all")
    assert classify_metadata(db_path) == "corrupt"


def test_json_of_unknown_shape_is_corrupt(db_path):
    write_info(db_path, {"something": "else"})
    assert classify_metadata(db_path) == "corrupt"


def test_json_scalar_is_corrupt(db_path):
    write_info(db_path, "42")
    assert classify_metadata(db_path) == "corrupt"


def test_absent_metadata_is_missing(db_path):
    assert classify_metadata(db_path) == "missing"


def test_orphaned_metadata_still_classified(tmp_path):
    """No .db beside it. Classification describes the -info only."""
    orphan = tmp_path / "gone.db"
    write_info(orphan, PYTURSO_INFO)
    assert classify_metadata(orphan) == "pyturso"


def test_empty_string_version_is_not_pyturso(db_path):
    write_info(db_path, {**PYTURSO_INFO, "version": ""})
    assert classify_metadata(db_path) == "corrupt"


def test_empty_client_unique_id_is_not_pyturso(db_path):
    write_info(db_path, {**PYTURSO_INFO, "client_unique_id": ""})
    assert classify_metadata(db_path) == "corrupt"


def test_non_string_client_unique_id_is_not_pyturso(db_path):
    write_info(db_path, {**PYTURSO_INFO, "client_unique_id": 123})
    assert classify_metadata(db_path) == "corrupt"


def test_unknown_string_version_is_not_silently_accepted(db_path):
    write_info(db_path, {**PYTURSO_INFO, "version": "v999"})
    assert classify_metadata(db_path) == "corrupt"


def test_remote_url_read_from_metadata(db_path):
    write_info(db_path, PYTURSO_INFO)
    assert metadata_remote_url(db_path) == (
        "https://wcmktnewkeeptest-orthelt.aws-us-east-1.turso.io"
    )


def test_remote_url_none_when_metadata_missing(db_path):
    assert metadata_remote_url(db_path) is None


def test_remote_url_none_when_metadata_libsql(db_path):
    write_info(db_path, LIBSQL_INFO)
    assert metadata_remote_url(db_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_replica_metadata.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mkts_backend.config.replica_metadata'`

- [ ] **Step 3: Write the implementation**

```python
# src/mkts_backend/config/replica_metadata.py
"""Shape inspection for the pyturso ``-info`` replica-metadata sidecar.

pyturso writes ``<database>-info`` beside every synced replica. A libsql-era
``-info`` is *valid JSON*, so checking existence (or merely parsing it) accepts
a file that pyturso cannot use: the first engine call then raises
``turso.lib.DatabaseError`` with nothing pointing at the metadata.

The two discriminators are stable across pyturso 0.7.x: ``version`` is the
string ``"v1"`` (libsql wrote an integer ``0``) and ``client_unique_id`` is a
non-empty string that libsql never wrote.

This module reads one file and returns plain data. It deliberately imports
nothing from ``db_config`` so it stays cheap to test and simple to port to the
frontend, which has no shared package with this repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

METADATA_SUFFIX = "-info"

MetadataKind = Literal["pyturso", "libsql", "corrupt", "missing"]


def metadata_path(db_path: str | Path) -> Path:
    """The ``-info`` sidecar beside ``db_path``."""
    return Path(f"{db_path}{METADATA_SUFFIX}")


def _load(db_path: str | Path) -> dict | None:
    """Parsed metadata mapping, or None if absent, unreadable or not a mapping."""
    info = metadata_path(db_path)
    if not info.exists():
        return None
    try:
        payload = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def classify_metadata(db_path: str | Path) -> MetadataKind:
    """Classify the ``-info`` sidecar beside ``db_path``.

    Returns:
        "pyturso" — usable pyturso metadata.
        "libsql"  — libsql-era metadata; the replica must be re-pulled.
        "corrupt" — present but unparseable or of an unrecognised shape.
        "missing" — no ``-info`` file.
    """
    if not metadata_path(db_path).exists():
        return "missing"
    meta = _load(db_path)
    if meta is None:
        return "corrupt"
    version = meta.get("version")
    client_unique_id = meta.get("client_unique_id")
    if (
        version == "v1"
        and isinstance(client_unique_id, str)
        and bool(client_unique_id.strip())
    ):
        return "pyturso"
    if "hash" in meta and isinstance(version, int):
        return "libsql"
    return "corrupt"


def metadata_remote_url(db_path: str | Path) -> str | None:
    """The remote this replica was bootstrapped against, or None.

    pyturso records the bootstrap remote in
    ``saved_configuration.remote_url``. Comparing it against the configured
    URL catches a test replica left in place under a production configuration
    without printing any token.
    """
    if classify_metadata(db_path) != "pyturso":
        return None
    meta = _load(db_path) or {}
    saved = meta.get("saved_configuration")
    if not isinstance(saved, dict):
        return None
    url = saved.get("remote_url")
    return url if isinstance(url, str) and url else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_replica_metadata.py -q`
Expected: 14 passed

- [ ] **Step 5: Verify the real replicas classify correctly**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run python -c "
from mkts_backend.config.settings_service import SettingsService
from mkts_backend.config.replica_metadata import classify_metadata, metadata_remote_url
for alias, cfg in SettingsService().database_routing().items():
    print(f\"{alias:16} {classify_metadata(cfg['file']):8} {metadata_remote_url(cfg['file'])}\")
"
```

Expected: every replica present on disk reports `pyturso` and a `…test-orthelt.aws-us-east-1.turso.io` URL. Replicas not on disk report `missing` and `None`.

- [ ] **Step 6: Commit**

```bash
cd /home/orthel/workspace/github/mkts-turso
git add src/mkts_backend/config/replica_metadata.py tests/test_replica_metadata.py
git commit -m "feat: classify pyturso replica metadata by shape, not existence"
```

---

### Task 2: Heal damaged metadata before connecting

**Files:**
- Modify: `src/mkts_backend/config/db_config.py:362-370` (`confirm_metadata_exists`), `:282-335` (`verify_db_exists`)
- Test: `tests/test_verify_db_exists.py`

**Interfaces:**
- Consumes: `classify_metadata` and `METADATA_SUFFIX` from Task 1.
- Produces: `DatabaseConfig.heal_metadata() -> bool`, called by `verify_db_exists()` and by Task 4's `sync` handler.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_db_exists.py`:

```python
class TestHealMetadata:
    """A replica whose -info is not pyturso metadata must be repaired, not used.

    verify_db_exists() previously accepted any -info file, so a libsql-era
    sidecar surviving a cutover passed the check and every later engine call
    raised turso.lib.DatabaseError.
    """

    def test_pyturso_metadata_is_left_alone(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path, monkeypatch)
        _write_pyturso_info(db.path)
        Path(db.path).write_bytes(b"")
        pulls = _count_pulls(db, monkeypatch)
        assert db.heal_metadata() is True
        assert pulls() == 0

    def test_libsql_metadata_triggers_repull(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path, monkeypatch)
        _write_libsql_info(db.path)
        Path(db.path).write_bytes(b"")
        pulls = _count_pulls(db, monkeypatch, heals=True)
        assert db.heal_metadata() is True
        assert pulls() == 1

    def test_corrupt_metadata_triggers_repull(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path, monkeypatch)
        Path(f"{db.path}-info").write_text("not json")
        Path(db.path).write_bytes(b"")
        pulls = _count_pulls(db, monkeypatch, heals=True)
        assert db.heal_metadata() is True
        assert pulls() == 1

    def test_missing_metadata_triggers_repull(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path, monkeypatch)
        Path(db.path).write_bytes(b"")
        pulls = _count_pulls(db, monkeypatch, heals=True)
        assert db.heal_metadata() is True
        assert pulls() == 1

    def test_pull_that_does_not_repair_returns_false(self, tmp_path, monkeypatch):
        """A pull that leaves non-pyturso metadata must fail loudly."""
        db = _make_db(tmp_path, monkeypatch)
        _write_libsql_info(db.path)
        Path(db.path).write_bytes(b"")
        monkeypatch.setattr(type(db), "pull", lambda self: None)
        assert db.heal_metadata() is False

    def test_verify_db_exists_heals_libsql_metadata(self, tmp_path, monkeypatch):
        """Case 2 (db + metadata both present) must no longer short-circuit
        on a libsql -info."""
        db = _make_db(tmp_path, monkeypatch)
        _write_libsql_info(db.path)
        Path(db.path).write_bytes(b"x")
        pulls = _count_pulls(db, monkeypatch, heals=True)
        assert db.verify_db_exists() is True
        assert pulls() >= 1
```

Add these helpers at the top of the same file:

```python
import json
from pathlib import Path

_PYTURSO_INFO = {
    "version": "v1",
    "client_unique_id": "turso-sync-py-test",
    "saved_configuration": {"remote_url": "https://test-db.turso.io"},
}
_LIBSQL_INFO = {"hash": "0" * 64, "version": 0, "generation": 1}


def _write_pyturso_info(path):
    Path(f"{path}-info").write_text(json.dumps(_PYTURSO_INFO))


def _write_libsql_info(path):
    Path(f"{path}-info").write_text(json.dumps(_LIBSQL_INFO))


def _make_db(tmp_path, monkeypatch):
    """A DatabaseConfig pointed at tmp_path with no real remote."""
    from mkts_backend.config.db_config import DatabaseConfig

    db = DatabaseConfig.__new__(DatabaseConfig)
    db.alias = "testing"
    db.path = str(tmp_path / "sample.db")
    db.turso_url = "https://test-db.turso.io"
    db.token = "test-token"
    db._engine = None
    return db


def _count_pulls(db, monkeypatch, heals=False):
    """Replace pull() with a counter. With heals=True it also writes valid
    pyturso metadata, standing in for a successful remote pull."""
    calls = []

    def fake_pull(self):
        calls.append(1)
        if heals:
            _write_pyturso_info(self.path)
            Path(self.path).write_bytes(b"x")

    monkeypatch.setattr(type(db), "pull", fake_pull)
    return lambda: len(calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_verify_db_exists.py::TestHealMetadata -q`
Expected: FAIL — `AttributeError: 'DatabaseConfig' object has no attribute 'heal_metadata'`

- [ ] **Step 3: Implement `heal_metadata()`**

Add to `src/mkts_backend/config/db_config.py`, immediately after `confirm_metadata_exists()` (currently ends at `:370`).

```python
    def heal_metadata(self) -> bool:
        """Ensure this replica has usable pyturso metadata, repairing it if not.

        A libsql-era or corrupt ``-info`` is valid JSON, so the old existence
        check accepted it and the first engine call raised
        ``turso.lib.DatabaseError`` with nothing naming the cause. Detect the
        bad shape here and re-pull instead.

        Returns:
            True if the replica now has pyturso metadata, False otherwise.
        """
        kind = classify_metadata(self.path)
        if kind == "pyturso":
            return True

        logger.warning(
            f"{self.alias} ({self.path}): replica metadata is '{kind}', not pyturso. "
            f"Repairing by re-pulling from Turso."
        )

        # Remove only the metadata sidecar. The .db, -wal and -changes stay:
        # pull() rebuilds -info against them. A full bundle nuke would
        # re-download the whole database for a sidecar-sized problem.
        Path(f"{self.path}{METADATA_SUFFIX}").unlink(missing_ok=True)

        self._engine = None           # drop any engine bound to the old files
        try:
            self.pull()
        except Exception as exc:
            logger.error(
                f"{self.alias} ({self.path}): pull failed while repairing "
                f"'{kind}' metadata: {exc}. Remedy: delete the replica bundle "
                f"({', '.join(s or '.db' for s in DB_FILE_SUFFIXES)}) and run "
                f"`uv run mkts-backend sync`."
            )
            return False

        healed = classify_metadata(self.path)
        if healed != "pyturso":
            logger.error(
                f"{self.alias} ({self.path}): metadata is still '{healed}' after "
                f"pull. Remedy: delete the replica bundle and run "
                f"`uv run mkts-backend sync`."
            )
            return False
        return True
```

Add the import at the top of `db_config.py`, beside the existing config imports:

```python
from mkts_backend.config.replica_metadata import (
    METADATA_SUFFIX,
    classify_metadata,
)
```

- [ ] **Step 4: Wire it into `verify_db_exists()`**

In `verify_db_exists()` (`:282-335`), replace the case-2 early return — currently `if db_exists and metadata_exists: return True` around `:305-308` — with:

```python
        if db_exists and metadata_exists:
            # Both files are present, but "present" is not "usable": a
            # libsql-era -info parses as JSON and passes an existence check.
            return self.heal_metadata()
```

and replace the final re-check at `:330-335` with:

```python
        if not Path(self.path).exists():
            logger.error(f"{self.alias}: database missing after sync ({self.path})")
            return False
        return self.heal_metadata()
```

Leave cases 3 and 4 (`nuke_db()` then `sync()`) unchanged — an orphaned or unpaired file is still a full-bundle problem.

- [ ] **Step 5: Run the full metadata suite**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_verify_db_exists.py tests/test_replica_metadata.py -q`
Expected: all pass, including the pre-existing `TestVerifyDbExists`, `TestNeedsInit`, `TestNukeMethods`, `TestConfirmMetadataExists`, `TestIntegrationScenarios`.

- [ ] **Step 6: Run the whole suite for regressions**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest -q`
Expected: ≥ 411 passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
cd /home/orthel/workspace/github/mkts-turso
git add src/mkts_backend/config/db_config.py tests/test_verify_db_exists.py
git commit -m "fix: repair non-pyturso replica metadata instead of failing on first connect"
```

---

### Task 3: Fail closed on a replica bootstrapped against the wrong remote

**Files:**
- Modify: `src/mkts_backend/config/db_config.py` (after `heal_metadata`)
- Test: `tests/test_replica_metadata.py`

**Interfaces:**
- Consumes: `metadata_remote_url` from Task 1.
- Produces:
  - `DatabaseConfig.remote_matches_metadata() -> bool | None` — `True` match, `False` mismatch, `None` unknown (no metadata or no configured URL).
  - `DatabaseConfig.assert_remote_compatible() -> None`, called before every SQLAlchemy or raw sync connection is opened.

**Why:** the review (`:171-189`) concluded a settings-only switch cannot guarantee environment isolation, because `settings.toml` holds only the *names* of credential variables. The `-info` sidecar records the remote each replica was actually bootstrapped against. Merely exposing a comparison helper is insufficient: every engine/pull/push path must refuse a mismatch before opening the file against the configured remote.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_replica_metadata.py`:

```python
class TestRemoteMatchesMetadata:
    """A test-data replica left under a production configuration must be
    caught before it is read or pushed."""

    def _db(self, tmp_path, url):
        from mkts_backend.config.db_config import DatabaseConfig

        db = DatabaseConfig.__new__(DatabaseConfig)
        db.alias = "primary"
        db.path = str(tmp_path / "market.db")
        db.turso_url = url
        db.token = "t"
        db._engine = None
        return db

    def test_matching_remote(self, tmp_path):
        db = self._db(tmp_path, "https://wcmktnewkeeptest-orthelt.aws-us-east-1.turso.io")
        write_info(tmp_path / "market.db", PYTURSO_INFO)
        assert db.remote_matches_metadata() is True

    def test_mismatched_remote(self, tmp_path):
        db = self._db(tmp_path, "https://wcmktnewkeep-orthelt.aws-us-east-1.turso.io")
        write_info(tmp_path / "market.db", PYTURSO_INFO)
        assert db.remote_matches_metadata() is False

    def test_scheme_and_trailing_slash_ignored(self, tmp_path):
        db = self._db(tmp_path, "libsql://wcmktnewkeeptest-orthelt.aws-us-east-1.turso.io/")
        write_info(tmp_path / "market.db", PYTURSO_INFO)
        assert db.remote_matches_metadata() is True

    def test_unknown_without_metadata(self, tmp_path):
        db = self._db(tmp_path, "https://anything.turso.io")
        assert db.remote_matches_metadata() is None

    def test_unknown_without_configured_url(self, tmp_path):
        db = self._db(tmp_path, None)
        write_info(tmp_path / "market.db", PYTURSO_INFO)
        assert db.remote_matches_metadata() is None

    def test_engine_refuses_mismatched_remote_before_create_engine(
        self, tmp_path, monkeypatch
    ):
        db = self._db(tmp_path, "https://wcmktnewkeep-orthelt.aws-us-east-1.turso.io")
        write_info(tmp_path / "market.db", PYTURSO_INFO)
        called = False

        def forbidden(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("create_engine must not run on a mismatched replica")

        monkeypatch.setattr("mkts_backend.config.db_config.create_engine", forbidden)
        with pytest.raises(RuntimeError, match="different Turso remote"):
            _ = db.engine
        assert called is False

    def test_push_and_pull_refuse_mismatched_remote(self, tmp_path):
        db = self._db(tmp_path, "https://wcmktnewkeep-orthelt.aws-us-east-1.turso.io")
        write_info(tmp_path / "market.db", PYTURSO_INFO)
        with pytest.raises(RuntimeError, match="different Turso remote"):
            db.push()
        with pytest.raises(RuntimeError, match="different Turso remote"):
            db.pull()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_replica_metadata.py::TestRemoteMatchesMetadata -q`
Expected: FAIL — `AttributeError: … 'remote_matches_metadata'`

- [ ] **Step 3: Implement**

Add to `src/mkts_backend/config/db_config.py` after `heal_metadata()`:

```python
    def remote_matches_metadata(self) -> bool | None:
        """Whether this replica was bootstrapped against the configured remote.

        pyturso records the bootstrap remote in the ``-info`` sidecar. After a
        production cutover the configuration changes but the files on disk do
        not, so a test replica can be read — and pushed — under a production
        configuration. Compare host and path only; scheme (``https`` vs
        ``libsql``) and a trailing slash are not meaningful, and no token is
        read or logged.

        Returns:
            True on match, False on mismatch, None when either side is unknown.
        """
        recorded = metadata_remote_url(self.path)
        if not recorded or not self.turso_url:
            return None

        def key(url: str) -> str:
            parsed = urlparse(url)
            return f"{parsed.netloc}{parsed.path.rstrip('/')}"

        return key(recorded) == key(self.turso_url)

    def assert_remote_compatible(self) -> None:
        """Fail before opening a replica against a different configured remote."""
        if self.remote_matches_metadata() is False:
            recorded = metadata_remote_url(self.path)
            raise RuntimeError(
                f"{self.alias} ({self.path}) was bootstrapped from {recorded}, "
                f"not the configured remote. Refusing to connect. Preserve any "
                f"needed local work, then run nuke_db() and cold sync explicitly."
            )
```

Extend the Task 1 import and add `urlparse`:

```python
from urllib.parse import urlparse

from mkts_backend.config.replica_metadata import (
    METADATA_SUFFIX,
    classify_metadata,
    metadata_remote_url,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_replica_metadata.py -q`
Expected: 21 passed

Also call `assert_remote_compatible()`:

- at the start of `engine` before `create_engine(...)`;
- at the start of `turso_sync_connection` before `tursosync.connect(...)`;
- at the start of `verify_db_exists()` before its paired-file fast path; and
- from Task 4's `sync` handler before any repair or pull.

Do **not** call it after a raw connection has already been created. Do **not** automatically delete a mismatched bundle. Invalid same-remote metadata follows Task 2's tested heal strategy; valid metadata naming another remote is a separate, fail-closed state.

- [ ] **Step 5: Verify against the live replicas**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run python -c "
from mkts_backend.config.settings_service import SettingsService
from mkts_backend.config.db_config import DatabaseConfig
for alias in SettingsService().database_routing():
    db = DatabaseConfig(alias)
    print(f'{alias:16} {db.remote_matches_metadata()}')
"
```

Expected: `True` for every replica present on disk, `None` for any not yet pulled. **Any `False` means a replica is pointed at a remote it was not bootstrapped from — stop and report it.**

- [ ] **Step 6: Commit**

```bash
cd /home/orthel/workspace/github/mkts-turso
git add src/mkts_backend/config/db_config.py tests/test_replica_metadata.py
git commit -m "feat: detect a replica bootstrapped against a different Turso remote"
```

---

# Phase 2 — Make `sync` a complete replacement for the refresh scripts

Owner decision (review `:91`): remove the refresh scripts and use pyturso `pull()`. `sync` must therefore cover every replica the scripts covered — today it covers three markets plus buildcost, and skips `sde` and `fittings` entirely.

### Task 4: `sync` covers every routed replica and heals metadata first

**Files:**
- Modify: `src/mkts_backend/cli_tools/command_registry.py:506-543` (`_handle_sync` and its registration)
- Test: `tests/test_sync_command.py` (create)

**Interfaces:**
- Consumes: `heal_metadata()` (Task 2); `SettingsService.database_routing()` (`settings_service.py:228-270`); `get_all_market_contexts()`; `expand_market_alias` (`cli_tools/market_args.py:29-35`).
- Produces: nothing later tasks import. Task 21 (cutover runbook) depends on its behavior.

**Design:** markets come from the market selector as today. Shared databases come from `database_routing()` minus the market aliases, so adding a `[shared.*]` block to `settings.toml` needs no code change. `[shared.testing]` is excluded unless `--include-testing` is explicit. A route marked `optional = true` is best-effort; any required route missing either its URL or token is a hard failure. Optional does not mean "skip every error": if credentials are present and the pull fails, report the failure and return a non-zero command result.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync_command.py
"""`sync` must cover every replica the deleted refresh scripts covered.

It previously pulled the three markets plus buildcost and silently skipped
the shared sde and fittings replicas, so a refresh left those two stale.
"""
from unittest.mock import MagicMock, patch

import pytest

from mkts_backend.cli_tools.command_registry import CommandRegistry
from mkts_backend.config.settings_service import (
    SettingsService,
    get_all_market_contexts,
)


@pytest.fixture
def routed_aliases():
    return set(SettingsService().database_routing())


@pytest.fixture
def market_aliases():
    return {ctx.database_alias for ctx in get_all_market_contexts().values()}


@pytest.fixture
def synced(monkeypatch):
    """Record every alias a DatabaseConfig was built for and pulled."""
    seen = []

    class Recorder:
        def __init__(self, alias=None, market_context=None):
            self.alias = alias or market_context.database_alias
            self.path = f"{self.alias}.db"

        def heal_metadata(self):
            return True

        def assert_remote_compatible(self):
            return None

        def sync(self):
            seen.append(self.alias)

    monkeypatch.setattr(
        "mkts_backend.config.db_config.DatabaseConfig", Recorder
    )
    return seen


def _run(args, market_alias="all"):
    handler = CommandRegistry().get("sync").handler
    return handler(args, market_alias)


def test_sync_all_covers_every_routed_replica(synced, routed_aliases):
    _run([])
    assert set(synced) >= routed_aliases - {"wcmkttest"}


def test_sync_excludes_testing_unless_explicit(synced):
    _run([])
    assert "wcmkttest" not in synced
    _run(["--include-testing"])
    assert "wcmkttest" in synced


def test_sync_covers_sde_and_fittings(synced):
    _run([])
    assert "sdelitetest" in synced or "sde" in " ".join(synced)
    assert any("fitting" in a for a in synced)


def test_single_market_skips_other_markets(synced, market_aliases):
    _run([], market_alias="primary")
    from mkts_backend.config.market_context import MarketContext
    primary = MarketContext.from_settings("primary").database_alias
    assert primary in synced
    assert not (market_aliases - {primary}) & set(synced)


def test_no_buildcost_flag_skips_buildcost(synced):
    _run(["--no-buildcost"])
    assert not any("buildcost" in a for a in synced)


def test_markets_only_flag_skips_shared(synced, market_aliases):
    _run(["--markets-only"])
    assert set(synced) == market_aliases


def test_heal_failure_aborts_that_replica(monkeypatch, capsys):
    class Broken:
        def __init__(self, alias=None, market_context=None):
            self.alias = alias or market_context.database_alias
            self.path = f"{self.alias}.db"
            self.pulled = False

        def heal_metadata(self):
            return False

        def sync(self):
            self.pulled = True
            raise AssertionError("sync must not run after heal_metadata fails")

    monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", Broken)
    assert _run([], market_alias="primary") is False


def test_missing_required_url_or_token_fails(monkeypatch):
    """Required routes must not silently become local-only databases."""
    # Parameterize URL missing and token missing for one required market and
    # one required shared route; assert the handler is False and sync is not called.


def test_optional_route_with_no_credentials_warns_and_skips(monkeypatch, capsys):
    # Remove one credential from buildcost/testing and assert command success,
    # no DatabaseConfig construction for that alias, and a visible warning.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sync_command.py -q`
Expected: FAIL — `sde`/`fittings` never appear in `synced`; `--markets-only` is not a recognised flag.

- [ ] **Step 3: Replace `_handle_sync`**

Replace `command_registry.py:506-535` with:

```python
    def _handle_sync(args: list[str], market_alias: str) -> bool:
        """Pull every configured replica from Turso.

        Markets come from the market selector; shared databases come from
        database_routing(), so a new [shared.*] block in settings.toml is
        picked up with no code change. This is the whole of the old
        dbrefresh*.sh procedure: the scripts used `turso db export`, which
        cannot produce pyturso metadata.
        """
        from mkts_backend.cli_tools.arg_utils import ParsedArgs
        from mkts_backend.cli_tools.market_args import expand_market_alias
        from mkts_backend.config import db_config
        from mkts_backend.config.market_context import MarketContext
        from mkts_backend.config.settings_service import (
            SettingsService,
            get_all_market_contexts,
        )

        p = ParsedArgs(args)
        skip_buildcost = p.has_flag("no-buildcost")
        markets_only = p.has_flag("markets-only")
        include_testing = p.has_flag("include-testing")

        routing = SettingsService().database_routing()
        market_db_aliases = {
            ctx.database_alias for ctx in get_all_market_contexts().values()
        }

        ok = True

        def credentials_present(cfg: dict) -> bool:
            return bool(
                cfg["turso_url_env"]
                and cfg["turso_token_env"]
                and os.getenv(cfg["turso_url_env"])
                and os.getenv(cfg["turso_token_env"])
            )

        def pull_one(db, label: str, required: bool) -> bool:
            print(f"Syncing database: {label}")
            try:
                db.assert_remote_compatible()
                if not db.heal_metadata():
                    raise RuntimeError("replica metadata could not be repaired")
                db.sync()
            except Exception as exc:
                logger.warning(f"{label} sync failed: {exc}")
                print(f"{'Error' if required else 'Warning'}: {label} sync failed: {exc}")
                return False
            logger.info(f"Database synced: {db.alias}")
            print(f"Database synced: {db.alias} ({db.path})")
            return True

        for mkt in expand_market_alias(market_alias):
            market_ctx = MarketContext.from_settings(mkt)
            cfg = routing[market_ctx.database_alias]
            if not credentials_present(cfg):
                print(f"Error: required credentials are incomplete for {mkt}")
                ok = False
                continue
            db = db_config.DatabaseConfig(market_context=market_ctx)
            ok &= pull_one(db, f"{market_ctx.name} ({market_ctx.alias})", required=True)

        if markets_only:
            return ok

        for alias, cfg in routing.items():
            if alias in market_db_aliases:
                continue
            if alias == SettingsService().shared_testing["database_alias"] and not include_testing:
                continue
            if skip_buildcost and "buildcost" in alias:
                continue
            if not credentials_present(cfg):
                level = "Warning" if cfg["optional"] else "Error"
                print(f"{level}: credentials are incomplete for {alias}")
                ok &= bool(cfg["optional"])
                continue
            db = db_config.DatabaseConfig(alias)
            ok &= pull_one(db, alias, required=not cfg["optional"])

        return ok
```

Note `db_config.DatabaseConfig` is referenced through the module so the test's `monkeypatch.setattr` on `mkts_backend.config.db_config.DatabaseConfig` takes effect. Add `import os` at the top of `command_registry.py` if it is not already imported.

Update the registration at `:539-543` to document the new flags:

```python
    reg.register(
        "sync",
        _handle_sync,
        description=(
            "Pull every configured replica from Turso "
            "(--markets-only, --no-buildcost to narrow)"
        ),
        default_market="all",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_sync_command.py -q`
Expected: all sync-command cases pass, including required/optional credential states and explicit testing inclusion.

- [ ] **Step 5: Run it for real against the test remotes**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend sync 2>&1 | tail -30
```

Expected: three markets plus `sdelitetest`, `wcfittingtest`, and (when credentials are present) `buildcosttest` report "Database synced". `wcmkttest` must not appear unless `--include-testing` was passed.

- [ ] **Step 6: Update the help text**

`src/mkts_backend/cli_tools/cli_help.py` — update the `sync` entry to list `--markets-only`, `--no-buildcost`, and `--include-testing`, and state that `sync` is a pull covering production-routed markets and shared databases by default.

- [ ] **Step 7: Commit**

```bash
cd /home/orthel/workspace/github/mkts-turso
git add src/mkts_backend/cli_tools/command_registry.py src/mkts_backend/cli_tools/cli_help.py tests/test_sync_command.py
git commit -m "feat: sync pulls every routed replica, including sde and fittings"
```

---

### Task 5: Expose database paths to the shell

**Files:**
- Modify: `src/mkts_backend/cli_tools/args_parser.py` (beside `--list-markets` at `:59-69`)
- Test: `tests/test_cli_routing.py`

**Interfaces:**
- Consumes: `SettingsService.database_routing()`.
- Produces: `mkts-backend --list-db-paths` (all replicas, `alias<TAB>file`) and `mkts-backend --db-path=<alias>` (one bare filename). Consumed by Task 18 and by `dbdeltest.sh` in Task 20.

**Why:** the two workflows hardcode six database filenames across ten places (`market-data-collection.yml:112-114,124-125,234-235`; `builder-costs-collection.yml:29-31,49-51`). The production switch must not require editing YAML. `--list-markets` already exists but prints `database_alias`, not `database_file`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_routing.py`:

```python
class TestDbPathFlags:
    """CI derives cache paths from these, so the format is contractual:
    --list-db-paths prints `alias\\tfile` per line; --db-path prints one
    bare filename with no decoration."""

    def test_list_db_paths_covers_every_routed_alias(self, capsys):
        from mkts_backend.cli_tools.args_parser import main
        from mkts_backend.config.settings_service import SettingsService

        with pytest.raises(SystemExit) as exc:
            main(["--list-db-paths"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        routing = SettingsService().database_routing()
        printed = dict(line.split("\t") for line in out.strip().splitlines())
        assert printed == {a: c["file"] for a, c in routing.items()}

    def test_db_path_by_database_alias(self, capsys):
        from mkts_backend.cli_tools.args_parser import main
        from mkts_backend.config.settings_service import SettingsService

        alias, cfg = next(iter(SettingsService().database_routing().items()))
        with pytest.raises(SystemExit) as exc:
            main([f"--db-path={alias}"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == cfg["file"]

    def test_db_path_by_market_section_name(self, capsys):
        """CI matrix legs are named primary/deployment/market3, which are
        section names, not database aliases."""
        from mkts_backend.cli_tools.args_parser import main
        from mkts_backend.config.market_context import MarketContext

        expected = MarketContext.from_settings("deployment").database_file
        with pytest.raises(SystemExit) as exc:
            main(["--db-path=deployment"])
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == expected

    def test_unknown_alias_exits_nonzero(self, capsys):
        from mkts_backend.cli_tools.args_parser import main

        with pytest.raises(SystemExit) as exc:
            main(["--db-path=nosuchdb"])
        assert exc.value.code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_cli_routing.py::TestDbPathFlags -q`
Expected: FAIL — the flags fall through to the "did you mean?" handler.

- [ ] **Step 3: Implement**

Add to `args_parser.py`, immediately after the `--list-markets` block at `:59-69`:

```python
    if "--list-db-paths" in argv:
        from mkts_backend.config.settings_service import SettingsService

        for alias, cfg in SettingsService().database_routing().items():
            print(f"{alias}\t{cfg['file']}")
        raise SystemExit(0)

    db_path_arg = next(
        (a for a in argv if a.startswith("--db-path=")), None
    )
    if db_path_arg:
        from mkts_backend.config.settings_service import (
            SettingsService,
            get_all_market_contexts,
        )

        wanted = db_path_arg.split("=", 1)[1]
        routing = SettingsService().database_routing()
        if wanted in routing:
            print(routing[wanted]["file"])
            raise SystemExit(0)
        # CI matrix legs use the [markets.<section>] name, not the alias.
        contexts = get_all_market_contexts()
        if wanted in contexts:
            print(contexts[wanted].database_file)
            raise SystemExit(0)
        print(
            f"Unknown database: {wanted}. "
            f"Known: {', '.join(sorted(set(routing) | set(contexts)))}",
            file=sys.stderr,
        )
        raise SystemExit(1)
```

Confirm `sys` is imported at the top of `args_parser.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_cli_routing.py -q`
Expected: all pass

- [ ] **Step 5: Verify the shell contract**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend --list-db-paths
uv run mkts-backend --db-path=deployment      # expect: wcmktnorth2test.db
uv run mkts-backend --db-path=sde             # expect: sdelitetest.db
uv run mkts-backend --db-path=nope; echo "exit=$?"   # expect: exit=1
```

- [ ] **Step 6: Document and commit**

Add both flags to `src/mkts_backend/cli_tools/cli_help.py`.

```bash
cd /home/orthel/workspace/github/mkts-turso
git add src/mkts_backend/cli_tools/args_parser.py src/mkts_backend/cli_tools/cli_help.py tests/test_cli_routing.py
git commit -m "feat: --list-db-paths and --db-path for shell and CI path derivation"
```

---

# Phase 3 — Remove `validate`

### Task 6: Delete `validate`

**Files:**
- Modify: `src/mkts_backend/config/db_config.py:180-197` (delete `validate_sync`)
- Modify: `src/mkts_backend/cli_tools/command_registry.py:546-567` (delete `_handle_validate` and its registration)
- Modify: `src/mkts_backend/cli_tools/cli_help.py:50`
- Modify: `tests/test_command_registry.py` (drop `TestValidateHandler`, `:103` registration list)
- Modify: `tests/test_suggestions.py:36`
- Modify: `README.md:291,304,307`, `CLAUDE.md:55-68`, `AGENTS.md:55-68`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. `push()` failing the command (Phase 4) is the replacement guarantee.

- [ ] **Step 1: Confirm nothing outside these files depends on it**

```bash
cd /home/orthel/workspace/github/mkts-turso
grep -rn "validate_sync\|\"validate\"\|'validate'\|mkts-backend validate" \
  --include='*.py' --include='*.yml' --include='*.sh' --include='*.md' . \
  | grep -v '\.venv' | grep -v 'validate-env' | grep -v 'validate_env'
```

Expected: only the files listed above. `--validate-env` is a different feature and must not be touched. If anything else appears, stop and report it.

- [ ] **Step 2: Write the failing test**

Replace `TestValidateHandler` in `tests/test_command_registry.py` with:

```python
class TestValidateRemoved:
    """`validate` reported a false positive on every market DB that had ever
    been written (pyturso leaves a trailing transaction marker, so
    cdc_operations never returns to 0) and crashed on never-pushed replicas
    (last_push_unix_time is None). It cannot be fixed against the public
    stats() API: last_pushed_change_id_hint is not exposed there. The
    guarantee it approximated is now enforced by push() failing the command.
    """

    def test_validate_is_not_registered(self):
        from mkts_backend.cli_tools.command_registry import CommandRegistry

        assert CommandRegistry().get("validate") is None

    def test_validate_sync_is_gone(self):
        from mkts_backend.config.db_config import DatabaseConfig

        assert not hasattr(DatabaseConfig, "validate_sync")

    def test_validate_env_still_works(self, capsys):
        """The unrelated --validate-env flag must survive."""
        from mkts_backend.cli_tools.args_parser import main

        with pytest.raises(SystemExit):
            main(["--validate-env"])
        assert capsys.readouterr().out
```

Also remove `"validate"` from the expected-command list at `tests/test_command_registry.py:103` and from the `KNOWN` set at `tests/test_suggestions.py:36`.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest tests/test_command_registry.py::TestValidateRemoved -q`
Expected: FAIL — `validate` is still registered.

- [ ] **Step 4: Delete the code**

- Delete `validate_sync()`, `db_config.py:180-197`.
- Delete `_handle_validate()` and its `reg.register("validate", …)` block, `command_registry.py:546-567`.
- Delete the `validate` entry at `cli_help.py:50`.
- Remove the `validate` sections from `README.md`, `CLAUDE.md` and `AGENTS.md`, including the "Known issue" paragraph in `CLAUDE.md`/`AGENTS.md` that documents the false positive — the issue no longer exists because the command no longer exists.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/orthel/workspace/github/mkts-turso && uv run pytest -q`
Expected: pass, count reduced by the three deleted `TestValidateHandler` tests plus the three added here.

- [ ] **Step 6: Commit**

```bash
cd /home/orthel/workspace/github/mkts-turso
git add -A
git commit -m "refactor: remove validate; push() failure is the real push guarantee

validate_sync() compared stats().cdc_operations against 0, which pyturso
never returns after a push (a trailing transaction marker remains), and
crashed on replicas that had never been pushed. It cannot be fixed against
the public API: last_pushed_change_id_hint lives in the -info sidecar, not
on stats(). Nothing consumed its exit code."
```

---

---

# Phase 4 — One `push()` per logical write transaction

All six groups are in scope (Decision B). Tasks 7–12, none skipped.

**The pattern, applied identically everywhere** (review `:139-145`):

1. write through `db.engine`;
2. finish the **complete logical command** in one caller-owned local transaction per database; an exception must roll back that database's whole bucket rather than leave committed CDC rows;
3. have the public command collect the distinct database aliases it touched and call `DatabaseConfig(alias).push()` **once per alias**, after that command's final write;
4. let a push failure propagate so the command fails;
5. assert both the local row and the push in a test.

Do not invent a `db` variable at a call site that does not own one. Most current writers instantiate `DatabaseConfig` internally; a fresh `DatabaseConfig(alias).push()` at the public command boundary still flushes that replica's CDC queue. Where a nested workflow is invoked once per market, it must report/accumulate touched aliases and the outermost command must push after the loop. A small explicit `set[str]` of touched aliases is preferred to a decorator or implicit auto-push.

Before editing, add a table to `tests/test_management_push.py` (or a nearby test comment) mapping each public CLI to: final write location, touched aliases, existing internal pushes, and intended push boundary. Re-run `rg -n "commit\(|\.begin\(|\.push\(|remote_engine" src/mkts_backend` after every task. The original line-number audit has already drifted (`fit_update.py` now exceeds 3,200 lines), so line numbers are navigation hints, not proof of coverage.

For commands composed from helpers that currently open/commit their own transactions, add an optional caller-owned `Connection`/`Session` parameter and keep the old self-managed behavior only for read-only or independently durable callers. Add a failure-in-the-last-step test that proves earlier writes are rolled back and that a later unrelated `push()` cannot publish them. Multi-database remote pushes cannot be atomic; surface the exact aliases that succeeded/failed and make retries idempotent.

**On the test bodies in Tasks 9–12:** the original plan did not inspect these signatures and some literal examples are wrong. Replace every placeholder with a real call before considering the task started. Tests must exercise the public command with a non-interactive fixture, assert the final local state, assert one push per distinct touched alias, and assert no push for a failed or dry-run alias. A test that calls a lower-level writer is insufficient because later command writes can occur after it.

**Shared test harness:** `tests/conftest.py:15-52` already defines `FakeDatabaseConfig`, whose `engine` and `remote_engine` are the same real SQLite engine and whose `push()`/`pull()`/`sync()` increment counters. Every test below uses `fake_db_factory` and asserts on `db.pushes`. Do not use `MagicMock` — `remote_engine` is a real property, so a mock satisfies it silently and that is exactly how these 18 gaps survived their existing tests.

### Task 7: Push watchlist writes

**Files:**
- Modify: `src/mkts_backend/cli_tools/add_watchlist.py:93-104`
- Modify: `src/mkts_backend/utils/db_utils.py:add_missing_items_to_watchlist` (make failure distinguishable from success; it currently returns truthy error strings)
- Test: `tests/test_management_push.py` (create)

**Interfaces:**
- Consumes: `FakeDatabaseConfig` from `tests/conftest.py`.
- Produces: `tests/test_management_push.py` module, extended by Tasks 8–12.

`utils/db_utils.py:add_missing_items_to_watchlist:16` commits at `:89` and is called from three places — `add_watchlist.py`, `utils/parse_fits.py:837`, `cli_tools/fit_update.py:1086`. Push at the **command** boundary, not inside the writer: `parse_fits.py:837` and `fit_update.py:1086` are mid-command and get their push from Tasks 9 and 10.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_management_push.py
"""Every management command must push its writes to Turso.

Under pyturso, DatabaseConfig.engine and DatabaseConfig.remote_engine are the
same local engine: a commit through either leaves the write in the local CDC
queue. Only push() sends it. These tests assert the local row AND the push,
because a test that checks only the row passes on a command whose writes never
leave the machine.
"""
from sqlalchemy import text


class TestWatchlistPush:
    def test_add_watchlist_pushes_after_insert(self, tmp_path, monkeypatch, fake_db_factory):
        from mkts_backend.cli_tools import add_watchlist

        db = fake_db_factory(tmp_path / "market.db", alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY, "
                "type_name TEXT, group_id INTEGER, group_name TEXT, "
                "category_id INTEGER, category_name TEXT)"
            ))
        monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(add_watchlist, "DatabaseConfig", lambda *a, **k: db)

        assert add_watchlist.add_watchlist(["--type-id=34"], market_alias="primary")

        with db.engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM watchlist WHERE type_id = 34")
            ).scalar() == 1
        assert db.pushes == 1, "watchlist insert never reached Turso"

    def test_push_failure_fails_the_command(self, tmp_path, monkeypatch, fake_db_factory):
        from mkts_backend.cli_tools import add_watchlist

        db = fake_db_factory(tmp_path / "market.db", alias="wcmktnewkeeptest")
        with db.engine.begin() as conn:
            conn.execute(text("CREATE TABLE watchlist (type_id INTEGER PRIMARY KEY)"))

        def boom():
            raise RuntimeError("turso unreachable")

        db.push = boom
        monkeypatch.setattr("mkts_backend.utils.db_utils.DatabaseConfig", lambda *a, **k: db)
        monkeypatch.setattr(add_watchlist, "DatabaseConfig", lambda *a, **k: db)

        assert add_watchlist.add_watchlist(["--type-id=34"], market_alias="primary") is False
```

Adjust the `main()` call signature to whatever `add_watchlist.py:32` actually takes — read it first; do not assume.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_management_push.py::TestWatchlistPush -q`
Expected: FAIL on `assert db.pushes == 1` — actual `0`.

- [ ] **Step 3: Add the push**

`add_watchlist.py` currently has no `db` object in the loop: `process_add_watchlist()` delegates to `add_missing_items_to_watchlist()`, which constructs and discards its own `DatabaseConfig`. It also treats error strings as success because every non-empty string is truthy. First make the writer return an unambiguous success/count or raise, then at the public loop boundary push a fresh config for the same alias:

```python
            if not process_add_watchlist(type_ids, remote=remote, db_alias=db_alias):
                all_ok = False
                continue
            try:
                DatabaseConfig(db_alias).push()
            except Exception as exc:
                logger.error(f"{db_alias}: push failed: {exc}")
                all_ok = False
```

Do not run `_mirror_to_build_watchlist(...)` unless every requested market write and push succeeded. The mirror already pushes through `upsert_build_watchlist`; its failure remains best-effort after the market-side durability guarantee has been met.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_management_push.py -q`
Expected: 2 passed

- [ ] **Step 5: Verify live**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend add_watchlist --market=primary --type-id=34 2>&1 | tail -5
uv run python -c "
from mkts_backend.config.db_config import DatabaseConfig
db = DatabaseConfig('wcmktnewkeeptest'); db.pull()
from sqlalchemy import text
with db.engine.connect() as c:
    print(c.execute(text('SELECT count(*) FROM watchlist WHERE type_id=34')).scalar())
"
```

Expected: `1` after a fresh pull — proving the row round-tripped through Turso rather than sitting in the local queue.

- [ ] **Step 6: Commit**

```bash
git add src/mkts_backend/cli_tools/add_watchlist.py tests/test_management_push.py
git commit -m "fix: push watchlist additions to Turso"
```

---

### Task 8: Push module-equivalent writes and delete the fake remote sync

**Files:**
- Modify: `src/mkts_backend/db/equiv_handlers.py:269-314` (delete `sync_equiv_to_remote`), `:213`, `:238`, `:265` (its call sites)
- Modify: `src/mkts_backend/cli_tools/equiv_manager.py:141` (`_equiv_add_all`), `:169` (`_equiv_remove_all`)
- Test: `tests/test_equiv_remote_sync.py`, `tests/test_management_push.py`

**Interfaces:**
- Consumes: Task 7's test module.
- Produces: nothing.

`sync_equiv_to_remote()` reads the local `module_equivalents` table, then deletes and reinserts that same local table through `remote_engine` (an alias of `engine`), and never pushes. Its docstring still cites libsql's pull-only `sync()`. Deleting it is strictly better than adding a push: the function's entire purpose was to substitute for a push that now exists.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_management_push.py`:

```python
class TestEquivPush:
    def test_equiv_add_pushes_each_market(self, tmp_path, monkeypatch, fake_db_factory):
        from mkts_backend.cli_tools import equiv_manager
        from mkts_backend.config.settings_service import get_all_market_contexts

        dbs = {}

        def factory(alias=None, market_context=None):
            key = alias or market_context.database_alias
            if key not in dbs:
                db = fake_db_factory(tmp_path / f"{key}.db", alias=key)
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE TABLE module_equivalents ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "equiv_group_id INTEGER, type_id INTEGER, type_name TEXT)"
                    ))
                dbs[key] = db
            return dbs[key]

        monkeypatch.setattr("mkts_backend.config.db_config.DatabaseConfig", factory)
        equiv_manager.main(["add", "--type-ids=11269,11270", "--all"])

        expected = {c.database_alias for c in get_all_market_contexts().values()}
        assert set(dbs) == expected
        assert all(db.pushes == 1 for db in dbs.values()), {
            k: v.pushes for k, v in dbs.items()
        }

    def test_one_market_failure_does_not_abort_the_rest(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """equiv iterates every configured market; a single unreachable
        remote must not leave the others unwritten."""
        # build the same factory, make the first alias' push raise,
        # assert the remaining aliases still reached pushes == 1
```

```python
# tests/test_equiv_remote_sync.py — replace the sync_equiv_to_remote tests
class TestSyncEquivToRemoteRemoved:
    """sync_equiv_to_remote() read the local table, then deleted and
    reinserted that same local table through remote_engine (an alias of
    engine) and never pushed. push() replaces it."""

    def test_function_is_gone(self):
        from mkts_backend.db import equiv_handlers

        assert not hasattr(equiv_handlers, "sync_equiv_to_remote")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_management_push.py::TestEquivPush tests/test_equiv_remote_sync.py -q`
Expected: FAIL — `sync_equiv_to_remote` still present; `db.pushes == 0`.

- [ ] **Step 3: Delete the function and add pushes**

- Delete `equiv_handlers.py:269-314` entirely.
- Delete its three call sites at `:213`, `:238`, `:265`.
- Import `DatabaseConfig` in `equiv_manager.py`. Inside each of the two market loops (`_equiv_add_all:111` and `_equiv_remove_all:154`), push a fresh config for the same `MarketContext` after the handler returns; the handler does not return its internal `db`:

```python
                try:
                    DatabaseConfig(market_context=market_ctx).push()
                except Exception as exc:
                    logger.error(f"{market_ctx.database_alias}: push failed: {exc}")
                    ok = False
                    continue
```

Return `ok` from both functions so one unreachable market fails the command without skipping the rest.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_management_push.py tests/test_equiv_remote_sync.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q` — no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/mkts_backend/db/equiv_handlers.py src/mkts_backend/cli_tools/equiv_manager.py tests/
git commit -m "fix: push equiv writes per market; delete the local-only sync_equiv_to_remote"
```

---

### Task 9: Push fittings-database writes

**Files:**
- Modify: `src/mkts_backend/utils/parse_fits.py:704` (`update_fit_workflow`)
- Modify: every public caller of `update_fit_workflow` in `cli_tools/fit_update.py` and `cli_tools/command_registry.py`
- Modify: `cli_tools/fit_update.py:1627` (`create_doctrine_command`)
- Test: `tests/test_management_push.py`

**Interfaces:** consumes Task 7's module; produces nothing.

Writers on the shared `fittings` replica: `upsert_fittings_fitting:517`, `insert_fit_items_to_db:446`, `create_doctrine:296`, `add_doctrine_to_watch:385`, `ensure_doctrine_link:568`, `remove_doctrine_link:613`, `remove_all_doctrine_links_for_fit:651`.

**Caution:** `insert_fit_items_to_db:446` and `upsert_fittings_fitting:517` toggle `PRAGMA foreign_keys` off around a delete+insert (`:461`, `:504`, `:532`, `:561`). That churns rowids in `fittings_fittingitem`. The table has no secondary `UNIQUE`, so the documented push failure does not apply — but verify with a real push in Step 5 rather than trusting the analysis.

- [ ] **Step 1: Write the failing test**

```python
class TestFittingsPush:
    def test_update_fit_workflow_pushes_fittings_db(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """update_fit_workflow writes the fittings replica and the market
        replica; both must push."""
        # build fittings + market FakeDatabaseConfig, patch DatabaseConfig,
        # run update_fit_workflow on a small EFT fixture,
        # assert fittings_db.pushes == 1 and market_db.pushes == 1

    def test_multi_market_update_pushes_fittings_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """The workflow is called once per market, but fittings is shared;
        the outer CLI invocation must push it only once."""
        # update a fit present in two markets; assert fittings pushes == 1,
        # and each market pushes == 1 after its final write.
```

Use an existing EFT fixture from `tests/test_eft_parser.py` rather than inventing one.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_management_push.py::TestFittingsPush -q` → FAIL, `pushes == 0`.

- [ ] **Step 3: Add the push**

Do **not** push inside `update_fit_workflow`: it is invoked once per market, repeats writes to the shared fittings database, and in interactive add mode the caller writes `market_flag` and `lead_ship` after the workflow returns. Add an optional `touched_aliases: set[str]` accumulator; after successful writes the workflow adds `"fittings"` and the resolved target market database alias. Every outer caller must create one set for the whole CLI invocation, pass it through every workflow call, add aliases for its later writes, and finally push each distinct alias once. Dry runs never add aliases and never push.

At the end of the standalone `create_doctrine_command` (`fit_update.py:1627`), after `create_doctrine()` returns successfully:

```python
    DatabaseConfig("fittings").push()
```

Do not push inside `create_doctrine`/`upsert_fittings_fitting`/`insert_fit_items_to_db` — they are called several times within one command and a push per call would multiply round trips for no benefit. Add a coverage assertion for every `update_fit_workflow(` call site so a new caller cannot omit the accumulator/finalizer silently.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_management_push.py -q`

- [ ] **Step 5: Verify a real push survives the rowid churn**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend fit-update update --fit=<an existing test fit id> --market=primary
uv run python -c "
from mkts_backend.config.db_config import DatabaseConfig
DatabaseConfig('fittings').pull(); print('pull after push OK')
"
```

Expected: the command succeeds and the follow-up pull succeeds. **If the push raises `UNIQUE constraint failed`,** the delete+insert must become an upsert before this task can close — record which table and stop.

- [ ] **Step 6: Commit**

```bash
git add src/mkts_backend/utils/parse_fits.py src/mkts_backend/cli_tools/fit_update.py tests/test_management_push.py
git commit -m "fix: push fittings-database writes to Turso"
```

---

### Task 10: Push market doctrine writes

**Files:**
- Modify: `src/mkts_backend/utils/parse_fits.py:772-838` (market half of `update_fit_workflow`)
- Modify: `src/mkts_backend/cli_tools/fit_update.py:2735` (`update_target_command`), `:2832` (`update_friendly_name_command`), `:2862` (`populate_friendly_names_command`), `:2224` (`remove_fit_command`), `:2445` (`doctrine_remove_fit_command`)
- Modify: `src/mkts_backend/utils/doctrine_update.py:1388` — delete `sync_friendly_names_to_remote`
- Test: `tests/test_management_push.py`, `tests/test_doctrine_fit_remote.py`

**Interfaces:** consumes Task 7's module; produces nothing.

`sync_friendly_names_to_remote:1388` is the same self-referential shape as `sync_equiv_to_remote`: it reads one local replica and writes each target local replica through the alias. Delete the helper, but preserve the intended fan-out across all configured markets.

`update_friendly_name_command` currently updates `db_alias` once as "local", then loops every configured market as "remote"; the default alias is duplicated, while the other market writes are intentional. Replace the local/remote split with one deduplicated target-alias loop and push each successfully updated alias once. `populate_friendly_names_command` needs the same fan-out; pushing only the source alias would silently stop propagating friendly names to the other markets.

`refresh_doctrines_for_fit:1077` does `DELETE FROM doctrines WHERE fit_id` (`:1116`) then re-INSERT (`:1143`) on an AUTOINCREMENT PK. Same churn caution as Task 9 — verify with a real push.

- [ ] **Step 1: Write the failing tests**

```python
class TestDoctrinePush:
    def test_update_target_pushes(self, tmp_path, monkeypatch, fake_db_factory):
        ...  # assert doctrine_fits row updated AND db.pushes == 1

    def test_update_friendly_name_writes_once_and_pushes_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """The command used to write the same replica twice under a
        local/remote split that no longer exists."""
        ...  # assert the UPDATE ran once and db.pushes == 1

    def test_remove_fit_pushes_market_and_fittings(self, tmp_path, monkeypatch, fake_db_factory):
        ...  # both replicas push exactly once
```

```python
# tests/test_doctrine_fit_remote.py (append)
def test_sync_friendly_names_to_remote_removed():
    from mkts_backend.utils import doctrine_update

    assert not hasattr(doctrine_update, "sync_friendly_names_to_remote")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_management_push.py::TestDoctrinePush tests/test_doctrine_fit_remote.py -q`

- [ ] **Step 3: Apply the edits**

- `parse_fits.py`: add the resolved market alias to Task 9's `touched_aliases` accumulator after the market-side block. There is no `market_db` variable here, and pushing inside this workflow would occur before later interactive-add writes.
- `fit_update.py:2762` (`_update_target_single`): after `upsert_ship_target` at `:2816`, add `db.push()`.
- `fit_update.py:2832`: replace the local-plus-remote split with a deduplicated loop over `{db_alias, *_configured_market_db_aliases()}`; update each alias once and then push each successful alias once.
- `fit_update.py:2862`: delete `sync_friendly_names_to_remote`; run `populate_friendly_names_from_json` once per distinct configured market alias and push each successful alias once.
- `fit_update.py:2224` and `:2445`: these functions have no `market_db`/`fittings_db` variables and currently call independently committing helpers. Refactor the helpers to participate in caller-owned per-database transactions; after successful commit, call `DatabaseConfig(db_alias).push()` and, only when a fittings link changed, `DatabaseConfig("fittings").push()`.
- `doctrine_update.py`: delete `sync_friendly_names_to_remote:1388-1453`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_management_push.py tests/test_doctrine_fit_remote.py -q`

- [ ] **Step 5: Verify a real push after the doctrines delete+insert**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend update-target --fit=<test fit id> --target=5 --market=primary
uv run python -c "
from mkts_backend.config.db_config import DatabaseConfig
DatabaseConfig('wcmktnewkeeptest').pull(); print('pull after push OK')"
```

- [ ] **Step 6: Commit**

```bash
git add src/mkts_backend/utils/ src/mkts_backend/cli_tools/fit_update.py tests/
git commit -m "fix: push market doctrine writes; drop the duplicate local/remote write split"
```

---

### Task 11: Push multi-alias loop commands

**Files:**
- Modify: `src/mkts_backend/cli_tools/fit_update.py:1244` (`_execute_market_plan`, loops at `:1368` and `:1415`), `:2146` (`doctrine_add_fit_command`), `:2419` (`update_lead_ship_command`)
- Test: `tests/test_management_push.py`, `tests/test_fit_update_assign.py`

**Interfaces:** consumes Task 7's module; produces nothing.

These commands write several market replicas in one invocation. The push must come **after** both loops in `_execute_market_plan` — the orphan-cleanup pass at `:1415` can add writes to a bucket already processed by `:1368` — and must be per distinct alias. Each alias bucket must use one transaction so a raised bucket is rolled back; merely skipping its push would leave partial CDC rows for a later command to publish.

- [ ] **Step 1: Write the failing test**

```python
class TestMultiAliasPush:
    def test_assign_market_pushes_each_touched_alias_once(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """Two markets touched, two pushes, one each — not one push for the
        first alias repeated, and not a push per step."""
        ...

    def test_alias_whose_bucket_raised_is_not_pushed(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """A failed bucket must not push a half-applied plan."""
        ...

    def test_orphan_cleanup_writes_are_included_in_the_push(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """The second loop at fit_update.py:1415 adds writes after the first
        loop; a push placed inside the first loop would miss them."""
        ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_management_push.py::TestMultiAliasPush -q`

- [ ] **Step 3: Implement**

In `_execute_market_plan`, track which aliases were written and which failed, then push after both loops:

```python
        pushed_ok = True
        for alias in sorted(touched - failed):
            db = DatabaseConfig(alias)
            try:
                db.push()
            except Exception as exc:
                logger.error(f"{alias}: push failed: {exc}")
                pushed_ok = False
        return pushed_ok and not failed
```

Populate `touched` where each bucket's writes are applied (`:1368` and `:1415`) and `failed` in the existing bucket-level `except`. Apply the same shape to `doctrine_add_fit_command` (`:2146`) and `update_lead_ship_command` (`:2419`), which each loop over `target_aliases`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_management_push.py tests/test_fit_update_assign.py -q`

- [ ] **Step 5: Verify live across two markets**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend fit-update assign-market --fit=<test fit id> --market=deployment
uv run python -c "
from mkts_backend.config.db_config import DatabaseConfig
for a in ('wcmktnewkeeptest', 'wcmktnorth2test'):
    DatabaseConfig(a).pull()
print('pulls after push OK')"
```

- [ ] **Step 6: Commit**

```bash
git add src/mkts_backend/cli_tools/fit_update.py tests/
git commit -m "fix: push each market touched by assign-market and its siblings"
```

---

### Task 12: Fix the structure and buildcost seams

**Files:**
- Modify: `src/mkts_backend/cli_tools/add_structure.py:154`, `:165`, `:176-185`, `:249-251`
- Modify: `src/mkts_backend/cli_tools/add_watchlist.py:136`
- Modify: `src/mkts_backend/builder_costs/repository.py:29-39`
- Test: `tests/test_add_structure.py`, `tests/test_management_push.py`, `tests/test_buildcost_runner.py`

**Interfaces:** consumes Task 7's module; produces nothing.

Three separate defects:

1. **`add_structure` writes twice.** `:154` calls `upsert_structures(db.remote_engine, …)` and `:165` calls `upsert_structures(db.engine, …)`. Both are the same local replica, so the rows are written twice and the `--remote-only` / `--local` flags do nothing. The "partial success" warnings at `:176-185` can never fire. Delete the `:154` call and the flag branching; write once, then push.
2. **`_mirror_to_build_watchlist` pulls where it means to push.** `add_watchlist.py:136` calls `buildcost_db.sync()`. The data does still reach Turso — `builder_costs/repository.py:upsert_build_watchlist` pushes at `:161` — so this is a wrong-direction no-op that pulls back over freshly pushed state, not data loss. Delete the `sync()` call. Keep the surrounding blanket `try/except` (`:114`, `:140`): buildcost is optional and a missing `TURSO_BUILDCOST_*` must not fail the market-side write.
3. **`init_buildcost_tables` DDL is never pushed.** `repository.py:29` runs `create(checkfirst=True)` at `:39` with no push. Worse, `runner.run()` calls it *before* `verify_db_exists()`, so a missing replica can be created locally before bootstrap. Verify/pull all three replicas first, then initialize buildcost schema. Detect whether any table was actually absent and push once only when DDL was created; a no-op daily run must not make an unnecessary schema push.

- [ ] **Step 1: Write the failing tests**

```python
class TestStructureAndBuildcostSeams:
    def test_add_structure_writes_once_and_pushes(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """upsert_structures used to run twice against the same replica."""
        calls = []
        # patch upsert_structures to record its engine argument
        # assert len(calls) == 1 and db.pushes == 1

    def test_mirror_does_not_pull_after_writing(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """add_watchlist.py:136 called sync() — a pull — where a push was
        meant; the pull overwrote freshly pushed local state."""
        # run the mirror; assert buildcost_db.pulls == 0

    def test_mirror_failure_does_not_fail_the_market_write(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        """buildcost is optional."""

    def test_init_buildcost_tables_pushes_schema(
        self, tmp_path, monkeypatch, fake_db_factory
    ):
        from mkts_backend.builder_costs import repository

        db = fake_db_factory(tmp_path / "buildcost.db", alias="buildcost")
        repository.init_buildcost_tables(db)
        assert db.pushes == 1

    def test_existing_buildcost_schema_does_not_push(self, tmp_path, monkeypatch, fake_db_factory):
        # Initialize once, reset the counter, call again; assert pushes == 0.

    def test_runner_verifies_replica_before_schema_init(monkeypatch):
        # Record calls and assert verify_db_exists precedes init_buildcost_tables.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_management_push.py::TestStructureAndBuildcostSeams tests/test_add_structure.py -q`

- [ ] **Step 3: Apply the three fixes**

- `add_structure.py`: delete the `:154` `upsert_structures(db.remote_engine, …)` call and the `--remote-only`/`--local` branching around it; delete the now-unreachable warnings at `:176-185`; add `db.push()` after the surviving `:165` call. Also replace the ad-hoc `-info` unlink at `:249-251` with `db.heal_metadata()` (Task 2) and delete its stale libsql comment.
- `add_watchlist.py`: delete the `buildcost_db.sync()` call at `:136`; update the `_mirror_to_build_watchlist` docstring at `:111-112` to state that `upsert_build_watchlist` pushes.
- `builder_costs/runner.py`: move the `verify_db_exists()` loop before `init_buildcost_tables(buildcost_db)`.
- `builder_costs/repository.py`: inspect the three target table names before creation, create missing tables with their final names, and call `db.push()` once only if at least one table was created.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_management_push.py tests/test_add_structure.py tests/test_buildcost_runner.py -q`

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: ≥ 411 passed (plus the new tests), 0 failed.

- [ ] **Step 6: Re-audit `remote_engine`; do not remove it in this task**

Run:

```bash
cd /home/orthel/workspace/github/mkts-turso
rg -n "remote_engine|remote=True|remote=False" src tests
```

The current tree has many read and compatibility consumers across `db_utils.py`, `doctrine_update.py`, `parse_fits.py`, and `fit_update.py`; deleting the property here would require broad unlisted edits and would mix cleanup with the durability fixes. Confirm no **writer** still relies on the alias, retain the compatibility property for this migration, and open a separately scoped cleanup after cutover.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "fix: single structure write with push; drop the mirror's wrong-direction pull

Leaves the compatibility remote_engine alias in place; its full removal needs
a separately scoped read/compatibility audit."
```

---

# Phase 5 — Frontend

All tasks run in `/home/orthel/workspace/github/wcmkts-pyturso-migration` on branch `pyturso-migration-main`. Baseline: 644 passed, 22 subtests passed.

### Task 13: Open sync-managed replicas through the sync dialect

**Files:**
- Modify: `config.py:141` (default `dialect` parameter), `:152` (URL construction)
- Test: `tests/test_pyturso_dialect.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DatabaseConfig(alias).url` built on `sqlite+turso_sync` for every sync-managed replica.

**Why this is a blocker, not documentation debt:** the frontend builds every engine on `sqlite+turso` (`config.py:141`; no call site passes `dialect=`) while pulling through raw `turso.sync.connect()` (`config.py:259-264`). A plain `sqlite+turso` connection auto-checkpoints the WAL at 1000 frames, which destroys the baseline `pull()` needs and panics turso core in `wal.rs` `frame_watermark`. The backend hit this and fixed it by moving to `sqlite+turso_sync` (`db_config.py:120-137` carries the comment). The frontend has the same replicas and the same pull path, and has not been fixed. Switching the dialect does **not** make the frontend push — it stays read-only by policy, which Task 17 documents.

The frontend also has an intentional cached/local-only degraded mode. Preserve it explicitly: a replica with remote credentials uses `sqlite+turso_sync` plus `connect_args`; a no-credential test fixture or degraded read must use an ordinary SQLite **read-only** connection and must never later call `sync()` in the same process. Do not pass `remote_url=None` to the sync dialect, and do not use the plain `sqlite+turso` dialect as the fallback.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pyturso_dialect.py (append)
class TestSyncDialect:
    """Sync-managed replicas must not be opened on the plain dialect: it
    auto-checkpoints the WAL at 1000 frames and destroys the pull baseline."""

    def test_default_dialect_is_sync(self):
        import inspect
        from config import DatabaseConfig

        sig = inspect.signature(DatabaseConfig.__init__)
        assert sig.parameters["dialect"].default == "sqlite+turso_sync"

    def test_every_configured_replica_uses_the_sync_dialect(self):
        from config import DatabaseConfig
        from settings_service import get_all_market_configs

        aliases = [c.database_alias for c in get_all_market_configs().values()]
        aliases += ["sde", "build_cost"]
        for alias in aliases:
            assert DatabaseConfig(alias).url.startswith("sqlite+turso_sync:///"), alias

    def test_plain_dialect_is_absent_from_the_codebase(self):
        """Guard against a reintroduction."""
        import pathlib
        import re

        hits = []
        for p in pathlib.Path(".").rglob("*.py"):
            if ".venv" in p.parts or "tests" in p.parts:
                continue
            if re.search(r'"sqlite\+turso"|\'sqlite\+turso\'', p.read_text()):
                hits.append(str(p))
        assert hits == [], hits

    def test_no_credentials_uses_read_only_sqlite_without_sync_args(self, tmp_path):
        # Construct a configured local replica without Turso credentials;
        # assert reads work, writes fail, and no sqlite+turso dialect is used.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/orthel/workspace/github/wcmkts-pyturso-migration && uv run pytest tests/test_pyturso_dialect.py::TestSyncDialect -q`
Expected: FAIL — default is `"sqlite+turso"`.

- [ ] **Step 3: Change the default**

For credentialed replicas, `config.py:141`:

```python
    def __init__(self, alias, dialect: str = "sqlite+turso_sync"):
```

The sync dialect needs the remote to be reachable at connect time, so `:152` must also pass the connect args the backend uses (`db_config.py:120-137`):

```python
        self.url = f"{dialect}:///{self.path}"
        self._connect_args = {
            "remote_url": self.turso_url,
            "auth_token": self.token,
        }
```

and the `create_engine(...)` call must pass `connect_args=self._connect_args`. Because `_engines` is shared by alias, its cache key must include the engine mode (credentialed sync versus local read-only), or a test/degraded engine created first can be reused after credentials appear. Read the surrounding engine construction before editing; do not guess where it lives.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pyturso_dialect.py -q`

- [ ] **Step 5: Verify the app actually reads**

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
uv run pytest -q                      # expect >= 644 passed
uv run streamlit run app.py --server.headless true &
sleep 20 && curl -sf http://localhost:8501/_stcore/health && echo " health OK"
kill %1
```

Then open the app and load the market page, the doctrines page and the build-costs page. A `wal.rs` panic or a `turso.lib.DatabaseError` here means the dialect change surfaced a latent problem — record it and stop.

- [ ] **Step 6: Commit**

```bash
git add config.py tests/test_pyturso_dialect.py
git commit -m "fix: open sync-managed replicas through sqlite+turso_sync

The plain sqlite+turso dialect auto-checkpoints the WAL at 1000 frames,
destroying the baseline pull() needs. The backend fixed this; the frontend
had the same replicas and the same pull path."
```

---

### Task 14: Port the metadata classifier

**Files:**
- Create: `replica_metadata.py` (verbatim copy of `src/mkts_backend/config/replica_metadata.py` from Task 1, imports adjusted)
- Modify: `config.py:198-211` (`_replica_metadata_valid`), `init_db.py:28-69` (`verify_db_content`)
- Test: `tests/test_replica_metadata.py` (copy of the backend file, import path adjusted)

**Interfaces:**
- Consumes: backend Task 1's classifier contract and Task 3's fail-closed remote-identity rule.
- Produces: `classify_metadata`, `metadata_remote_url`, and frontend remote-compatibility checks for both live and backup replica pairs.

Both existing checks parse the `-info` as JSON and accept anything that parses. A libsql `-info` (`{"hash": …, "version": 0}`) is valid JSON, so the "deploy-day upgrade path" both docstrings describe never fires. Duplicating the module across the two repositories is correct — they share no package, and a shim would be more code than the 60 lines it saves.

- [ ] **Step 1: Copy the module and its tests**

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
cp /home/orthel/workspace/github/mkts-turso/src/mkts_backend/config/replica_metadata.py .
cp /home/orthel/workspace/github/mkts-turso/tests/test_replica_metadata.py tests/
sed -i 's/from mkts_backend\.config\.replica_metadata import/from replica_metadata import/' tests/test_replica_metadata.py
```

Remove `TestRemoteMatchesMetadata` from the copied test file for now — it depends on the backend `DatabaseConfig` shape. Step 4 reintroduces the frontend equivalent.

- [ ] **Step 2: Run the copied tests**

Run: `uv run pytest tests/test_replica_metadata.py -q`
Expected: 12 passed. If the frontend's `pytest.ini` `pythonpath = .` does not pick up the module, fix the import rather than moving the file.

- [ ] **Step 3: Write the failing integration test**

```python
# tests/test_init_db.py (append)
class TestLibsqlMetadataRejected:
    """A libsql-era -info parses as JSON, so the old checks accepted it and
    the first engine call raised turso.lib.DatabaseError."""

    def test_replica_metadata_valid_rejects_libsql(self, tmp_path):
        import json
        from config import DatabaseConfig

        db = DatabaseConfig.__new__(DatabaseConfig)
        db.path = str(tmp_path / "m.db")
        (tmp_path / "m.db").write_bytes(b"x")
        (tmp_path / "m.db-info").write_text(
            json.dumps({"hash": "0" * 64, "version": 0, "generation": 1})
        )
        assert db._replica_metadata_valid() is False

    def test_verify_db_content_rejects_libsql(self, tmp_path):
        import json
        import sqlite3
        from init_db import verify_db_content

        p = tmp_path / "m.db"
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE t (a INTEGER)")
        con.commit()
        con.close()
        (tmp_path / "m.db-info").write_text(
            json.dumps({"hash": "0" * 64, "version": 0, "generation": 1})
        )
        assert verify_db_content(str(p)) is False

    def test_sync_refuses_live_replica_from_different_remote(self, tmp_path):
        # Valid pyturso metadata naming a test remote + configured production
        # URL must raise before _pull_once() or engine construction.

    def test_restore_refuses_backup_from_different_remote(self, tmp_path):
        # A .db.bak/.db-info.bak pair from test must not be restored after
        # production secrets are configured.
```

- [ ] **Step 4: Run test to verify it fails, then implement**

Run: `uv run pytest tests/test_init_db.py::TestLibsqlMetadataRejected -q` → FAIL (both return `True`).

Replace `config.py:198-211`:

```python
    def _replica_metadata_valid(self) -> bool:
        """True when {path}-info is usable pyturso metadata.

        A libsql-era -info is valid JSON, so parsing alone accepts a file
        pyturso cannot use; classify_metadata checks the shape.
        """
        from replica_metadata import classify_metadata

        return classify_metadata(self.path) == "pyturso"
```

In `init_db.py:28-69`, replace the trailing `json.load` block with:

```python
        from replica_metadata import classify_metadata

        kind = classify_metadata(path)
        if kind != "pyturso":
            logger.warning(
                f"DB {path} has {kind} metadata, not pyturso; treating as not ready"
            )
            return False
        return True
```

`_ensure_replica_consistency()` (`config.py:232-250`) already routes an invalid-shape check through `_remove_replica_files()` and a fresh bootstrap, but valid metadata naming a different remote would still pass. Add a fail-closed comparison before `_pull_once()` and before SQLAlchemy engine construction. Do not auto-delete a remote mismatch. `restore_from_backup()` must classify the backup `-info.bak`, compare its recorded remote to current configuration, and refuse a mismatch before replacing live files.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_init_db.py tests/test_replica_metadata.py tests/test_sync_pyturso.py -q`
Then: `uv run pytest -q` — expect ≥ 644 passed.

- [ ] **Step 6: Verify the live replicas classify correctly**

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
uv run python -c "
from replica_metadata import classify_metadata, metadata_remote_url
for f in ('wcmktnewkeep.db','wcmktnorth2.db','wcmktbkg.db','sdelite.db','buildcost.db'):
    print(f'{f:20} {classify_metadata(f):8} {metadata_remote_url(f)}')
"
```

Expected: all five `pyturso`, all five URLs ending in `test-orthelt.aws-us-east-1.turso.io`. **This is the evidence that production-named files currently hold test data** — capture the output for the cutover record (Task 22).

- [ ] **Step 7: Commit**

```bash
git add replica_metadata.py config.py init_db.py tests/
git commit -m "fix: reject libsql-era replica metadata by shape, not by JSON parse"
```

---

### Task 15: Move the `industry_index` write to a local-only cache database

**Files:** modify `repositories/build_cost_repo.py:105-108`, `:214-216`, `:238`; test `tests/test_build_cost_repo.py`.

**Interfaces:** consumes nothing; produces a process-shared `_cache_engine()` and cache-specific read/write helpers in `repositories/build_cost_repo.py`.

**Context:** `repositories/build_cost_repo.py:_write_industry_index_impl:105-108` runs `to_sql("industry_index", conn, if_exists="replace")` — DROP + CREATE + INSERT — on the sync-managed `buildcost.db`, with no push. Callers: `build_cost_repo.py:214-216` → `services/build_cost_service.py:334` (`_fetch_and_store_industry_index:289`) and `:263` (`check_and_update_industry_index`) → `pages/build_costs.py:249-259`, reached from page init at `:244`. Any viewer opening the Build Costs page with an expired ESI cache triggers schema DDL into the CDC queue of a shared replica.

- [ ] **Step 1: Write the failing test**

```python
class TestIndustryIndexIsLocalOnly:
    """industry_index is a per-viewer ESI cache, not shared market data. It
    must not put DDL into the CDC queue of the shared buildcost replica."""

    def test_written_to_the_cache_db_not_buildcost(self, tmp_path, monkeypatch):
        # assert the engine passed to _write_industry_index_impl points at
        # streamlit_cache.db, and that buildcost.db has no industry_index table

    def test_buildcost_replica_untouched_by_a_page_load(self, tmp_path, monkeypatch):
        # Run the refresh path; assert buildcost has no industry_index table
        # and the cache DB does. Do not use mtime as the oracle: opening SQLite
        # can legitimately update sidecars without changing application data.
```

- [ ] **Step 2: Run test to verify it fails.** Run: `uv run pytest tests/test_build_cost_repo.py -q`

- [ ] **Step 3: Implement.** Add a module-level cache engine beside the pattern the backend uses for `cli_cache.db`:

```python
# repositories/build_cost_repo.py
from functools import lru_cache
from pathlib import Path

_CACHE_DB = Path(__file__).resolve().parents[1] / "streamlit_cache.db"

@lru_cache(maxsize=1)
def _cache_engine():
    """Local-only SQLite for per-viewer ESI caches.

    industry_index is refetched from ESI on expiry and is not shared state, so
    it does not belong in the sync-managed buildcost replica: to_sql(
    if_exists="replace") emits DROP/CREATE DDL, and pyturso replays DDL from
    sqlite_schema text on push.
    """
    from sqlalchemy import create_engine

    return create_engine(f"sqlite:///{_CACHE_DB}")
```

Point `write_industry_index()` (`:214-216`) and every `industry_index` **read** at `_cache_engine()`. Do not route cache reads through `_build_cost_reader()`/`BaseRepository`: its recovery ladder syncs the shared replica and is the wrong policy for a disposable local cache. Use a small direct `pd.read_sql_query` helper that returns a cache miss cleanly when the table does not exist. Find all reads first:

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
grep -rn "industry_index" --include='*.py' . | grep -v '\.venv' | grep -v tests/
```

Add `streamlit_cache.db*` to `.gitignore`.

- [ ] **Step 4: Run tests to verify they pass.** Run: `uv run pytest -q`

- [ ] **Step 5: Drop the stranded table from the replica**

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
uv run python -c "
import sqlite3
c = sqlite3.connect('buildcost.db')
print(c.execute(\"SELECT name FROM sqlite_master WHERE name='industry_index'\").fetchall())
"
```

If present, it was created locally and never pushed, so the authoritative fix is a full replica-bundle rebuild rather than issuing a DROP into CDC. Use the frontend replica-removal routine after disposing engines, remove the corresponding `.bak` pair too, then restart the app to trigger `init_db()`. Do not use an unreviewed `rm buildcost.db*` glob.

- [ ] **Step 6: Commit**

```bash
git add repositories/build_cost_repo.py .gitignore tests/
git commit -m "fix: keep the industry_index ESI cache out of the shared buildcost replica"
```

---

### Task 16: Align frontend dependencies

**Files:** modify `pyproject.toml:8,14,17,18,20`; regenerate `uv.lock`.

**Interfaces:** consumes nothing; produces nothing.

- [ ] **Step 1: Record the current state**

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
uv run python -c "import turso, sqlalchemy; print(turso.__version__, sqlalchemy.__version__)"
ls .venv/lib/python*/site-packages | grep -i libsql
```

Expected: `0.7.1 2.0.49`, plus `libsql-0.1.11` and `libsql_experimental-0.0.55` — installed but absent from `pyproject.toml`.

- [ ] **Step 2: Edit `pyproject.toml`**

- `pyturso>=0.7.1` → `pyturso>=0.7.2` (match the backend).
- `sqlalchemy>=2.0.25` → `sqlalchemy>=2.0.42`. The dialect's package metadata declares only `>=2.0`, which under-declares its real floor — the dialect is first-party (`~/workspace/turso-dev`). Add a comment saying so, since the declared floor and the metadata disagree and the next reader will otherwise "correct" it back.
- Delete `asyncio>=4.0.0` (`:8`), `sql>=2022.4.0` (`:17`), `typing>=3.10.0.0` (`:20`) — PyPI packages shadowing stdlib modules.

- [ ] **Step 3: Rebuild the environment**

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
uv sync
uv run python -c "import turso, sqlalchemy; print(turso.__version__, sqlalchemy.__version__)"
ls .venv/lib/python*/site-packages | grep -i libsql || echo "libsql gone (expected)"
```

Expected: `0.7.2 2.0.49` or later; no `libsql*`. If `uv sync` strips `pyturso` itself, it is not declared correctly — see the backend memory note on the dialect not being declared in `pyproject.toml`.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: ≥ 644 passed. A failure here is a real pyturso 0.7.1→0.7.2 behavior change — investigate, do not pin back.

- [ ] **Step 5: Smoke-test the app**

```bash
uv run streamlit run app.py --server.headless true &
sleep 20 && curl -sf http://localhost:8501/_stcore/health && echo " health OK"; kill %1
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: pyturso 0.7.2, sqlalchemy floor 2.0.42, drop stdlib-shadow deps"
```

- [ ] **Step 7: Align the backend's declared pyturso version**

```bash
cd /home/orthel/workspace/github/mkts-turso
grep -n "pyturso\|sqlalchemy" pyproject.toml
uv sync && uv run python -c "import turso; print(turso.__version__)"
```

Current verified state: the backend already declares `pyturso>=0.7.1` and `sqlalchemy>=2.0.45`. Change only pyturso to `>=0.7.2`; the existing SQLAlchemy floor is already above 2.0.42. Re-run `uv sync`, verify the resolved versions, run the backend suite, and commit:

```bash
git add pyproject.toml uv.lock
git commit -m "chore: align backend on pyturso 0.7.2"
```

This is a prerequisite for Task 19 — a CI test job on a clean runner fails immediately without it.

---

### Task 17: Record the admin deferral accurately

**Files:** modify `pages/admin.py:316`, `pages/admin_doctrines.py:73`, `AGENTS.md`, `README.md`.

**Interfaces:** consumes nothing; produces nothing.

**Owner decision (review `:223-228`):** the local-write-plus-push admin redesign is deferred; keep the disabled state and make the limitation visible.

The review's description is wrong in a way that matters for the release note: `_get_write_engine()` (`admin_repo.py:761-770`) is called by 15 sites including **read** methods (`:115, 123, 140, 156, 163, 177, 183, 190, 197, 214, 237, 246, 297, 337, 468`), so with `write_target = "remote"` (`settings.toml:163`) the entire admin surface raises `NotImplementedError` — both pages are dead, not read-only. Both currently render `Write target: remote` and then fail on first data access.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_admin_repo.py (append)
def test_admin_pages_state_the_disabled_reason_before_failing():
    """Both pages showed 'Write target: remote' and then raised. State the
    deferral instead."""
    from pathlib import Path

    for page in ("pages/admin.py", "pages/admin_doctrines.py"):
        text = Path(page).read_text()
        assert "disabled during the pyturso migration" in text, page
```

- [ ] **Step 2: Run test to verify it fails.** Run: `uv run pytest tests/test_admin_repo.py -q`

- [ ] **Step 3: Replace the status line on both pages**

```python
st.warning(
    "Admin is disabled during the pyturso migration. Reads and writes both "
    "route through the remote write path, which was removed when pyturso "
    "made every engine local. Re-enabling it needs a local-write-plus-push "
    "rework, deferred by decision."
)
st.stop()
```

`st.stop()` before any data access is what turns a stack trace into an explanation.

- [ ] **Step 4: Run tests to verify they pass.** Run: `uv run pytest -q`

- [ ] **Step 5: Delete the dead helper**

`admin_repo.py:772-775` defines `_read_local()`, which nothing calls. Remove it.

- [ ] **Step 6: Commit**

```bash
git add pages/admin.py pages/admin_doctrines.py repositories/admin_repo.py AGENTS.md README.md tests/
git commit -m "docs: state the admin deferral on both pages instead of raising"
```

---

# Phase 6 — CI, caches and scripts

Back in `/home/orthel/workspace/github/mkts-turso`.

### Task 18: Derive workflow database paths from settings

**Files:**
- Modify: `.github/workflows/market-data-collection.yml:112-114`, `:124-125`, `:234-235`
- Modify: `.github/workflows/builder-costs-collection.yml:29-31`, `:49-51`
- Test: `tests/test_workflow_paths.py` (create)

**Interfaces:**
- Consumes: `--db-path` / `--list-db-paths` from Task 5.
- Produces: nothing.

Ten hardcoded filenames across two workflows. The production switch must not require editing YAML (review `:206-217`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_paths.py
"""The workflows must not hardcode database filenames.

Every hardcoded name is a second place the production switch has to be made,
and the two drift silently: a stale cache path restores nothing and the run
cold-pulls, which looks like success.
"""
import re
from pathlib import Path

import pytest

from mkts_backend.config.settings_service import SettingsService

WORKFLOWS = [
    Path(".github/workflows/market-data-collection.yml"),
    Path(".github/workflows/builder-costs-collection.yml"),
]


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_no_database_filename_is_hardcoded(wf):
    text = wf.read_text()
    files = {c["file"] for c in SettingsService().database_routing().values()}
    found = sorted(f for f in files if f in text)
    assert found == [], f"{wf.name} hardcodes {found}"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_no_bare_db_filename_pattern_remains(wf):
    """Catches a renamed database the fixture above would miss."""
    hits = re.findall(r"\b\w*(?:mkt|sde|fitting|buildcost)\w*\.db\b", wf.read_text())
    assert hits == [], f"{wf.name} still names {sorted(set(hits))}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workflow_paths.py -q`
Expected: FAIL listing all six filenames.

- [ ] **Step 3: Replace the market workflow's path resolution**

Replace the `Resolve market DB file` step (`market-data-collection.yml:110-115`, the `db=wcmkt…test.db` case block) with:

```yaml
      - name: Resolve database paths
        id: dbfile
        run: |
          echo "db=$(uv run mkts-backend --db-path=${{ matrix.market }})" >> "$GITHUB_OUTPUT"
          echo "sde=$(uv run mkts-backend --db-path=sde)" >> "$GITHUB_OUTPUT"
          echo "fittings=$(uv run mkts-backend --db-path=fittings)" >> "$GITHUB_OUTPUT"
```

This step must run **after** `uv sync`. Then replace the shared-cache `path:` at `:124-125` and `:234-235` with:

```yaml
          path: |
            ${{ steps.dbfile.outputs.sde }}*
            ${{ steps.dbfile.outputs.fittings }}*
```

`:135` and `:224` already use `${{ steps.dbfile.outputs.db }}*` and need no change.

- [ ] **Step 4: Replace the builder-costs workflow's paths**

Add the same resolution step before the restore at `:29`, adding `buildcost`, then replace `:29-31` and `:49-51`:

```yaml
          path: |
            ${{ steps.dbfile.outputs.sde }}*
            ${{ steps.dbfile.outputs.primary }}*
            ${{ steps.dbfile.outputs.buildcost }}*
```

While here, fix the cache key at `:32` and `:52`: `builder-cost-dbs-v4-${{ github.run_id }}` can never hit on its primary key, so every run restores through `restore-keys` and saves a new entry. Match the market workflow's daily bucket:

```yaml
          key: builder-cost-dbs-v4-${{ steps.cachedate.outputs.date }}
```

Add the `cachedate` step (copy from `market-data-collection.yml:100-103`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_workflow_paths.py -q` → 4 passed

- [ ] **Step 6: Validate the YAML and dry-run the resolution**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run python -c "
import yaml, pathlib
for p in pathlib.Path('.github/workflows').glob('*.yml'):
    yaml.safe_load(p.read_text()); print('ok', p.name)"
for m in primary deployment market3; do
  echo "$m -> $(uv run mkts-backend --db-path=$m)"
done
```

Expected: both files parse; the three markets resolve to `wcmktnewkeeptest.db`, `wcmktnorth2test.db`, `wcmktbkgtest.db`.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ tests/test_workflow_paths.py
git commit -m "ci: derive database paths from settings instead of hardcoding six filenames"
```

---

### Task 19: Add a test job to CI

**Files:** modify `.github/workflows/` (new `tests.yml`).

**Interfaces:** consumes nothing; produces nothing.

Neither workflow runs `pytest` — there is no `pytest` invocation anywhere in `.github/`. The whole of this plan is test-driven, and none of it is currently gated. This task is what makes Phases 1–4 stick.

- [ ] **Step 1: Create the workflow**

```yaml
# .github/workflows/tests.yml
name: Tests

on:
  push:
    branches: ["**"]
  pull_request:

jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: astral-sh/setup-uv@v7
      - run: uv sync
      - run: uv run pytest -q
```

No database caches and no Turso secrets: the suite must pass without a remote. If it does not, that is a finding — record which tests need a live replica and either fixture them or mark them `@pytest.mark.requires_remote` and deselect them here.

- [ ] **Step 2: Verify the suite passes with no credentials**

```bash
cd /home/orthel/workspace/github/mkts-turso
PYTHON_DOTENV_DISABLED=1 env \
  TURSO_WCMKTNEWKEEP_URL= TURSO_WCMKTNEWKEEP_TOKEN= \
  TURSO_WCMKTNORTH_URL= TURSO_WCMKTNORTH_TOKEN= \
  TURSO_WCMKTBKG_URL= TURSO_WCMKTBKG_TOKEN= \
  TURSO_SDE_URL= TURSO_SDE_TOKEN= \
  TURSO_FITTING_URL= TURSO_FITTING_TOKEN= \
  TURSO_BUILDCOST_URL= TURSO_BUILDCOST_TOKEN= \
  uv run pytest -q 2>&1 | tail -20
```

`env -i` alone was not a valid proof because `load_dotenv()` reloads the repository `.env`, and existing `.db` files can still mask fixture leaks. Explicitly disable dotenv, blank every Turso credential, and run the same suite once in CI on a clean checkout with no database cache. Known risk from the review (`:248-258`): `tests/test_fit_check.py::TestGetFitMarketStatus::{test_calculates_fits_correctly,test_calculates_fit_price}` still call the real `get_equiv_stock()` database path and the real Jita price fetch. Fix them here — mock `get_equiv_stock` and the Jita fetch — rather than excluding them.

- [ ] **Step 3: Commit locally; confirm on the final production PR branch**

```bash
git add .github/workflows/tests.yml tests/test_fit_check.py
git commit -m "ci: run the test suite on every push"
```

Do not push an incomplete migration merely to exercise CI. After all implementation tasks and local verification are complete, Task 22 pushes the new branch to `OrthelT/mkts_backend`; require this workflow to pass on that branch before merging its PR to `main`.

- [ ] **Step 4: Mirror it in the frontend**

Repeat Steps 1–3 in `/home/orthel/workspace/github/wcmkts-pyturso-migration` with `uv run pytest -q` (its `pytest.ini` already sets `testpaths` and `pythonpath`).

---

### Task 20: Retire the refresh scripts

**Files:**
- Delete: `dbrefreshtest.sh` (tracked)
- Remove locally after confirming the targets: frontend `dbrefresh.sh` and `dbrefreshtest.sh` (ignored/untracked, so no commit records their deletion)
- Modify: `dbdeltest.sh:43-48`, `:57-63`, `:97-104`, `:5-21`
- Modify: `scripts/wipe_gha_db_cache.sh:26-27`, `:35-39`
- Test: `tests/test_scripts.py` (create)

**Interfaces:** consumes `--list-db-paths` (Task 5).

Owner decision (review `:91`): remove the refresh scripts, use pyturso `pull()`. Task 4 made `mkts-backend sync` a complete replacement.

Three defects to close:

1. `dbrefreshtest.sh` uses `turso db export --with-metadata`, which cannot produce pyturso metadata, and its delete step removes `${db}.info` — a file that never exists — instead of `${db}.db-info`, leaving `-changes` and `-wal-revert` beside a fresh database. Delete the script.
2. `dbdeltest.sh` hardcodes the database list twice (`:5-12` prod, `:14-21` test) and the sidecar-suffix list three times (`:43-48`, `:57-63`, `:97-104`). Derive both.
3. `scripts/wipe_gha_db_cache.sh` defaults `GHA_CACHE_REF` to `refs/heads/main` and cannot reach `builder-cost-dbs-v4-*` at all: it always appends `-shared-` or `-mkt-<leg>-`, and the builder key is `builder-cost-dbs-v4-<date>` (after Task 18) with no infix.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scripts.py
"""Shell helpers must not carry their own copy of the database list.

dbrefreshtest.sh deleted `${db}.info` — a name that never existed — so the
real -info survived every "refresh" alongside a stale -changes queue.
"""
from pathlib import Path

from mkts_backend.config.settings_service import SettingsService


def test_dbrefreshtest_is_gone():
    assert not Path("dbrefreshtest.sh").exists()


def test_dbdeltest_does_not_hardcode_database_names():
    text = Path("dbdeltest.sh").read_text()
    files = {c["file"] for c in SettingsService().database_routing().values()}
    found = sorted(f for f in files if f.removesuffix(".db") in text)
    assert found == [], f"dbdeltest.sh hardcodes {found}"


def test_wipe_script_covers_builder_cost_caches():
    text = Path("scripts/wipe_gha_db_cache.sh").read_text()
    assert "builder-cost-dbs-v4" in text


def test_wipe_script_does_not_default_to_main():
    """The caller must name the exact cache ref; an implicit ref can wipe
    the wrong family or silently find nothing."""
    text = Path("scripts/wipe_gha_db_cache.sh").read_text()
    assert "refs/heads/main}" not in text
```

- [ ] **Step 2: Run test to verify it fails.** Run: `uv run pytest tests/test_scripts.py -q`

- [ ] **Step 3: Delete `dbrefreshtest.sh`**

```bash
cd /home/orthel/workspace/github/mkts-turso
git rm dbrefreshtest.sh
```

- [ ] **Step 4: Derive `dbdeltest.sh`'s lists**

Replace the two hardcoded arrays (`:5-21`) with a call to the CLI, and collapse the three suffix copies into one:

```bash
SUFFIXES=("" "-shm" "-wal" "-info" "-changes" "-wal-revert")

mapfile -t DB_FILES < <(uv run mkts-backend --list-db-paths | cut -f2)
```

Delete the `prod|test` argument and the prod array with it: the file list now comes from whichever `settings.toml` is active, which is the property the whole migration is built on. Update the usage text. `DB_FILES` entries already include `.db`; every check/delete must concatenate the suffix directly (`"${db}${suffix}"`), never append another `.db`.

The frontend refresh scripts are ignored by `*.sh`, so `git rm` cannot remove them. They are still dangerous local tools naming the old export flow. Preview their exact paths and remove them as the already-approved local cleanup; retain the frontend delete helper until Task 22, or replace it with an equivalent settings-derived helper before deleting it.

- [ ] **Step 5: Extend the cache-wipe script**

In `scripts/wipe_gha_db_cache.sh`:

```bash
REF="${GHA_CACHE_REF:?set GHA_CACHE_REF to the branch whose caches to wipe}"
```

Requiring it is safer than defaulting: the production cutover uses `refs/heads/main`, while implementation branches have separate caches. Add a `buildercost` target that matches `builder-cost-dbs-v4-`, and include it in `all`.

- [ ] **Step 6: Run tests and exercise the scripts**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run pytest tests/test_scripts.py -q
bash -n dbdeltest.sh && bash -n scripts/wipe_gha_db_cache.sh && echo "syntax OK"
GHA_CACHE_REF=refs/heads/final-migration bash scripts/wipe_gha_db_cache.sh --help 2>&1 | head
```

Do **not** run `dbdeltest.sh` or an actual cache wipe here — Task 22 does that as a deliberate cutover step.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: delete dbrefreshtest.sh; derive script db lists from settings"
```

---

# Phase 7 — Cutover

### Task 21: Pre-cutover verification

**Files:** none. This is a gate, not a change.

- [ ] **Step 1: Both suites green**

```bash
cd /home/orthel/workspace/github/mkts-turso && uv run pytest -q
cd /home/orthel/workspace/github/wcmkts-pyturso-migration && uv run pytest -q
```

Expected: backend ≥ 411, frontend ≥ 644. Record both counts.

- [ ] **Step 2: Every replica classifies as pyturso and matches its configured remote**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run python -c "
from mkts_backend.config.settings_service import SettingsService
from mkts_backend.config.db_config import DatabaseConfig
from mkts_backend.config.replica_metadata import classify_metadata, metadata_remote_url
for alias in SettingsService().database_routing():
    db = DatabaseConfig(alias)
    print(f'{alias:16} {classify_metadata(db.path):8} match={db.remote_matches_metadata()} {metadata_remote_url(db.path)}')
"
```

Expected: every on-disk replica `pyturso`, `match=True`, and a `…test-…turso.io` URL. Any `match=False` stops the cutover.

- [ ] **Step 3: A full pipeline run round-trips through Turso**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend update-markets --primary --history 2>&1 | tail -20
uv run mkts-backend sync --market=primary
```

Expected: the pipeline pushes; the follow-up pull succeeds without a `wal.rs` panic or `UNIQUE constraint failed`.

- [ ] **Step 4: Each fixed management command round-trips**

Run one command from each of Tasks 7–12 against documented disposable test IDs and re-pull, as in each task's Step 5. Record the exact commands, before/after rows, and cleanup commands; push the cleanup and re-pull again. Do not change an arbitrary existing fit target or remove a real test fixture without a restoration step.

- [ ] **Step 5: The frontend reads every page**

```bash
cd /home/orthel/workspace/github/wcmkts-pyturso-migration
uv run streamlit run app.py --server.headless true &
sleep 20 && curl -sf http://localhost:8501/_stcore/health && echo " health OK"; kill %1
```

Then open market, doctrines and build-costs pages by hand.

### Task 22: The switch

**Files/state:** backend and frontend `settings.toml`; local `.env` / `.streamlit/secrets.toml`; GitHub Actions repository/environment secrets; hosted frontend secrets; workflow enabled state; Turso production backups/restore points.

**Do not start until Task 21 is fully green.** These steps are ordered so that no process can reach a production remote with a test replica on disk.

Before the maintenance window, fill in and have the owner approve this cutover record:

```text
Scheduled workflow repository: OrthelT/mkts_backend
Scheduled workflow default/ref: main / refs/heads/main
Backend Actions secrets: OrthelT/mkts_backend repository or environment secrets
Frontend deployment + secret store: Streamlit Cloud web interface at streamlit.io
Backend landing: new branch on OrthelT/mkts_backend -> PR to main -> merge
Frontend landing: new branch on OrthelT/wcmkts_new -> PR to main -> merge
Backend pre-merge SHA: <record immediately before merge>
Frontend pre-merge SHA: <record immediately before merge>
Code rollback: hard-reset deployed code to the corresponding pre-merge SHA
Replica/cache reset: delete through the GitHub/Streamlit web UI and let it rebuild from Turso
Turso data after rollback: retain the current cloud data; cache rebuilding does not rewind it
Rollback decision owner: repository owner/operator
```

No placeholder may remain when Step 1 begins. In particular, record both pre-merge SHAs.

- [ ] **Step 1: Stop everything that syncs and prove quiescence**

Disable both schedules in the **actual scheduled repository**, stop any host cron/systemd jobs, and stop the deployed Streamlit instance (not only a local process). Wait for or cancel in-flight market and builder runs, then record that no run remains active. GitHub scheduled workflows run from the latest commit on the repository's default branch, so a local or non-default-branch YAML edit does not disable the deployed schedule: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule

- [ ] **Step 2: Push production PR branches, pass CI, and merge to `main`**

Only after every implementation task and both local suites are green:

1. Push `final-migration` as a new branch to `OrthelT/mkts_backend` (`origin`), open a PR to `main`, and require the new backend test workflow to pass.
2. Push `pyturso-migration-main` as a new branch to `OrthelT/wcmkts_new` (`origin`), open a PR to `main`, and require the frontend test workflow to pass.
3. Immediately before each merge, record the current production `main` SHA in the cutover record as that repository's code restore point.
4. Merge both reviewed PRs to `main` while all writers remain stopped.
5. Verify the SHA deployed by GitHub Actions and Streamlit Cloud is the corresponding merge result. Do not change production Turso secrets until both new application versions are deployed.

Never push commits directly to either `origin/main`. A PR merge is the go-live code transition.

- [ ] **Step 3: Wipe the production CI caches**

The operator may delete these cache families through the GitHub web UI. The script below is the repeatable CLI alternative and the list commands verify the result:

```bash
cd /home/orthel/workspace/github/mkts-turso
GHA_CACHE_REF=refs/heads/main bash scripts/wipe_gha_db_cache.sh all
gh cache list --limit 100 --ref refs/heads/main --key turso-dbs-v4-
gh cache list --limit 100 --ref refs/heads/main --key builder-cost-dbs-v4-
```

`all` already includes `buildercost` after Task 20. Expected: both filtered lists are empty on the production `main` ref.

- [ ] **Step 4: Delete every local replica bundle, both worktrees**

```bash
cd /home/orthel/workspace/github/mkts-turso && bash dbdeltest.sh
cd /home/orthel/workspace/github/wcmkts-pyturso-migration && bash dbdeltest.sh prod
ls /home/orthel/workspace/github/wcmkts-pyturso-migration/*.db* 2>/dev/null || echo "frontend clear"
```

The frontend's local filenames are production-named while its data is test data (review `:192-204`), so its files must go even though the names will not change. The frontend helper must also remove `.db.bak`, `.db-info.bak`, and staged `.tmp` backup files; a restored test backup reintroduces exactly the state this step removes. Preview the exact paths before confirmation and verify every expected artifact is absent afterward.

- [ ] **Step 5: Verify merged settings, then switch credentials**

- Backend `settings.toml`: verify the merged PR changed `database_file` to production names for all three markets and the production shared blocks (`sde`, `fittings`, `buildcost`). **Do not change `[shared.testing]`; it remains `wcmkttest.db` and test-only.**
- Backend `.env`: `TURSO_*_URL` / `TURSO_*_TOKEN` → production values. Variable **names** do not change (review `:173-189`); the credential values in each execution environment decide isolation.
- GitHub Actions: update the same `TURSO_*` values in `OrthelT/mkts_backend` repository/environment secrets. Local `.env` does not affect Actions.
- Frontend `settings.toml`: verify the merged PR retains production filenames and removes the orphan `wcmktprod` entry.
- Frontend `.streamlit/secrets.toml`: each local `[<key>_turso]` `url`/`token` → production.
- Hosted frontend: update the equivalent values through the Streamlit Cloud web interface at streamlit.io. Do not assume the local secrets file is deployed.

- [ ] **Step 6: Verify the target before pulling anything**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend --validate-env
uv run python -c "
from urllib.parse import urlparse
from mkts_backend.config.settings_service import SettingsService
from mkts_backend.config.db_config import DatabaseConfig
testing_alias = SettingsService().shared_testing["database_alias"]
for alias in SettingsService().database_routing():
    if alias == testing_alias:
        continue
    db = DatabaseConfig(alias)
    print(f'{alias:16} {urlparse(db.turso_url or \"\").netloc}')
"
```

Expected: every production-routed host is an allow-listed production hostname with no `test` substring, and no token is printed. Do not use "does not contain test" as the only check: compare the exact expected database name/host recorded before the window. `[shared.testing]` is intentionally excluded.

- [ ] **Step 7: Cold pull and perform read-only validation**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend sync
uv run python -c "
from mkts_backend.config.settings_service import SettingsService
from mkts_backend.config.db_config import DatabaseConfig
testing_alias = SettingsService().shared_testing["database_alias"]
for alias in SettingsService().database_routing():
    if alias == testing_alias:
        continue
    db = DatabaseConfig(alias)
    print(f'{alias:16} match={db.remote_matches_metadata()}')
"
```

Expected: every production replica `match=True` — now against production. Also run `PRAGMA integrity_check`, verify the expected table set, and compare key row counts/update timestamps with the read-only production baseline captured before the window. Do not run a writer until these checks pass.

- [ ] **Step 8: Supervised production canaries**

```bash
cd /home/orthel/workspace/github/mkts-turso
uv run mkts-backend update-markets --market=primary --history 2>&1 | tail -30
```

Watch for push failures, then cold-pull the primary replica again and verify its update timestamp/row counts. Run the deployment and market3 legs under supervision, then `update-builder-costs`; do not let the first unsupervised schedule be the first production write for those databases. Start the frontend only after all backend canaries pass and confirm it shows production data.

- [ ] **Step 9: Re-enable the schedules**

Re-enable both workflows. Watch the first scheduled run of each to completion.

- [ ] **Step 10: Rollback drill and closeout**

Before ending the window, verify both recorded pre-merge SHAs are resolvable. The owner-selected rollback is:

1. stop GitHub Actions and the Streamlit deployment;
2. move the production code back to the recorded pre-merge SHAs;
3. delete the GitHub Actions and deployed frontend replica caches through their web interfaces; and
4. restart so fresh replicas are pulled from Turso.

This restores the previous application code and eliminates stale cached database files. Turso remains the authoritative database, so rows already pushed there remain present after the caches rebuild. That is intentional unless the failure itself wrote incorrect cloud data; incorrect Turso rows require a separate corrective data operation, not another cache deletion.

---

# Phase 8 — Documentation (operational docs before cutover; closeout after)

### Task 23: Bring both repositories' docs in line

**Files:**
- Backend: `AGENTS.md`, `CLAUDE.md`, `README.md`, `docs/migration-review.md`, `docs/cli-tools.md`
- Frontend: `AGENTS.md`, `README.md`, `docs/read_df_consolidation.md`

Complete Steps 1–2 and commit them **before Task 21** so operators and agents do not execute the cutover from stale instructions. Step 3 is the post-cutover closeout that marks the review items resolved only after evidence exists.

- [ ] **Step 1: Backend**

- Remove the "Known issue: `validate` reports pending work" paragraph — Task 6 removed the command, so the issue no longer exists.
- Correct the sidecar count: the bundle is the `.db` plus up to five sidecars, and **no `-shm` exists in practice**; `sdelitetest`/`wcfittingtest` have no `-wal-revert`. The current text asserts five sidecars unconditionally.
- Update the ESI-cache diagram labels that still say cache rows are read/written "remote" — both use the local replica.
- Replace bare `uv run mkts-backend` pipeline examples with `update-markets`.
- Document `sync`'s new coverage and `[shared.testing]` exclusion, `--markets-only`, `--include-testing`, `--list-db-paths`, and `--db-path`.
- Document `heal_metadata()` and `remote_matches_metadata()` in the `DatabaseConfig` section, replacing the "**Gap:** it only checks that `<file>-info` *exists*" note that Task 2 closed.
- Remove `dbrefreshtest.sh` references; point at `mkts-backend sync`.

- [ ] **Step 2: Frontend**

- `docs/read_df_consolidation.md` describes a removed `remote_engine` fallback — delete or rewrite that section.
- `README.md` still documents libsql APIs and `wcmktprod` — replace with pyturso and the real aliases.
- `AGENTS.md` presents `wcmktprod` as the central database throughout — replace with the three real market aliases.
- Add the sync-dialect rule (Task 13) and the read-only policy. After Task 15 there is no exception: `industry_index` lives in `streamlit_cache.db` and the frontend writes nothing to a synced replica.
- Update the test count: `docs/testing.md:56` says 329 + 16; the real figure is 644 + 22.
- Delete `run_tests.py` — it lists 8 test files, 6 of which no longer exist, and is not the runner.

- [ ] **Step 3: Update `docs/migration-review.md`**

Apply the corrections table from the top of this plan, and mark each `UC:` item resolved with the task that resolved it.

- [ ] **Step 4: Commit both**

```bash
cd /home/orthel/workspace/github/mkts-turso && git add -A && git commit -m "docs: align backend docs with the completed pyturso migration"
cd /home/orthel/workspace/github/wcmkts-pyturso-migration && git add -A && git commit -m "docs: align frontend docs with the completed pyturso migration"
```

---

## Deferred by decision — not in scope

| Item | Review ref | Reason |
|---|---|---|
| Frontend admin local-write-plus-push redesign | `:223-228` | Owner deferred. Task 17 makes the disabled state legible. |
| Frontend `wcmkt` active-market sentinel removal | `:263-265` | Needs a broader API cleanup. |
| A separate frontend fittings database | `:266-267` | Resolved in the review: doctrine and fit reads use the market databases. |
| `fit_update.py` split (3,200+ LOC) | — | Pre-existing debt, tracked separately. Phase 4 touches it but does not restructure it. |
| Remove backend `remote_engine` / `remote=` compatibility API | `:147-148` | Current tree still has many read and compatibility consumers; perform a dedicated call-site audit after the durability fixes and cutover. |

## Execution notes

- **Ordering:** Phase 1 → 2 → 3 → 4 in the backend; Phase 5 is independent and can run in parallel in the other worktree. Phase 6 depends on Task 5. Task 23 Steps 1–2 must land before Phase 7. Phase 7 depends on everything plus the filled cutover record; Task 23 Step 3 closes the review afterward.
- **Deviation from the review's recommended order** (`:322-332`): the review puts the `push()` audit at step 4, after the `validate` fix. This plan puts `validate` first because the recommended resolution is deletion, and deleting it before Phase 4 avoids writing tests against a command that is about to go.
- **Scope:** this plan spans two repositories and could reasonably be split. It is kept as one document because the cutover in Phase 7 is a single coordinated event and the two halves must land together. If it is split, Phases 1–4 + 6 and Phase 5 are the seam, with Phase 7 owned by whichever plan lands second.

## Coverage against `docs/migration-review.md`

| Review section | Tasks |
|---|---|
| §1 Reject and rebuild non-pyturso metadata (`:40-78`) | 1, 2, 14 |
| §1 Refresh scripts and pull coverage (`:80-97`) | 4, 20 |
| §1 Cutover cache wipe (`:99-107`) | 20, 22 |
| §2 `push()` on management writes (`:109-148`) | 7–12, all six groups |
| §3 Make `validate` trustworthy (`:150-169`) | 6 — removed, not fixed |
| Secrets authoritative for the remote target (`:173-189`) | 3, 22 Steps 5–6 |
| Local naming still needs a safe switch (`:191-204`) | 22 Step 4 |
| GitHub Actions filenames hardcoded (`:206-217`) | 18 |
| Frontend admin writes disabled (`:221-228`) | 17 (deferred by decision) |
| Frontend plain dialect / `industry_index` (`:230-246`) | 13, 15 |
| Two backend tests touch live services (`:248-258`) | 19 Step 2 |
| Routing cleanup (`:260-267`) | 22 Step 5 (`wcmktprod`); rest deferred |
| Dependencies (`:269-284`) | 16 — frontend bump plus backend `pyturso>=0.7.2` |
| Documentation debt (`:286-300`) | 23 |
| `UC:` db_config changes already made (`:334-337`) | starting state; `sync()`→`pull()` and the `checkpoint()` calls verified in place at `db_config.py:159-212` |
