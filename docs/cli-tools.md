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

## Managing EVE authorization

Run the setup menu before your first market collection or character asset lookup:

```bash
uv run mkts-backend esi-auth
```

Choose **Configure application credentials**, then **Authorize market data
access** or **Authorize character**. Setup opens browser sign-in, captures the
callback, and saves the refresh token and application credentials to the
project's `.env`. No existing refresh token is needed. If automatic capture
fails, paste the full redirect URL when prompted.

You can also choose an action directly:

```bash
uv run mkts-backend esi-auth --market-data
uv run mkts-backend esi-auth --char=your_character_key
uv run mkts-backend esi-auth --status
```

Use one option at a time. Replace `your_character_key` with a key configured
under `[characters.*]`. Market data authorization is shared by collection;
`--market` does not select a separate authorization. Character authorization
is separate and enables asset lookups for that configured character.

`--status` checks saved credential and cache presence without showing secrets,
contacting EVE, or requiring an interactive terminal. It does not validate
tokens or structure access. Authorization requires a terminal. See
[EVE authorization setup](setup.md#eve-authorization) for application
registration, callback settings, token storage, and scheduled jobs.

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
uv run fitcheck module --list-all --market=primary
uv run fitcheck module --list-all=build --market=primary
uv run fitcheck module --list-all --market=all --output=markdown
uv run mkts-backend assets --name="Damage Control"
uv run mkts-backend assets --id=11379 --refresh
```

`module` shows fits that use an item. `module --list-all` lists every item
below at least one fit's target, with the quantity needed. `--list-all=build`
keeps only items that can be manufactured; `--list-all=import` keeps the rest.
Plain `--list-all` (or `--list-all=all`) shows both. Its `--output` is
`multibuy` (default, for EVE Multibuy or jEveAssets), `markdown` (a Discord post
grouped into Ship, Module, Charge, and Other), or `csv` (writes
`<market>_low_stock.csv` in the current directory, or
`<market>_low_stock_build.csv` / `<market>_low_stock_import.csv`). `assets` searches configured characters'
assets, cached locally for one hour. Asset access requires authorization for
those characters; see [setup](setup.md#eve-authorization).

## Managing fits and doctrines

`fit-manager` is the one command for adding, changing, and removing fits and
doctrines. It changes shared fitting/market data and uploads each change to
Turso. Download current data with `sync` before editing.

```bash
uv run mkts-backend fit-manager --help                # list the subcommands
uv run mkts-backend fit-manager update --help         # one subcommand's options and examples
```

Every subcommand has its own help page with its options, the markets it
writes, and examples. An option that a subcommand does not list is rejected
with an error, so a typo cannot be silently ignored.

| Subcommand | Purpose | Markets written |
|---|---|---|
| `add` | Add a new fit from EFT text and link it to doctrines | Selected market; `all` for every market |
| `update` | Replace the items of an existing fit | Every market that already tracks the fit |
| `remove` | Stop tracking a fit in a market | Selected market; `all` for every market |
| `list-fits` | List the fits tracked in a market | Reads one market |
| `create-doctrine` | Create a new doctrine | None (shared fittings database) |
| `list-doctrines` | List the doctrines assigned to a market | Reads one market |
| `doctrine-add-fit` | Link existing fits to a doctrine | Selected market; `all` for every market |
| `doctrine-remove-fit` | Unlink fits from a doctrine | One market |
| `assign-market` | Set which markets stock a fit or doctrine | `primary` (with market3), `deployment`, or `all` |
| `unassign-market` | Remove a fit or doctrine from a market | `primary` (with market3), `deployment`, or `all` |
| `update-target` | Set the target quantity for a fit | Selected market; `primary` also writes market3 |
| `update-lead-ship` | Set the lead ship shown for a doctrine | Selected market; `all` for every market |
| `update-friendly-name` | Set the short display name for a doctrine | Every market |
| `populate-friendly-names` | Set display names in bulk from a JSON file | Every market |

Subcommands that take a market ask which one to use when you leave out
`--market` in a terminal. `update`, `create-doctrine`, `update-friendly-name`,
and `populate-friendly-names` take no market option and reject one.

### Adding and updating fits

```bash
# Add a new fit: paste it, then answer the prompts
uv run mkts-backend fit-manager add --paste --interactive --market=primary

# Add a new fit from files
uv run mkts-backend fit-manager add --file="my-fit.txt" --meta-file="metadata.json" --market=deployment

# Preview, then save, new items for an existing fit
uv run mkts-backend fit-manager update --fit-id=313 --file="my-fit.txt" --dry-run
uv run mkts-backend fit-manager update --fit-id=313 --file="my-fit.txt"
```

Give the fit with `--file` or `--paste`. In the paste editor, press **Esc, then
Enter** to submit. This differs from `fitcheck --paste`.

`add` needs the fit's metadata from `--interactive` (prompts) or `--meta-file`:

```json
{
  "fit_id": 313,
  "name": "Hurricane Fleet Issue - Arty",
  "description": "Standard doctrine fit",
  "doctrine_id": 42,
  "target": 300
}
```

`update` replaces a fit's items in **every market where the fit already
exists** and keeps its stored name, doctrines, and target. Add `--meta-file` to
replace those too. Add `--no-clear` to keep items that the new fit no longer
lists. `update` fails for a fit that no market tracks; use `add` for that.
`--dry-run` is available on `add` and `update` only.

### Doctrines

```bash
uv run mkts-backend fit-manager create-doctrine
uv run mkts-backend fit-manager doctrine-add-fit --fit-id=313,314,315 --market=primary
uv run mkts-backend fit-manager doctrine-remove-fit --doctrine-id=21 --fit-id=313 --market=primary
```

`doctrine-add-fit` always asks which doctrine to use. `--skip-targets` keeps
existing targets and skips the target prompts. `doctrine-remove-fit` unlinks a
fit from one doctrine and keeps the fit.

### Targets and labels

```bash
uv run mkts-backend fit-manager update-target --fit-id=313 --target=300 --market=deployment
uv run mkts-backend fit-manager update-lead-ship --doctrine-id=21 --fit-id=313 --market=deployment
uv run mkts-backend fit-manager update-friendly-name --doctrine-id=21 --name="Hurricane"
```

`update-target` asks for confirmation in a terminal; omit `--target` to enter
it at the prompt.

A friendly name is the short display label for a doctrine. Friendly-name
changes apply to **all configured markets**. `populate-friendly-names` loads
`doctrine_names.json` from the working folder:

```json
[
  {"fit_id": 313, "doctrine_id": 21, "friendly_name": "Hurricane"},
  {"fit_id": 550, "doctrine_id": 34, "friendly_name": "Muninn"}
]
```

### Assignment and removal scope

The fitting assignment model links `primary` and `market3`. Assigning to
`primary` includes market3; a target change for `primary` also applies to
market3. Deployment is managed separately.

```bash
uv run mkts-backend fit-manager assign-market --fit-id=313 --market=deployment
uv run mkts-backend fit-manager unassign-market --fit-id=313 --market=deployment
```

Assignment replaces the fit's market membership; choosing deployment removes
its primary/market3 assignment. Use `--market=all` to assign everywhere. Pass
`--doctrine-id` in place of `--fit-id` to act on a whole doctrine.

`remove` stops tracking a fit in the selected market: it deletes the fit from
that market's doctrines, targets, and stock data. `remove --market=all` does
this in every market. A removal asks for confirmation and cannot be undone.

### Replaced commands

`fit-manager` replaced three commands. Running an old name prints this mapping
and exits with an error.

| Old command | Use |
|---|---|
| `fit-update <subcommand>` | `fit-manager <subcommand>` (same subcommand names) |
| `update-target` | `fit-manager update-target` |
| `update-fit --fit-file=... --meta-file=...` | `fit-manager update --file=... --meta-file=...` for an existing fit, or `fit-manager add` for a new fit |

`fit-manager` does not accept `--remote`, `--local-only`, or `--update-targets`.
Those options never changed where data was saved: every change goes to the
local copy and is then uploaded, and targets are always saved with a fit.

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
| `mkts-backend esi-auth` | Manage ESI application credentials and authorize market data access or characters |
| `mkts-backend --check_tables --market=primary` | Inspect database tables |
| `mkts-backend --list-db-paths` | Print configured database filenames |
| `mkts-backend --validate-env` | Check required credential presence, not remote connectivity |

Prefix these commands with `uv run`. See [builder costs](builder_costs.md) for
manufacturing commands and [setup](setup.md) for credentials.

For command help use `uv run fitcheck --help`,
`uv run mkts-backend fit-manager --help`, or
`uv run mkts-backend build-watchlist --help`. Some commands show the general
help page rather than a dedicated page.
