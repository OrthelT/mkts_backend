import { createClient, type Client } from "@libsql/client/web";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Env {
  TURSO_PRIMARY_URL: string;
  TURSO_PRIMARY_TOKEN: string;
  TURSO_DEPLOYMENT_URL: string;
  TURSO_DEPLOYMENT_TOKEN: string;
  API_KEY?: string;
  CORS_ORIGIN: string;
  RATE_LIMIT_MAX: string;
  DEFAULT_PAGE_SIZE: string;
  MAX_PAGE_SIZE: string;
}

type MarketAlias = "primary" | "deployment";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Simple per-IP rate limiter using the Workers Cache API. */
const rateLimit = async (
  ip: string,
  maxPerMinute: number,
): Promise<boolean> => {
  // Use a global Map as a lightweight in-memory counter.
  // Workers isolates are short-lived so this resets naturally.
  const now = Math.floor(Date.now() / 60_000); // minute bucket
  const key = `${ip}:${now}`;
  const count = (rateCounts.get(key) ?? 0) + 1;
  rateCounts.set(key, count);

  // Prune old keys occasionally
  if (rateCounts.size > 10_000) {
    for (const k of rateCounts.keys()) {
      if (!k.endsWith(`:${now}`)) rateCounts.delete(k);
    }
  }

  return count <= maxPerMinute;
};

const rateCounts = new Map<string, number>();

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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

function getClient(env: Env, market: MarketAlias): Client {
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

/** Parse comma-separated type_ids from query string into safe integers. */
function parseTypeIds(param: string | null): number[] | null {
  if (!param) return null;
  const ids = param
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => !isNaN(n) && n > 0);
  return ids.length > 0 ? ids : null;
}

/** Build a WHERE clause for type_ids filtering. Uses parameterized queries. */
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
    // market_history.type_id is stored as string
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

async function handleIndex(): Promise<Response> {
  return jsonResponse({
    name: "WinterCo Markets API",
    version: "0.1.0",
    endpoints: {
      "GET /stats": {
        description: "Market statistics (price, volume, days remaining)",
        params: {
          type_ids: "Comma-separated type IDs to filter (e.g. ?type_ids=34,35,36)",
          market: "Market alias: primary (default) or deployment",
          limit: "Page size (default 100, max 500)",
          offset: "Pagination offset",
        },
      },
      "GET /orders": {
        description: "Current market orders",
        params: {
          type_ids: "Comma-separated type IDs to filter",
          buy: "Set to 'true' for buy orders only",
          sell: "Set to 'true' for sell orders only",
          market: "Market alias: primary (default) or deployment",
          limit: "Page size (default 100, max 500)",
          offset: "Pagination offset",
        },
      },
      "GET /history": {
        description: "Historical market data",
        params: {
          type_ids: "Comma-separated type IDs to filter",
          days: "Number of days of history (default 30)",
          market: "Market alias: primary (default) or deployment",
          limit: "Page size (default 100, max 500)",
          offset: "Pagination offset",
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Main fetch handler
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

    // Only allow GET
    if (request.method !== "GET") {
      return errorResponse("Method not allowed", 405);
    }

    // Rate limiting
    const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
    const maxReqs = parseInt(env.RATE_LIMIT_MAX, 10) || 60;
    if (!(await rateLimit(ip, maxReqs))) {
      return errorResponse("Rate limit exceeded. Max 60 requests per minute.", 429);
    }

    // Optional API key auth (if API_KEY secret is set, require it)
    if (env.API_KEY) {
      const provided = request.headers.get("X-API-Key");
      if (provided !== env.API_KEY) {
        return errorResponse("Unauthorized. Provide a valid X-API-Key header.", 401);
      }
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    // Market selection: ?market=primary|deployment
    const marketParam = url.searchParams.get("market") ?? "primary";
    if (marketParam !== "primary" && marketParam !== "deployment") {
      return errorResponse(
        "Invalid market. Use 'primary' or 'deployment'.",
        400,
      );
    }
    const client = getClient(env, marketParam as MarketAlias);

    let response: Response;

    try {
      switch (path) {
        case "/":
          response = await handleIndex();
          break;
        case "/stats":
          response = await handleStats(client, url, env);
          break;
        case "/orders":
          response = await handleOrders(client, url, env);
          break;
        case "/history":
          response = await handleHistory(client, url, env);
          break;
        default:
          response = errorResponse("Not found", 404);
      }
    } catch (err) {
      console.error("Handler error:", err);
      response = errorResponse("Internal server error", 500);
    }

    // Apply CORS headers to all responses
    const headers = new Headers(response.headers);
    for (const [k, v] of Object.entries(corsHeaders(env.CORS_ORIGIN))) {
      headers.set(k, v);
    }

    return new Response(response.body, {
      status: response.status,
      headers,
    });
  },
};
