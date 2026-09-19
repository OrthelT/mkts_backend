# CLI guide

Run commands from the project folder. Start with the [README](../README.md) if this
is your first session. Replace example IDs and filenames with your own.

## Choosing a market

```bash
uv run mkts-backend --list-markets
```

* TIP["mkts can be used as an alias for mkts-backend."]


Use `--market=primary`, `--market=deployment`, or `--market=market3` after the
command. Checking one fit, listing fits, and `needed` take one market at a time.
`module` also supports `--market=all` for a comparison.

Commands will usually default to either all market or the primary market if `--market` is not specified.


Most lookups default to `primary`. `mkts-backend update-markets` and
`mkts-backend sync` default to all markets. The older `--both` selector now means
all configured markets, including the third market; use `all` instead.

The examples use `fitcheck` for reports and `mkts-backend` for management.
`mkts` is an alias for `mkts-backend`. Although the entry points share commands,
their help and default-market handling differ; use the forms shown here.

## Downloading current data

```bash
# Every market plus shared item, fitting, and optional builder-cost data
uv run mkts-backend sync

# One market plus shared databases
uv run mkts-backend sync --market=deployment

# Only that market's database
uv run mkts-backend sync --market=deployment --markets-only
```

`sync` downloads saved data from Turso. It does not run EVE market collection or
upload local edits. `--no-buildcost` skips builder-cost data; `--include-testing`
also downloads the configured development database.

## Checking fits and making shopping lists

```bash
uv run fitcheck list-fits --market=primary
uv run fitcheck --fit=42 --market=primary
uv run fitcheck --file="my-fit.txt" --market=deployment
uv run fitcheck --fit=42 --market=primary --target=50 --output=multibuy
uv run fitcheck --fit=42 --market=primary --output=csv
uv run fitcheck --fit=42 --market=primary --output=markdown
```

A **fit ID** identifies one ship loadout. A **doctrine ID** identifies a group of
fits. Use `list-fits` to find a fit ID; it is not an EVE item/type ID.

File input must contain EFT text, beginning with a line such as
`[Hurricane Fleet Issue, Fit name]`. File checks use saved market statistics,
falling back to saved orders for items outside the watchlist. Fit-ID checks use
the latest calculated doctrine data. Neither mode requests fresh structure orders.

`--target=N` overrides the report's target without changing the saved target.
Multibuy and Markdown exports require a target and items below that target.
CSV exports are saved to a filename reported by the command.

`--paste` also accepts EFT input. For fit checks, finish with Ctrl+D on an empty
line on Linux/macOS (or two consecutive blank lines). To avoid terminal-specific
paste controls, save the EFT text to a file and use `--file`.

Jita comparison prices reuse a cache for the configured TTL (currently one hour).
`--refresh` fetches fresh Jita prices; it does not refresh local market stock.
`--no-jita` hides the Jita comparison columns.

## Restocking across fits

```bash
uv run fitcheck needed --market=primary
uv run fitcheck needed --ship=Maelstrom --market=primary
uv run fitcheck needed --fit=42 --market=deployment
uv run fitcheck needed --target=0.5 --market=primary
uv run fitcheck needed --assets --market=primary
uv run fitcheck needed --assets --refresh --market=primary
```

Here `--target=0.5` filters for fits below **50% of their saved target**; it is not
a new target quantity. `--ship` and `--fit` accept comma-separated values.
`--assets` adds configured characters' asset information. For `needed`,
`--refresh` bypasses the asset cache only.

## Finding modules and assets

```bash
uv run fitcheck module --id=11269 --market=primary
uv run fitcheck module --name="Multispectrum Energized Membrane II" --market=all
uv run mkts-backend assets --name="Damage Control"
uv run mkts-backend assets --id=11379 --refresh
```

`module` shows fits that use an item. `assets` searches configured characters'
assets, cached locally for one hour. Asset access requires authorization for
those characters; see [setup](setup.md#eve-authorization).

## Managing fits and doctrines

These commands change shared fitting/market data and normally upload changes to
Turso. Download current data with `sync` before editing. Read the prompts and the
market scope below before confirming changes.

Use `fit-update` for the interactive workflows:

```bash
uv run mkts-backend fit-update list-fits --market=primary
uv run mkts-backend fit-update list-doctrines --market=primary
uv run mkts-backend fit-update add --paste --interactive --market=primary
uv run mkts-backend fit-update update --fit-id=313 --file="my-fit.txt" --market=primary --dry-run
uv run mkts-backend fit-update update --fit-id=313 --file="my-fit.txt" --market=primary
uv run mkts-backend fit-update create-doctrine --market=primary
uv run mkts-backend fit-update doctrine-add-fit --market=primary
uv run mkts-backend fit-update doctrine-remove-fit --market=primary
```

For the numbered multiline editor used when adding/updating a fit, press **Esc,
then Enter** to submit pasted EFT text. This differs from `fitcheck --paste`.

`add --interactive` prompts for metadata and market selection. `update` replaces
fit contents in **every market where the fit already exists**, even when
`--market` names just one. `--dry-run` previews the add/update workflow; it is
not a universal preview flag for every management command.

`doctrine-add-fit` currently prompts for the doctrine even if a `--doctrine-id`
is supplied. Both doctrine link commands offer interactive selection. To pass
several fits, use `--fit-id=313,314,315` (singular `fit-id`), not `--fit-ids`.
`doctrine-add-fit --skip-targets` keeps existing targets and skips target prompts.

### Changing saved targets and labels

```bash
uv run mkts-backend fit-update update-target --fit-id=313 --target=300 --market=deployment
uv run mkts-backend fit-update update-lead-ship --doctrine-id=21 --fit-id=313 --market=deployment
uv run mkts-backend fit-update update-friendly-name --doctrine-id=21 --name="Hurricane" --market=all
```

`update-target` prompts for confirmation in a terminal; omit `--target` to enter
it at the prompt. The top-level `mkts-backend update-target` also exists but
requires both values and does not provide the same confirmation prompt.

A friendly name is the short display label for a doctrine. Friendly-name changes
apply to **all configured markets**, regardless of the market selector.
`populate-friendly-names` loads a `doctrine_names.json` file from the working
folder, with entries such as `{"21": "Hurricane", "34": "Muninn"}`, and also
updates all markets.

### Assignment and removal scope

The fitting assignment model currently links `primary` and `market3`.
Assigning to `primary` includes market3; saved target changes for `primary` also
apply to market3. Deployment can be managed separately.

```bash
uv run mkts-backend fit-update assign-market --fit-id=313 --market=deployment
uv run mkts-backend fit-update unassign-market --fit-id=313 --market=deployment
```

Assignment replaces the fit's market membership; choosing deployment can remove
its primary/market3 assignment. Use `--market=all` to assign everywhere.
These commands also accept `--doctrine-id` to act on a whole doctrine.
`unassign-market` removes membership from the selected market group.

`doctrine-remove-fit` unlinks a fit from a doctrine while keeping its fit record.
The separate `fit-update remove` command removes wider tracking. In the current
implementation, `remove --market=primary` selects **all markets**. Have the
maintainer handle full removals; do not use it as a primary-only delete.

### File and metadata workflow

`update-fit` is a separate file-based command; it is not an alias for
`fit-update` and does not take its subcommands or paste mode.

```bash
uv run mkts-backend update-fit --fit-file="my-fit.txt" --fit-id=313 --interactive --market=deployment --dry-run
```

Replace `--fit-id=313 --interactive` with `--meta-file="metadata.json"` to use:

```json
{
  "fit_id": 313,
  "name": "Hurricane Fleet Issue - Arty",
  "description": "Standard doctrine fit",
  "doctrine_id": 42,
  "target": 300
}
```

Remove `--dry-run` to save. `--update-targets` also updates target-related tables;
`--no-clear` keeps existing fitting items. Legacy `--remote`, `--local`, and
`--local-only` flags are not reliable ways to isolate an edit from shared data:
the engines use the same local replica and management writers still push.

## Watchlists and interchangeable modules

```bash
# Add EVE type IDs to the deployment market watchlist
uv run mkts-backend add-watchlist --type-id=11379,11269 --market=deployment

# Search without changing groups
uv run mkts-backend equiv find "Thermal Armor Hardener"
uv run mkts-backend equiv list --market=primary

# Save an interchangeable-module group
uv run mkts-backend equiv add --type-ids=13984,17838 --market=primary
```

The market watchlist controls which items receive calculated statistics.
`add-watchlist --file` reads item names, one per line; `--paste` accepts those
names in the multiline editor. Successfully adding market items also attempts
to add buildable items to the independent builder-cost watchlist.

Equivalence groups combine stock for interchangeable modules. `equiv find`
compares SDE attributes; adding `--add` saves its result. `equiv remove --id=1`
deletes a group. Equivalence operations default to **all markets**; use an explicit
`--market=primary`, `deployment`, or `market3` to narrow them. Omit the selector
for all markets (the current equiv handler does not expand `--market=all`).

## Collection and other tools

| Command | Purpose |
|---|---|
| `mkts-backend update-markets --market=primary --history` | Collect orders and history, calculate statistics, and upload results |
| `mkts-backend update-builder-costs` | Refresh the shared manufacturing-cost database |
| `mkts-backend build-watchlist` | Manage manufacturing items; requires a subcommand |
| `mkts-backend add-structure --dry-run` | Preview structure imports from the configured Google Sheet |
| `mkts-backend parse-items --input="structure_data.txt" --output="market_prices.csv"` | Convert copied structure data into a priced CSV |
| `mkts-backend esi-auth` | Choose a configured character and authorize EVE access |
| `mkts-backend --check_tables --market=primary` | Inspect database tables |
| `mkts-backend --list-db-paths` | Print configured database filenames |
| `mkts-backend --validate-env` | Check required credential presence, not remote connectivity |

Prefix these commands with `uv run`. See [builder costs](builder_costs.md) for
manufacturing commands and [setup](setup.md) for credentials.

For command help use `uv run fitcheck --help`,
`uv run mkts-backend fit-update --help`, or
`uv run mkts-backend build-watchlist --help`. Some commands show the general
help page rather than a dedicated page.
