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
 * Gate 1 — IP Type Check
 *
 * Decision order (first match wins):
 *   1. Tor exit node   → block BANNED_PROXY_TOR
 *   2. Datacenter ASN  → block BANNED_PROXY_DC
 *   3. Bot score <40   → block BANNED_PROXY_TOR (bot/automated traffic)
 *   4. otherwise       → pass
 *
 * Uses only the CF object — zero KV reads, zero external calls.
 * Typical wall-clock: <0.05 ms (pure in-memory).
 */

import type { GateResult, PlayerRequest, GateConfig } from "../types.js";

// ─── Datacenter ASN list ─────────────────────────────────────────────────────
// Sources: DigitalOcean, AWS, Google, Azure, Vultr, Linode/Akamai,
//          Hetzner, OVH, Cloudflare, Liquid Web, Fastly, Zayo
export const DATACENTER_ASNS = new Set<number>([
  14061, // DigitalOcean
  16509, // Amazon (AWS EC2)
  15169, // Google Cloud
  8075,  // Microsoft Azure
  20473, // Vultr Holdings
  63949, // Akamai Connected Cloud (Linode)
  24940, // Hetzner Online
  16276, // OVH SAS
  13335, // Cloudflare (used by VPN providers riding CF infra)
  32244, // Liquid Web
  20940, // Akamai Technologies
  54113, // Fastly
  174,   // Cogent Communications (frequent VPN host)
]);

// ─── Known datacenter org-name substrings ────────────────────────────────────
// Catches ASNs not in the hardcoded list but whose org name is obviously DC.
const DATACENTER_ORG_PATTERNS: RegExp[] = [
  /hosting/i,
  /datacenter/i,
  /data\s*center/i,
  /cloud/i,
  /server/i,
  /vps/i,
  /coloc/i,
  /colo\b/i,
  /dedicated/i,
  /amazon/i,
  /google/i,
  /microsoft\s+azure/i,
  /digitalocean/i,
  /linode/i,
  /vultr/i,
  /hetzner/i,
  /ovh/i,
  /leaseweb/i,
  /fastly/i,
  /zayo/i,
];

/**
 * Returns true when the ASN organisation name looks like a commercial
 * datacenter or hosting provider.
 */
function isDcOrg(org: string): boolean {
  for (const re of DATACENTER_ORG_PATTERNS) {
    if (re.test(org)) return true;
  }
  return false;
}

// ─── Gate function ───────────────────────────────────────────────────────────

export function checkIpType(
  req: PlayerRequest,
  config: GateConfig,
): GateResult {
  const { cf } = req;

  // 1. Tor exit — Cloudflare sets cf.isTor = "1" on known Tor exit nodes.
  if (cf.isTor === "1") {
    return {
      action: "block",
      reason: "BANNED_PROXY_TOR",
      gate: 1,
      detail: `Tor exit detected for ip=${req.ip}`,
    };
  }

  // 2. Datacenter ASN — hardcoded set first (O(1)), then org-name patterns.
  const asn = cf.asn ?? 0;
  if (asn !== 0 && DATACENTER_ASNS.has(asn)) {
    return {
      action: "block",
      reason: "BANNED_PROXY_DC",
      gate: 1,
      detail: `Datacenter ASN ${asn} (${cf.asOrganization ?? "unknown"}) for ip=${req.ip}`,
    };
  }

  const org = cf.asOrganization ?? "";
  if (org.length > 0 && isDcOrg(org)) {
    return {
      action: "block",
      reason: "BANNED_PROXY_DC",
      gate: 1,
      detail: `Datacenter org pattern match: "${org}" asn=${asn} ip=${req.ip}`,
    };
  }

  // 3. Bot Management score — score is 1 (bot) → 99 (human).
  //    Low score on a residential IP can indicate automated scraping or
  //    credential-stuffing scripts. We treat sub-threshold as proxy-class traffic.
  const botScore = cf.botManagement?.score ?? 99;
  if (botScore < config.botScoreThreshold) {
    return {
      action: "block",
      reason: "BANNED_PROXY_TOR",
      gate: 1,
      detail: `Bot score ${botScore} < threshold ${config.botScoreThreshold} for ip=${req.ip}`,
    };
  }

  return { action: "pass", reason: "PASS", gate: 1 };
}
