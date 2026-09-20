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
markets. It also requires `CLIENT_ID` and `SECRET_KEY`, plus `REFRESH_TOKEN`
unless the token cache already holds a refresh token; a lookup-only installation
may work without those collection credentials.
This check does not authenticate against EVE or Turso.

## Setting up `.env`

The project's `.env` file holds your credentials. If it does not already exist,
copy `.env.example` to `.env`, then add your database credentials. Keep an
existing configured file. The EVE setup menu below fills in application
credentials and refresh tokens without replacing unrelated entries. Never
share `.env` or commit it to a public repository.

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
## EVE authorization
Market collection requires an EVE developer application's `CLIENT_ID` and
`SECRET_KEY`, plus an authorized character's refresh token — either in
`REFRESH_TOKEN` or in the token cache that `esi-auth` writes. The character must
have access to the market structure. The structure-market scope is
`esi-markets.structure_markets.v1`.

Run the authentication setup menu before your first collection:

```bash
uv run mkts-backend esi-auth
```

Choose **Configure application credentials** to enter your EVE application's
client ID and secret. The menu shows the callback URL and scopes to register at
the [EVE Developer Portal](https://developers.eveonline.com/), and saves the
credentials in the project's `.env` without replacing unrelated settings.
Then choose **Authorize market data access**, sign in through the browser, and
return to the terminal. The local callback is captured automatically; if that
is unavailable, paste the full redirect URL when prompted. No existing refresh
token is needed. The command saves `REFRESH_TOKEN` in `.env` and the token cache
configured by `[auth].token_file` (normally `token.json` in the working directory).

To start market data authorization directly or inspect saved setup:

```bash
uv run mkts-backend esi-auth --market-data
uv run mkts-backend esi-auth --status
```

Direct authorization also prompts for application credentials if they are
missing. `--status` reports credential and cache presence without displaying
secrets or contacting EVE; it does not prove tokens are valid or that the
character can access a structure. It works without an interactive terminal.
Authorization itself requires a terminal; collection and scheduled jobs never
open the setup menu or browser automatically.
For GitHub Actions, copy the resulting `.env` credential values to the matching
repository secrets; local setup does not update GitHub secrets.

The default callback is `http://localhost:8000/callback`; `[auth].callback_url`
is authoritative and must match your registered application's callback exactly.
The current setup supports an HTTP callback on `localhost` or `127.0.0.1`.
Use a browser on the same computer as the terminal for automatic capture.
If the browser does not open, open the printed authorization link yourself.
If the listener cannot start or the two-minute wait expires, complete sign-in
and paste the full redirect URL into the terminal when prompted, even if the
browser shows a connection error for the callback page.

Run setup and collection from the project folder so they share token caches.
Changing an existing client ID through the menu clears market and configured
character token caches. Authorize each target you use again; tokens issued to
the previous application cannot be reused with the new one.

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

Choose **Authorize character** and complete browser authorization. To select a
configured key directly, use:

```bash
uv run mkts-backend esi-auth --char=your_character_key
```

Replace `your_character_key` with the key under `[characters.*]`, not the
character's display name or numeric ID. Sign in as the character associated
with that key. The setup menu displays the character scopes to register for
structure access, market data, and character/corporation assets. Authorization
saves `token_<key>.json` in the working directory and saves its refresh
token in `.env` using the character's configured `token_env` name. Market data
and character authorization are separate menu choices. Treat `.env` and token
files as secrets.

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
