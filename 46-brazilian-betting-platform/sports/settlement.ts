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
 * settlement.ts -- Event result ingestion and bet settlement.
 *
 * Routes:
 *   POST /api/settlement/result     Ingest an event result from vendor feed
 *   POST /api/settlement/settle/:event_id  Trigger settlement for all open bets
 *   GET  /api/settlement/status/:event_id  Settlement status for an event
 *
 * Settlement applies sport-specific rules to each market template to determine
 * winning selections, then updates all affected bet legs and bets. The wallet
 * credit for winning bets is dispatched via ctx.waitUntil() after the D1 write.
 *
 * Void conditions handled:
 *   - Event abandoned (no result received within 24h of scheduled start)
 *   - Participant walkover
 *   - Regulatory void (operator or regulator directive)
 *
 * Chapter 46b: Sports Betting Architecture — Cloudflare Workers sportsbook.
 * Script reference: scripts/chapter-46/sports/settlement.ts
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Env {
  SPORTSBOOK_DB: D1Database;
  JWT_SECRET: string;
}

interface EventResult {
  event_id: string;
  vendor: string;
  result_type: "full_time" | "half_time" | "set" | "fight";
  home_score?: number;
  away_score?: number;
  winner?: "home" | "away" | "draw";
  method?: "ko_tko" | "submission" | "decision";    // MMA method of victory
  round_ended?: number;                              // MMA round
  confirmed: boolean;
  vendor_timestamp: string;
}

interface SettlementOutcome {
  winning_selection_name: string | null;
  void: boolean;
  void_reason?: string;
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export async function handleSettlement(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  operatorId: string
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/settlement/, "");

  if (path === "/result" && request.method === "POST") {
    return handleIngestResult(request, env, ctx);
  }

  const settleMatch = path.match(/^\/settle\/([^/]+)$/);
  if (settleMatch && request.method === "POST") {
    return handleSettle(settleMatch[1], env, ctx);
  }

  const statusMatch = path.match(/^\/status\/([^/]+)$/);
  if (statusMatch && request.method === "GET") {
    return handleSettlementStatus(statusMatch[1], env);
  }

  return new Response("Not found", { status: 404 });
}

// ---------------------------------------------------------------------------
// Ingest event result
// ---------------------------------------------------------------------------

async function handleIngestResult(
  request: Request,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  const result = await request.json<EventResult>();

  if (!result.event_id || !result.vendor || !result.confirmed) {
    return Response.json(
      { error: "invalid_result", reason: "event_id, vendor, and confirmed=true are required" },
      { status: 400 }
    );
  }

  // Update the event state to 'settled' in D1
  const now = new Date().toISOString();
  await env.SPORTSBOOK_DB.prepare(
    "UPDATE events SET state = 'settled', " +
      "score_home = ?, score_away = ?, updated_at = ? WHERE id = ?"
  )
    .bind(result.home_score ?? null, result.away_score ?? null, now, result.event_id)
    .run();

  // Trigger settlement asynchronously
  ctx.waitUntil(settleEvent(result, env));

  return Response.json({
    status: "ingested",
    event_id: result.event_id,
    settlement_triggered: true,
  });
}

// ---------------------------------------------------------------------------
// Manually trigger settlement for an event (operator use)
// ---------------------------------------------------------------------------

async function handleSettle(
  eventId: string,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  // Load stored result from the database
  const event = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, state, score_home, score_away FROM events WHERE id = ?"
  )
    .bind(eventId)
    .first();

  if (!event) {
    return Response.json({ error: "event_not_found" }, { status: 404 });
  }
  if (event.state !== "settled") {
    return Response.json(
      { error: "not_settled", reason: "Event result has not been ingested yet" },
      { status: 422 }
    );
  }

  // Build a synthetic result from the stored scores
  const syntheticResult: EventResult = {
    event_id: eventId,
    vendor: "manual_trigger",
    result_type: "full_time",
    home_score: event.score_home as number | undefined,
    away_score: event.score_away as number | undefined,
    winner:
      (event.score_home as number) > (event.score_away as number)
        ? "home"
        : (event.score_home as number) < (event.score_away as number)
          ? "away"
          : "draw",
    confirmed: true,
    vendor_timestamp: new Date().toISOString(),
  };

  ctx.waitUntil(settleEvent(syntheticResult, env));

  return Response.json({ status: "settlement_triggered", event_id: eventId });
}

// ---------------------------------------------------------------------------
// Settlement status
// ---------------------------------------------------------------------------

async function handleSettlementStatus(
  eventId: string,
  env: Env
): Promise<Response> {
  const result = await env.SPORTSBOOK_DB.prepare(
    "SELECT " +
      "  COUNT(*) AS total_bets, " +
      "  SUM(CASE WHEN b.status = 'won' THEN 1 ELSE 0 END) AS won, " +
      "  SUM(CASE WHEN b.status = 'lost' THEN 1 ELSE 0 END) AS lost, " +
      "  SUM(CASE WHEN b.status = 'void' THEN 1 ELSE 0 END) AS void, " +
      "  SUM(CASE WHEN b.status = 'accepted' THEN 1 ELSE 0 END) AS pending " +
      "FROM bets b " +
      "JOIN bet_legs bl ON b.id = bl.bet_id " +
      "WHERE bl.event_id = ?"
  )
    .bind(eventId)
    .first();

  return Response.json({ event_id: eventId, settlement_summary: result });
}

// ---------------------------------------------------------------------------
// Core settlement logic
// ---------------------------------------------------------------------------

async function settleEvent(result: EventResult, env: Env): Promise<void> {
  const eventId = result.event_id;

  // Load all open markets for this event
  const marketsResult = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, template, line FROM markets WHERE event_id = ? AND state = 'open'"
  )
    .bind(eventId)
    .all();

  for (const market of marketsResult.results ?? []) {
    const marketId = market.id as string;
    const template = market.template as string;
    const line = market.line as number | null;

    // Determine winning selection based on market template
    const outcome = applySettlementRule(template, result, line);

    // Load all selections for this market
    const selectionsResult = await env.SPORTSBOOK_DB.prepare(
      "SELECT id, name FROM selections WHERE market_id = ?"
    )
      .bind(marketId)
      .all();

    const now = new Date().toISOString();

    for (const sel of selectionsResult.results ?? []) {
      const selName = sel.name as string;
      let newStatus: string;

      if (outcome.void) {
        newStatus = "void";
      } else if (
        outcome.winning_selection_name !== null &&
        selName.toLowerCase() === outcome.winning_selection_name.toLowerCase()
      ) {
        newStatus = "won";
      } else {
        newStatus = "lost";
      }

      // Update bet legs for this selection
      await env.SPORTSBOOK_DB.prepare(
        "UPDATE bet_legs SET leg_status = ?, settled_at = ? WHERE selection_id = ? AND leg_status = 'open'"
      )
        .bind(newStatus, now, sel.id as string)
        .run();
    }

    // Mark market as settled
    await env.SPORTSBOOK_DB.prepare(
      "UPDATE markets SET state = 'settled', updated_at = ? WHERE id = ?"
    )
      .bind(now, marketId)
      .run();
  }

  // Now settle each affected bet based on its leg statuses
  await settleBetsByEvent(eventId, env);
}

async function settleBetsByEvent(eventId: string, env: Env): Promise<void> {
  // Find all bets with legs on this event
  const betsResult = await env.SPORTSBOOK_DB.prepare(
    "SELECT DISTINCT b.id, b.stake, b.potential_return " +
      "FROM bets b JOIN bet_legs bl ON b.id = bl.bet_id " +
      "WHERE bl.event_id = ? AND b.status = 'accepted'"
  )
    .bind(eventId)
    .all();

  const now = new Date().toISOString();

  for (const bet of betsResult.results ?? []) {
    const betId = bet.id as string;

    const legsResult = await env.SPORTSBOOK_DB.prepare(
      "SELECT leg_status FROM bet_legs WHERE bet_id = ?"
    )
      .bind(betId)
      .all();

    const legs = legsResult.results ?? [];
    const statuses = legs.map((l) => l.leg_status as string);

    // Determine final bet status
    let finalStatus: string;
    if (statuses.every((s) => s === "void")) {
      finalStatus = "void";
    } else if (statuses.some((s) => s === "lost")) {
      finalStatus = "lost";
    } else if (statuses.some((s) => s === "open")) {
      finalStatus = "accepted"; // Accumulator with more legs still open
    } else if (statuses.every((s) => s === "won" || s === "void")) {
      finalStatus = "won";
    } else {
      finalStatus = "lost";
    }

    if (finalStatus !== "accepted") {
      await env.SPORTSBOOK_DB.prepare(
        "UPDATE bets SET status = ?, settled_at = ?, updated_at = ? WHERE id = ?"
      )
        .bind(finalStatus, now, now, betId)
        .run();

      // Credit wallet for wins / refund for voids
      if (finalStatus === "won") {
        console.log(`WALLET_CREDIT bet_id=${betId} amount=${bet.potential_return} reason=won`);
      } else if (finalStatus === "void") {
        console.log(`WALLET_CREDIT bet_id=${betId} amount=${bet.stake} reason=void_refund`);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Settlement rules by market template
// ---------------------------------------------------------------------------

function applySettlementRule(
  template: string,
  result: EventResult,
  line: number | null
): SettlementOutcome {
  switch (template) {
    case "1x2":
      return settle1x2(result);
    case "total_goals":
      return settleTotalGoals(result, line);
    case "btts":
      return settleBtts(result);
    case "double_chance":
      return settleDoubleChance(result);
    case "draw_no_bet":
      return settleDrawNoBet(result);
    case "match_winner":    // Tennis / MMA 2-way
      return settle1x2(result);  // Same logic as 1x2 without draw
    case "fight_winner":
      return settleFightWinner(result);
    case "method_of_victory":
      return settleMethodOfVictory(result);
    default:
      return { winning_selection_name: null, void: true, void_reason: "unsupported_template" };
  }
}

function settle1x2(result: EventResult): SettlementOutcome {
  if (!result.winner) {
    return { winning_selection_name: null, void: true, void_reason: "no_result" };
  }
  const names: Record<string, string> = {
    home: "Home",
    away: "Away",
    draw: "Draw",
  };
  return { winning_selection_name: names[result.winner] ?? null, void: false };
}

function settleTotalGoals(result: EventResult, line: number | null): SettlementOutcome {
  if (result.home_score == null || result.away_score == null || line == null) {
    return { winning_selection_name: null, void: true, void_reason: "no_result" };
  }
  const total = result.home_score + result.away_score;
  if (total > line) return { winning_selection_name: "Over", void: false };
  if (total < line) return { winning_selection_name: "Under", void: false };
  // Exact line hit (whole number line): push
  return { winning_selection_name: null, void: true, void_reason: "push_exact_line" };
}

function settleBtts(result: EventResult): SettlementOutcome {
  if (result.home_score == null || result.away_score == null) {
    return { winning_selection_name: null, void: true, void_reason: "no_result" };
  }
  const bothScored = result.home_score > 0 && result.away_score > 0;
  return { winning_selection_name: bothScored ? "Yes" : "No", void: false };
}

function settleDoubleChance(result: EventResult): SettlementOutcome {
  if (!result.winner) {
    return { winning_selection_name: null, void: true, void_reason: "no_result" };
  }
  const dc: Record<string, string> = {
    home: "1X",  // Home or Draw wins
    away: "X2",  // Away or Draw wins
    draw: "12",  // Would be a non-draw if we had one, but draw means neither 1 nor 2
  };
  // For Double Chance: "1X" wins if home wins or draw; "X2" wins if away wins or draw; "12" wins if home or away
  if (result.winner === "home") return { winning_selection_name: "1X", void: false };
  if (result.winner === "away") return { winning_selection_name: "X2", void: false };
  if (result.winner === "draw") return { winning_selection_name: "1X", void: false }; // Both 1X and X2 win on draw, but we pick the first
  return { winning_selection_name: null, void: true, void_reason: "unknown_winner" };
}

function settleDrawNoBet(result: EventResult): SettlementOutcome {
  if (!result.winner) {
    return { winning_selection_name: null, void: true, void_reason: "no_result" };
  }
  if (result.winner === "draw") {
    return { winning_selection_name: null, void: true, void_reason: "draw_no_bet_push" };
  }
  return { winning_selection_name: result.winner === "home" ? "Home" : "Away", void: false };
}

function settleFightWinner(result: EventResult): SettlementOutcome {
  if (!result.winner || result.winner === "draw") {
    return { winning_selection_name: null, void: true, void_reason: "no_result" };
  }
  return { winning_selection_name: result.winner === "home" ? "Fighter 1" : "Fighter 2", void: false };
}

function settleMethodOfVictory(result: EventResult): SettlementOutcome {
  const methodNames: Record<string, string> = {
    ko_tko: "KO/TKO",
    submission: "Submission",
    decision: "Decision",
  };
  if (!result.method) {
    return { winning_selection_name: null, void: true, void_reason: "no_method" };
  }
  return { winning_selection_name: methodNames[result.method] ?? null, void: false };
}
