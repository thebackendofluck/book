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
 * exclusion-worker/src/index.ts
 * -----------------------------
 * Hono-based Cloudflare Worker for national exclusion registry checks.
 *
 * Endpoints:
 *   POST /api/v1/exclusion/check         — Check a player against a registry
 *   POST /api/v1/exclusion/check/all     — Check against all known registries
 *   GET  /api/v1/exclusion/status/:id    — Cached exclusion status from KV
 *   DELETE /api/v1/exclusion/cache/:id   — Invalidate cached status
 *   GET  /health                         — Liveness probe
 *
 * Bindings:
 *   KV: EXCLUSION_CACHE  — Stores check results keyed by playerId+jurisdiction
 */

import { Hono } from "hono";
import { z } from "zod";
import { zValidator } from "@hono/zod-validator";
import { cors } from "hono/cors";
import { logger } from "hono/logger";

import {
  checkRegistry,
  type ExclusionCheckResult,
  type Jurisdiction,
} from "./registries";

// ---------------------------------------------------------------------------
// Environment bindings
// ---------------------------------------------------------------------------

export interface Env {
  EXCLUSION_CACHE: KVNamespace;
  GAMSTOP_API_KEY?: string;
  SPELPAUS_API_TOKEN?: string;
  ROFUS_LICENSE_NUMBER?: string;
  ENVIRONMENT?: string;
}

// ---------------------------------------------------------------------------
// Zod schemas
// ---------------------------------------------------------------------------

const CheckSchema = z.object({
  playerId: z.string().min(1),
  jurisdiction: z.string().min(2).max(3).toUpperCase(),
});

const CheckAllSchema = z.object({
  playerId: z.string().min(1),
  jurisdictions: z
    .array(z.string().min(2).max(3).toUpperCase())
    .min(1)
    .max(10),
});

// ---------------------------------------------------------------------------
// KV helpers
// ---------------------------------------------------------------------------

const CACHE_TTL_SECONDS = 900; // 15 minutes

function cacheKey(playerId: string, jurisdiction: string): string {
  return `excl:${playerId}:${jurisdiction}`;
}

async function getCached(
  kv: KVNamespace,
  playerId: string,
  jurisdiction: string,
): Promise<ExclusionCheckResult | null> {
  const raw = await kv.get(cacheKey(playerId, jurisdiction), "json");
  return raw as ExclusionCheckResult | null;
}

async function setCache(
  kv: KVNamespace,
  result: ExclusionCheckResult & { playerId: string },
): Promise<void> {
  await kv.put(
    cacheKey(result.playerId, result.jurisdiction),
    JSON.stringify(result),
    { expirationTtl: CACHE_TTL_SECONDS },
  );
}

// ---------------------------------------------------------------------------
// Credential resolver
// ---------------------------------------------------------------------------

function resolveCredential(
  env: Env,
  jurisdiction: Jurisdiction,
): string | undefined {
  switch (jurisdiction) {
    case "GB": return env.GAMSTOP_API_KEY;
    case "SE": return env.SPELPAUS_API_TOKEN;
    case "DK": return env.ROFUS_LICENSE_NUMBER;
    default:   return undefined;
  }
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

const app = new Hono<{ Bindings: Env }>();

app.use("*", logger());
app.use("/api/*", cors({ origin: "*", allowMethods: ["GET", "POST", "DELETE"] }));

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

app.get("/", (c) =>
  c.json({
    service: "exclusion",
    status: "operational",
    version: "1.0.0",
    platform: "AcmetoCasino",
    domain: "cloud-acmetocasino.com",
    endpoints: [
      "/health",
      "/api/v1/exclusion/check",
      "/api/v1/exclusion/check/all",
      "/api/v1/exclusion/status/:id",
      "/api/v1/exclusion/cache/:id",
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
    service: "exclusion-worker",
    supportedJurisdictions: ["GB", "SE", "DK", "BR"],
    timestamp: new Date().toISOString(),
  }),
);

// ---------------------------------------------------------------------------
// Single registry check
// ---------------------------------------------------------------------------

app.post(
  "/api/v1/exclusion/check",
  zValidator("json", CheckSchema),
  async (c) => {
    const { playerId, jurisdiction } = c.req.valid("json");

    // Try cache first
    const cached = await getCached(c.env.EXCLUSION_CACHE, playerId, jurisdiction);
    if (cached) {
      return c.json({ ...cached, source: "CACHE" as const });
    }

    const credential = resolveCredential(c.env, jurisdiction);
    const result = await checkRegistry(playerId, jurisdiction, credential);

    await setCache(c.env.EXCLUSION_CACHE, { ...result, playerId });

    const status = result.isExcluded ? 200 : 200;
    return c.json(result, status);
  },
);

// ---------------------------------------------------------------------------
// Multi-jurisdiction check
// ---------------------------------------------------------------------------

app.post(
  "/api/v1/exclusion/check/all",
  zValidator("json", CheckAllSchema),
  async (c) => {
    const { playerId, jurisdictions } = c.req.valid("json");

    const results = await Promise.all(
      jurisdictions.map(async (jurisdiction) => {
        const cached = await getCached(
          c.env.EXCLUSION_CACHE,
          playerId,
          jurisdiction,
        );
        if (cached) return { ...cached, source: "CACHE" as const };

        const credential = resolveCredential(c.env, jurisdiction);
        const result = await checkRegistry(playerId, jurisdiction, credential);
        await setCache(c.env.EXCLUSION_CACHE, { ...result, playerId });
        return result;
      }),
    );

    const isExcludedAnywhere = results.some((r) => r.isExcluded);
    const activeExclusions = results.filter((r) => r.isExcluded);

    return c.json({
      playerId,
      isExcludedAnywhere,
      exclusionCount: activeExclusions.length,
      activeExclusions,
      allResults: results,
      checkedAt: new Date().toISOString(),
    });
  },
);

// ---------------------------------------------------------------------------
// Cached status lookup
// ---------------------------------------------------------------------------

app.get("/api/v1/exclusion/status/:id", async (c) => {
  const playerId = c.req.param("id");
  const jurisdiction = c.req.query("jurisdiction") ?? "GB";

  const cached = await getCached(c.env.EXCLUSION_CACHE, playerId, jurisdiction);

  if (!cached) {
    return c.json(
      {
        error: "No cached exclusion status — submit a check request first",
        playerId,
        jurisdiction,
      },
      404,
    );
  }

  return c.json({ ...cached, source: "CACHE" as const });
});

// ---------------------------------------------------------------------------
// Cache invalidation
// ---------------------------------------------------------------------------

app.delete("/api/v1/exclusion/cache/:id", async (c) => {
  const playerId = c.req.param("id");
  const jurisdiction = c.req.query("jurisdiction");

  if (jurisdiction) {
    await c.env.EXCLUSION_CACHE.delete(cacheKey(playerId, jurisdiction));
    return c.json({ deleted: 1, playerId, jurisdiction });
  }

  // Delete for all known jurisdictions
  const known = ["GB", "SE", "DK", "BR"];
  await Promise.all(
    known.map((j) =>
      c.env.EXCLUSION_CACHE.delete(cacheKey(playerId, j)),
    ),
  );

  return c.json({ deleted: known.length, playerId, jurisdictions: known });
});

// ---------------------------------------------------------------------------
// Error handler
// ---------------------------------------------------------------------------

app.onError((err, c) => {
  console.error("[exclusion-worker] unhandled error:", err);
  return c.json({ error: "Internal server error" }, 500);
});

export default app;
