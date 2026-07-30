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
 * betslip.ts -- Betslip state management via BetslipDO Durable Object.
 *
 * Routes:
 *   POST /api/betslip/add     Add a selection to the betslip
 *   POST /api/betslip/remove  Remove a selection
 *   GET  /api/betslip/state   Get current betslip state
 *   POST /api/betslip/clear   Clear all selections
 *
 * The betslip is session-scoped state managed by a Durable Object keyed on
 * the authenticated player's session ID. This ensures:
 *   - Single-threaded consistency within a session
 *   - Automatic garbage collection when idle
 *   - Survival across page reloads and network interruptions
 *   - Zero coordination overhead between Worker instances
 *
 * Chapter 46b: Sports Betting Architecture — Cloudflare Workers sportsbook.
 * Script reference: scripts/chapter-46/sports/betslip.ts
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Env {
  SPORTSBOOK_DB: D1Database;
  ODDS_CACHE: KVNamespace;
  SESSIONS: KVNamespace;
  BETSLIP: DurableObjectNamespace;
  JWT_SECRET: string;
}

interface BetslipSelection {
  selectionId: string;
  marketId: string;
  eventId: string;
  name: string;
  marketName: string;
  eventName: string;
  odds: number;
  oddsVersion: number;
  addedAt: number;
}

interface AddSelectionRequest {
  selectionId: string;
  marketId: string;
  eventId: string;
  name: string;
  marketName: string;
  eventName: string;
  odds: number;
  oddsVersion: number;
}

interface RemoveSelectionRequest {
  selectionId: string;
}

// ---------------------------------------------------------------------------
// Route handler (Worker entry point for /api/betslip/*)
// ---------------------------------------------------------------------------

export async function handleBetslip(
  request: Request,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  // Extract session ID from JWT (simplified — production uses full JWT validation)
  const authHeader = request.headers.get("Authorization") ?? "";
  const sessionId = extractSessionId(authHeader);
  if (!sessionId) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  // Route to the player's Durable Object instance (keyed on session ID)
  const doId = env.BETSLIP.idFromName(sessionId);
  const stub = env.BETSLIP.get(doId);

  // Forward the request to the DO with the original path
  const url = new URL(request.url);
  const doPath = url.pathname.replace(/^\/api\/betslip/, "");
  const doUrl = new URL(`http://do-internal${doPath}`);

  return stub.fetch(new Request(doUrl.toString(), request));
}

// ---------------------------------------------------------------------------
// BetslipDO -- Durable Object implementation
// ---------------------------------------------------------------------------

/**
 * BetslipDO manages the state of a single player's active betslip.
 *
 * One instance per player session; automatically hibernated after 60 seconds
 * of inactivity. All mutations are in-memory — the DO is the source of truth
 * for the session, not the database.
 *
 * Correlation controls prevent combining selections from the same market
 * (exact duplicate) or correlated markets on the same event within an
 * accumulator (e.g., 1X2 + Asian Handicap on the same match).
 */
export class BetslipDO implements DurableObject {
  private selections: Map<string, BetslipSelection> = new Map();
  private betType: "single" | "accumulator" = "single";

  // Football accumulator compatibility matrix (same event)
  // Markets in the same event that cannot be combined
  private readonly CORRELATED_TEMPLATES = new Set<string>([
    "1x2:asian_handicap",
    "asian_handicap:1x2",
    "1x2:double_chance",
    "double_chance:1x2",
    "1x2:draw_no_bet",
    "draw_no_bet:1x2",
    "1x2:first_half_result",
    "first_half_result:1x2",
    "double_chance:draw_no_bet",
    "draw_no_bet:double_chance",
  ]);

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    switch (url.pathname) {
      case "/add":
        return this.addSelection(await request.json<AddSelectionRequest>());
      case "/remove":
        return this.removeSelection(await request.json<RemoveSelectionRequest>());
      case "/state":
        return this.getState();
      case "/clear":
        this.selections.clear();
        return Response.json({ cleared: true, selections: [] });
      case "/validate":
        return this.validateForPlacement();
      default:
        return new Response("not found", { status: 404 });
    }
  }

  // ---------------------------------------------------------------------------
  // Mutations
  // ---------------------------------------------------------------------------

  private async addSelection(sel: AddSelectionRequest): Promise<Response> {
    if (!sel.selectionId || !sel.marketId || !sel.eventId) {
      return Response.json(
        { error: "invalid_request", reason: "missing_required_fields" },
        { status: 400 }
      );
    }

    // Check compatibility before adding
    const conflict = this.checkCompatibility(sel);
    if (conflict) {
      return Response.json(
        { error: "incompatible_selection", reason: conflict },
        { status: 409 }
      );
    }

    this.selections.set(sel.selectionId, {
      ...sel,
      addedAt: Date.now(),
    });

    // Auto-detect bet type: single if one selection, accumulator if multiple
    this.betType = this.selections.size > 1 ? "accumulator" : "single";

    return Response.json({
      added: true,
      bet_type: this.betType,
      selections: this.serializeSelections(),
      combined_odds: this.calculateCombinedOdds(),
    });
  }

  private async removeSelection(req: RemoveSelectionRequest): Promise<Response> {
    if (!this.selections.has(req.selectionId)) {
      return Response.json(
        { error: "selection_not_found" },
        { status: 404 }
      );
    }

    this.selections.delete(req.selectionId);
    this.betType = this.selections.size > 1 ? "accumulator" : "single";

    return Response.json({
      removed: true,
      bet_type: this.betType,
      selections: this.serializeSelections(),
      combined_odds: this.calculateCombinedOdds(),
    });
  }

  private getState(): Response {
    return Response.json({
      bet_type: this.betType,
      selection_count: this.selections.size,
      selections: this.serializeSelections(),
      combined_odds: this.calculateCombinedOdds(),
    });
  }

  private validateForPlacement(): Response {
    if (this.selections.size === 0) {
      return Response.json(
        { valid: false, reason: "empty_betslip" },
        { status: 400 }
      );
    }

    // Check max selections (20 per accumulator)
    if (this.selections.size > 20) {
      return Response.json(
        {
          valid: false,
          reason: "too_many_selections",
          max: 20,
          current: this.selections.size,
        },
        { status: 400 }
      );
    }

    return Response.json({
      valid: true,
      bet_type: this.betType,
      selections: this.serializeSelections(),
      combined_odds: this.calculateCombinedOdds(),
    });
  }

  // ---------------------------------------------------------------------------
  // Compatibility checks
  // ---------------------------------------------------------------------------

  private checkCompatibility(newSel: AddSelectionRequest): string | null {
    for (const existing of this.selections.values()) {
      // Same selection ID: reject as exact duplicate
      if (existing.selectionId === newSel.selectionId) {
        return "duplicate_selection";
      }

      // Same market: always reject (can't back two outcomes in the same market)
      if (existing.marketId === newSel.marketId) {
        return "duplicate_market";
      }

      // Same event in an accumulator: reject if markets are correlated
      if (this.betType === "accumulator" && existing.eventId === newSel.eventId) {
        // For simplicity we allow same-event combinations only for total/btts markets
        // In production this would reference the full market template correlation matrix
        return "correlated_same_event";
      }
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private serializeSelections(): BetslipSelection[] {
    return Array.from(this.selections.values()).sort(
      (a, b) => a.addedAt - b.addedAt
    );
  }

  private calculateCombinedOdds(): number {
    let product = 1.0;
    for (const sel of this.selections.values()) {
      product *= sel.odds;
    }
    return Math.round(product * 1000) / 1000;
  }
}

// ---------------------------------------------------------------------------
// Helper utilities
// ---------------------------------------------------------------------------

function extractSessionId(authHeader: string): string | null {
  // Production: validate JWT and extract sub claim.
  // Simplified here: expect "Bearer <session-id>"
  if (!authHeader.startsWith("Bearer ")) return null;
  const token = authHeader.slice(7).trim();
  return token.length > 8 ? token : null;
}
