// Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Gate 8 — KYC Status Check
 *
 * Queries the D1 database for the player's current KYC verification state.
 * This is the only gate that touches a relational database. D1 at the CF
 * edge is co-located with the Worker — expected p99 < 2 ms.
 *
 * Gate is SKIPPED for unauthenticated requests (no playerId). That is
 * intentional: KYC checks only apply to logged-in players attempting to
 * place bets or make deposits. Public browsing is allowed.
 *
 * D1 table schema (see also migrations in terraform/):
 *
 *   CREATE TABLE player_kyc (
 *     player_id   TEXT PRIMARY KEY,
 *     status      TEXT NOT NULL CHECK (status IN ('none','pending','approved','rejected','frozen')),
 *     tier        INTEGER NOT NULL DEFAULT 0,  -- 0=basic, 1=enhanced, 2=full
 *     reviewed_at TEXT,
 *     reviewer    TEXT,
 *     notes       TEXT
 *   );
 *
 * KYC state machine:
 *   none     → player exists but hasn't submitted documents → block (cannot play)
 *   pending  → documents submitted, under review             → review (soft block)
 *   approved → fully verified                                → pass
 *   rejected → documents rejected                            → block
 *   frozen   → account temporarily frozen by compliance      → block
 */

import type { Env, GateResult, PlayerRequest, GateConfig } from "../types.js";

// ─── D1 row type ──────────────────────────────────────────────────────────────

type KycStatus = "none" | "pending" | "approved" | "rejected" | "frozen";

interface KycRow {
  player_id: string;
  status: KycStatus;
  tier: number;
  reviewed_at: string | null;
  reviewer: string | null;
  notes: string | null;
}

// ─── Gate function ────────────────────────────────────────────────────────────

export async function checkKycStatus(
  req: PlayerRequest,
  _config: GateConfig,
  env: Env,
): Promise<GateResult> {
  // Gate skipped for unauthenticated requests.
  if (!req.playerId) {
    return {
      action: "pass",
      reason: "PASS",
      gate: 8,
      detail: "No playerId — KYC gate skipped (unauthenticated request)",
    };
  }

  // D1 prepared statement — parameterised to prevent injection.
  const stmt = env.PLAYER_DB.prepare(
    "SELECT player_id, status, tier, reviewed_at, reviewer, notes FROM player_kyc WHERE player_id = ?1 LIMIT 1",
  ).bind(req.playerId);

  const result = await stmt.first<KycRow>();

  // Player not found in KYC table — treat as unverified.
  if (result === null) {
    return {
      action: "block",
      reason: "KYC_BLOCKED",
      gate: 8,
      detail: `Player ${req.playerId} has no KYC record — cannot allow gameplay`,
    };
  }

  switch (result.status) {
    case "approved":
      return { action: "pass", reason: "PASS", gate: 8 };

    case "pending":
      return {
        action: "review",
        reason: "KYC_PENDING",
        gate: 8,
        detail: `Player ${req.playerId} KYC pending (tier=${result.tier})`,
      };

    case "none":
      return {
        action: "block",
        reason: "KYC_BLOCKED",
        gate: 8,
        detail: `Player ${req.playerId} has not submitted KYC documents`,
      };

    case "rejected":
      return {
        action: "block",
        reason: "KYC_BLOCKED",
        gate: 8,
        detail: `Player ${req.playerId} KYC rejected${result.notes ? `: ${result.notes}` : ""}`,
      };

    case "frozen":
      return {
        action: "block",
        reason: "KYC_BLOCKED",
        gate: 8,
        detail: `Player ${req.playerId} account frozen by compliance${result.notes ? `: ${result.notes}` : ""}`,
      };

    default: {
      // Defensive: unknown status — block conservatively.
      const unknownStatus = (result as KycRow).status;
      return {
        action: "block",
        reason: "KYC_BLOCKED",
        gate: 8,
        detail: `Player ${req.playerId} unknown KYC status "${unknownStatus}" — blocking conservatively`,
      };
    }
  }
}
