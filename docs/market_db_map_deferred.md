# Deferred Fix: `MARKET_DB_MAP["primary"] = "wcmkt"`

## Status

**Deferred.** Partially mitigated by the `fit-update` refactor (April 2026) that
removed the visible `db_alias="wcmkt"` defaults in the CLI layer. The root
mapping is still unchanged.

## Issue

`src/mkts_backend/cli_tools/market_args.py` currently defines:

```python
MARKET_DB_MAP: dict[str, str] = {
    "primary": "wcmkt",      # <-- deprecated alias
    "deployment": "wcmktnorth",
}
```

`"wcmkt"` is deprecated — the real primary-market DB alias is `"wcmktprod"`.
The code path works today only because `DatabaseConfig.__init__` normalizes
`"wcmkt"` to `_production_db_alias` (which is `"wcmktprod"` per `settings.toml`),
so every caller that passes `"wcmkt"` ends up on `wcmktprod` without errors.

Why this matters:

- The mapping leaks `"wcmkt"` to every caller that does
  `MARKET_DB_MAP.get(market_alias, …)`.
- New code grepping for "what DB alias does `primary` mean?" gets the wrong
  answer (`wcmkt`) even though the runtime value is `wcmktprod`.
- Any future tightening of `DatabaseConfig` that drops the
  `wcmkt → wcmktprod` normalization will break callers silently.

## What this PR did (partial mitigation)

- Dropped the `db_alias="wcmkt"` default from
  `update_lead_ship_command` — it now resolves DB aliases from `market_flag`
  via `_configured_market_db_aliases`, which reads DB aliases directly from
  `MarketContext.from_settings(market).database_alias` (i.e. from
  `settings.toml`, not `MARKET_DB_MAP`).
- Dropped the hard-coded `"wcmkt"` fallback in
  `command_registry._handle_fit_update`. When the user's `--market` covers
  multiple DBs (e.g. `both`), the handler now prompts interactively rather
  than silently defaulting.
- Replaced every hard-coded `["wcmkt", "wcmktnorth"]` and
  `("wcmktprod", "wcmktnorth")` tuple inside `_execute_market_plan` and
  `doctrine_add_fit_command` with `_configured_market_db_aliases()`, which
  is config-driven and already returns the correct aliases.

These changes make the system tolerant of the mapping being wrong but do
not fix the mapping itself. `MARKET_DB_MAP["primary"]` still returns
`"wcmkt"`, and several legacy call sites (listed below) still depend on
the `DatabaseConfig` normalization.

## What the full fix looks like

1. **Change the mapping** in `src/mkts_backend/cli_tools/market_args.py`:

   ```python
   MARKET_DB_MAP: dict[str, str] = {
       "primary": "wcmktprod",
       "deployment": "wcmktnorth",
   }
   ```

2. **Audit remaining `"wcmkt"` string literals** in the codebase. Current
   hits (as of this PR) that still assume the deprecated alias:

   ```
   src/mkts_backend/cli_tools/fit_update.py       — multiple `db_alias="wcmkt"` defaults on
                                                     helper functions that still exist
                                                     (remove_fit_command, update_target_command,
                                                     doctrine_remove_fit_command, etc.)
   src/mkts_backend/utils/doctrine_update.py      — every helper defaults to `db_alias="wcmkt"`
   src/mkts_backend/utils/db_utils.py             — `add_missing_items_to_watchlist(…, db_alias="wcmkt")`
   ```

   Most of these are fine for now (the `DatabaseConfig` normalization handles
   it), but the defaults should be changed to `"wcmktprod"` for clarity.

3. **Remove the `DatabaseConfig` normalization** once step 2 lands. The
   normalization is at roughly `config/config.py:~101`, aliasing `"wcmkt"`
   to `_production_db_alias`. It's a safety net for legacy callers; once
   those callers are gone, the net should come down to prevent new callers
   from latching onto the deprecated name.

4. **Verify with `grep -rn '"wcmkt"' src/`**: should return zero hits.
   Any remaining hit is either a config key (allowed) or a bug (must fix).

## Why this was deferred

Touching every call site risks incorrect behavior where a function that
currently accepts `db_alias="wcmkt"` and internally normalizes to
`wcmktprod` suddenly takes `"wcmktprod"` literally — which should be a
no-op, but is a broad blast radius to verify in one PR. Keeping the fix
scoped to the `fit-update` refactor avoided mixing concerns.

## Pointers for the follow-up

- Start with `grep -rn '"wcmkt"\b' src/` to find every literal.
- Change defaults (`db_alias="wcmkt"` → `db_alias="wcmktprod"`) in
  `doctrine_update.py` first — it's the most-called utility module.
- Then flip `MARKET_DB_MAP["primary"]` and run the full test suite.
- Finally remove the normalization in `DatabaseConfig`.
- Smoke test: `mkts-backend fit-update list-fits --market=primary` must
  hit `wcmktprod.db` locally and `wcmktprod_turso` remotely — same as
  pre-change. Same for `--market=deployment` hitting `wcmktnorth`.
