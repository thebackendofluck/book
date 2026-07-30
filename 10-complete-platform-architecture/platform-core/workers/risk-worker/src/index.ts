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
 * risk-worker/src/index.ts
 * ------------------------
 * Hono-based Cloudflare Worker for player risk scoring and alerting.
 *
 * Endpoints:
 *   POST /api/v1/risk/score        — Evaluate risk for a player context
 *   GET  /api/v1/risk/player/:id   — Cached risk profile from KV
 *   POST /api/v1/risk/alert        — Ingest an external risk signal
 *   GET  /health                   — Liveness probe
 *
 * Bindings:
 *   KV: PLAYER_RISK_CACHE  — Stores computed risk profiles (TTL 15 min)
 */

import { Hono } from "hono";
import { z } from "zod";
import { zValidator } from "@hono/zod-validator";
import { cors } from "hono/cors";
import { logger } from "hono/logger";

import {
  evaluateRules,
  computeRiskScore,
  type PlayerContext,
  type AlertResult,
} from "./rules";

// ---------------------------------------------------------------------------
// Environment bindings
// ---------------------------------------------------------------------------

export interface Env {
  PLAYER_RISK_CACHE: KVNamespace;
  ENVIRONMENT?: string;
  ALERT_WEBHOOK_URL?: string;
}

// ---------------------------------------------------------------------------
// Zod schemas
// ---------------------------------------------------------------------------

const PlayerContextSchema = z.object({
  playerId: z.string().min(1),
  sessionDurationMinutes: z.number().nonnegative().default(0),
  depositCount24h: z.number().int().nonnegative().default(0),
  depositAmountGBP24h: z.number().nonnegative().default(0),
  withdrawalCount24h: z.number().int().nonnegative().default(0),
  withdrawalAmountGBP24h: z.number().nonnegative().default(0),
  depositCount7d: z.number().int().nonnegative().default(0),
  depositAmountGBP7d: z.number().nonnegative().default(0),
  singleDepositAmountGBP: z.number().nonnegative().default(0),
  singleWithdrawalAmountGBP: z.number().nonnegative().default(0),
  netLoss24h: z.number().nonnegative().default(0),
  netLoss7d: z.number().nonnegative().default(0),
  netLoss30d: z.number().nonnegative().default(0),
  hourOfDay: z.number().int().min(0).max(23).default(12),
  isFirstDeposit: z.boolean().default(false),
  daysAccountAge: z.number().int().nonnegative().default(365),
  uniqueGamesPlayed24h: z.number().int().nonnegative().default(0),
  rapidBetChangeDetected: z.boolean().default(false),
  countryCode: z.string().length(2).default("GB"),
  isSelfExcluded: z.boolean().default(false),
  pendingKycDocuments: z.boolean().default(false),
  loginCount24h: z.number().int().nonnegative().default(1),
  failedLoginCount24h: z.number().int().nonnegative().default(0),
  kycStatus: z.enum(["NONE", "PENDING", "APPROVED", "REJECTED"]).default("APPROVED"),
});

const ExternalAlertSchema = z.object({
  playerId: z.string().min(1),
  source: z.string().min(1),
  ruleId: z.string().min(1),
  riskLevel: z.enum(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
  description: z.string(),
  timestamp: z.string().datetime().optional(),
});

// ---------------------------------------------------------------------------
// Risk profile stored in KV
// ---------------------------------------------------------------------------

interface RiskProfile {
  playerId: string;
  score: number;
  alerts: AlertResult[];
  evaluatedAt: string;
  expiresAt: string;
}

const CACHE_TTL_SECONDS = 900; // 15 minutes

async function getCachedProfile(
  kv: KVNamespace,
  playerId: string,
): Promise<RiskProfile | null> {
  const raw = await kv.get(`risk:${playerId}`, "json");
  return raw as RiskProfile | null;
}

async function setCachedProfile(
  kv: KVNamespace,
  profile: RiskProfile,
): Promise<void> {
  await kv.put(`risk:${profile.playerId}`, JSON.stringify(profile), {
    expirationTtl: CACHE_TTL_SECONDS,
  });
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

const app = new Hono<{ Bindings: Env }>();

app.use("*", logger());
app.use("/api/*", cors({ origin: "*", allowMethods: ["GET", "POST"] }));

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

app.get("/", (c) =>
  c.json({
    service: "risk",
    status: "operational",
    version: "1.0.0",
    platform: "AcmetoCasino",
    domain: "cloud-acmetocasino.com",
    endpoints: [
      "/health",
      "/api/v1/risk/score",
      "/api/v1/risk/player/:id",
      "/api/v1/risk/alert",
    ],
    documentation: "https://thebackendofluck.com",
  }),
);

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

app.get("/health", (c) =>
  c.json({
    status: "ok",
    service: "risk-worker",
    rulesCount: 19,
    timestamp: new Date().toISOString(),
  }),
);

// ---------------------------------------------------------------------------
// Risk score evaluation
// ---------------------------------------------------------------------------

app.post(
  "/api/v1/risk/score",
  zValidator("json", PlayerContextSchema),
  async (c) => {
    const body = c.req.valid("json") as PlayerContext;

    const alerts = evaluateRules(body);
    const score = computeRiskScore(alerts);

    const now = new Date();
    const expiresAt = new Date(now.getTime() + CACHE_TTL_SECONDS * 1000);

    const profile: RiskProfile = {
      playerId: body.playerId,
      score,
      alerts,
      evaluatedAt: now.toISOString(),
      expiresAt: expiresAt.toISOString(),
    };

    await setCachedProfile(c.env.PLAYER_RISK_CACHE, profile);

    // Fire-and-forget webhook for CRITICAL alerts
    const criticalAlerts = alerts.filter((a) => a.riskLevel === "CRITICAL");
    if (criticalAlerts.length > 0 && c.env.ALERT_WEBHOOK_URL) {
      c.executionCtx.waitUntil(
        fetch(c.env.ALERT_WEBHOOK_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            playerId: body.playerId,
            score,
            criticalAlerts,
            timestamp: now.toISOString(),
          }),
        }).catch((err) => console.error("[risk] webhook dispatch failed:", err)),
      );
    }

    return c.json(
      {
        playerId: body.playerId,
        score,
        alertCount: alerts.length,
        criticalCount: criticalAlerts.length,
        topAlert: alerts[0] ?? null,
        alerts,
        evaluatedAt: profile.evaluatedAt,
      },
      200,
    );
  },
);

// ---------------------------------------------------------------------------
// Cached risk profile
// ---------------------------------------------------------------------------

app.get("/api/v1/risk/player/:id", async (c) => {
  const playerId = c.req.param("id");
  const profile = await getCachedProfile(c.env.PLAYER_RISK_CACHE, playerId);

  if (!profile) {
    return c.json(
      { error: "No risk profile found — submit a score request first" },
      404,
    );
  }

  return c.json(profile);
});

// ---------------------------------------------------------------------------
// External alert ingestion
// ---------------------------------------------------------------------------

app.post(
  "/api/v1/risk/alert",
  zValidator("json", ExternalAlertSchema),
  async (c) => {
    const body = c.req.valid("json");

    // Merge into existing KV profile or create a stub
    const existing = await getCachedProfile(
      c.env.PLAYER_RISK_CACHE,
      body.playerId,
    );

    const externalAlert: AlertResult = {
      ruleId: body.ruleId,
      ruleName: `External: ${body.source}`,
      riskLevel: body.riskLevel,
      description: body.description,
      suggestedAction:
        body.riskLevel === "CRITICAL"
          ? "ESCALATE"
          : body.riskLevel === "HIGH"
            ? "BLOCK"
            : "REVIEW",
    };

    const baseAlerts = existing?.alerts ?? [];
    const merged = [...baseAlerts, externalAlert];
    const score = computeRiskScore(merged);

    const now = new Date();
    const updated: RiskProfile = {
      playerId: body.playerId,
      score,
      alerts: merged,
      evaluatedAt: now.toISOString(),
      expiresAt: new Date(
        now.getTime() + CACHE_TTL_SECONDS * 1000,
      ).toISOString(),
    };

    await setCachedProfile(c.env.PLAYER_RISK_CACHE, updated);

    return c.json({ playerId: body.playerId, score, alertsTotal: merged.length }, 200);
  },
);

// ---------------------------------------------------------------------------
// Error handler
// ---------------------------------------------------------------------------

app.onError((err, c) => {
  console.error("[risk-worker] unhandled error:", err);
  return c.json({ error: "Internal server error" }, 500);
});

export default app;
