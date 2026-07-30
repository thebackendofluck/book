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
 * Gate 5 — Fraud Score
 *
 * Computes a weighted fraud score (0–100) from multiple velocity and
 * behavioural signals. Each signal contributes a weighted additive component.
 * The final score is clamped to [0, 100].
 *
 * KV layout (FRAUD_VELOCITY namespace):
 *   "vel:<ip>:1m"   → counter (requests in last 60 s)
 *   "vel:<ip>:5m"   → counter (requests in last 300 s)
 *   "vel:<ip>:1h"   → counter (requests in last 3600 s)
 *   "cs:<ip>"       → country:score JSON for geo-mismatch tracking
 *
 * All KV increments are fire-and-forget (waitUntil) so they don't block the
 * gate's decision. The gate reads the CURRENT counters synchronously, then
 * schedules counter increments in the background.
 *
 * Typical wall-clock: <1 ms (1 parallel KV read batch + in-memory scoring).
 */

import type { Env, GateResult, PlayerRequest, GateConfig } from "../types.js";

// ─── Signal weights ───────────────────────────────────────────────────────────
// Sum of all max-weight contributions = 100.
// Adjust these in response to observed fraud patterns.

const WEIGHTS = {
  // Velocity signals — all windows are additive
  rate1m: 20,    // requests in last 1 minute
  rate5m: 15,    // requests in last 5 minutes
  rate1h: 10,    // requests in last 1 hour

  // CF Bot Management score (inverted: low bot score = higher fraud signal)
  botScore: 20,

  // Geography signals
  countryMismatch: 15, // accept-language vs cf.country mismatch

  // Session / structural anomalies
  noUserAgent: 10, // missing or generic user agent
  knownBadUa: 10,  // known fraud tooling UA strings
} as const;

// ─── Known bad user-agent substrings ─────────────────────────────────────────
const BAD_UA_PATTERNS: RegExp[] = [
  /python-requests/i,
  /go-http-client/i,
  /axios\/\d/i,
  /node-fetch/i,
  /curl\//i,
  /wget\//i,
  /libwww/i,
  /scrapy/i,
  /phantomjs/i,
  /headlesschrome/i,
  /selenium/i,
  /puppeteer/i,
  /playwright/i,
];

// Accept-Language → ISO-3166 country code heuristic.
// Maps the primary language tag to the most common associated country.
// Not exhaustive — covers ~90 % of traffic.
const LANG_TO_COUNTRY: Record<string, string> = {
  "pt-br": "BR",
  "pt-pt": "PT",
  "pt":    "BR",
  "en-us": "US",
  "en-gb": "GB",
  "en":    "US",
  "es-mx": "MX",
  "es-ar": "AR",
  "es-es": "ES",
  "es":    "ES",
  "de":    "DE",
  "fr":    "FR",
  "it":    "IT",
  "nl":    "NL",
  "pl":    "PL",
  "ru":    "RU",
  "tr":    "TR",
  "sv":    "SE",
  "fi":    "FI",
  "nb":    "NO",
  "da":    "DK",
  "el":    "GR",
  "ro":    "RO",
  "cs":    "CZ",
  "hu":    "HU",
  "sk":    "SK",
  "uk":    "UA",
  "bg":    "BG",
  "hr":    "HR",
  "lt":    "LT",
  "lv":    "LV",
  "et":    "EE",
  "sl":    "SI",
  "mt":    "MT",
};

function parseAcceptLanguageCountry(header: string): string | null {
  // Accept-Language: pt-BR,pt;q=0.9,en;q=0.8
  const primary = header.split(",")[0].trim().toLowerCase().split(";")[0].trim();
  return LANG_TO_COUNTRY[primary] ?? null;
}

// ─── Velocity helpers ─────────────────────────────────────────────────────────

interface VelocityCounters {
  count1m: number;
  count5m: number;
  count1h: number;
}

async function readVelocityCounters(
  ip: string,
  env: Env,
): Promise<VelocityCounters> {
  // Batch all three KV reads in parallel — one round-trip.
  const [v1m, v5m, v1h] = await Promise.all([
    env.FRAUD_VELOCITY.get(`vel:${ip}:1m`, { type: "text", cacheTtl: 10 }),
    env.FRAUD_VELOCITY.get(`vel:${ip}:5m`, { type: "text", cacheTtl: 30 }),
    env.FRAUD_VELOCITY.get(`vel:${ip}:1h`, { type: "text", cacheTtl: 60 }),
  ]);

  return {
    count1m: v1m !== null ? parseInt(v1m, 10) || 0 : 0,
    count5m: v5m !== null ? parseInt(v5m, 10) || 0 : 0,
    count1h: v1h !== null ? parseInt(v1h, 10) || 0 : 0,
  };
}

/**
 * Increment velocity counters in the background (non-blocking).
 * Uses KV's atomic-ish pattern: read-modify-write is not perfectly atomic in
 * CF KV, but for rate-limiting purposes the slight inaccuracy is acceptable.
 * For strict atomicity, use a Durable Object counter instead.
 */
async function incrementVelocityCounters(
  ip: string,
  counters: VelocityCounters,
  config: GateConfig,
  env: Env,
): Promise<void> {
  await Promise.all([
    env.FRAUD_VELOCITY.put(
      `vel:${ip}:1m`,
      String(counters.count1m + 1),
      { expirationTtl: config.velocityWindow1mSec },
    ),
    env.FRAUD_VELOCITY.put(
      `vel:${ip}:5m`,
      String(counters.count5m + 1),
      { expirationTtl: config.velocityWindow5mSec },
    ),
    env.FRAUD_VELOCITY.put(
      `vel:${ip}:1h`,
      String(counters.count1h + 1),
      { expirationTtl: config.velocityWindow1hSec },
    ),
  ]);
}

// ─── Scoring ──────────────────────────────────────────────────────────────────

interface ScoreBreakdown {
  total: number;
  velocity1m: number;
  velocity5m: number;
  velocity1h: number;
  botScore: number;
  countryMismatch: number;
  userAgent: number;
}

function computeScore(
  req: PlayerRequest,
  counters: VelocityCounters,
  config: GateConfig,
): ScoreBreakdown {
  let total = 0;
  const breakdown: ScoreBreakdown = {
    total: 0,
    velocity1m: 0,
    velocity5m: 0,
    velocity1h: 0,
    botScore: 0,
    countryMismatch: 0,
    userAgent: 0,
  };

  // ── 1-minute velocity ───────────────────────────────────────────────────
  // Sigmoid-style: score increases steeply near the rate limit.
  if (counters.count1m > 0) {
    const ratio = Math.min(counters.count1m / config.rateLimit1m, 1.0);
    breakdown.velocity1m = Math.round(WEIGHTS.rate1m * ratio);
    total += breakdown.velocity1m;
  }

  // ── 5-minute velocity ───────────────────────────────────────────────────
  const limit5m = config.rateLimit1m * 3; // 3× the per-minute limit
  if (counters.count5m > 0) {
    const ratio = Math.min(counters.count5m / limit5m, 1.0);
    breakdown.velocity5m = Math.round(WEIGHTS.rate5m * ratio);
    total += breakdown.velocity5m;
  }

  // ── 1-hour velocity ─────────────────────────────────────────────────────
  const limit1h = config.rateLimit1m * 30; // 30× the per-minute limit
  if (counters.count1h > 0) {
    const ratio = Math.min(counters.count1h / limit1h, 1.0);
    breakdown.velocity1h = Math.round(WEIGHTS.rate1h * ratio);
    total += breakdown.velocity1h;
  }

  // ── Bot Management score (inverted) ────────────────────────────────────
  // score=99 (human) → 0 points; score=1 (bot) → full WEIGHTS.botScore points
  const cfBotScore = req.cf.botManagement?.score ?? 99;
  if (cfBotScore < 99) {
    const inverted = (99 - cfBotScore) / 98; // normalise to [0, 1]
    breakdown.botScore = Math.round(WEIGHTS.botScore * inverted);
    total += breakdown.botScore;
  }

  // ── Country vs Accept-Language mismatch ────────────────────────────────
  if (req.acceptLanguage && req.cf.country) {
    const langCountry = parseAcceptLanguageCountry(req.acceptLanguage);
    if (langCountry !== null && langCountry !== req.cf.country) {
      breakdown.countryMismatch = WEIGHTS.countryMismatch;
      total += breakdown.countryMismatch;
    }
  }

  // ── User-Agent checks ───────────────────────────────────────────────────
  const ua = req.userAgent ?? "";
  if (ua.length === 0) {
    breakdown.userAgent = WEIGHTS.noUserAgent;
    total += breakdown.userAgent;
  } else {
    for (const re of BAD_UA_PATTERNS) {
      if (re.test(ua)) {
        breakdown.userAgent = WEIGHTS.knownBadUa;
        total += breakdown.userAgent;
        break;
      }
    }
  }

  breakdown.total = Math.min(total, 100);
  return breakdown;
}

// ─── Gate function ────────────────────────────────────────────────────────────

export async function checkFraudScore(
  req: PlayerRequest,
  config: GateConfig,
  env: Env,
  ctx: ExecutionContext,
): Promise<GateResult> {
  const counters = await readVelocityCounters(req.ip, env);
  const breakdown = computeScore(req, counters, config);
  const score = breakdown.total;

  // Schedule counter increment in the background — does not block the response.
  ctx.waitUntil(incrementVelocityCounters(req.ip, counters, config, env));

  if (score >= config.fraudBlockThreshold) {
    return {
      action: "block",
      reason: "HIGH_FRAUD_SCORE",
      gate: 5,
      detail: `Fraud score ${score} ≥ block threshold ${config.fraudBlockThreshold}. `
        + `vel1m=${breakdown.velocity1m} vel5m=${breakdown.velocity5m} `
        + `vel1h=${breakdown.velocity1h} bot=${breakdown.botScore} `
        + `geo=${breakdown.countryMismatch} ua=${breakdown.userAgent}`,
    };
  }

  if (score >= config.fraudReviewThreshold) {
    return {
      action: "review",
      reason: "HIGH_FRAUD_SCORE",
      gate: 5,
      detail: `Fraud score ${score} ≥ review threshold ${config.fraudReviewThreshold}`,
    };
  }

  return { action: "pass", reason: "PASS", gate: 5 };
}
