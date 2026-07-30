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
 * cashout.ts -- Cash-out quote generation and acceptance.
 *
 * Routes:
 *   GET  /api/cashout/quote/:bet_id   Generate a cash-out quote (10s validity)
 *   POST /api/cashout/accept/:bet_id  Accept a quote and execute the cash-out
 *
 * The cash-out lifecycle:
 *   QUOTE_GENERATED -> VALIDATING (player accepts) -> EXECUTED (odds unchanged)
 *                                                  -> REGENERATED (odds drifted)
 *   QUOTE_GENERATED -> EXPIRED (10s elapsed)
 *   QUOTE_GENERATED -> INVALIDATED (market suspended)
 *
 * Quotes are cached in KV (ODDS_CACHE) with a 15-second TTL. When the player
 * accepts, the engine re-reads current odds from D1 and validates against the
 * cached snapshot to detect drift before executing.
 *
 * Chapter 46b: Sports Betting Architecture — Cloudflare Workers sportsbook.
 * Script reference: scripts/chapter-46/sports/cashout.ts
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Env {
  SPORTSBOOK_DB: D1Database;
  ODDS_CACHE: KVNamespace;
  JWT_SECRET: string;
}

interface CashoutQuote {
  bet_id: string;
  quote_value: number;          // Amount in BRL offered to the player
  fair_value: number;           // Pre-margin value for transparency
  margin_applied: number;       // Operator margin (e.g., 0.05 = 5%)
  expires_at: string;           // ISO timestamp — quote valid for 10 seconds
  odds_snapshot: Record<string, number>;  // selection_id -> current odds
  partial?: {
    percentage: number;
    quote_value: number;
  };
}

const QUOTE_TTL_SECONDS = 10;
const OPERATOR_MARGIN = 0.05;   // 5% cash-out margin

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export async function handleCashout(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  playerId: string
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/cashout/, "");

  const quoteMatch = path.match(/^\/quote\/([^/]+)$/);
  if (quoteMatch && request.method === "GET") {
    return handleGetQuote(quoteMatch[1], url, env, playerId);
  }

  const acceptMatch = path.match(/^\/accept\/([^/]+)$/);
  if (acceptMatch && request.method === "POST") {
    return handleAcceptQuote(acceptMatch[1], request, env, ctx, playerId);
  }

  return new Response("Not found", { status: 404 });
}

// ---------------------------------------------------------------------------
// Generate a cash-out quote
// ---------------------------------------------------------------------------

async function handleGetQuote(
  betId: string,
  url: URL,
  env: Env,
  playerId: string
): Promise<Response> {
  // Verify bet belongs to player and is open
  const bet = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, stake, combined_odds, potential_return, status, bet_type " +
      "FROM bets WHERE id = ? AND player_id = ?"
  )
    .bind(betId, playerId)
    .first();

  if (!bet) {
    return Response.json({ error: "bet_not_found" }, { status: 404 });
  }
  if (bet.status !== "accepted") {
    return Response.json(
      { error: "not_eligible", reason: `Bet status is '${bet.status}' — cash-out requires 'accepted'` },
      { status: 422 }
    );
  }

  // Load open legs
  const legsResult = await env.SPORTSBOOK_DB.prepare(
    "SELECT selection_id, market_id, odds_at_placement, leg_status " +
      "FROM bet_legs WHERE bet_id = ? ORDER BY created_at"
  )
    .bind(betId)
    .all();

  const legs = legsResult.results ?? [];

  // Check if any leg already lost
  for (const leg of legs) {
    if (leg.leg_status === "lost") {
      return Response.json(
        { error: "not_eligible", reason: "One or more legs have already lost" },
        { status: 422 }
      );
    }
  }

  // Fetch current odds for all open legs
  const oddsSnapshot: Record<string, number> = {};
  let settledProduct = 1.0;
  let openLegsImpliedProbProduct = 1.0;

  for (const leg of legs) {
    if (leg.leg_status === "won") {
      settledProduct *= leg.odds_at_placement as number;
      continue;
    }

    // Open leg — get current odds
    const currentSel = await env.SPORTSBOOK_DB.prepare(
      "SELECT odds FROM selections WHERE id = ? AND active = 1"
    )
      .bind(leg.selection_id as string)
      .first();

    if (!currentSel) {
      return Response.json(
        {
          error: "not_eligible",
          reason: "One or more selections are suspended or unavailable",
        },
        { status: 422 }
      );
    }

    oddsSnapshot[leg.selection_id as string] = currentSel.odds as number;
    openLegsImpliedProbProduct *= 1.0 / (currentSel.odds as number);
  }

  // Calculate fair value:
  //   fair_value = stake * settled_product * (implied_prob of remaining legs winning)
  //             = stake * settled_product * 1/combined_open_odds
  const openOddsProduct = Object.values(oddsSnapshot).reduce(
    (acc, o) => acc * o,
    1.0
  );
  const fairValue = (bet.stake as number) * settledProduct * (1.0 / openOddsProduct);
  const offeredValue = Math.max(
    0,
    Math.min(fairValue * (1.0 - OPERATOR_MARGIN), bet.potential_return as number)
  );

  const expiresAt = new Date(Date.now() + QUOTE_TTL_SECONDS * 1000).toISOString();

  const quote: CashoutQuote = {
    bet_id: betId,
    quote_value: Math.round(offeredValue * 100) / 100,
    fair_value: Math.round(fairValue * 100) / 100,
    margin_applied: OPERATOR_MARGIN,
    expires_at: expiresAt,
    odds_snapshot: oddsSnapshot,
  };

  // Check if partial cash-out requested
  const partialPct = parseFloat(url.searchParams.get("partial") ?? "0");
  if (partialPct > 0 && partialPct < 100) {
    quote.partial = {
      percentage: partialPct,
      quote_value: Math.round(offeredValue * (partialPct / 100) * 100) / 100,
    };
  }

  // Cache quote in KV (15s TTL — slightly longer than validity window)
  await env.ODDS_CACHE.put(
    `cashout:quote:${betId}`,
    JSON.stringify(quote),
    { expirationTtl: QUOTE_TTL_SECONDS + 5 }
  );

  return Response.json({ quote });
}

// ---------------------------------------------------------------------------
// Accept a cash-out quote
// ---------------------------------------------------------------------------

async function handleAcceptQuote(
  betId: string,
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  playerId: string
): Promise<Response> {
  const body = await request.json<{ accepted_value?: number; partial_percentage?: number }>();

  // Load cached quote
  const cachedQuote = await env.ODDS_CACHE.get<CashoutQuote>(
    `cashout:quote:${betId}`,
    "json"
  );

  if (!cachedQuote) {
    return Response.json(
      { error: "quote_expired", message: "Cash-out quote has expired. Request a new quote." },
      { status: 410 }
    );
  }

  // Check quote not expired (belt-and-suspenders — KV TTL should cover this)
  if (new Date(cachedQuote.expires_at) < new Date()) {
    return Response.json(
      { error: "quote_expired", message: "Cash-out quote has expired. Request a new quote." },
      { status: 410 }
    );
  }

  // Revalidate current odds against snapshot
  const driftedSelections: string[] = [];
  for (const [selId, quotedOdds] of Object.entries(cachedQuote.odds_snapshot)) {
    const currentSel = await env.SPORTSBOOK_DB.prepare(
      "SELECT odds FROM selections WHERE id = ? AND active = 1"
    )
      .bind(selId)
      .first();

    if (!currentSel || currentSel.odds !== quotedOdds) {
      driftedSelections.push(selId);
    }
  }

  if (driftedSelections.length > 0) {
    // Invalidate the old quote — client must request a new one
    await env.ODDS_CACHE.delete(`cashout:quote:${betId}`);
    return Response.json(
      {
        error: "odds_changed",
        changed_selections: driftedSelections,
        message: "Odds have changed since quote was generated. Please request a new cash-out quote.",
      },
      { status: 409 }
    );
  }

  // Execute cash-out: update bet status + log cashout amount
  const now = new Date().toISOString();
  const cashoutAmount = cachedQuote.quote_value;

  await env.SPORTSBOOK_DB.prepare(
    "UPDATE bets SET status = 'cashed_out', cashout_amount = ?, settled_at = ?, " +
      "updated_at = ? WHERE id = ? AND player_id = ? AND status = 'accepted'"
  )
    .bind(cashoutAmount, now, now, betId, playerId)
    .run();

  await env.SPORTSBOOK_DB.prepare(
    "UPDATE bet_legs SET leg_status = 'cashed_out', settled_at = ? WHERE bet_id = ?"
  )
    .bind(now, betId)
    .run();

  // Invalidate quote from cache
  await env.ODDS_CACHE.delete(`cashout:quote:${betId}`);

  // Post-execution: credit wallet + SIGAP report (non-blocking)
  ctx.waitUntil(
    (async () => {
      // Production: call wallet service to credit cashout_amount
      console.log(`CASHOUT_EXECUTED bet_id=${betId} amount=${cashoutAmount}`);
      // Production: report cash-out to SIGAP
      console.log(`SIGAP_CASHOUT bet_id=${betId} amount=${cashoutAmount}`);
    })()
  );

  return Response.json({
    status: "cashed_out",
    bet_id: betId,
    cashout_amount: cashoutAmount,
    executed_at: now,
  });
}
