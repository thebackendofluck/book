// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * odds.ts -- Odds feed handlers: event listing, market details, live SSE stream.
 *
 * Routes:
 *   GET  /api/odds/events          List events by sport/competition
 *   GET  /api/odds/events/:id      Event with all markets and selections
 *   GET  /api/odds/markets/:id     Single market with current odds
 *   GET  /api/odds/stream          SSE stream of live odds updates
 *
 * Storage:
 *   D1  (SPORTSBOOK_DB) -- canonical odds truth
 *   KV  (ODDS_CACHE)    -- hot odds cache (30-60s TTL)
 *
 * Chapter 46b: Sports Betting Architecture — Cloudflare Workers sportsbook.
 * Script reference: scripts/chapter-46/sports/odds.ts
 */

export interface Env {
  SPORTSBOOK_DB: D1Database;
  ODDS_CACHE: KVNamespace;
  SESSIONS: KVNamespace;
  JWT_SECRET: string;
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export async function handleOdds(
  request: Request,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/odds/, "");

  if (path === "/stream") {
    return handleOddsStream(request, env);
  }

  const eventMatch = path.match(/^\/events\/([^/]+)$/);
  if (eventMatch) {
    return handleGetEvent(eventMatch[1], env);
  }

  const marketMatch = path.match(/^\/markets\/([^/]+)$/);
  if (marketMatch) {
    return handleGetMarket(marketMatch[1], env);
  }

  if (path === "/events" || path === "/events/") {
    return handleListEvents(url, env);
  }

  return new Response("Not found", { status: 404 });
}

// ---------------------------------------------------------------------------
// List events
// ---------------------------------------------------------------------------

async function handleListEvents(url: URL, env: Env): Promise<Response> {
  const sport = url.searchParams.get("sport");
  const competition = url.searchParams.get("competition");
  const phase = url.searchParams.get("phase") ?? "pre_match"; // pre_match | live
  const limit = Math.min(parseInt(url.searchParams.get("limit") ?? "50"), 100);

  let query =
    "SELECT e.id, e.home_name, e.away_name, e.scheduled_at, e.state, e.phase, " +
    "       e.score_home, e.score_away, e.minute, " +
    "       c.name AS competition_name, s.name AS sport_name " +
    "FROM events e " +
    "JOIN competitions c ON e.competition_id = c.id " +
    "JOIN sports s ON c.sport_id = s.id " +
    "WHERE e.state NOT IN ('settled', 'voided', 'cancelled') ";

  const params: (string | number)[] = [];

  if (sport) {
    query += "AND s.name = ? ";
    params.push(sport);
  }
  if (competition) {
    query += "AND c.id = ? ";
    params.push(competition);
  }
  if (phase) {
    query += "AND e.phase = ? ";
    params.push(phase);
  }

  query += "ORDER BY e.scheduled_at ASC LIMIT ?";
  params.push(limit);

  try {
    const result = await env.SPORTSBOOK_DB.prepare(query)
      .bind(...params)
      .all();
    return Response.json({ events: result.results ?? [] });
  } catch (err) {
    console.error("handleListEvents error:", err);
    return Response.json({ error: "database_error" }, { status: 500 });
  }
}

// ---------------------------------------------------------------------------
// Get single event with markets + selections
// ---------------------------------------------------------------------------

async function handleGetEvent(eventId: string, env: Env): Promise<Response> {
  // Try KV cache first (30-second TTL for pre-match, 5s for live)
  const cacheKey = `odds:event:${eventId}`;
  const cached = await env.ODDS_CACHE.get(cacheKey, "json");
  if (cached) {
    return Response.json(cached);
  }

  // Fetch from D1
  const eventRow = await env.SPORTSBOOK_DB.prepare(
    "SELECT e.*, c.name AS competition_name, s.name AS sport_name " +
      "FROM events e " +
      "JOIN competitions c ON e.competition_id = c.id " +
      "JOIN sports s ON c.sport_id = s.id " +
      "WHERE e.id = ?"
  )
    .bind(eventId)
    .first();

  if (!eventRow) {
    return Response.json({ error: "event_not_found" }, { status: 404 });
  }

  const marketsResult = await env.SPORTSBOOK_DB.prepare(
    "SELECT m.id, m.template, m.name, m.state, m.line, m.margin " +
      "FROM markets m WHERE m.event_id = ? AND m.state != 'settled' " +
      "ORDER BY m.template"
  )
    .bind(eventId)
    .all();

  const markets = [];
  for (const market of marketsResult.results ?? []) {
    const selectionsResult = await env.SPORTSBOOK_DB.prepare(
      "SELECT id, name, odds, odds_version, implied_prob " +
        "FROM selections WHERE market_id = ? AND active = 1 " +
        "ORDER BY implied_prob DESC"
    )
      .bind(market.id as string)
      .all();

    markets.push({
      ...market,
      selections: selectionsResult.results ?? [],
    });
  }

  const payload = { event: eventRow, markets };

  // Cache: 30s for pre-match, 5s for live
  const phase = (eventRow.phase as string) ?? "pre_match";
  const ttl = phase === "live" ? 5 : 30;
  await env.ODDS_CACHE.put(cacheKey, JSON.stringify(payload), {
    expirationTtl: ttl,
  });

  return Response.json(payload);
}

// ---------------------------------------------------------------------------
// Get single market with current odds
// ---------------------------------------------------------------------------

async function handleGetMarket(marketId: string, env: Env): Promise<Response> {
  const marketRow = await env.SPORTSBOOK_DB.prepare(
    "SELECT m.id, m.event_id, m.template, m.name, m.state, m.line, m.margin " +
      "FROM markets m WHERE m.id = ?"
  )
    .bind(marketId)
    .first();

  if (!marketRow) {
    return Response.json({ error: "market_not_found" }, { status: 404 });
  }

  const selectionsResult = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, name, odds, odds_version, implied_prob " +
      "FROM selections WHERE market_id = ? AND active = 1"
  )
    .bind(marketId)
    .all();

  return Response.json({
    market: { ...marketRow, selections: selectionsResult.results ?? [] },
  });
}

// ---------------------------------------------------------------------------
// SSE live odds stream
// ---------------------------------------------------------------------------

/**
 * Server-Sent Events stream for live odds updates.
 *
 * The client connects with ?event_id=<uuid> and receives a stream of JSON
 * objects whenever the odds for that event change in the KV cache. The
 * Worker polls KV every 2 seconds and pushes a `data:` line to the client
 * when the version number advances.
 *
 * Usage (browser):
 *   const es = new EventSource("/api/odds/stream?event_id=<uuid>");
 *   es.onmessage = (e) => { const odds = JSON.parse(e.data); ... };
 */
async function handleOddsStream(request: Request, env: Env): Promise<Response> {
  const eventId = new URL(request.url).searchParams.get("event_id");
  if (!eventId) {
    return new Response("event_id required", { status: 400 });
  }

  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      let lastVersion = 0;
      let active = true;

      // Send an initial comment to establish the connection
      controller.enqueue(encoder.encode(": connected\n\n"));

      // Poll KV for version changes every 2 seconds
      // Note: In a production deployment, a push-based mechanism (DO pub/sub
      // or Cloudflare Durable Objects) would be preferred over polling.
      const poll = async () => {
        while (active) {
          try {
            const cached = (await env.ODDS_CACHE.get(
              `odds:event:${eventId}`,
              "json"
            )) as { version: number } | null;

            if (cached && cached.version > lastVersion) {
              lastVersion = cached.version;
              const data = `data: ${JSON.stringify(cached)}\n\n`;
              controller.enqueue(encoder.encode(data));
            }

            // Keep-alive ping every 15s to prevent proxy timeouts
            if (Date.now() % 15000 < 2000) {
              controller.enqueue(encoder.encode(": ping\n\n"));
            }
          } catch {
            active = false;
            controller.close();
            return;
          }

          // Sleep 2 seconds between polls
          await new Promise<void>((resolve) => setTimeout(resolve, 2000));
        }
      };

      void poll();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
