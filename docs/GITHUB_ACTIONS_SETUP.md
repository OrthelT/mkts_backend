# Scheduled jobs (maintainer guide)

Everyday CLI users do not need to configure GitHub Actions. These workflows keep
the shared data current; check their YAML files when changing schedules or secrets.

| Workflow | Current schedule (UTC) | Job |
|---|---|---|
| [Market collection](../.github/workflows/market-data-collection.yml) | Hourly, at minute 20 | One collection job per primary, deployment, and market3 |
| [Builder costs](../.github/workflows/builder-costs-collection.yml) | Daily, 06:45 | Refresh shared manufacturing estimates |
| [Tests](../.github/workflows/tests.yml) | Pushes and pull requests | Run `uv run pytest -q` |

## Credentials

Set repository Actions secrets under **Settings → Secrets and variables → Actions**.
Local `.env` values are not uploaded to GitHub.

Market collection uses:

- `CLIENT_ID`, `SECRET_KEY`, `REFRESH_TOKEN` for EVE authorization.
- `TURSO_WCMKTNEWKEEP_URL` / `TURSO_WCMKTNEWKEEP_TOKEN`.
- `TURSO_WCMKTNORTH_URL` / `TURSO_WCMKTNORTH_TOKEN`.
- `TURSO_WCMKTBKG_URL` / `TURSO_WCMKTBKG_TOKEN`.
- `TURSO_SDE_URL` / `TURSO_SDE_TOKEN`.
- `TURSO_FITTING_URL` / `TURSO_FITTING_TOKEN`.
- `JANICE_KEY` for optional Jita price fallback.
- `GOOGLE_SA_JSON` for the Google service-account JSON used by Sheets exports.

Builder-cost collection uses the primary and SDE pairs above, plus
`TURSO_BUILDCOST_URL` / `TURSO_BUILDCOST_TOKEN`.

Keep workflow secret mappings aligned with the variable names in settings.
Changing a local database filename does not change which Turso remote a secret targets.

## Running and checking jobs

Open the repository's **Actions** page, select a workflow, and use **Run workflow**.
Market collection offers market and history inputs. Inspect each market's result;
one successful market job does not imply that all markets succeeded.
Download the log artifact if a run fails. The market workflow retains its logs
for seven days. Builder-cost collection has a 30-minute job timeout; market
collection applies a 15-minute timeout to its collection step.

For a local collection, use the actual subcommand:

```bash
uv run mkts-backend update-markets --market=primary --history
uv run mkts-backend update-builder-costs
```

## Database caches

Workflows resolve filenames through `mkts-backend --db-path=...` and cache each
database with its pyturso sidecars. Cache keys are unique per run. The market
matrix assigns each market cache to its own job and shared SDE/fittings cache
publication to one job.

Keep database and sidecar files together. Do not replace the per-run cache keys
with a repeating key: restoring a replica older than its last upload can cause
later writes to be silently skipped. See [AGENTS.md](../AGENTS.md) for the sync
constraints. Cache removal/rebuild and remote data rollback are separate operations;
clearing a cache cannot undo writes already sent to Turso.
