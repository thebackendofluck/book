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
 * Gate 4 — IP Blacklist Check
 *
 * KV layout:
 *   key : "bl:<ip>"
 *   value: JSON — BlacklistEntry
 *
 * KV reads at the edge are served from Cloudflare's edge cache.
 * After the first request to a given edge PoP, subsequent reads for the same
 * key are served from the local cache with p99 < 1 ms.
 *
 * The worker never writes to the blacklist — writes happen out-of-band via
 * the Admin API or a separate management worker. TTL is enforced by the KV
 * expiration, not by logic here.
 */

import type { Env, GateResult, PlayerRequest, GateConfig } from "../types.js";

// ─── KV value schema ─────────────────────────────────────────────────────────

interface BlacklistEntry {
  /** ISO timestamp of when the ban was created. */
  bannedAt: string;
  /** Human-readable reason stored for audit trail. */
  reason: string;
  /** Optional ISO timestamp of expiry (informational — KV TTL controls actual expiry). */
  expiresAt?: string;
  /** Which admin/system created the ban. */
  createdBy?: string;
}

// ─── Gate function ───────────────────────────────────────────────────────────

export async function checkBlacklist(
  req: PlayerRequest,
  _config: GateConfig,
  env: Env,
): Promise<GateResult> {
  const key = `bl:${req.ip}`;

  // KV.get with cacheTtl tells CF to cache at the edge for this many seconds,
  // making repeated reads for the same key essentially free.
  const raw = await env.IP_BLACKLIST.get(key, {
    type: "text",
    cacheTtl: 300, // cache for 5 minutes at edge PoP
  });

  if (raw === null) {
    // Not on the blacklist.
    return { action: "pass", reason: "PASS", gate: 4 };
  }

  let entry: BlacklistEntry;
  try {
    entry = JSON.parse(raw) as BlacklistEntry;
  } catch {
    // Malformed entry — still block; presence of any value means banned.
    return {
      action: "block",
      reason: "BANNED_IP_BLACKLIST",
      gate: 4,
      detail: `Blacklisted ip=${req.ip} (malformed entry, blocking conservatively)`,
    };
  }

  return {
    action: "block",
    reason: "BANNED_IP_BLACKLIST",
    gate: 4,
    detail: `Blacklisted ip=${req.ip} since ${entry.bannedAt}, reason="${entry.reason}"`,
  };
}

// ─── Admin helpers (called by management worker, not the hot path) ───────────

/**
 * Add an IP to the blacklist.
 * @param ttlSeconds  KV expiration in seconds. 0 = no expiry.
 */
export async function addToBlacklist(
  ip: string,
  reason: string,
  env: Env,
  ttlSeconds = 86400,
  createdBy = "system",
): Promise<void> {
  const entry: BlacklistEntry = {
    bannedAt: new Date().toISOString(),
    reason,
    expiresAt: ttlSeconds > 0
      ? new Date(Date.now() + ttlSeconds * 1000).toISOString()
      : undefined,
    createdBy,
  };

  const options: KVNamespacePutOptions = ttlSeconds > 0
    ? { expirationTtl: ttlSeconds }
    : {};

  await env.IP_BLACKLIST.put(`bl:${ip}`, JSON.stringify(entry), options);
}

/** Remove an IP from the blacklist immediately. */
export async function removeFromBlacklist(
  ip: string,
  env: Env,
): Promise<void> {
  await env.IP_BLACKLIST.delete(`bl:${ip}`);
}
