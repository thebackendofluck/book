// Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * backoffice-worker/src/index.ts
 * ------------------------------
 * Hono-based Cloudflare Worker for iGaming backoffice admin API.
 *
 * Endpoints:
 *   GET  /api/admin/players/search        — Player search (name, email, ID)
 *   GET  /api/admin/players/:id           — Player profile
 *   GET  /api/admin/kyc/queue             — KYC pending review queue
 *   POST /api/admin/kyc/:id/decision      — Approve/reject KYC
 *   GET  /api/admin/withdrawals/queue     — Pending withdrawal queue
 *   POST /api/admin/withdrawals/:id/approve — Approve withdrawal
 *   POST /api/admin/withdrawals/:id/reject  — Reject withdrawal
 *   GET  /api/admin/audit                 — Audit log (compliance only)
 *   GET  /api/admin/dev/token             — Dev JWT generator (dev env only)
 *   GET  /health                          — Liveness probe
 *
 * Bindings:
 *   D1: AUDIT_LOG  — Stores all admin actions for compliance
 */

import { Hono } from "hono";
import { z } from "zod";
import { zValidator } from "@hono/zod-validator";
import { cors } from "hono/cors";
import { logger } from "hono/logger";

import {
  requireAuth,
  generateDevToken,
  type AuthContext,
  type Role,
} from "./auth";

// ---------------------------------------------------------------------------
// Environment bindings
// ---------------------------------------------------------------------------

export interface Env {
  AUDIT_LOG: D1Database;
  JWT_SECRET?: string;
  ENVIRONMENT?: string;
}

// Hono context variables
type Variables = { auth: AuthContext };

// ---------------------------------------------------------------------------
// Zod schemas
// ---------------------------------------------------------------------------

const PlayerSearchSchema = z.object({
  q: z.string().min(2),
  limit: z.coerce.number().int().min(1).max(100).default(20),
  offset: z.coerce.number().int().nonnegative().default(0),
});

const KycDecisionSchema = z.object({
  decision: z.enum(["APPROVED", "REJECTED"]),
  reason: z.string().optional(),
  reviewedBy: z.string().optional(),
});

const WithdrawalActionSchema = z.object({
  reason: z.string().optional(),
});

// ---------------------------------------------------------------------------
// Stub data helpers
// ---------------------------------------------------------------------------

function stubPlayer(id: string) {
  return {
    id,
    email: `player.${id.slice(0, 6)}@example.com`,
    name: `Player ${id.slice(0, 8)}`,
    kycStatus: "PENDING" as const,
    accountStatus: "ACTIVE" as const,
    registeredAt: new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString(),
    jurisdiction: "GB",
    depositTotal: Math.floor(Math.random() * 10000),
    withdrawalTotal: Math.floor(Math.random() * 5000),
  };
}

function stubWithdrawal(id: string) {
  return {
    id,
    playerId: `player_${Math.random().toString(36).slice(2, 10)}`,
    amount: Math.floor(Math.random() * 500) + 50,
    currency: "GBP",
    method: "bank_transfer",
    status: "PROCESSING",
    requestedAt: new Date(Date.now() - Math.random() * 3600 * 1000).toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Audit log helper
// ---------------------------------------------------------------------------

async function writeAuditLog(
  db: D1Database,
  action: string,
  adminId: string,
  targetId: string,
  details: Record<string, unknown>,
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO audit_log (id, action, admin_id, target_id, details, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      crypto.randomUUID(),
      action,
      adminId,
      targetId,
      JSON.stringify(details),
      Date.now(),
    )
    .run();
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

const app = new Hono<{ Bindings: Env; Variables: Variables }>();

app.use("*", logger());
app.use(
  "/api/*",
  cors({ origin: "*", allowMethods: ["GET", "POST", "DELETE", "PATCH"] }),
);

// ---------------------------------------------------------------------------
// Root (public)
// ---------------------------------------------------------------------------

app.get("/", (c) =>
  c.json({
    service: "backoffice",
    status: "operational",
    version: "1.0.0",
    platform: "AcmetoCasino",
    domain: "cloud-acmetocasino.com",
    endpoints: [
      "/health",
      "/api/admin/players/search",
      "/api/admin/players/:id",
      "/api/admin/kyc/queue",
      "/api/admin/kyc/:id/decision",
      "/api/admin/withdrawals/queue",
      "/api/admin/withdrawals/:id/approve",
      "/api/admin/withdrawals/:id/reject",
      "/api/admin/audit",
    ],
    documentation: "https://thebackendofluck.com",
  }),
);

// ---------------------------------------------------------------------------
// Health (public)
// ---------------------------------------------------------------------------

app.get("/health", (c) =>
  c.json({
    status: "ok",
    service: "backoffice-worker",
    timestamp: new Date().toISOString(),
  }),
);

// ---------------------------------------------------------------------------
// Dev token generator (non-production only)
// ---------------------------------------------------------------------------

app.get("/api/admin/dev/token", async (c) => {
  if (c.env.ENVIRONMENT === "production") {
    return c.json({ error: "Not available in production" }, 403);
  }

  const role = (c.req.query("role") ?? "customer_support") as Role;
  const userId = c.req.query("userId") ?? "dev-user-001";
  const secret = c.env.JWT_SECRET ?? "dev-secret-replace-in-production";

  const token = await generateDevToken(userId, role, secret);

  return c.json({
    token,
    role,
    userId,
    note: "This endpoint is disabled in production",
  });
});

// ---------------------------------------------------------------------------
// Player search (customer_support+)
// ---------------------------------------------------------------------------

app.get(
  "/api/admin/players/search",
  requireAuth(["customer_support"]),
  zValidator("query", PlayerSearchSchema),
  async (c) => {
    const { q, limit, offset } = c.req.valid("query");
    const auth = c.get("auth");

    // Stub: return synthetic results
    const players = Array.from({ length: Math.min(limit, 3) }, (_, i) =>
      stubPlayer(`player_${q}_${i + offset}`),
    );

    await writeAuditLog(c.env.AUDIT_LOG, "PLAYER_SEARCH", auth.userId, "SEARCH", { q, limit, offset });

    return c.json({
      query: q,
      total: players.length,
      limit,
      offset,
      players,
    });
  },
);

// ---------------------------------------------------------------------------
// Player profile (customer_support+)
// ---------------------------------------------------------------------------

app.get(
  "/api/admin/players/:id",
  requireAuth(["customer_support"]),
  async (c) => {
    const id = c.req.param("id");
    const auth = c.get("auth");

    const player = stubPlayer(id);
    await writeAuditLog(c.env.AUDIT_LOG, "PLAYER_VIEW", auth.userId, id, {});

    return c.json(player);
  },
);

// ---------------------------------------------------------------------------
// KYC queue (kyc_analyst+)
// ---------------------------------------------------------------------------

app.get(
  "/api/admin/kyc/queue",
  requireAuth(["kyc_analyst"]),
  async (c) => {
    const limit = Number(c.req.query("limit") ?? 20);

    const queue = Array.from({ length: Math.min(limit, 5) }, (_, i) => ({
      ...stubPlayer(`kyc_pending_${i}`),
      kycStatus: "PENDING",
      documentTypes: ["passport", "proof_of_address"],
      submittedAt: new Date(
        Date.now() - i * 2 * 3600 * 1000,
      ).toISOString(),
    }));

    return c.json({ total: queue.length, queue });
  },
);

// ---------------------------------------------------------------------------
// KYC decision (kyc_analyst+)
// ---------------------------------------------------------------------------

app.post(
  "/api/admin/kyc/:id/decision",
  requireAuth(["kyc_analyst"]),
  zValidator("json", KycDecisionSchema),
  async (c) => {
    const playerId = c.req.param("id");
    const body = c.req.valid("json");
    const auth = c.get("auth");

    await writeAuditLog(c.env.AUDIT_LOG, "KYC_DECISION", auth.userId, playerId, {
      decision: body.decision,
      reason: body.reason ?? null,
    });

    return c.json({
      playerId,
      kycStatus: body.decision,
      decidedBy: auth.userId,
      decidedAt: new Date().toISOString(),
    });
  },
);

// ---------------------------------------------------------------------------
// Withdrawal queue (payments_team+)
// ---------------------------------------------------------------------------

app.get(
  "/api/admin/withdrawals/queue",
  requireAuth(["payments_team"]),
  async (c) => {
    const limit = Number(c.req.query("limit") ?? 20);

    const queue = Array.from({ length: Math.min(limit, 5) }, (_, i) =>
      stubWithdrawal(`wdl_pending_${i}`),
    );

    return c.json({ total: queue.length, queue });
  },
);

// ---------------------------------------------------------------------------
// Withdrawal approve (payments_team+)
// ---------------------------------------------------------------------------

app.post(
  "/api/admin/withdrawals/:id/approve",
  requireAuth(["payments_team"]),
  zValidator("json", WithdrawalActionSchema),
  async (c) => {
    const withdrawalId = c.req.param("id");
    const body = c.req.valid("json");
    const auth = c.get("auth");

    await writeAuditLog(
      c.env.AUDIT_LOG,
      "WITHDRAWAL_APPROVED",
      auth.userId,
      withdrawalId,
      { reason: body.reason ?? null },
    );

    return c.json({
      id: withdrawalId,
      status: "COMPLETED",
      approvedBy: auth.userId,
      approvedAt: new Date().toISOString(),
    });
  },
);

// ---------------------------------------------------------------------------
// Withdrawal reject (payments_team+)
// ---------------------------------------------------------------------------

app.post(
  "/api/admin/withdrawals/:id/reject",
  requireAuth(["payments_team"]),
  zValidator("json", WithdrawalActionSchema),
  async (c) => {
    const withdrawalId = c.req.param("id");
    const body = c.req.valid("json");
    const auth = c.get("auth");

    await writeAuditLog(
      c.env.AUDIT_LOG,
      "WITHDRAWAL_REJECTED",
      auth.userId,
      withdrawalId,
      { reason: body.reason ?? "No reason provided" },
    );

    return c.json({
      id: withdrawalId,
      status: "CANCELLED",
      rejectedBy: auth.userId,
      rejectedAt: new Date().toISOString(),
      reason: body.reason,
    });
  },
);

// ---------------------------------------------------------------------------
// Audit log (compliance+)
// ---------------------------------------------------------------------------

app.get(
  "/api/admin/audit",
  requireAuth(["compliance"]),
  async (c) => {
    const limit = Number(c.req.query("limit") ?? 50);
    const offset = Number(c.req.query("offset") ?? 0);

    const rows = await c.env.AUDIT_LOG.prepare(
      `SELECT id, action, admin_id, target_id, details, created_at
       FROM audit_log
       ORDER BY created_at DESC
       LIMIT ? OFFSET ?`,
    )
      .bind(limit, offset)
      .all<Record<string, unknown>>();

    const entries = (rows.results ?? []).map((row) => ({
      id: row.id,
      action: row.action,
      adminId: row.admin_id,
      targetId: row.target_id,
      details: JSON.parse((row.details as string) ?? "{}"),
      createdAt: new Date((row.created_at as number)).toISOString(),
    }));

    return c.json({ total: entries.length, limit, offset, entries });
  },
);

// ---------------------------------------------------------------------------
// Error handler
// ---------------------------------------------------------------------------

app.onError((err, c) => {
  console.error("[backoffice-worker] unhandled error:", err);
  return c.json({ error: "Internal server error" }, 500);
});

export default app;
