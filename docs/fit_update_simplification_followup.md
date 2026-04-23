# Follow-up: `fit_update.py` Simplification

## Status

**Deferred** from the April 2026 CLI-tool fixes PR. That PR landed the
correctness + batching fixes described in `cli-tool-fixes.md` but did not
achieve the broader simplification the plan gestured at
(`~3,051 → ~2,400 lines`). Current size: **3,301 lines** across 13
subcommands. This memo captures what's left.

## Why it was deferred

The original `cli-tool-fixes.md` plan had four concrete bug fixes and a
generic "the code is incredibly verbose" hunch. The PR tackled the four
bugs (DB batching, ship_targets gap, lead_ship gap, `update-lead-ship`
market handling) plus a shared `_provision_fit_in_market` helper. The
*further* deduplication below would have:

- Mixed correctness work with pure cleanup in one PR.
- Expanded the blast radius for review and staging verification.
- Required touching `interactive_add_fit` (currently 318 lines of its own
  interactive flow) which has no reported bugs.

Shipping them separately lets each land on its own merits.

## What's left to simplify

### 1. Third "add a fit" code path still duplicates the provisioning sequence

`interactive_add_fit` (lines 282–598 in `fit_update.py`) adds a new fit
from an EFT file. It calls `update_fit_workflow` (in
`utils/parse_fits.py`) per target market DB, which internally does its
own `upsert_doctrine_fits` / `upsert_doctrine_map` / `upsert_ship_target`
/ `refresh_doctrines_for_fit` sequence — parallel to
`_provision_fit_in_market` but not reusing it.

Why fixing this is non-trivial:

- `update_fit_workflow` also writes into `fittings_fittingitem` / `fittings_fitting` (the fittings DB, not market DB), which `_provision_fit_in_market` does not do.
- The fittings-DB writes are the *identity-establishing* step: `_provision_fit_in_market` assumes the fit already exists in fittings and only updates per-market tables. For `add`, the fit is being created.

Proposed split:

- `parse_fits.register_fit_in_fittings_db(fit_id, parse_result)` — owns
  fittings-DB writes only.
- `_provision_fit_in_market(conn, p, market_flag)` — owns market-DB
  writes only (already exists).
- `interactive_add_fit` composes: parse EFT → register in fittings →
  bucket per-market provision (same pattern as
  `doctrine_add_fit_command` post-refactor).

Payoff: one canonical "fit gets into market DB" code path, used by
`add`, `assign-market`, `doctrine-add-fit`. Lead-ship + ship-target
provisioning would be guaranteed identical across all three.

Estimated reduction: ~150 lines in `interactive_add_fit` + ~80 in
`update_fit_workflow`.

### 2. Legacy wrappers kept for backwards compatibility

The PR kept two thin wrappers so no unaudited caller breaks:

- `_provision_market_db(p, alias, new_flag, remote)` →
  `_prepare_watchlist_for_fit` + `engine.begin()` +
  `_provision_fit_in_market`. Lines 1104–1124.
- `_cleanup_market_db(fit_id, doctrine_id, alias, remote)` →
  `engine.begin()` + `_cleanup_fit_in_market`. Lines 1148–1162.

Grep shows they are called only from the old non-batched paths
(`_execute_market_plan` does not use them anymore). Removing them is
safe once a full caller audit confirms nothing outside
`fit_update.py` imports them.

Payoff: ~40 lines removed + clearer "one way to do it" semantics.

### 3. Hard-coded `("wcmktprod", "wcmktnorth")` in fallback searches

Six call sites still iterate the tuple directly, all in
`fit_update.py`:

```
line  637  for fallback in ("wcmktprod", "wcmktnorth"):       # assign-market fallback lookup
line  714  for fallback in ("wcmktprod", "wcmktnorth"):       # assign-doctrine-market fallback lookup
line  897  for target in ("wcmktprod", "wcmktnorth"):         # _get_remote_market_flags
line 1381  for fallback in ("wcmktprod", "wcmktnorth"):       # unassign fallback lookup
line 1453  for fallback in ("wcmktprod", "wcmktnorth"):       # unassign-doctrine fallback lookup
line 2754  for target in ("wcmkt", "wcmktnorth"):             # (unclear context, verify before change)
line 2784  for target in ("wcmkt", "wcmktnorth"):             # (unclear context, verify before change)
```

These are "search for a fit across known remote DBs" loops. Replacing
the tuples with `_configured_market_db_aliases()` makes them
config-driven. The catch: a few of them pair the tuple with a specific
semantic ("try remote wcmktprod first, then wcmktnorth") where ordering
matters for the user-visible result. Audit the intent before each
replacement.

Payoff: ~30 lines saved, plus automatic support for future markets.

### 4. `_FLAG_ALIAS_MAP` encodes market semantics inline

`_flag_to_aliases` (lines 753–762) hard-codes:

```python
_FLAG_ALIAS_MAP = {
    "primary":    {"wcmktprod"},
    "deployment": {"wcmktnorth"},
    "both":       {"wcmktprod", "wcmktnorth"},
}
```

This is functionally a specialized case of
`_configured_market_db_aliases(market_flag)` returning a `set` instead
of a `list`. Unifying them:

```python
def _flag_to_aliases(flag: str) -> set[str]:
    return set(_configured_market_db_aliases(flag))
```

Single source of truth for flag → DB resolution. Will also automatically
pick up any new markets added to `settings.toml`.

Payoff: ~10 lines. Mostly a clarity win.

### 5. `fit_update.py` decomposition into per-subcommand modules

The file currently holds 13 subcommand handlers plus ~15 shared helpers
in 3,301 lines. Natural decomposition:

```
src/mkts_backend/cli_tools/fit_update/
├── __init__.py            # re-exports fit_update_command (dispatcher)
├── _shared.py             # _configured_market_db_aliases, _flag_to_aliases,
│                          # _get_doctrine_fits_rows, _apply_step,
│                          # _provision_fit_in_market, _cleanup_fit_in_market,
│                          # _prepare_watchlist_for_fit, _execute_market_plan,
│                          # _plan_market_action, _display_market_preview
├── add.py                 # interactive_add_fit
├── assign.py              # assign_market_command, assign_doctrine_market
├── unassign.py            # unassign_market_command, unassign_doctrine_market
├── doctrine.py            # create_doctrine_command, doctrine_add_fit_command,
│                          # doctrine_remove_fit_command
├── remove.py              # remove_fit_command
├── update_target.py       # update_target_command
├── lead_ship.py           # update_lead_ship_command
├── friendly_name.py       # update_friendly_name_command,
│                          # populate_friendly_names_command
└── list.py                # list_fits_command, list_doctrines_command
```

The dispatcher (`fit_update_command`) then becomes a thin ~50-line
router that imports lazily (same pattern the `CommandRegistry` already
uses).

Payoff: the pieces become independently reviewable and each file stays
under ~400 lines. Not a functional change — purely organizational.

This is the biggest item on the list and the one most likely to cause
merge conflicts with in-flight work. Do it in a standalone PR with no
logic changes.

## Suggested order

1. **#4 `_flag_to_aliases`** — tiny, safe, easy to verify. Warm-up.
2. **#3 fallback-tuple replacement** — per-site audit, low risk.
3. **#2 legacy wrapper removal** — grep confirms zero external callers,
   then delete.
4. **#1 `interactive_add_fit` dedup** — the real simplification win;
   moves ~230 lines through two helper extractions.
5. **#5 file decomposition** — last, once the content has stabilized.

Doing #5 first would make #1–#4 harder to review because each change
would span multiple new files. Save it for the end.

## Related

- `market_db_map_deferred.md` — the `MARKET_DB_MAP["primary"] = "wcmkt"`
  follow-up. Pairs naturally with #3 and #4 here since they all touch
  the market-alias resolution layer.
