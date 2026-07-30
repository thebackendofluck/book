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
 * bets.ts -- Bet placement, history, and detail handlers.
 *
 * Routes:
 *   POST /api/bets/place        Place a bet (10-step pipeline)
 *   GET  /api/bets/history      Player bet history (paginated)
 *   GET  /api/bets/:id          Bet detail with legs
 *
 * The bet placement pipeline executes 10 sequential validation steps before
 * writing to D1. Steps 9-10 (event emission + SIGAP reporting) run via
 * ctx.waitUntil() so they do not block the 200 OK response to the player.
 *
 * Chapter 46b: Sports Betting Architecture — Cloudflare Workers sportsbook.
 * Script reference: scripts/chapter-46/sports/bets.ts
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Env {
  SPORTSBOOK_DB: D1Database;
  ODDS_CACHE: KVNamespace;
  SESSIONS: KVNamespace;
  JWT_SECRET: string;
  SIGAP_CERT?: string;
  SIGAP_KEY?: string;
}

interface PlaceBetRequest {
  request_id: string;           // Client-generated idempotency key (UUID)
  bet_type: "single" | "accumulator";
  stake: number;                // In BRL
  currency: string;             // Must be 'BRL' for Brazil
  selections: PlaceBetSelection[];
  player_metadata: {
    cpf: string;
    geolocation_token: string;
  };
}

interface PlaceBetSelection {
  selection_id: string;
  market_id: string;
  event_id: string;
  selection_name: string;
  market_name: string;
  event_name: string;
  odds: number;
  odds_version: number;
}

const REJECTION_REASON = {
  ODDS_CHANGED: "odds_changed",
  MARKET_SUSPENDED: "market_suspended",
  EVENT_CANCELLED: "event_cancelled",
  LIMIT_EXCEEDED: "limit_exceeded",
  INSUFFICIENT_BALANCE: "insufficient_balance",
  RG_BLOCK: "rg_block",
  GEOLOCATION_FAILED: "geolocation_failed",
  CPF_MISMATCH: "cpf_mismatch",
  SESSION_EXPIRED: "session_expired",
  KYC_INCOMPLETE: "kyc_incomplete",
  MAX_PAYOUT_EXCEEDED: "max_payout_exceeded",
  DUPLICATE_REQUEST: "duplicate_request",
  SELF_EXCLUDED: "self_excluded",
  COOLOFF_ACTIVE: "cooloff_active",
  STAKE_BELOW_MINIMUM: "stake_below_minimum",
  STAKE_ABOVE_MAXIMUM: "stake_above_maximum",
} as const;

const MIN_STAKE = 1.0;       // R$1.00
const MAX_STAKE = 10_000.0;  // R$10,000.00
const MAX_PAYOUT = 500_000.0; // R$500,000.00

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export async function handleBets(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  playerId: string
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/bets/, "");

  if (path === "/place" && request.method === "POST") {
    return handlePlaceBet(request, env, ctx, playerId);
  }

  if (path === "/history" && request.method === "GET") {
    return handleBetHistory(url, env, playerId);
  }

  const detailMatch = path.match(/^\/([^/]+)$/);
  if (detailMatch && request.method === "GET") {
    return handleGetBet(detailMatch[1], env, playerId);
  }

  return new Response("Not found", { status: 404 });
}

// ---------------------------------------------------------------------------
// Place bet — 10-step pipeline
// ---------------------------------------------------------------------------

async function handlePlaceBet(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  playerId: string
): Promise<Response> {
  let body: PlaceBetRequest;
  try {
    body = await request.json<PlaceBetRequest>();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }

  // ── Step 1: Validate request structure ────────────────────────────────────
  if (!body.request_id || !body.selections?.length || !body.stake) {
    return Response.json(
      { error: "invalid_request", reason: "missing_required_fields" },
      { status: 400 }
    );
  }

  // ── Step 2: Idempotency check ──────────────────────────────────────────────
  const existing = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, status, stake, combined_odds FROM bets WHERE request_id = ?"
  )
    .bind(body.request_id)
    .first();

  if (existing) {
    return Response.json({
      status: "duplicate",
      bet_id: existing.id,
      message: "Bet already placed with this request_id",
    });
  }

  // ── Step 3: CPF / KYC / geolocation checks ────────────────────────────────
  // Production: calls compliance microservice.
  // Here we validate CPF format (11 digits) as a minimal gate.
  if (!isValidCpfFormat(body.player_metadata?.cpf)) {
    return Response.json(
      {
        error: "bet_rejected",
        reason: REJECTION_REASON.CPF_MISMATCH,
        message: "CPF validation failed",
      },
      { status: 422 }
    );
  }

  // ── Step 4: Market state validation ───────────────────────────────────────
  for (const sel of body.selections) {
    const market = await env.SPORTSBOOK_DB.prepare(
      "SELECT m.state, e.state AS event_state " +
        "FROM markets m JOIN events e ON m.event_id = e.id " +
        "WHERE m.id = ?"
    )
      .bind(sel.market_id)
      .first();

    if (!market) {
      return Response.json(
        {
          error: "bet_rejected",
          reason: REJECTION_REASON.MARKET_SUSPENDED,
          selection_id: sel.selection_id,
        },
        { status: 422 }
      );
    }
    if (market.state !== "open") {
      return Response.json(
        {
          error: "bet_rejected",
          reason: REJECTION_REASON.MARKET_SUSPENDED,
          selection_id: sel.selection_id,
          market_state: market.state,
        },
        { status: 422 }
      );
    }
    if ((market.event_state as string).includes("cancel")) {
      return Response.json(
        {
          error: "bet_rejected",
          reason: REJECTION_REASON.EVENT_CANCELLED,
          selection_id: sel.selection_id,
        },
        { status: 422 }
      );
    }
  }

  // ── Step 5: Odds drift check ──────────────────────────────────────────────
  const oddsChanges: string[] = [];
  for (const sel of body.selections) {
    const currentSel = await env.SPORTSBOOK_DB.prepare(
      "SELECT odds, odds_version FROM selections WHERE id = ? AND active = 1"
    )
      .bind(sel.selection_id)
      .first();

    if (!currentSel) {
      return Response.json(
        {
          error: "bet_rejected",
          reason: REJECTION_REASON.MARKET_SUSPENDED,
          selection_id: sel.selection_id,
        },
        { status: 422 }
      );
    }

    if (currentSel.odds_version !== sel.odds_version) {
      oddsChanges.push(sel.selection_id);
    }
  }

  if (oddsChanges.length > 0) {
    return Response.json(
      {
        error: "bet_rejected",
        reason: REJECTION_REASON.ODDS_CHANGED,
        changed_selections: oddsChanges,
        message: "Odds changed since betslip was loaded. Please review and resubmit.",
      },
      { status: 422 }
    );
  }

  // ── Step 6: Stake validation ──────────────────────────────────────────────
  if (body.stake < MIN_STAKE) {
    return Response.json(
      {
        error: "bet_rejected",
        reason: REJECTION_REASON.STAKE_BELOW_MINIMUM,
        min_stake: MIN_STAKE,
      },
      { status: 422 }
    );
  }
  if (body.stake > MAX_STAKE) {
    return Response.json(
      {
        error: "bet_rejected",
        reason: REJECTION_REASON.STAKE_ABOVE_MAXIMUM,
        max_stake: MAX_STAKE,
      },
      { status: 422 }
    );
  }

  // Calculate combined odds and potential return
  const combinedOdds = body.selections.reduce(
    (acc, sel) => acc * sel.odds,
    1.0
  );
  const potentialReturn = Math.round(body.stake * combinedOdds * 100) / 100;

  if (potentialReturn > MAX_PAYOUT) {
    return Response.json(
      {
        error: "bet_rejected",
        reason: REJECTION_REASON.MAX_PAYOUT_EXCEEDED,
        max_payout: MAX_PAYOUT,
        your_payout: potentialReturn,
      },
      { status: 422 }
    );
  }

  // ── Step 7: Wallet balance reservation ───────────────────────────────────
  // Production: calls wallet microservice with a 30-second reservation TTL.
  // Here we assume the balance check is delegated to the wallet service.

  // ── Step 8: Persist bet (idempotent insert) ───────────────────────────────
  const betId = crypto.randomUUID();
  const now = new Date().toISOString();

  try {
    await env.SPORTSBOOK_DB.prepare(
      "INSERT INTO bets (id, request_id, player_id, bet_type, stake, currency, " +
        "combined_odds, potential_return, status, created_at, updated_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?)"
    )
      .bind(
        betId,
        body.request_id,
        playerId,
        body.bet_type,
        body.stake,
        body.currency ?? "BRL",
        combinedOdds,
        potentialReturn,
        now,
        now
      )
      .run();
  } catch (err) {
    // UNIQUE constraint on request_id — return existing bet
    const dup = await env.SPORTSBOOK_DB.prepare(
      "SELECT id FROM bets WHERE request_id = ?"
    )
      .bind(body.request_id)
      .first();
    return Response.json({
      status: "duplicate",
      bet_id: dup?.id ?? null,
    });
  }

  // Insert bet legs
  for (const sel of body.selections) {
    await env.SPORTSBOOK_DB.prepare(
      "INSERT INTO bet_legs (id, bet_id, selection_id, market_id, event_id, " +
        "selection_name, market_name, event_name, odds_at_placement, odds_version, " +
        "leg_status, created_at) " +
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)"
    )
      .bind(
        crypto.randomUUID(),
        betId,
        sel.selection_id,
        sel.market_id,
        sel.event_id,
        sel.selection_name,
        sel.market_name,
        sel.event_name,
        sel.odds,
        sel.odds_version,
        now
      )
      .run();
  }

  // ── Step 9-10: Async post-placement tasks (waitUntil) ─────────────────────
  // These run after the 200 response is sent — they must NOT block the player.
  ctx.waitUntil(
    (async () => {
      // Step 9: Emit bet_placed event (update exposure counters, downstream consumers)
      await emitBetPlacedEvent(betId, playerId, body, combinedOdds, potentialReturn);

      // Step 10: Report to SIGAP (Lei 14.790 regulatory reporting)
      await reportToSigap(betId, body, env).catch((err) => {
        console.error("SIGAP report failed, will retry:", err);
        // Queue for retry via the SIGAP retry cron (*/5 * * * *)
      });
    })()
  );

  return Response.json(
    {
      status: "accepted",
      bet_id: betId,
      combined_odds: combinedOdds,
      potential_return: potentialReturn,
      created_at: now,
    },
    { status: 201 }
  );
}

// ---------------------------------------------------------------------------
// Bet history
// ---------------------------------------------------------------------------

async function handleBetHistory(
  url: URL,
  env: Env,
  playerId: string
): Promise<Response> {
  const status = url.searchParams.get("status"); // accepted|won|lost|void
  const limit = Math.min(parseInt(url.searchParams.get("limit") ?? "20"), 100);
  const offset = parseInt(url.searchParams.get("offset") ?? "0");

  let query =
    "SELECT id, bet_type, stake, currency, combined_odds, potential_return, " +
    "       status, cashout_amount, settled_at, created_at " +
    "FROM bets WHERE player_id = ? ";
  const params: (string | number)[] = [playerId];

  if (status) {
    query += "AND status = ? ";
    params.push(status);
  }

  query += "ORDER BY created_at DESC LIMIT ? OFFSET ?";
  params.push(limit, offset);

  const result = await env.SPORTSBOOK_DB.prepare(query)
    .bind(...params)
    .all();

  return Response.json({
    bets: result.results ?? [],
    limit,
    offset,
  });
}

// ---------------------------------------------------------------------------
// Bet detail
// ---------------------------------------------------------------------------

async function handleGetBet(
  betId: string,
  env: Env,
  playerId: string
): Promise<Response> {
  const bet = await env.SPORTSBOOK_DB.prepare(
    "SELECT * FROM bets WHERE id = ? AND player_id = ?"
  )
    .bind(betId, playerId)
    .first();

  if (!bet) {
    return Response.json({ error: "bet_not_found" }, { status: 404 });
  }

  const legs = await env.SPORTSBOOK_DB.prepare(
    "SELECT id, selection_id, selection_name, market_name, event_name, " +
      "       odds_at_placement, leg_status, settled_at " +
      "FROM bet_legs WHERE bet_id = ? ORDER BY created_at"
  )
    .bind(betId)
    .all();

  return Response.json({ bet: { ...bet, legs: legs.results ?? [] } });
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function isValidCpfFormat(cpf: string | undefined): boolean {
  if (!cpf) return false;
  const digits = cpf.replace(/\D/g, "");
  return digits.length === 11;
}

async function emitBetPlacedEvent(
  betId: string,
  playerId: string,
  body: PlaceBetRequest,
  combinedOdds: number,
  potentialReturn: number
): Promise<void> {
  // Production: publish to Kafka / Queues / Pub-Sub for downstream consumers
  // (exposure counter updates, VIP tier recalculation, RG monitoring, etc.)
  console.log(
    `BET_PLACED bet_id=${betId} player_id=${playerId} stake=${body.stake} ` +
      `combined_odds=${combinedOdds} potential_return=${potentialReturn}`
  );
}

async function reportToSigap(
  betId: string,
  body: PlaceBetRequest,
  env: Env
): Promise<void> {
  // Production: POST to SIGAP API with signed payload (Lei 14.790/2023)
  // Uses SIGAP_CERT + SIGAP_KEY for mTLS authentication.
  console.log(`SIGAP_REPORT bet_id=${betId} stake=${body.stake} currency=${body.currency}`);
}
