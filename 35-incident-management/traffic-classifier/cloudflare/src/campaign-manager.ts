// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * campaign-manager.ts
 *
 * Handles marketing campaign lifecycle.  When a campaign is active for a
 * geographic region, the edge classifier raises all rate-limit thresholds by
 * the specified multiplier and skips the JS challenge step, allowing legitimate
 * burst traffic from promotions (e.g. a Brazilian bonus launch) to pass through
 * without false-positive friction.
 *
 * Routes (all require X-Admin-Secret header — enforced in edge-classifier.ts):
 *   POST /campaign/start   — activate a campaign
 *   POST /campaign/stop    — deactivate a campaign by geo
 *   GET  /campaign/active  — list all active campaigns
 *
 * KV schema (namespace: CAMPAIGNS):
 *   campaign_active:geo:<COUNTRY_CODE>  →  JSON CampaignRecord (TTL = duration)
 *   campaign_history:<timestamp>:<geo>  →  JSON CampaignRecord (TTL = 30 days)
 */

import type { Env, CampaignRecord } from "./types.js";

// ─── Request/response bodies ──────────────────────────────────────────────────

interface StartCampaignBody {
  geo: string;          // ISO 3166-1 alpha-2 country code, e.g. "BR"
  multiplier?: number;  // Rate limit multiplier, default 5
  durationSeconds?: number; // How long the campaign runs, default 3 hours
  note?: string;        // Free-form description for audit trail
}

interface StopCampaignBody {
  geo: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function errorResponse(message: string, status = 400): Response {
  return jsonResponse({ error: message }, status);
}

async function parseBody<T>(request: Request): Promise<T | null> {
  try {
    return (await request.json()) as T;
  } catch {
    return null;
  }
}

// ─── Handlers ────────────────────────────────────────────────────────────────

async function startCampaign(request: Request, env: Env): Promise<Response> {
  const body = await parseBody<StartCampaignBody>(request);
  if (!body) return errorResponse("Invalid JSON body");
  if (!body.geo || !/^[A-Z]{2}$/.test(body.geo.toUpperCase())) {
    return errorResponse("geo must be a valid ISO 3166-1 alpha-2 code (e.g. BR)");
  }

  const geo = body.geo.toUpperCase();
  const multiplier = Math.max(1, Math.min(body.multiplier ?? 5, 20)); // clamp 1–20
  const durationSeconds = Math.max(60, Math.min(body.durationSeconds ?? 10_800, 86_400)); // 1 min – 24 hrs
  const now = Date.now();

  const record: CampaignRecord = {
    geo,
    multiplier,
    startedAt: now,
    expiresAt: now + durationSeconds * 1_000,
    note: body.note,
  };

  const activeKey = `campaign_active:geo:${geo}`;
  const historyKey = `campaign_history:${now}:${geo}`;

  await Promise.all([
    env.CAMPAIGNS.put(activeKey, JSON.stringify(record), {
      expirationTtl: durationSeconds,
    }),
    env.CAMPAIGNS.put(historyKey, JSON.stringify(record), {
      expirationTtl: 30 * 86_400, // 30-day audit trail
    }),
  ]);

  return jsonResponse({
    status: "started",
    campaign: record,
    message: `Campaign for ${geo} active for ${durationSeconds}s with ${multiplier}x multiplier`,
  }, 201);
}

async function stopCampaign(request: Request, env: Env): Promise<Response> {
  const body = await parseBody<StopCampaignBody>(request);
  if (!body) return errorResponse("Invalid JSON body");
  if (!body.geo || !/^[A-Z]{2}$/.test(body.geo.toUpperCase())) {
    return errorResponse("geo must be a valid ISO 3166-1 alpha-2 code");
  }

  const geo = body.geo.toUpperCase();
  const activeKey = `campaign_active:geo:${geo}`;

  // Read the existing record before deletion for the audit log
  const existing = await env.CAMPAIGNS.get(activeKey);
  if (!existing) {
    return errorResponse(`No active campaign found for geo: ${geo}`, 404);
  }

  const record = JSON.parse(existing) as CampaignRecord;
  const stopRecord = { ...record, stoppedAt: Date.now(), stoppedEarly: true };

  await Promise.all([
    env.CAMPAIGNS.delete(activeKey),
    env.CAMPAIGNS.put(
      `campaign_stopped:${Date.now()}:${geo}`,
      JSON.stringify(stopRecord),
      { expirationTtl: 30 * 86_400 },
    ),
  ]);

  return jsonResponse({
    status: "stopped",
    campaign: stopRecord,
    message: `Campaign for ${geo} deactivated`,
  });
}

async function listActiveCampaigns(env: Env): Promise<Response> {
  // KV list() with prefix scans the active campaigns namespace.
  // Workers KV list() returns up to 1000 keys per call; for small campaign
  // tables this is a single round trip.
  const listing = await env.CAMPAIGNS.list({ prefix: "campaign_active:geo:" });

  const campaigns: CampaignRecord[] = [];
  const now = Date.now();

  for (const key of listing.keys) {
    const raw = await env.CAMPAIGNS.get(key.name);
    if (!raw) continue;
    try {
      const rec = JSON.parse(raw) as CampaignRecord;
      // Double-check expiry in case KV TTL hasn't fired yet
      if (rec.expiresAt > now) {
        campaigns.push({
          ...rec,
          // Add remaining TTL as a convenience field
          ...(({ expiresAt }) => ({
            remainingSeconds: Math.floor((expiresAt - now) / 1_000),
          }))(rec),
        });
      }
    } catch {
      // Skip malformed entries
    }
  }

  return jsonResponse({ campaigns, count: campaigns.length });
}

// ─── Router ───────────────────────────────────────────────────────────────────

export async function handleCampaign(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/campaign/start" && method === "POST") {
    return startCampaign(request, env);
  }
  if (path === "/campaign/stop" && method === "POST") {
    return stopCampaign(request, env);
  }
  if (path === "/campaign/active" && method === "GET") {
    return listActiveCampaigns(env);
  }

  return new Response(
    JSON.stringify({ error: "Unknown campaign route", path, method }),
    { status: 404, headers: { "Content-Type": "application/json" } },
  );
}
