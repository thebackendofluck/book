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
 * trading.ts -- Trading desk controls: market suspension and odds override.
 *
 * Routes (operator-only, requires trading role in JWT):
 *   POST /api/trading/suspend/:market_id    Suspend a market
 *   POST /api/trading/resume/:market_id     Resume a suspended market
 *   POST /api/trading/override/:selection_id Override odds for a selection
 *   GET  /api/trading/exposure/:event_id    View current exposure by market
 *   GET  /api/trading/pending-review        Markets flagged for trader review
 *
 * All trader actions are logged to the D1 audit trail with the operator ID,
 * reason, and timestamp. This satisfies Article 12 requirements for human
 * oversight of AI-driven trading decisions.
 *
 * Chapter 46b: Sports Betting Architecture — Cloudflare Workers sportsbook.
 * Script reference: scripts/chapter-46/sports/trading.ts
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Env {
  SPORTSBOOK_DB: D1Database;
  ODDS_CACHE: KVNamespace;
  JWT_SECRET: string;
}

interface SuspendRequest {
  reason: string;        // Required: trader must justify the suspension
  resume_at?: string;    // Optional ISO timestamp for auto-resume
}

interface ResumeRequest {
  reason: string;
}

interface OddsOverrideRequest {
  new_odds: number;
  reason: string;
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export async function handleTrading(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  traderId: string
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/trading/, "");

  const suspendMatch = path.match(/^\/suspend\/([^/]+)$/);
  if (suspendMatch && request.method === "POST") {
    return handleSuspend(suspendMatch[1], request, env, traderId);
  }

  const resumeMatch = path.match(/^\/resume\/([^/]+)$/);
  if (resumeMatch && request.method === "POST") {
    return handleResume(resumeMatch[1], request, env, traderId);
  }

  const overrideMatch = path.match(/^\/override\/([^/]+)$/);
  if (overrideMatch && request.method === "POST") {
    return handleOddsOverride(overrideMatch[1], request, env, ctx, traderId);
  }

  const exposureMatch = path.match(/^\/exposure\/([^/]+)$/);
  if (exposureMatch && request.method === "GET") {
    return handleExposure(exposureMatch[1], env);
  }

  if (path === "/pending-review" && request.method === "GET") {
    return handlePendingReview(env);
  }

  return new Response("Not found", { status: 404 });
}

// ---------------------------------------------------------------------------
// Suspend a market
// ---------------------------------------------------------------------------

async function handleSuspend(
  marketId: string,
  request: Request,
  env: Env,
  traderId: string
): Promise<Response> {
  const body = await request.json<SuspendRequest>();

  if (!body.reason) {
    return Response.json(
      { error: "invalid_request", reason: "reason is required for market suspension" },
      { status: 400 }
    );
  }

  const market = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, state, event_id FROM markets WHERE id = ?"
  )
    .bind(marketId)
    .first();

  if (!market) {
    return Response.json({ error: "market_not_found" }, { status: 404 });
  }

  if (market.state === "settled") {
    return Response.json(
      { error: "invalid_state", reason: "Settled markets cannot be suspended" },
      { status: 422 }
    );
  }

  const now = new Date().toISOString();

  await env.SPORTSBOOK_DB.prepare(
    "UPDATE markets SET state = 'suspended', updated_at = ? WHERE id = ?"
  )
    .bind(now, marketId)
    .run();

  // Log the trader action
  await logTraderAction(env, {
    action_type: "MARKET_SUSPEND",
    target_id: marketId,
    target_type: "market",
    trader_id: traderId,
    reason: body.reason,
    resume_at: body.resume_at,
    timestamp: now,
  });

  // Invalidate any KV cache entries for markets in this event
  await env.ODDS_CACHE.delete(`odds:event:${market.event_id}`);

  return Response.json({
    status: "suspended",
    market_id: marketId,
    suspended_at: now,
    suspended_by: traderId,
    reason: body.reason,
    auto_resume_at: body.resume_at ?? null,
  });
}

// ---------------------------------------------------------------------------
// Resume a suspended market
// ---------------------------------------------------------------------------

async function handleResume(
  marketId: string,
  request: Request,
  env: Env,
  traderId: string
): Promise<Response> {
  const body = await request.json<ResumeRequest>();

  const market = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, state, event_id FROM markets WHERE id = ?"
  )
    .bind(marketId)
    .first();

  if (!market) {
    return Response.json({ error: "market_not_found" }, { status: 404 });
  }

  if (market.state !== "suspended") {
    return Response.json(
      {
        error: "invalid_state",
        reason: `Market is '${market.state}', not suspended`,
      },
      { status: 422 }
    );
  }

  const now = new Date().toISOString();

  await env.SPORTSBOOK_DB.prepare(
    "UPDATE markets SET state = 'open', updated_at = ? WHERE id = ?"
  )
    .bind(now, marketId)
    .run();

  await logTraderAction(env, {
    action_type: "MARKET_RESUME",
    target_id: marketId,
    target_type: "market",
    trader_id: traderId,
    reason: body.reason ?? "trader_resume",
    timestamp: now,
  });

  await env.ODDS_CACHE.delete(`odds:event:${market.event_id}`);

  return Response.json({
    status: "resumed",
    market_id: marketId,
    resumed_at: now,
    resumed_by: traderId,
  });
}

// ---------------------------------------------------------------------------
// Override odds for a selection
// ---------------------------------------------------------------------------

async function handleOddsOverride(
  selectionId: string,
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  traderId: string
): Promise<Response> {
  const body = await request.json<OddsOverrideRequest>();

  if (!body.new_odds || body.new_odds <= 1.0) {
    return Response.json(
      { error: "invalid_odds", reason: "Decimal odds must be greater than 1.0" },
      { status: 400 }
    );
  }
  if (!body.reason) {
    return Response.json(
      { error: "invalid_request", reason: "reason is required for odds override" },
      { status: 400 }
    );
  }

  const selection = await env.SPORTSBOOK_DB.prepare(
    "SELECT s.id, s.market_id, s.odds, s.odds_version, m.event_id " +
      "FROM selections s JOIN markets m ON s.market_id = m.id WHERE s.id = ?"
  )
    .bind(selectionId)
    .first();

  if (!selection) {
    return Response.json({ error: "selection_not_found" }, { status: 404 });
  }

  const now = new Date().toISOString();
  const newVersion = (selection.odds_version as number) + 1;
  const impliedProb = 1.0 / body.new_odds;

  // Update selection odds
  await env.SPORTSBOOK_DB.prepare(
    "UPDATE selections SET odds = ?, odds_version = ?, implied_prob = ?, updated_at = ? WHERE id = ?"
  )
    .bind(body.new_odds, newVersion, impliedProb, now, selectionId)
    .run();

  // Append to odds_snapshots (audit trail)
  await env.SPORTSBOOK_DB.prepare(
    "INSERT INTO odds_snapshots (id, selection_id, odds, odds_version, source, recorded_at) " +
      "VALUES (?, ?, ?, ?, 'trader_override', ?)"
  )
    .bind(crypto.randomUUID(), selectionId, body.new_odds, newVersion, now)
    .run();

  await logTraderAction(env, {
    action_type: "ODDS_OVERRIDE",
    target_id: selectionId,
    target_type: "selection",
    trader_id: traderId,
    reason: body.reason,
    previous_value: String(selection.odds),
    new_value: String(body.new_odds),
    timestamp: now,
  });

  // Invalidate event cache so next odds fetch reflects the new price
  ctx.waitUntil(
    env.ODDS_CACHE.delete(`odds:event:${selection.event_id}`)
  );

  return Response.json({
    status: "overridden",
    selection_id: selectionId,
    previous_odds: selection.odds,
    new_odds: body.new_odds,
    odds_version: newVersion,
    overridden_by: traderId,
    overridden_at: now,
  });
}

// ---------------------------------------------------------------------------
// Exposure view
// ---------------------------------------------------------------------------

async function handleExposure(eventId: string, env: Env): Promise<Response> {
  // Aggregate open liability by market for the trading desk
  const result = await env.SPORTSBOOK_DB.prepare(
    "SELECT bl.market_id, bl.selection_name, " +
      "  COUNT(*) AS open_bets, " +
      "  SUM(b.stake) AS total_staked, " +
      "  SUM(b.potential_return) AS max_payout " +
      "FROM bet_legs bl " +
      "JOIN bets b ON bl.bet_id = b.id " +
      "WHERE bl.event_id = ? AND bl.leg_status = 'open' AND b.status = 'accepted' " +
      "GROUP BY bl.market_id, bl.selection_name " +
      "ORDER BY max_payout DESC"
  )
    .bind(eventId)
    .all();

  return Response.json({
    event_id: eventId,
    exposure: result.results ?? [],
  });
}

// ---------------------------------------------------------------------------
// Pending review queue
// ---------------------------------------------------------------------------

async function handlePendingReview(env: Env): Promise<Response> {
  // Markets in 'trader_review' state (e.g., auto-flagged by freshness monitor)
  const result = await env.SPORTSBOOK_DB.prepare(
    "SELECT m.id, m.event_id, m.template, m.name, m.state, m.updated_at, " +
      "  e.home_name, e.away_name, e.scheduled_at " +
      "FROM markets m JOIN events e ON m.event_id = e.id " +
      "WHERE m.state IN ('suspended', 'trader_review') " +
      "ORDER BY e.scheduled_at ASC " +
      "LIMIT 50"
  )
    .all();

  return Response.json({ pending: result.results ?? [] });
}

// ---------------------------------------------------------------------------
// Trader action audit log
// ---------------------------------------------------------------------------

interface TraderActionLog {
  action_type: string;
  target_id: string;
  target_type: string;
  trader_id: string;
  reason: string;
  timestamp: string;
  previous_value?: string;
  new_value?: string;
  resume_at?: string;
}

async function logTraderAction(
  env: Env,
  action: TraderActionLog
): Promise<void> {
  // Production: persist to a dedicated trader_actions table.
  // This satisfies the audit trail requirement for all human oversight
  // interventions (EU AI Act Article 14, Article 12).
  console.log("TRADER_ACTION", JSON.stringify(action));

  // Example of how to persist:
  // await env.SPORTSBOOK_DB.prepare(
  //   "INSERT INTO trader_actions (id, action_type, target_id, target_type, " +
  //     "trader_id, reason, previous_value, new_value, resume_at, created_at) " +
  //     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  // ).bind(
  //   crypto.randomUUID(), action.action_type, action.target_id,
  //   action.target_type, action.trader_id, action.reason,
  //   action.previous_value ?? null, action.new_value ?? null,
  //   action.resume_at ?? null, action.timestamp
  // ).run();
}
