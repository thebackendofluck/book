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
 * Gate 6 — Device Fingerprint
 *
 * Uses the JA3 TLS fingerprint hash provided by Cloudflare Bot Management
 * (cf.botManagement.ja3Hash) to detect anomalies:
 *
 *   1. Blocklisted JA3 hash — known-bad TLS client signatures stored in KV.
 *   2. JA3 churn per IP — too many distinct JA3 hashes from the same IP
 *      within the tracking window indicates TLS fingerprint rotation, a
 *      common evasion technique.
 *   3. IP churn per JA3 — one TLS fingerprint seen from too many distinct
 *      IPs is a botnet / shared-tool signal.
 *
 * KV layout (DEVICE_FINGERPRINTS namespace):
 *   "ja3:block:<hash>"    → "1"  (blocklisted hash; presence = block)
 *   "ja3:ip:<ip>"         → JSON — FingerprintHistory
 *   "ja3:hash:<hash>"     → JSON — HashHistory
 *
 * Typical wall-clock: <1 ms (2–3 parallel KV reads).
 */

import type { Env, GateResult, PlayerRequest, GateConfig } from "../types.js";

// ─── KV value schemas ─────────────────────────────────────────────────────────

/** Stored under "ja3:ip:<ip>" — tracks distinct JA3 hashes seen for this IP. */
interface FingerprintHistory {
  /** Distinct JA3 hashes seen, with the ISO timestamp of first observation. */
  hashes: Record<string, string>; // { "<ja3hash>": "<firstSeenAt ISO>" }
  updatedAt: string;
}

/** Stored under "ja3:hash:<hash>" — tracks distinct IPs seen for this hash. */
interface HashHistory {
  /** Distinct IPs seen, with the ISO timestamp of first observation. */
  ips: Record<string, string>; // { "<ip>": "<firstSeenAt ISO>" }
  updatedAt: string;
}

// ─── Thresholds ───────────────────────────────────────────────────────────────

/** Max distinct JA3 hashes allowed per IP within the tracking window. */
const MAX_JA3_PER_IP = 3;

/** Max distinct IPs allowed for a single JA3 hash within the window. */
const MAX_IPS_PER_JA3 = 50;

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function readFingerprintHistory(
  ip: string,
  ja3: string,
  env: Env,
): Promise<{ ipHistory: FingerprintHistory | null; hashHistory: HashHistory | null }> {
  const [ipRaw, hashRaw] = await Promise.all([
    env.DEVICE_FINGERPRINTS.get(`ja3:ip:${ip}`, {
      type: "text",
      cacheTtl: 60,
    }),
    env.DEVICE_FINGERPRINTS.get(`ja3:hash:${ja3}`, {
      type: "text",
      cacheTtl: 60,
    }),
  ]);

  let ipHistory: FingerprintHistory | null = null;
  let hashHistory: HashHistory | null = null;

  if (ipRaw !== null) {
    try { ipHistory = JSON.parse(ipRaw) as FingerprintHistory; } catch { /* treat as absent */ }
  }
  if (hashRaw !== null) {
    try { hashHistory = JSON.parse(hashRaw) as HashHistory; } catch { /* treat as absent */ }
  }

  return { ipHistory, hashHistory };
}

async function updateHistory(
  ip: string,
  ja3: string,
  ipHistory: FingerprintHistory | null,
  hashHistory: HashHistory | null,
  config: GateConfig,
  env: Env,
): Promise<void> {
  const now = new Date().toISOString();

  // Update IP → hashes index.
  const newIpHistory: FingerprintHistory = ipHistory ?? { hashes: {}, updatedAt: now };
  if (!newIpHistory.hashes[ja3]) {
    newIpHistory.hashes[ja3] = now;
  }
  newIpHistory.updatedAt = now;

  // Update hash → IPs index.
  const newHashHistory: HashHistory = hashHistory ?? { ips: {}, updatedAt: now };
  if (!newHashHistory.ips[ip]) {
    newHashHistory.ips[ip] = now;
  }
  newHashHistory.updatedAt = now;

  await Promise.all([
    env.DEVICE_FINGERPRINTS.put(
      `ja3:ip:${ip}`,
      JSON.stringify(newIpHistory),
      { expirationTtl: config.fingerprintTtl },
    ),
    env.DEVICE_FINGERPRINTS.put(
      `ja3:hash:${ja3}`,
      JSON.stringify(newHashHistory),
      { expirationTtl: config.fingerprintTtl },
    ),
  ]);
}

// ─── Gate function ────────────────────────────────────────────────────────────

export async function checkDeviceFingerprint(
  req: PlayerRequest,
  config: GateConfig,
  env: Env,
  ctx: ExecutionContext,
): Promise<GateResult> {
  const ja3 = req.cf.botManagement?.ja3Hash;

  // If CF Bot Management didn't provide a JA3 hash, skip this gate.
  // This happens on non-TLS requests or when Bot Management is not enabled.
  if (!ja3 || ja3.length === 0) {
    return { action: "pass", reason: "PASS", gate: 6, detail: "No JA3 hash available — gate skipped" };
  }

  // 1. Check if this specific JA3 hash is on the blocklist.
  const blocked = await env.DEVICE_FINGERPRINTS.get(`ja3:block:${ja3}`, {
    type: "text",
    cacheTtl: 120,
  });

  if (blocked !== null) {
    return {
      action: "block",
      reason: "DEVICE_ANOMALY",
      gate: 6,
      detail: `JA3 hash ${ja3} is on the device blocklist for ip=${req.ip}`,
    };
  }

  // 2. Read history for IP and hash.
  const { ipHistory, hashHistory } = await readFingerprintHistory(req.ip, ja3, env);

  // 3. JA3 churn per IP — too many distinct fingerprints from this IP.
  if (ipHistory !== null) {
    const distinctCount = Object.keys(ipHistory.hashes).length;
    // Count the new hash only if it is not already in history.
    const isNew = !ipHistory.hashes[ja3];
    const total = isNew ? distinctCount + 1 : distinctCount;
    if (total > config.ja3DistinctLimit) {
      // Update in background before returning.
      ctx.waitUntil(updateHistory(req.ip, ja3, ipHistory, hashHistory, config, env));
      return {
        action: "block",
        reason: "DEVICE_ANOMALY",
        gate: 6,
        detail: `JA3 churn: ip=${req.ip} has ${total} distinct fingerprints (limit=${config.ja3DistinctLimit})`,
      };
    }
  }

  // 4. IP churn per JA3 — single fingerprint seen from too many IPs (botnet).
  if (hashHistory !== null) {
    const distinctIps = Object.keys(hashHistory.ips).length;
    const isNewIp = !hashHistory.ips[req.ip];
    const total = isNewIp ? distinctIps + 1 : distinctIps;
    if (total > MAX_IPS_PER_JA3) {
      ctx.waitUntil(updateHistory(req.ip, ja3, ipHistory, hashHistory, config, env));
      return {
        action: "review",
        reason: "DEVICE_ANOMALY",
        gate: 6,
        detail: `JA3 hash ${ja3} seen from ${total} IPs (limit=${MAX_IPS_PER_JA3}) — possible shared tool`,
      };
    }
  }

  // Update history in the background — does not block the response.
  ctx.waitUntil(updateHistory(req.ip, ja3, ipHistory, hashHistory, config, env));

  return { action: "pass", reason: "PASS", gate: 6 };
}

// ─── Admin helper — add a JA3 hash to the blocklist ──────────────────────────

export async function blockJa3Hash(
  ja3Hash: string,
  env: Env,
  ttlSeconds = 86400,
): Promise<void> {
  await env.DEVICE_FINGERPRINTS.put(
    `ja3:block:${ja3Hash}`,
    "1",
    { expirationTtl: ttlSeconds },
  );
}
