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
 * scale-signal.ts
 *
 * Sends scale-up and scale-down instructions to the origin infrastructure
 * autoscaler when traffic events warrant a capacity change.
 *
 * This module is called from two places:
 *
 *   1. From the campaign-manager, as a side-effect of POST /campaign/start and
 *      POST /campaign/stop, to pre-warm or drain origin capacity.
 *
 *   2. From the edge-classifier's waitUntil() when an attack is confirmed, to
 *      push the block list to the origin and trigger an alert.
 *
 * All origin calls are fire-and-forget and wrapped in try/catch so they never
 * propagate errors into the main request path.
 *
 * Routes (admin only, X-Admin-Secret required):
 *   POST /scale/up    — manual scale-up with optional profile
 *   POST /scale/down  — manual scale-down with grace period
 *   GET  /scale/status — retrieve last scale signal sent
 */

import type { Env, CampaignRecord, AttackEvent } from "./types.js";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ScaleProfile {
  minInstances?: number;
  maxInstances?: number;
  targetCpuPercent?: number;
  reason: string;
  gracePeriodSeconds?: number;
}

interface ScaleUpPayload {
  event: "scale_up";
  profile: ScaleProfile;
  campaign?: CampaignRecord;
  triggeredAt: string;
}

interface ScaleDownPayload {
  event: "scale_down";
  gracePeriodSeconds: number;
  reason: string;
  triggeredAt: string;
}

interface AttackAlertPayload {
  event: "attack_confirmed";
  blockList: string[];       // IPs to add to origin-side WAF
  asnList: string[];         // ASNs to throttle at the load balancer
  attackEvents: AttackEvent[];
  triggeredAt: string;
}

// ─── Signal senders ──────────────────────────────────────────────────────────

/**
 * Send a scale-up signal to the origin autoscaler.
 * Called by the campaign manager when a campaign starts and by the classifier
 * when sustained attack traffic is detected (origin needs capacity headroom).
 */
export async function sendScaleUp(
  env: Env,
  profile: ScaleProfile,
  campaign?: CampaignRecord,
): Promise<void> {
  const payload: ScaleUpPayload = {
    event: "scale_up",
    profile,
    campaign,
    triggeredAt: new Date().toISOString(),
  };

  try {
    const resp = await fetch(env.ORIGIN_AUTOSCALER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Edge-Signal": "scale_up",
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      console.error(
        `[scale-signal] scale_up failed: HTTP ${resp.status}`,
        await resp.text(),
      );
    } else {
      console.log(
        `[scale-signal] scale_up sent: ${JSON.stringify(profile)}`,
      );
    }
  } catch (err) {
    // Never propagate — this is best-effort signalling
    console.error("[scale-signal] scale_up error:", err);
  }
}

/**
 * Send a scale-down signal with a grace period.
 * The origin will wait `gracePeriodSeconds` before actually draining instances
 * to avoid prematurely killing connections from a post-campaign traffic tail.
 */
export async function sendScaleDown(
  env: Env,
  gracePeriodSeconds: number,
  reason: string,
): Promise<void> {
  const payload: ScaleDownPayload = {
    event: "scale_down",
    gracePeriodSeconds,
    reason,
    triggeredAt: new Date().toISOString(),
  };

  try {
    const resp = await fetch(env.ORIGIN_AUTOSCALER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Edge-Signal": "scale_down",
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      console.error(
        `[scale-signal] scale_down failed: HTTP ${resp.status}`,
        await resp.text(),
      );
    } else {
      console.log(
        `[scale-signal] scale_down sent: grace=${gracePeriodSeconds}s reason=${reason}`,
      );
    }
  } catch (err) {
    console.error("[scale-signal] scale_down error:", err);
  }
}

/**
 * Push a confirmed attack block list to the origin.
 * The origin's internal WAF or nginx deny list will block these IPs/ASNs at
 * the application layer as a secondary defence layer (defence-in-depth).
 */
export async function sendAttackAlert(
  env: Env,
  events: AttackEvent[],
): Promise<void> {
  const blockList = [...new Set(events.map((e) => e.ip))];
  const asnList = [...new Set(events.map((e) => e.asn))];

  const payload: AttackAlertPayload = {
    event: "attack_confirmed",
    blockList,
    asnList,
    attackEvents: events,
    triggeredAt: new Date().toISOString(),
  };

  try {
    const resp = await fetch(env.ORIGIN_ALERT_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Edge-Signal": "attack_confirmed",
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      console.error(
        `[scale-signal] attack_alert failed: HTTP ${resp.status}`,
        await resp.text(),
      );
    } else {
      console.log(
        `[scale-signal] attack_alert sent: ${blockList.length} IPs, ${asnList.length} ASNs`,
      );
    }
  } catch (err) {
    console.error("[scale-signal] attack_alert error:", err);
  }
}

// ─── Campaign-triggered scale helpers ────────────────────────────────────────

/**
 * Called when a campaign starts.  Derives a scale profile from the campaign
 * multiplier and sends it to the autoscaler.
 */
export async function onCampaignStart(
  env: Env,
  campaign: CampaignRecord,
): Promise<void> {
  const profile: ScaleProfile = {
    // Each unit of multiplier adds 20% capacity headroom
    minInstances: Math.ceil(2 * campaign.multiplier),
    maxInstances: Math.ceil(20 * campaign.multiplier),
    targetCpuPercent: 60,
    reason: `campaign_start:${campaign.geo}:${campaign.note ?? ""}`,
  };
  await sendScaleUp(env, profile, campaign);
}

/**
 * Called when a campaign stops.  Sends a scale-down with a 15-minute grace
 * period to absorb the post-campaign traffic tail.
 */
export async function onCampaignStop(
  env: Env,
  campaign: CampaignRecord,
): Promise<void> {
  await sendScaleDown(
    env,
    900, // 15-minute grace period
    `campaign_stop:${campaign.geo}`,
  );
}

// ─── Admin route handlers ────────────────────────────────────────────────────

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

interface ScaleUpRequestBody {
  profile: ScaleProfile;
}

interface ScaleDownRequestBody {
  gracePeriodSeconds?: number;
  reason?: string;
}

async function handleScaleUp(request: Request, env: Env): Promise<Response> {
  let body: ScaleUpRequestBody;
  try {
    body = (await request.json()) as ScaleUpRequestBody;
  } catch {
    return jsonResponse({ error: "Invalid JSON body" }, 400);
  }

  if (!body.profile?.reason) {
    return jsonResponse({ error: "profile.reason is required" }, 400);
  }

  // Send synchronously for admin-triggered scale (operator wants confirmation)
  const payload: ScaleUpPayload = {
    event: "scale_up",
    profile: body.profile,
    triggeredAt: new Date().toISOString(),
  };

  try {
    const resp = await fetch(env.ORIGIN_AUTOSCALER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Edge-Signal": "scale_up",
      },
      body: JSON.stringify(payload),
    });

    return jsonResponse({
      status: "sent",
      originStatus: resp.status,
      payload,
    }, resp.ok ? 200 : 502);
  } catch {
    return jsonResponse({ error: "Autoscaler unreachable" }, 502);
  }
}

async function handleScaleDown(request: Request, env: Env): Promise<Response> {
  let body: ScaleDownRequestBody = {};
  try {
    body = (await request.json()) as ScaleDownRequestBody;
  } catch { /* use defaults */ }

  const gracePeriodSeconds = body.gracePeriodSeconds ?? 300;
  const reason = body.reason ?? "manual_scale_down";

  const payload: ScaleDownPayload = {
    event: "scale_down",
    gracePeriodSeconds,
    reason,
    triggeredAt: new Date().toISOString(),
  };

  try {
    const resp = await fetch(env.ORIGIN_AUTOSCALER_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Edge-Signal": "scale_down",
      },
      body: JSON.stringify(payload),
    });

    return jsonResponse({
      status: "sent",
      originStatus: resp.status,
      payload,
    }, resp.ok ? 200 : 502);
  } catch {
    return jsonResponse({ error: "Autoscaler unreachable" }, 502);
  }
}

async function handleScaleStatus(env: Env): Promise<Response> {
  // The autoscaler exposes a GET endpoint on the same base URL
  try {
    const resp = await fetch(`${env.ORIGIN_AUTOSCALER_URL}/status`, {
      method: "GET",
      headers: { "X-Edge-Signal": "status_check" },
    });
    const body = await resp.text();
    return new Response(body, {
      status: resp.status,
      headers: {
        "Content-Type": resp.headers.get("Content-Type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return jsonResponse(
      { error: "Autoscaler unreachable" },
      502,
    );
  }
}

// ─── Router ───────────────────────────────────────────────────────────────────

export async function handleScaleSignal(
  request: Request,
  env: Env,
  _ctx: ExecutionContext,
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/scale/up" && method === "POST") {
    return handleScaleUp(request, env);
  }
  if (path === "/scale/down" && method === "POST") {
    return handleScaleDown(request, env);
  }
  if (path === "/scale/status" && method === "GET") {
    return handleScaleStatus(env);
  }

  return jsonResponse(
    { error: "Unknown scale route", path, method },
    404,
  );
}
