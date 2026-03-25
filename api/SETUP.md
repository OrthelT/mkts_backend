# mkts-api — Cloudflare Worker Setup

## Prerequisites

- Node.js 18+
- A Cloudflare account (free tier is fine)
- Turso CLI (`curl -sSfL https://get.tur.so/install.sh | bash`)

## Initial Setup

```bash
cd api
npm install
```

## Database Migration

Auth tables (api_keys, standings, character_affiliations) live in the **primary** Turso database. Run the migration:

```bash
turso db shell <your-primary-db> < migrations/001_auth_tables.sql
```

### Seed Data

Add an API key for a character:
```sql
INSERT INTO api_keys (api_key, character_id, character_name)
VALUES ('your-generated-key-here', 2116257395, 'Orthel Toralen');
```

Add alliance standings:
```sql
-- WinterCo and allies at standing 10
INSERT INTO standings (entity_id, entity_type, entity_name, standing)
VALUES (99003214, 'alliance', 'Fraternity.', 10);
```

Generate API keys with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

## Configure Secrets

```bash
npx wrangler secret put TURSO_PRIMARY_URL
npx wrangler secret put TURSO_PRIMARY_TOKEN
npx wrangler secret put TURSO_DEPLOYMENT_URL
npx wrangler secret put TURSO_DEPLOYMENT_TOKEN
```

## Local Development

```bash
cat > .dev.vars << 'EOF'
TURSO_PRIMARY_URL=libsql://your-primary-db.turso.io
TURSO_PRIMARY_TOKEN=your-token
TURSO_DEPLOYMENT_URL=libsql://your-deployment-db.turso.io
TURSO_DEPLOYMENT_TOKEN=your-deployment-token
EOF

npm run dev
# Worker runs at http://localhost:8787
```

## Deploy

```bash
npm run deploy
```

## Custom Domain (GoDaddy)

1. **Add your domain to Cloudflare** (free plan):
   - Cloudflare dashboard → "Add a site" → enter your domain
   - Cloudflare gives you two nameservers
   - GoDaddy → DNS Management → change nameservers to the Cloudflare ones
   - Wait for propagation (~10-60 min)

2. **Route your Worker to a subdomain**:
   - Cloudflare dashboard → Workers & Pages → mkts-api → Settings → Domains & Routes
   - Add a custom domain, e.g. `api.yourdomain.com`
   - SSL is handled automatically

## Auth Flow

1. Request includes `X-API-Key` header
2. Worker looks up the key in `api_keys` → gets `character_id`
3. Worker looks up `character_id` in `character_affiliations` → gets `alliance_id`
4. Worker looks up `alliance_id` in `standings` → gets standing value
5. If alliance not found or standing < threshold (default 5) → **403 Forbidden**

A cron trigger runs every 6 hours to refresh character affiliations from the ESI
`POST /characters/affiliation/` endpoint. New characters will have their
affiliation populated within 6 hours of their API key being created.

## API Endpoints

All data endpoints require `X-API-Key`. Responses include market config headers:

| Header | Example |
|---|---|
| `X-Market-Alias` | `primary` |
| `X-Market-Name` | `4-HWWF Keepstar` |
| `X-Market-Region-Id` | `10000003` |
| `X-Market-System-Id` | `30000240` |
| `X-Market-Structure-Id` | `1035466617946` |

### Usage

```bash
KEY="your-api-key"

# API index (no auth required)
curl https://api.yourdomain.com/

# Primary market stats
curl -H "X-API-Key: $KEY" https://api.yourdomain.com/primary/stats

# Primary market stats filtered by type IDs
curl -H "X-API-Key: $KEY" "https://api.yourdomain.com/primary/stats?type_ids=34,35,36"

# Deployment market sell orders
curl -H "X-API-Key: $KEY" "https://api.yourdomain.com/deployment/orders?sell=true&type_ids=34"

# Primary history (last 7 days)
curl -H "X-API-Key: $KEY" "https://api.yourdomain.com/primary/history?type_ids=34,35&days=7"

# Pagination
curl -H "X-API-Key: $KEY" "https://api.yourdomain.com/primary/orders?limit=50&offset=100"
```

## Configuration

| Setting | Location | Default |
|---|---|---|
| `STANDINGS_THRESHOLD` | `wrangler.toml` | `5` |
| `RATE_LIMIT_MAX` | `wrangler.toml` | `60` req/min/IP |
| `DEFAULT_PAGE_SIZE` | `wrangler.toml` | `100` |
| `MAX_PAGE_SIZE` | `wrangler.toml` | `500` |
| Cron schedule | `wrangler.toml` | Every 6 hours |
