# mkts-api — Cloudflare Worker Setup

## Prerequisites

- Node.js 18+
- A Cloudflare account (free tier is fine)

## Initial Setup

```bash
cd api
npm install
```

## Configure Secrets

Set your Turso credentials and API key as Cloudflare secrets:

```bash
# Turso databases (use the URLs/tokens from your existing .env)
npx wrangler secret put TURSO_PRIMARY_URL
npx wrangler secret put TURSO_PRIMARY_TOKEN
npx wrangler secret put TURSO_DEPLOYMENT_URL
npx wrangler secret put TURSO_DEPLOYMENT_TOKEN

# API key — generate something random and share with your users
npx wrangler secret put API_KEY
```

## Local Development

```bash
# Create a .dev.vars file with your secrets for local testing
cat > .dev.vars << 'EOF'
TURSO_PRIMARY_URL=libsql://your-db.turso.io
TURSO_PRIMARY_TOKEN=your-token
TURSO_DEPLOYMENT_URL=libsql://your-deployment-db.turso.io
TURSO_DEPLOYMENT_TOKEN=your-deployment-token
API_KEY=your-dev-api-key
EOF

npm run dev
# Worker runs at http://localhost:8787
```

## Deploy

```bash
npm run deploy
# Outputs: https://mkts-api.<your-subdomain>.workers.dev
```

## Custom Domain (GoDaddy)

1. **Add your domain to Cloudflare** (free plan):
   - In Cloudflare dashboard → "Add a site" → enter your domain
   - Cloudflare gives you two nameservers (e.g. `anna.ns.cloudflare.com`)
   - In GoDaddy → DNS Management → change nameservers to the Cloudflare ones
   - Wait for propagation (~10-60 min)

2. **Route your Worker to a subdomain**:
   - In Cloudflare dashboard → Workers & Pages → mkts-api → Settings → Domains & Routes
   - Add a custom domain, e.g. `api.yourdomain.com`
   - Cloudflare handles SSL automatically

## Usage

All requests require the `X-API-Key` header:

```bash
# Get API info
curl -H "X-API-Key: your-key" https://api.yourdomain.com/

# Market stats (all items)
curl -H "X-API-Key: your-key" https://api.yourdomain.com/stats

# Filter by type_ids
curl -H "X-API-Key: your-key" "https://api.yourdomain.com/stats?type_ids=34,35,36"

# Orders (sell only)
curl -H "X-API-Key: your-key" "https://api.yourdomain.com/orders?type_ids=34&sell=true"

# History (last 7 days)
curl -H "X-API-Key: your-key" "https://api.yourdomain.com/history?type_ids=34,35&days=7"

# Deployment market instead of primary
curl -H "X-API-Key: your-key" "https://api.yourdomain.com/stats?market=deployment"

# Pagination
curl -H "X-API-Key: your-key" "https://api.yourdomain.com/orders?limit=50&offset=100"
```

## Rate Limits

- 60 requests per minute per IP (configurable in wrangler.toml)
- Returns HTTP 429 when exceeded
