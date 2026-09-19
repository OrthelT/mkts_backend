# Setup and troubleshooting

This page is for the person preparing the installation. Users can start
with the [README](../README.md) once their credentials and databases are ready.

## Existing installation
Run `uv sync` from the repository folder to install the locked dependencies.
Python 3.12 or newer is required. Copy `.env.example` to `.env` only if you do not
already have a configured `.env`, then supply credentials for the databases you
will use.

Configuration lives in `src/mkts_backend/config/settings.toml`:

- `[markets.<name>]` identifies each market, structure, database file, and the
  environment-variable names containing its Turso URL and token.
- `[shared.*]` routes SDE item data, fittings, builder costs, and the testing database.
- `[characters.*]` and `[corporations.*]` configure asset lookups.
- `[google_sheets]` controls optional market exports; `[buildcost]` selects the
  structure import sheet.
- `[jita].cache_ttl_hours` controls reuse of Jita comparison prices.

The database URL/token **values** in `.env` choose the remote server. A filename
or alias that contains `test` does not make a production credential safe for testing.

```bash
uv run mkts-backend --list-markets
uv run mkts-backend --list-db-paths
uv run mkts-backend sync
uv run fitcheck list-fits --market=primary
```

The default sync needs each market's credentials plus SDE and fittings credentials.
Missing optional buildcost/testing credentials do not prevent ordinary market use.
For one market and the shared databases, use `sync --market=primary`; add
`--markets-only` only when the shared data is already available.

`--validate-env` checks credential presence for collection across all configured
markets. It also requires `CLIENT_ID`, `SECRET_KEY`, and `REFRESH_TOKEN`; a
lookup-only installation may work without those collection credentials.
This check does not authenticate against EVE or Turso.

# Setting Up `.env` file 
The `.env`` file is where you will save all your credentials. Never share this file or commit it to
a public git repository. A file called `.env.example` is included. Copy it to a new file called  `.env` and populate it with your own credentials. 

Rename `.env.example` to `.env` then fill in your credentials. 

## Turso Database Setup
You'll need to configure database credentials. Setup a [Turso](https://turso.tech/) account if you don't already have one. 

### Install the Turso cli
Linux/Windows (WSL)
```bash
curl -sSfL https://get.tur.so/install.sh | bash
```
macOS 
```bash 
`brew install tursodatabase/tap/turso 
  ```
First time log-in. From the bash command prompt:
```bash
turso auth login 
```

### Get Database Credentials 
Get database urls and authorization tokens to paste into your .env file.
```bash
# list available databases and their urls
turso db list
# Create db tokens
turso tokens create <database_name>

```
Once you have your Turso credentials set up run this to install the databases. 
```bash
uv run mkts-backend sync

```
### EVE Developer Credentials
Market collection requires an EVE developer application's `CLIENT_ID` and
`SECRET_KEY`, plus an authorized character's `REFRESH_TOKEN`. The character must
have access to the market structure. The structure-market scope is
`esi-markets.structure_markets.v1`.

You can configure your application at the [Eve Developer page](https://developers.eveonline.com/)
- The current OAuth code uses the callback `http://localhost:8000/callback`. Register that callback for the EVE application.
- Set the following scopes: `esi-markets.structure_markets.v1`, `esi-assets.read_assets.v1`
- Paste these into your .env file `CLIENT_ID` AND `SECRET_KEY` fields. 

# Advanced Configuration Options 
## Google Sheets (optional)
For Sheets exports or structure imports, supply service-account credentials via
`GOOGLE_APPLICATION_CREDENTIALS` (path to a JSON file),
`GOOGLE_SERVICE_ACCOUNT_FILE` (local filename), or `GOOGLE_SHEET_KEY` (literal
JSON). The file credentials take precedence over literal JSON.

Enable the Sheets/Drive APIs for the account's project and share the intended
spreadsheet with the service-account email. Give edit access for exports, or
read access when only importing structures. Configure the sheet URL and worksheet
names in settings. Ordinary fit checks do not need Google credentials.

## Setting up characters to include in asset searches 
You can display character assets when checking for items needed on the market with: 
```bash 
uv run mkts-backend needed --assets
```
You will need esi authorization for these characters, which are configured in settings.toml. Change the character keys to your own characters and then run 
```bash
uv run mkts-backend esi-auth
```

Choose a character and complete the browser authorization. To select a configured
key directly, use `esi-auth --char=your_character_key`. The command requests the
scopes in `esi/esi_auth.py::REQUIRED_SCOPES`, saves a per-character token file,
and reports the saved filename. Treat token files as secrets. The configured
character's `token_env` names the environment variable for its refresh token. For the market collector, set `REFRESH_TOKEN` to the authorized
market character's refresh token; the collector uses its separate `token.json`
cache. Per-character asset credentials follow the settings entries.

## Creating a new market
This is a maintainer task. Create its Turso database and add a complete
`[markets.<alias>]` block, including unique `database_alias`, `database_file`,
`turso_url_env`, `turso_token_env`, structure/region/system IDs, name, and
`gsheets_url` (empty when unused). Supply the matching credentials.

SDE and fitting data must already exist. Adding a settings block enables routing,
but does not populate doctrine reference data or remove the current
primary/market3 fitting-assignment relationship.

`scripts/seed_new_market.py` copies reference tables from an existing market.
Have the maintainer review its dry-run with explicit source/destination database
aliases before using `--apply`; applying replaces destination reference tables.
Then run `update-markets --market=<new-alias> --history` to populate market data.
Do not treat the seeding script as a routine refresh command.

## Troubleshooting
| Message or symptom | What to do |
|---|---|
| `uv` not found | Install uv, reopen the terminal, and run from the project folder. |
| Unknown command or option | Copy an example from the CLI guide. Use `--market=deployment`, including the equals sign. |
| Fit not found | List fits for that same market and check the ID. |
| Old stock figures | Run `sync`; check when collection last ran. `--refresh` on a fit check only refreshes Jita prices. |
| Missing credentials | Fill in the named `.env` entries. Database names and credential-variable names come from settings. |
| EVE authorization error | Reauthorize the intended character and confirm the application, scopes, and structure access. |
| Missing table/reference data | Run `sync`. A new remote database needs maintainer setup and reference data. |
| Replica remote mismatch | Stop and have the maintainer check `.env` and database routing before rebuilding anything. |
| Push/upload failed | The edit may exist locally or have partly reached Turso. Report the error; `sync` is a download and does not retry the upload. |

Logs are in `logs/mkts-backend.log` (with rotated backups). Share the command,
error, and relevant log excerpt with the maintainer after checking for secrets.
Do not delete database files or copy an old database over the current one to
repair an error; replicas have associated sync state and may contain pending edits.
