# pyturso Migration Review — Confirmed Outstanding Work

docs/migration-review.md now independently verified by code.

**Date:** 2026-08-23
**Original reviewer:** Claude (Opus 5)
**Independent verification:** Codex
**Scope:** both migration worktrees named in `docs/migration-prep.md`

| Element | Path | Branch | Tracks |
|---|---|---|---|
| Backend (`mkts_backend`) | `/home/orthel/workspace/github/mkts-turso` | `mkts-turso-main` | `staging/mkts-turso-main` |
| Frontend (`wcmkts_new`) | `/home/orthel/workspace/github/wcmkts-pyturso-migration` | `pyturso-migration-main` | `staging/main` |

This revision checks the original report against the current source, settings,
replicas, installed packages, and test suites. It incorporates the operator's
comments as decisions rather than leaving them as unresolved annotations.

## Verdict

The migration is **not ready to merge yet**. The current test replicas are healthy
and both suites pass, but three cutover-safety items remain:

1. Both applications accept libsql-era metadata as valid, so a persistent replica
   or CI cache from production can fail on its first pyturso connection.
   UC: Fix 
2. Several backend management commands commit to the local replica without a final
   `push()`, so they can report success without updating Turso.
   UC: Identify them and let me choose. 
3. `validate` currently reports pen:ding work after a successful push, so it cannot
   be used to prove item 2 has been fixed.
   UC: Evaluate deprecating this function altogether. Do we really need it?

The frontend admin write path remains disabled, but the owner has explicitly
accepted that regression for a later refactor because the admin UI is not currently
used regularly. It is therefore recorded below as an accepted deferral, not a merge
blocker.

## Required before merge

### 1. Reject and rebuild non-pyturso metadata

**Finding confirmed in both repositories.**

Backend `DatabaseConfig.confirm_metadata_exists()` only checks whether
`<database>-info` exists. Frontend `DatabaseConfig._replica_metadata_valid()` and
`init_db.verify_db_content()` parse the file as JSON but do not validate its shape.
A libsql-era file such as `{"hash": ..., "version": 0, ...}` is valid JSON, so all
three checks accept it. pyturso metadata currently has a string version (`"v1"`) and
a string `client_unique_id`.
- Upon encountering libsql metadata, delete the metadata file and run .pull() to  repopulate it.

This is a cutover risk for **any surviving production replica or cache**, rather
than literally every host: ephemeral hosts without a persisted database will cold
bootstrap correctly. Persistent developer files, Streamlit files, and GitHub Actions
caches must be assumed unsafe until checked or wiped.

All current replicas are healthy: six backend and five frontend `-info` files were
independently checked and contain pyturso `v1` metadata. The earlier report said
"twelve" replicas; the verified total is eleven.

Required changes:

- Centralize a metadata predicate in each repository. At minimum, require a JSON
  object with a non-empty string `version` and non-empty string
  `client_unique_id`; reject integer-version libsql metadata.
  UC: No. Delete libsql metadata .db-info file. Run pull. Correct file will populate.d 
- Run that predicate before every `tursosync.connect()` bootstrap/pull path. An
  invalid pair must be disposed, removed as one six-file replica bundle, and cold
  pulled. 
  UC: No. Only delete the .db-info and .pull()
- Add regression fixtures for valid pyturso metadata, libsql metadata, corrupt JSON,
  missing metadata, and orphaned metadata.
- Preserve a useful named error if connecting still fails after the guard, including
  the database alias/path and the `nuke + pull` remedy. yes. but for missing metadata .pull() should heal. 

The replica bundle is the `.db` plus five sidecars: `-shm`, `-wal`, `-info`,
`-changes`, and `-wal-revert`. Partial deletion is unsafe.
UC: deleting .db-info files is ok if regenerated with .pull()

#### Refresh scripts and pull coverage

The original script finding is confirmed:

- Frontend `dbrefresh.sh` and `dbrefreshtest.sh` call
  `turso db export --with-metadata`; the production script also names production
  remotes.
- The untracked backend `dbrefreshtest.sh` is a copy of the same pattern.
- Their delete step removes `${db}.info` instead of the real `${db}.db-info` and
  leaves `-changes` and `-wal-revert` behind.

Per the operator's decision, remove these refresh scripts and use pyturso `pull()`.
The backend `sync` CLI already performs pulls, but it currently covers configured
markets plus buildcost only. It does **not** cover the shared SDE or fittings
replicas, and it calls `sync()` without first applying the metadata-format guard.
Extend it (or add explicit shared/all options) so it is a complete replacement for
the old refresh procedure. The frontend `init_db()` already enumerates its market,
SDE, and buildcost replicas; it still needs the stronger metadata predicate.

#### Cutover cache wipe

Make the cache reset an explicit deployment step before the first production
pyturso job. `scripts/wipe_gha_db_cache.sh` covers `turso-dbs-v4-*`, but:

- its default ref is `refs/heads/main`, not this staging branch; and
- it does not cover `builder-cost-dbs-v4-*`.

Delete both cache families or extend the script before relying on it for cutover.

### 2. Add `push()` to backend management write transactions

**The behavior is confirmed; the original reference counts were too broad.**

`DatabaseConfig.remote_engine` is now an alias for `engine`. A commit through either
name is local-only until `DatabaseConfig.push()` succeeds. The original report
treated nearly every `remote_engine` reference as a write; some are reads,
compatibility branches, a comment, or `DatabaseConfig` itself. The actionable unit
is a complete command transaction, not a raw reference count.

Confirmed broken or misleading paths include:

- ship-target writers in `utils/utils.py`;
- watchlist mutation through `utils/db_utils.py` and the `add-watchlist` CLI;
- doctrine/fit writers in `utils/doctrine_update.py`, `utils/parse_fits.py`,
  `utils/add2doctrines_table.py`, and `cli_tools/fit_update.py`;
- `cli_tools/add_structure.py`; and
- module-equivalent mutations in `db/equiv_handlers.py`.

`sync_equiv_to_remote()` is especially misleading: it reads the local table, then
deletes and reinserts that same local table through the alias, and never pushes. It
churns local primary keys but, contrary to the original report, the current
`module_equivalents` schema has no secondary `UNIQUE` constraint, so the claimed
specific `UNIQUE constraint failed` mechanism is not established for this table.

`_mirror_to_build_watchlist()` has the direction reversed after writing buildcost:
it calls `buildcost_db.sync()` (a pull) instead of `push()`. Its docstring still
describes the old direct-remote-write model.

The hourly market pipeline and `builder_costs/repository.py` already push. The fix
should preserve that transaction boundary pattern:

1. write through `db.engine`;
2. finish the full logical command locally;
3. call `db.push()` once;
4. surface push failure as command failure; and
5. add a test that asserts both the local change and the push call.

After the behavior is fixed, remove `remote_engine` and obsolete `remote=` branching
in a separate cleanup so future local-only commits are harder to introduce.

### 3. Make `validate` trustworthy

**The false-positive behavior and its immediate cause are confirmed.**

Current output reports all three freshly pushed market replicas as pending. Each has
`stats().cdc_operations == 1`. The last `turso_cdc` row is a transaction marker
(`change_type = 2`, `table_name = NULL`), while `last_pushed_change_id_hint` points
to the preceding substantive change. A second push does not remove the marker.

`validate_sync()` currently returns `cdc_operations == 0`, so it can never report a
previously written replica as clean under this observed pyturso behavior.

Do not encode an unexplained global `<= 1` rule. Validate that the only row after
the last pushed change is the trailing transaction marker (or use an upstream API
that exposes substantive pending changes), then add these regression cases:

- cold replica with no writes;
- committed but unpushed write;
- successful push with only the marker remaining; and
- failed push with substantive CDC rows remaining.

## Configuration and cutover procedure

### Secrets are intentionally authoritative for the remote target

The original report correctly observed that `settings.toml` stores environment
variable/secret names rather than remote URLs. The operator confirmed this is
intentional: production and test use the same secret shape, and production secrets
must not be copied into this worktree.

Accordingly, the prep requirement should be read as:

- `settings.toml` is authoritative for aliases, local filenames, and the names of
  credential sources; and
- `.env` / `.streamlit/secrets.toml` is authoritative for credentials and the actual
  remote target.

This is not a code blocker, but it means a settings-only switch cannot by itself
guarantee environment isolation. The cutover checklist must include verification of
the remote database names without printing tokens.

### Local naming still needs a safe switch

The two worktrees intentionally use different local naming today:

| Worktree | Local files | Remotes |
|---|---|---|
| Backend | test-named | test |
| Frontend | production-named | test |

The frontend therefore has production-named files containing test data. Before
switching its secrets to production, delete the complete replica bundles so test
metadata/data cannot be reused against production remotes. A mandatory wipe is
sufficient; adopting test-named frontend files would make the state more obvious but
is not required if the wipe is enforced.

### GitHub Actions filenames are hardcoded

Confirmed backend workflow references:

- `market-data-collection.yml` hardcodes all three market test filenames and the
  SDE/fittings cache paths.
- `builder-costs-collection.yml` hardcodes SDE, primary-market, and buildcost test
  filenames.

These paths must be changed for production in addition to `settings.toml`, or derived
from `SettingsService` at runtime. Deriving them is safer and keeps the workflow from
drifting again.

## Accepted deferrals and non-blocking work

### Frontend admin writes are disabled

Confirmed: `repositories/admin_repo.py::_get_write_engine()` raises
`NotImplementedError` for `write_target = "remote"`, which is the current setting.
Changing it to `local` would not provide durable admin writes because no push follows.

**Owner decision:** defer the local-write-plus-push admin redesign. Keep the disabled
state and make the limitation visible in release/cutover notes.

### Frontend plain dialect is not a complete read-only guard

The frontend uses `sqlite+turso`, while the backend uses
`sqlite+turso_sync` for sync-managed replicas. The owner wants the frontend to remain
non-pushing outside the deferred admin module. That policy should be documented, but
the current code is not actually read-only:

`repositories/build_cost_repo.py::_write_industry_index_impl()` writes
`industry_index` into the sync-managed `buildcost.db` using
`to_sql(..., if_exists="replace")`. It does not push. This local DDL/data rewrite can
coexist poorly with later pulls and invalidates a blanket claim that the plain
dialect is only used for reads.

Before documenting the dialect as a guard, either move `industry_index` to an
explicit local-only cache database, stop rewriting it in the frontend, or document
and test the intended local-overlay behavior across subsequent pulls. Any future
durable frontend writer must use the sync dialect and an explicit push boundary.

### Two backend tests still touch live services

`tests/test_fit_check.py::TestGetFitMarketStatus::{test_calculates_fits_correctly,
test_calculates_fit_price}` patch the main marketstats lookup, target lookup, and ship
classification, but still call the real `get_equiv_stock()` database path and the
real Jita price-fetch path. They pass on this machine because configured replicas are
present.

This is not a production behavior blocker, but these unit tests should mock those
remaining dependencies or use fixtures. Neither repository currently has a test job
in the reviewed workflows, so passing locally does not gate the merge.

### Routing cleanup

- Frontend `[db_paths]` contains unused `wcmktprod`, and `config.py`'s standalone CLI
  defaults to it.
- Frontend `wcmkt` is an intentional active-market sentinel, not simply a missing
  alias; removing it requires a broader API cleanup.
- The frontend does not need a separate fittings database: its current doctrine and
  fit reads use the market databases. The original "confirm" item is resolved.

### Dependencies

The original dependency section needed correction:

- Backend lock and installed environment are already on pyturso 0.7.2.
- Frontend lock and installed environment are on pyturso 0.7.1; update it to the
  selected migration version if both applications are meant to match.
- Installed pyturso metadata declares `sqlalchemy>=2.0` for its `sqlalchemy` extra.
  The original report's claimed hard floor of SQLAlchemy 2.0.42 was not supported by
  the package metadata or current code inspection. The frontend's 2.0.25 declaration
  may still be raised to the tested version, but do not present 2.0.42 as a confirmed
  package requirement.
- Declaring `pyturso[sqlalchemy]` is clearer, but both projects already declare
  SQLAlchemy directly, so the missing extra is not a functional defect.
- Frontend `asyncio`, `sql`, and `typing` dependencies are pre-existing stdlib/stub
  shadows and can be removed separately.

### Documentation debt

Backend `AGENTS.md` has been substantially updated but is not fully clean. Remaining
stale examples include bare `uv run mkts-backend` pipeline commands and ESI cache
diagram labels that still say cache rows are read/written "remote" even though they
use the local replica.

Frontend documentation remains substantially stale:

- `docs/read_df_consolidation.md` describes a removed `remote_engine` fallback;
- `README.md` still documents libsql APIs and `wcmktprod`; and
- `AGENTS.md` still presents `wcmktprod` as the central database in many examples.

Update these before or with the production merge so operational guidance does not
reintroduce the old connection model.

## Verification performed in this pass

| Check | Result |
|---|---|
| Backend `.venv/bin/pytest -q` | **411 passed** |
| Frontend `.venv/bin/pytest -q` | **644 passed, 22 subtests passed** |
| Backend metadata | 6/6 current replica files are pyturso `v1` |
| Frontend metadata | 5/5 current replica files are pyturso `v1` |
| Backend `--check_tables --primary` | successful read of the configured test replica |
| Backend `validate --all` | runs, but false-positive on all 3 markets |
| Backend installed versions | pyturso 0.7.2; SQLAlchemy 2.0.51 |
| Frontend installed versions | pyturso 0.7.1; SQLAlchemy 2.0.49 |
| Backend remote URL names | all configured URLs resolve to test remotes |
| Frontend remote URL names | all reviewed URL sections resolve to test remotes |
| Tracked secrets / database files | none found in either repository |

The previous cold-reload recovery was not repeated because it is destructive. The
current metadata, successful table read, and both passing suites confirm the repaired
state without nuking a replica again.

## Recommended order

1. Add metadata-shape guards and tests in both repositories.
2. Remove the refresh scripts; make backend `sync` cover every intended shared
   replica; document and rehearse the full cache/replica wipe.
3. Fix `validate` and add real post-push regression coverage.
4. Audit backend management commands and add one push per logical write transaction.
5. Resolve the frontend `industry_index` local-write exception.
6. Derive CI database paths from settings and prepare the production settings edit.
7. Clean up migration documentation and dependency alignment.
8. Leave admin writes disabled until the explicitly deferred refactor.

UC: Changes made to src/mkts_backend/config/db_config.py
- refactor sync() to call .pull() instead of initiating it's own connection. 
- add conn.checkpoint() after successful pull or push to checkpoint WAL. 

