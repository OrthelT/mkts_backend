import { createClient, type Client } from "@libsql/client/web";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Env {
  TURSO_PRIMARY_URL: string;
  TURSO_PRIMARY_TOKEN: string;
  TURSO_DEPLOYMENT_URL: string;
  TURSO_DEPLOYMENT_TOKEN: string;
  CORS_ORIGIN: string;
  RATE_LIMIT_MAX: string;
  DEFAULT_PAGE_SIZE: string;
  MAX_PAGE_SIZE: string;
  STANDINGS_THRESHOLD: string;
}

type MarketAlias = "primary" | "deployment";

interface MarketConfig {
  alias: MarketAlias;
  name: string;
  regionId: number;
  systemId: number;
  structureId: number;
}

const MARKETS: Record<MarketAlias, MarketConfig> = {
  primary: {
    alias: "primary",
    name: "4-HWWF Keepstar",
    regionId: 10000003,
    systemId: 30000240,
    structureId: 1035466617946,
  },
  deployment: {
    alias: "deployment",
    name: "B-9C24 Keepstar",
    regionId: 10000023,
    systemId: 30002029,
    structureId: 1046831245129,
  },
};

interface AuthResult {
  characterId: number;
  characterName: string;
}

// ---------------------------------------------------------------------------
// Rate limiting
// ---------------------------------------------------------------------------

const rateCounts = new Map<string, number>();

function checkRateLimit(ip: string, maxPerMinute: number): boolean {
  const now = Math.floor(Date.now() / 60_000);
  const key = `${ip}:${now}`;
  const count = (rateCounts.get(key) ?? 0) + 1;
  rateCounts.set(key, count);

  if (rateCounts.size > 10_000) {
    for (const k of rateCounts.keys()) {
      if (!k.endsWith(`:${now}`)) rateCounts.delete(k);
    }
  }

  return count <= maxPerMinute;
}

// ---------------------------------------------------------------------------
// Response helpers
// ---------------------------------------------------------------------------

function jsonResponse(
  data: unknown,
  status = 200,
  extraHeaders?: Record<string, string>,
): Response {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...extraHeaders,
  };
  return new Response(JSON.stringify(data), { status, headers });
}

function errorResponse(message: string, status: number): Response {
  return jsonResponse({ error: message }, status);
}

function corsHeaders(origin: string): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
  };
}

function marketHeaders(config: MarketConfig): Record<string, string> {
  return {
    "X-Market-Alias": config.alias,
    "X-Market-Name": config.name,
    "X-Market-Region-Id": String(config.regionId),
    "X-Market-System-Id": String(config.systemId),
    "X-Market-Structure-Id": String(config.structureId),
  };
}

// ---------------------------------------------------------------------------
// Database clients
// ---------------------------------------------------------------------------

function getMarketClient(env: Env, market: MarketAlias): Client {
  if (market === "deployment") {
    return createClient({
      url: env.TURSO_DEPLOYMENT_URL,
      authToken: env.TURSO_DEPLOYMENT_TOKEN,
    });
  }
  return createClient({
    url: env.TURSO_PRIMARY_URL,
    authToken: env.TURSO_PRIMARY_TOKEN,
  });
}

/** Auth tables live in the primary database. */
function getAuthClient(env: Env): Client {
  return createClient({
    url: env.TURSO_PRIMARY_URL,
    authToken: env.TURSO_PRIMARY_TOKEN,
  });
}

// ---------------------------------------------------------------------------
// Auth: API key → character → affiliation → standings check
// ---------------------------------------------------------------------------

async function authenticate(
  apiKey: string | null,
  authClient: Client,
  threshold: number,
): Promise<{ ok: true; auth: AuthResult } | { ok: false; error: Response }> {
  if (!apiKey) {
    return {
      ok: false,
      error: errorResponse("Unauthorized. Provide X-API-Key header.", 401),
    };
  }

  // 1. Look up API key → character
  const keyResult = await authClient.execute({
    sql: "SELECT character_id, character_name FROM api_keys WHERE api_key = ? AND active = 1",
    args: [apiKey],
  });

  if (keyResult.rows.length === 0) {
    return {
      ok: false,
      error: errorResponse("Unauthorized. Invalid or inactive API key.", 401),
    };
  }

  const characterId = keyResult.rows[0].character_id as number;
  const characterName = keyResult.rows[0].character_name as string;

  // 2. Look up character → alliance affiliation
  const affResult = await authClient.execute({
    sql: "SELECT alliance_id FROM character_affiliations WHERE character_id = ?",
    args: [characterId],
  });

  if (affResult.rows.length === 0) {
    return {
      ok: false,
      error: errorResponse(
        "Forbidden. Character affiliation unknown. Affiliations are refreshed periodically — try again later.",
        403,
      ),
    };
  }

  const allianceId = affResult.rows[0].alliance_id as number | null;

  if (!allianceId) {
    return {
      ok: false,
      error: errorResponse(
        "Forbidden. Character is not in an alliance.",
        403,
      ),
    };
  }

  // 3. Check alliance standing
  const standingResult = await authClient.execute({
    sql: "SELECT standing FROM standings WHERE entity_id = ?",
    args: [allianceId],
  });

  if (standingResult.rows.length === 0) {
    return {
      ok: false,
      error: errorResponse(
        "Forbidden. Alliance not found on standings list.",
        403,
      ),
    };
  }

  const standing = standingResult.rows[0].standing as number;

  if (standing < threshold) {
    return {
      ok: false,
      error: errorResponse(
        "Forbidden. Insufficient standings.",
        403,
      ),
    };
  }

  return { ok: true, auth: { characterId, characterName } };
}

// ---------------------------------------------------------------------------
// Query helpers
// ---------------------------------------------------------------------------

function parseTypeIds(param: string | null): number[] | null {
  if (!param) return null;
  const ids = param
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => !isNaN(n) && n > 0);
  return ids.length > 0 ? ids : null;
}

function typeIdFilter(
  typeIds: number[] | null,
  column = "type_id",
): { clause: string; args: number[] } {
  if (!typeIds || typeIds.length === 0) return { clause: "", args: [] };
  const placeholders = typeIds.map(() => "?").join(", ");
  return {
    clause: `WHERE ${column} IN (${placeholders})`,
    args: typeIds,
  };
}

function parsePagination(
  url: URL,
  env: Env,
): { limit: number; offset: number } {
  const defaultSize = parseInt(env.DEFAULT_PAGE_SIZE, 10) || 100;
  const maxSize = parseInt(env.MAX_PAGE_SIZE, 10) || 500;
  let limit = parseInt(url.searchParams.get("limit") ?? "", 10) || defaultSize;
  limit = Math.min(Math.max(1, limit), maxSize);
  let offset = parseInt(url.searchParams.get("offset") ?? "", 10) || 0;
  offset = Math.max(0, offset);
  return { limit, offset };
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

async function handleStats(
  client: Client,
  url: URL,
  env: Env,
): Promise<Response> {
  const typeIds = parseTypeIds(url.searchParams.get("type_ids"));
  const { limit, offset } = parsePagination(url, env);
  const filter = typeIdFilter(typeIds);

  const sql = `SELECT type_id, type_name, group_id, group_name, category_id, category_name,
                      total_volume_remain, min_price, price, avg_price, avg_volume,
                      days_remaining, last_update
               FROM marketstats ${filter.clause}
               ORDER BY type_id
               LIMIT ? OFFSET ?`;

  const result = await client.execute({
    sql,
    args: [...filter.args, limit, offset],
  });

  return jsonResponse({
    count: result.rows.length,
    limit,
    offset,
    data: result.rows,
  });
}

async function handleOrders(
  client: Client,
  url: URL,
  env: Env,
): Promise<Response> {
  const typeIds = parseTypeIds(url.searchParams.get("type_ids"));
  const buyOnly = url.searchParams.get("buy") === "true";
  const sellOnly = url.searchParams.get("sell") === "true";
  const { limit, offset } = parsePagination(url, env);

  const conditions: string[] = [];
  const args: (number | boolean)[] = [];

  if (typeIds) {
    const placeholders = typeIds.map(() => "?").join(", ");
    conditions.push(`type_id IN (${placeholders})`);
    args.push(...typeIds);
  }
  if (buyOnly) {
    conditions.push("is_buy_order = ?");
    args.push(1);
  } else if (sellOnly) {
    conditions.push("is_buy_order = ?");
    args.push(0);
  }

  const where =
    conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  const sql = `SELECT order_id, type_id, type_name, is_buy_order, price,
                      volume_remain, duration, issued
               FROM marketorders ${where}
               ORDER BY type_id, price
               LIMIT ? OFFSET ?`;

  const result = await client.execute({
    sql,
    args: [...args, limit, offset],
  });

  return jsonResponse({
    count: result.rows.length,
    limit,
    offset,
    data: result.rows,
  });
}

async function handleHistory(
  client: Client,
  url: URL,
  env: Env,
): Promise<Response> {
  const typeIds = parseTypeIds(url.searchParams.get("type_ids"));
  const days = parseInt(url.searchParams.get("days") ?? "", 10) || 30;
  const { limit, offset } = parsePagination(url, env);

  const conditions: string[] = [];
  const args: (number | string)[] = [];

  if (typeIds) {
    const placeholders = typeIds.map(() => "?").join(", ");
    conditions.push(`type_id IN (${placeholders})`);
    args.push(...typeIds.map(String));
  }

  conditions.push("date >= datetime('now', ?)");
  args.push(`-${days} days`);

  const where = `WHERE ${conditions.join(" AND ")}`;

  const sql = `SELECT date, type_id, type_name, average, volume, highest, lowest, order_count
               FROM market_history ${where}
               ORDER BY type_id, date DESC
               LIMIT ? OFFSET ?`;

  const result = await client.execute({
    sql,
    args: [...args, limit, offset],
  });

  return jsonResponse({
    count: result.rows.length,
    limit,
    offset,
    days,
    data: result.rows,
  });
}

function handleIndex(): Response {
  const marketEndpoints = (alias: string) => ({
    [`GET /${alias}/stats`]: {
      description: "Market statistics (price, volume, days remaining)",
      params: {
        type_ids: "Comma-separated type IDs to filter (e.g. ?type_ids=34,35,36)",
        limit: "Page size (default 100, max 500)",
        offset: "Pagination offset",
      },
    },
    [`GET /${alias}/orders`]: {
      description: "Current market orders",
      params: {
        type_ids: "Comma-separated type IDs to filter",
        buy: "Set to 'true' for buy orders only",
        sell: "Set to 'true' for sell orders only",
        limit: "Page size (default 100, max 500)",
        offset: "Pagination offset",
      },
    },
    [`GET /${alias}/history`]: {
      description: "Historical market data",
      params: {
        type_ids: "Comma-separated type IDs to filter",
        days: "Number of days of history (default 30)",
        limit: "Page size (default 100, max 500)",
        offset: "Pagination offset",
      },
    },
  });

  return jsonResponse({
    name: "WinterCo Markets API",
    version: "0.2.0",
    auth: "Provide X-API-Key header. Key must be linked to a character whose alliance has standings >= 5.",
    markets: MARKETS,
    endpoints: {
      ...marketEndpoints("primary"),
      ...marketEndpoints("deployment"),
    },
    headers: {
      "X-Market-Alias": "Market alias for this response",
      "X-Market-Name": "Human-readable market name (e.g. 4-HWWF Keepstar)",
      "X-Market-Region-Id": "Eve region ID",
      "X-Market-System-Id": "Eve system ID",
      "X-Market-Structure-Id": "Eve structure ID",
    },
  });
}

// ---------------------------------------------------------------------------
// Scheduled handler: refresh character affiliations from ESI
// ---------------------------------------------------------------------------

async function refreshAffiliations(env: Env): Promise<void> {
  const authClient = getAuthClient(env);

  // Get all active characters
  const keysResult = await authClient.execute(
    "SELECT DISTINCT character_id FROM api_keys WHERE active = 1",
  );

  const characterIds = keysResult.rows.map((r) => r.character_id as number);
  if (characterIds.length === 0) return;

  // Call ESI affiliation endpoint (accepts up to 1000 IDs)
  const esiResponse = await fetch(
    "https://esi.evetech.net/latest/characters/affiliation/?datasource=tranquility",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(characterIds),
    },
  );

  if (!esiResponse.ok) {
    console.error(
      `ESI affiliation request failed: ${esiResponse.status} ${esiResponse.statusText}`,
    );
    return;
  }

  const affiliations = (await esiResponse.json()) as Array<{
    character_id: number;
    corporation_id: number;
    alliance_id?: number;
    faction_id?: number;
  }>;

  // Upsert each affiliation
  for (const aff of affiliations) {
    await authClient.execute({
      sql: `INSERT INTO character_affiliations (character_id, corporation_id, alliance_id, last_checked)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(character_id)
            DO UPDATE SET corporation_id = excluded.corporation_id,
                          alliance_id = excluded.alliance_id,
                          last_checked = excluded.last_checked`,
      args: [aff.character_id, aff.corporation_id, aff.alliance_id ?? null],
    });
  }

  console.log(`Refreshed affiliations for ${affiliations.length} characters.`);
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

/** Parse /<market>/<endpoint> from pathname. */
function parseRoute(
  pathname: string,
): { market: MarketAlias; endpoint: string } | null {
  const clean = pathname.replace(/\/+$/, "");
  const match = clean.match(/^\/(primary|deployment)\/(stats|orders|history)$/);
  if (!match) return null;
  return { market: match[1] as MarketAlias, endpoint: match[2] };
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(env.CORS_ORIGIN),
      });
    }

    if (request.method !== "GET") {
      return errorResponse("Method not allowed", 405);
    }

    // Rate limiting
    const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
    const maxReqs = parseInt(env.RATE_LIMIT_MAX, 10) || 60;
    if (!checkRateLimit(ip, maxReqs)) {
      return errorResponse("Rate limit exceeded. Max 60 requests per minute.", 429);
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    // Root index does not require auth
    if (path === "/") {
      return applyHeaders(handleIndex(), env.CORS_ORIGIN);
    }

    // All other routes require auth
    const authClient = getAuthClient(env);
    const threshold = parseInt(env.STANDINGS_THRESHOLD, 10) || 5;
    const authResult = await authenticate(
      request.headers.get("X-API-Key"),
      authClient,
      threshold,
    );

    if (!authResult.ok) {
      return applyHeaders(authResult.error, env.CORS_ORIGIN);
    }

    // Route: /<market>/<endpoint>
    const route = parseRoute(path);
    if (!route) {
      return applyHeaders(errorResponse("Not found", 404), env.CORS_ORIGIN);
    }

    const marketConfig = MARKETS[route.market];
    const marketClient = getMarketClient(env, route.market);
    let response: Response;

    try {
      switch (route.endpoint) {
        case "stats":
          response = await handleStats(marketClient, url, env);
          break;
        case "orders":
          response = await handleOrders(marketClient, url, env);
          break;
        case "history":
          response = await handleHistory(marketClient, url, env);
          break;
        default:
          response = errorResponse("Not found", 404);
      }
    } catch (err) {
      console.error("Handler error:", err);
      response = errorResponse("Internal server error", 500);
    }

    return applyHeaders(response, env.CORS_ORIGIN, marketConfig);
  },

  /** Cron trigger: refresh character affiliations from ESI. */
  async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
    await refreshAffiliations(env);
  },
};

/** Merge CORS + market config headers onto a response. */
function applyHeaders(
  response: Response,
  corsOrigin: string,
  marketConfig?: MarketConfig,
): Response {
  const headers = new Headers(response.headers);
  for (const [k, v] of Object.entries(corsHeaders(corsOrigin))) {
    headers.set(k, v);
  }
  if (marketConfig) {
    for (const [k, v] of Object.entries(marketHeaders(marketConfig))) {
      headers.set(k, v);
    }
  }
  return new Response(response.body, { status: response.status, headers });
}
