# EVE Market Tools

Check doctrine stock, find items to restock, and manage fits for WinterCo markets.
Market collection feeds the [market website](https://github.com/OrthelT/wcmkts_new).

**Turso database credentials are required for both `sync` and `update-markets`.**
Ask the maintainer for the credentials and add them to `.env` before running
either command. `update-markets` also requires EVE API authorization.
See [first-time setup](#first-time-setup) if this installation is not configured yet.

## Start here
### Updating market data
Once your environment is setup, open a terminal in the `mkts_backend`
folder and then run on of these commands:
```bash
# Update market orders for all markets 
uv run mkts-backend update-markets
```
This refreshes all configured markets in one run, calculates market and doctrine statistics, and uploads the results. Run it whenever you want to update market data manually. 

Or, if just update local databases without pulling new data:
```bash
uv run mkts-backend sync 

```
Update all market data, including history data (takes several minutes to complete):
```bash
uv run mkts-backend update-markets --history
```

Only update a specific market:
```bash
uv run mkts-backend --market=[primary, deployment, market3]
```
>TIP[You can check configured markets with `mkts-backend list-markets`

Only pull updated data from the remote databases. Creates local db replicas if they don't already exist. 
```bash
uv run mkts-backend sync 
```
> TIP[**"mkts"** can be used as a shorter alias for **"mkts-backend"**.] 
```bash
uv run mkts update-markets
```
 
### Check market stocks
List doctrine fits and checks their stock. 
```bash
uv run fitcheck list-fits --market=primary
uv run fitcheck --fit=997 # returns stock of Osprey Navy Issue on the primary market 
```
See which doctrine items need to be restocked:
```bash
uv run fitcheck needed
uv run fitcheck needed --market=deployment # check the deployment market 
```

## Everyday commands

| What you want to do | Command |
|---|---|
| Refresh all markets | `uv run mkts-backend update-markets` |
| See configured markets | `uv run mkts-backend --list-markets` |
| Download the latest saved data | `uv run mkts-backend sync` |
| List fits | `uv run fitcheck list-fits --market=primary` |
| Check a saved fit | `uv run fitcheck --fit=42 --market=primary` |
| Check an EFT text file | `uv run fitcheck --file="my-fit.txt" --market=primary` |
| Show restocking needs | `uv run fitcheck needed --market=primary` |
| Find fits using an item | `uv run fitcheck module --name="Damage Control II" --market=primary` |
| Show fit-check help | `uv run fitcheck --help` |

Use `--market=deployment` for X47L-Q or `--market=market3` for BKG-Q2.
`primary` currently means 4-HWWF. Run `--list-markets` to check current names.
Put options after the command and use `--option=value`, as shown above.

For a shopping list that you can copy into EVE's Multibuy window:

```bash
uv run fitcheck --fit=42 --market=primary --target=50 --output=multibuy
```

`--target=50` means enough items for 50 fits. It changes this report only.
`--output=csv` saves a spreadsheet-friendly file; `--output=markdown` prints
text suitable for sharing. The command reports the export location when it saves a file.

The [CLI guide](docs/cli-tools.md) covers assets, adding and updating fits,
doctrine targets, and other commands. Commands in its management sections change
shared data and normally upload those changes automatically.

## First-time setup

You need Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).
The project uses Python 3.12 or newer; `uv sync` prepares its Python environment.

```bash
git clone https://github.com/OrthelT/mkts_backend.git
cd mkts_backend
uv sync
cp .env.example .env
```

You will need: 
- An Eve Developers Account. Set it up at [Eve Developer Site](https://developers.eveonline.com/)
- A [Turso Cloud](https://turso.tech/) account to access remote databases. 

Fill in `.env` using a text editor. This will store all of your credentials
and should never be shared. It will include credentials for:
- Databases  
- Eve Online Developer Application credentials
- Janice API key (optional)
- Google Sheets API token (optional)

Do not share that file. Existing users should keep their configured `.env`.

Then download the data and list the fits:
```bash
uv run mkts-backend sync
uv run fitcheck list-fits --market=primary
```

The supplied configuration connects to existing databases. Setting up a new
market requires populated reference databases as well as credentials; an empty
folder alone is not a complete installation. See [setup and troubleshooting](docs/setup.md)
for the maintainer's configuration steps.

## If something goes wrong

- **Old stock figures:** run `update-markets` to collect fresh data, or `sync` to
  download the latest saved collection. `fitcheck --refresh` refreshes Jita prices only.
- **Fit not found:** run `list-fits` for the same market and copy its fit ID.
- **Missing credentials or database error:** follow [troubleshooting](docs/setup.md#troubleshooting).
- **A command failed:** send the maintainer the command and error text, without credentials.
  More detail is in `logs/mkts-backend.log`.

## Further reading

- [CLI guide](docs/cli-tools.md): checking stock and managing fits.
- [Setup and troubleshooting](docs/setup.md): credentials, configuration, and common errors.
- [Builder costs](docs/builder_costs.md): manufacturing watchlist and cost refresh.
- [GitHub Actions](docs/GITHUB_ACTIONS_SETUP.md): scheduled jobs, for the maintainer.
- [AGENTS.md](AGENTS.md): code architecture and development rules, for coding agents.

EVE Online and its game data belong to CCP Games. This is a community project.
Contact `orthel_toralen` on Discord for project help.
