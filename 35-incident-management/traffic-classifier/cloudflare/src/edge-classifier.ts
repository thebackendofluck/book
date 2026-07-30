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
 * edge-classifier.ts
 *
 * Main Cloudflare Worker entry point.  Runs on EVERY inbound request before
 * it reaches the origin.  The entire classification pipeline must complete in
 * under 2 ms of CPU time; all state reads hit the edge-cached KV layer and
 * never make synchronous HTTP calls to the origin during the hot path.
 *
 * Decision pipeline (in order):
 *   1. Static blocklist checks (JA3, known-bad IP key in RATE_LIMITS KV)
 *   2. Bot Management score + Threat score from cf object
 *   3. Per-IP rate counters (per-second, per-minute) in RATE_LIMITS KV
 *   4. Per-ASN rate counters in RATE_LIMITS KV
 *   5. Geographic spike detection via baseline comparison
 *   6. Campaign lookup (CAMPAIGNS KV) → multiplies all thresholds if active
 *   7. Graduated response: ALLOW → RATE_LIMIT → JS_CHALLENGE → CAPTCHA → BLOCK
 *
 * Admin sub-routes (path-based dispatch within the same Worker):
 *   /campaign/*   → campaign-manager.ts
 *   /attacks/*    → attack-logger.ts
 *   /scale/*      → scale-signal.ts
 */

import {
  type Env,
  type ClassificationResult,
  type TrafficClass,
  type ResponseAction,
  type AttackEvent,
  THRESHOLDS,
  TTL,
} from "./types.js";
import { handleCampaign } from "./campaign-manager.js";
import { handleAttackLog } from "./attack-logger.js";
import { handleScaleSignal } from "./scale-signal.js";
export { AttackCounter } from "./attack-logger.js";

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Atomically increment a KV counter and return its new value.
 * Uses a read-modify-write cycle; for per-second counters this is fast enough
 * because the window is short and collisions are rare at the edge PoP level.
 * Durable Objects are used only for aggregate attack logging (attack-logger.ts).
 */
async function kvIncrement(
  kv: KVNamespace,
  key: string,
  ttlSeconds: number,
): Promise<number> {
  const raw = await kv.get(key);
  const current = raw === null ? 0 : parseInt(raw, 10);
  const next = current + 1;
  // Fire-and-forget: do not await the put so it doesn't block the hot path.
  // A missed increment at high throughput is acceptable; it self-corrects in
  // the next window (TTL expiry).
  void kv.put(key, String(next), { expirationTtl: ttlSeconds });
  return next;
}

/**
 * Build the KV key for a per-IP or per-ASN rolling window counter.
 * Window is aligned to wall-clock seconds/minutes to avoid expensive sliding
 * window arithmetic in the hot path.
 */
function windowKey(prefix: string, id: string, windowSec: number): string {
  const windowId = Math.floor(Date.now() / 1_000 / windowSec);
  return `${prefix}:${id}:${windowId}`;
}

// ─── Campaign lookup ──────────────────────────────────────────────────────────

async function getCampaignMultiplier(
  kv: KVNamespace,
  country: string,
): Promise<{ active: boolean; multiplier: number }> {
  const raw = await kv.get(`campaign_active:geo:${country.toUpperCase()}`);
  if (raw === null) return { active: false, multiplier: 1 };
  try {
    const rec = JSON.parse(raw) as { multiplier: number };
    return { active: true, multiplier: rec.multiplier ?? 5 };
  } catch {
    // Corrupted entry — treat as no campaign
    return { active: false, multiplier: 1 };
  }
}

// ─── JA3 blocklist check ──────────────────────────────────────────────────────

async function isJa3Blocked(kv: KVNamespace, ja3: string): Promise<boolean> {
  if (!ja3 || ja3 === "000000000000000000000000000000000") return false;
  const val = await kv.get(ja3);
  return val !== null;
}

// ─── IP hard-block check ──────────────────────────────────────────────────────

async function isIpBlocked(kv: KVNamespace, ip: string): Promise<boolean> {
  const val = await kv.get(`blocked_ip:${ip}`);
  return val !== null;
}

// ─── Geographic spike detection ───────────────────────────────────────────────

/**
 * Compare current per-minute request volume for a country against a stored
 * baseline (updated lazily).  A sudden 5x spike marks the traffic as
 * suspicious but does not by itself trigger a block.
 */
async function isGeoSpike(
  kv: KVNamespace,
  country: string,
  currentCount: number,
): Promise<boolean> {
  const baselineKey = `geo_baseline:${country.toUpperCase()}`;
  const raw = await kv.get(baselineKey);
  if (raw === null) {
    // No baseline yet — store the current count and return false.
    void kv.put(baselineKey, String(currentCount), {
      expirationTtl: TTL.GEO_BASELINE,
    });
    return false;
  }
  const baseline = parseInt(raw, 10);
  if (baseline === 0) return false;
  return currentCount > baseline * THRESHOLDS.GEO_SPIKE_MULTIPLIER;
}

// ─── Rate counter increment ───────────────────────────────────────────────────

async function incrementRateCounters(
  kv: KVNamespace,
  ip: string,
  asn: string,
): Promise<{
  ipPerSecond: number;
  ipPerMinute: number;
  asnPerSecond: number;
  asnPerMinute: number;
}> {
  const [ipPerSecond, ipPerMinute, asnPerSecond, asnPerMinute] =
    await Promise.all([
      kvIncrement(kv, windowKey("ip_s", ip, 1), TTL.RATE_LIMIT_SECOND),
      kvIncrement(kv, windowKey("ip_m", ip, 60), TTL.RATE_LIMIT_MINUTE),
      kvIncrement(kv, windowKey("asn_s", asn, 1), TTL.RATE_LIMIT_SECOND),
      kvIncrement(kv, windowKey("asn_m", asn, 60), TTL.RATE_LIMIT_MINUTE),
    ]);
  return { ipPerSecond, ipPerMinute, asnPerSecond, asnPerMinute };
}

// ─── Classification engine ────────────────────────────────────────────────────

function classify(opts: {
  ipPerSecond: number;
  ipPerMinute: number;
  asnPerSecond: number;
  asnPerMinute: number;
  botScore: number;
  threatScore: number;
  ja3Blocked: boolean;
  ipBlocked: boolean;
  geoSpike: boolean;
  campaignMultiplier: number;
}): ClassificationResult {
  const {
    ipPerSecond,
    ipPerMinute,
    asnPerSecond,
    asnPerMinute,
    botScore,
    threatScore,
    ja3Blocked,
    ipBlocked,
    geoSpike,
    campaignMultiplier,
  } = opts;

  const reasons: string[] = [];

  // Use a numeric severity level internally to avoid TypeScript narrowing issues
  // when the variable is mutated inside a closure (escalate function).
  // 0 = NORMAL, 1 = SUSPICIOUS, 2 = ATTACK
  let severity = 0;

  const severityToClass = (s: number): TrafficClass => {
    if (s >= 2) return "ATTACK";
    if (s === 1) return "SUSPICIOUS";
    return "NORMAL";
  };

  // Effective thresholds after campaign multiplier
  const ipMinWarn = THRESHOLDS.IP_PER_MINUTE_WARN * campaignMultiplier;
  const ipMinAttack = THRESHOLDS.IP_PER_MINUTE_ATTACK * campaignMultiplier;
  const ipSecAttack = THRESHOLDS.IP_PER_SECOND_ATTACK * campaignMultiplier;
  const asnMinWarn = THRESHOLDS.ASN_PER_MINUTE_WARN * campaignMultiplier;
  const asnMinAttack = THRESHOLDS.ASN_PER_MINUTE_ATTACK * campaignMultiplier;

  const escalate = (cls: TrafficClass, reason: string) => {
    reasons.push(reason);
    const level = cls === "ATTACK" ? 2 : 1;
    if (level > severity) severity = level;
  };

  // Hard blocks — skip all other checks, immediately ATTACK
  if (ipBlocked) escalate("ATTACK", "ip_blocklist");
  if (ja3Blocked) escalate("ATTACK", "ja3_blocklist");

  // Bot score (0 = bot, 100 = human; cf.botManagement.score)
  if (botScore <= THRESHOLDS.BOT_SCORE_ATTACK)
    escalate("ATTACK", `bot_score:${botScore}`);
  else if (botScore <= THRESHOLDS.BOT_SCORE_SUSPICIOUS)
    escalate("SUSPICIOUS", `bot_score:${botScore}`);

  // Cloudflare threat score
  if (threatScore >= THRESHOLDS.THREAT_SCORE_ATTACK)
    escalate("ATTACK", `threat_score:${threatScore}`);
  else if (threatScore >= THRESHOLDS.THREAT_SCORE_SUSPICIOUS)
    escalate("SUSPICIOUS", `threat_score:${threatScore}`);

  // Per-IP rate limits
  if (ipPerSecond >= ipSecAttack)
    escalate("ATTACK", `ip_req_per_sec:${ipPerSecond}`);
  if (ipPerMinute >= ipMinAttack)
    escalate("ATTACK", `ip_req_per_min:${ipPerMinute}`);
  else if (ipPerMinute >= ipMinWarn)
    escalate("SUSPICIOUS", `ip_req_per_min:${ipPerMinute}`);

  // Per-ASN rate limits
  if (asnPerSecond >= THRESHOLDS.IP_PER_SECOND_ATTACK * campaignMultiplier * 50)
    escalate("ATTACK", `asn_req_per_sec:${asnPerSecond}`);
  if (asnPerMinute >= asnMinAttack)
    escalate("ATTACK", `asn_req_per_min:${asnPerMinute}`);
  else if (asnPerMinute >= asnMinWarn)
    escalate("SUSPICIOUS", `asn_req_per_min:${asnPerMinute}`);

  // Geographic spike (suspicious only — needs corroboration from other signals)
  if (geoSpike) escalate("SUSPICIOUS", "geo_spike");

  const worstClass = severityToClass(severity);

  // Map class → graduated action
  let action: ResponseAction;
  const signalCount = reasons.length;

  if (severity >= 2) {
    action = "BLOCK";
  } else if (severity === 1) {
    // Graduated: single geo-spike only → rate limit; one other signal → JS challenge; two+ → captcha
    if (signalCount === 1 && geoSpike && reasons[0] === "geo_spike") {
      action = "RATE_LIMIT";
    } else if (signalCount <= 1) {
      action = "JS_CHALLENGE";
    } else {
      action = "CAPTCHA";
    }
  } else {
    action = "ALLOW";
  }

  return {
    trafficClass: worstClass,
    action,
    reasons,
    rateLimitMultiplier: campaignMultiplier,
    campaignActive: campaignMultiplier > 1,
  };
}

// ─── Response builders ────────────────────────────────────────────────────────

function buildBlockResponse(ip: string, reasons: string[]): Response {
  return new Response(
    JSON.stringify({
      error: "Access denied",
      code: "DDOS_BLOCK",
      reasons,
      ref: ip,
    }),
    {
      status: 403,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Edge-Decision": "block",
      },
    },
  );
}

function buildRateLimitResponse(retryAfter: number = 60): Response {
  return new Response(
    JSON.stringify({ error: "Rate limit exceeded", retryAfter }),
    {
      status: 429,
      headers: {
        "Content-Type": "application/json",
        "Retry-After": String(retryAfter),
        "Cache-Control": "no-store",
        "X-Edge-Decision": "rate_limit",
      },
    },
  );
}

/**
 * JS challenge: return a minimal HTML page that performs a trivial proof-of-work
 * computation and redirects back with a signed token.  In a real deployment this
 * would be Cloudflare's managed challenge; here we emit a simplified version for
 * illustrative purposes.
 */
function buildJsChallengeResponse(returnUrl: string): Response {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Security Check</title>
  <style>body{font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f9fafb;}</style>
</head>
<body>
  <div>
    <p>Verifying your browser&hellip;</p>
    <script>
      (function(){
        async function solve(){
          let n = 0;
          while(true){
            const candidate = String(n++);
            const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(candidate));
            const hex = Array.from(new Uint8Array(buf)).map(b=>b.toString(16).padStart(2,"0")).join("");
            if(hex.startsWith("0000")){
              const url = new URL(${JSON.stringify(returnUrl)});
              url.searchParams.set("_cf_chl", candidate);
              url.searchParams.set("_cf_ts", String(Date.now()));
              window.location.href = url.toString();
              return;
            }
          }
        }
        solve();
      })();
    </script>
  </div>
</body>
</html>`;
  return new Response(html, {
    status: 403,
    headers: {
      "Content-Type": "text/html;charset=UTF-8",
      "Cache-Control": "no-store",
      "X-Edge-Decision": "js_challenge",
    },
  });
}

function buildCaptchaResponse(returnUrl: string): Response {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Human Verification</title>
  <style>body{font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f9fafb;}</style>
</head>
<body>
  <div style="text-align:center;padding:2rem;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.1)">
    <h2>One more step</h2>
    <p>We need to verify you are human before continuing.</p>
    <!-- In production: embed hCaptcha or Cloudflare Turnstile widget here -->
    <div id="captcha-widget" data-return="${returnUrl}"></div>
  </div>
</body>
</html>`;
  return new Response(html, {
    status: 403,
    headers: {
      "Content-Type": "text/html;charset=UTF-8",
      "Cache-Control": "no-store",
      "X-Edge-Decision": "captcha",
    },
  });
}

// ─── Attack event persistence ─────────────────────────────────────────────────

/**
 * Persist the attack event to ATTACK_LOG KV and to the Durable Object
 * counter so that /attacks/asn-summary can query accurate aggregate counts.
 * This runs in a waitUntil() so it never blocks the response path.
 */
async function persistAttackEvent(
  env: Env,
  event: AttackEvent,
): Promise<void> {
  // Store individual event (key = <timestamp>:<ray> for natural sort order)
  const eventKey = `event:${event.timestamp}:${event.ray}`;
  await env.ATTACK_LOG.put(eventKey, JSON.stringify(event), {
    expirationTtl: TTL.ATTACK_BATCH,
  });

  // Increment DO counter for the ASN (race-condition free)
  const id = env.ATTACK_COUNTER.idFromName(event.asn);
  const stub = env.ATTACK_COUNTER.get(id);
  await stub.fetch("https://counter/increment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  });

  // Mark IP as blocked for future fast-path checks
  await env.RATE_LIMITS.put(`blocked_ip:${event.ip}`, "1", {
    expirationTtl: TTL.BLOCKED_IP,
  });
}

// ─── Admin route dispatcher ───────────────────────────────────────────────────

function isAdminRequest(url: URL): boolean {
  return (
    url.pathname.startsWith("/campaign/") ||
    url.pathname.startsWith("/attacks/") ||
    url.pathname.startsWith("/scale/")
  );
}

async function dispatchAdmin(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response> {
  const url = new URL(request.url);

  if (url.pathname.startsWith("/campaign/")) {
    return handleCampaign(request, env);
  }
  if (url.pathname.startsWith("/attacks/")) {
    return handleAttackLog(request, env);
  }
  if (url.pathname.startsWith("/scale/")) {
    return handleScaleSignal(request, env, ctx);
  }

  return new Response("Not found", { status: 404 });
}

// ─── Worker entry point ───────────────────────────────────────────────────────

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<Response> {
    const url = new URL(request.url);

    // Admin routes are protected by a pre-shared secret header.
    // They bypass the DDoS pipeline entirely.
    if (isAdminRequest(url)) {
      const secret = request.headers.get("X-Admin-Secret");
      if (secret !== (env as unknown as Record<string, string>)["ADMIN_SECRET"]) {
        return new Response("Forbidden", { status: 403 });
      }
      return dispatchAdmin(request, env, ctx);
    }

    // ── Extract signals from the Cloudflare cf object ──────────────────────
    const cf = request.cf ?? {};

    const ip =
      request.headers.get("CF-Connecting-IP") ??
      request.headers.get("X-Real-IP") ??
      "0.0.0.0";

    // botManagement is available on Bot Management paid add-on
    const botScore: number =
      (cf as Record<string, unknown>)["botManagement"] !== undefined
        ? ((cf as Record<string, unknown>)["botManagement"] as Record<string, number>)["score"] ?? 50
        : 50;

    const threatScore: number =
      typeof (cf as Record<string, unknown>)["threatScore"] === "number"
        ? ((cf as Record<string, unknown>)["threatScore"] as number)
        : 0;

    const asn: string =
      typeof (cf as Record<string, unknown>)["asn"] === "number"
        ? String((cf as Record<string, unknown>)["asn"])
        : typeof (cf as Record<string, unknown>)["asOrganization"] === "string"
          ? ((cf as Record<string, unknown>)["asOrganization"] as string)
          : "UNKNOWN";

    const country: string =
      typeof (cf as Record<string, unknown>)["country"] === "string"
        ? ((cf as Record<string, unknown>)["country"] as string).toUpperCase()
        : "XX";

    const ja3: string =
      (cf as Record<string, unknown>)["botManagement"] !== undefined
        ? (((cf as Record<string, unknown>)["botManagement"] as Record<string, unknown>)["ja3Hash"] as string) ?? ""
        : "";

    const ray: string = request.headers.get("CF-Ray") ?? "unknown";

    // ── Parallel fast-path lookups ────────────────────────────────────────
    // All KV reads happen concurrently to minimise latency.
    const [ja3Blocked, ipBlocked, { active: campaignActive, multiplier }, counters] =
      await Promise.all([
        isJa3Blocked(env.JA3_BLOCKLIST, ja3),
        isIpBlocked(env.RATE_LIMITS, ip),
        getCampaignMultiplier(env.CAMPAIGNS, country),
        // Increment counters regardless — blocked IPs cost one window slot,
        // which is acceptable given the fast-path block on the next hop.
        incrementRateCounters(env.RATE_LIMITS, ip, asn),
      ]);

    // Per-country volume for geo spike detection
    const geoCounterKey = windowKey("geo_m", country, 60);
    const geoRaw = await env.RATE_LIMITS.get(geoCounterKey);
    const geoPerMinute = geoRaw === null ? 0 : parseInt(geoRaw, 10);
    // Fire-and-forget country counter increment
    void kvIncrement(env.RATE_LIMITS, geoCounterKey, TTL.RATE_LIMIT_MINUTE);

    const geoSpike = await isGeoSpike(env.RATE_LIMITS, country, geoPerMinute);

    // ── Classify ──────────────────────────────────────────────────────────
    const result = classify({
      ...counters,
      botScore,
      threatScore,
      ja3Blocked,
      ipBlocked,
      geoSpike,
      campaignMultiplier: multiplier,
    });

    // ── Attach classification metadata to forwarded request headers ────────
    // These headers are stripped at the edge and never reach browsers.
    const forwardHeaders = new Headers(request.headers);
    forwardHeaders.set("X-Edge-Class", result.trafficClass);
    forwardHeaders.set("X-Edge-Action", result.action);
    forwardHeaders.set("X-Edge-Campaign", String(campaignActive));
    forwardHeaders.set("X-Edge-Reasons", result.reasons.join(","));

    // ── Graduated response ────────────────────────────────────────────────
    if (result.action === "RATE_LIMIT") {
      return buildRateLimitResponse(60);
    }

    if (result.action === "ALLOW") {
      // Pass through to origin — reconstruct request with enriched headers
      return fetch(new Request(request, { headers: forwardHeaders }));
    }

    if (result.action === "JS_CHALLENGE") {
      return buildJsChallengeResponse(request.url);
    }

    if (result.action === "CAPTCHA") {
      return buildCaptchaResponse(request.url);
    }

    // BLOCK — persist evidence asynchronously, return 403 immediately
    const attackEvent: AttackEvent = {
      ip,
      asn,
      country,
      ja3,
      timestamp: Date.now(),
      reasons: result.reasons,
      action: result.action,
      ray,
    };
    ctx.waitUntil(persistAttackEvent(env, attackEvent));
    return buildBlockResponse(ip, result.reasons);
  },
} satisfies ExportedHandler<Env>;
