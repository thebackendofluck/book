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
 * Gates 2 + 3 — VPN Detection and Known Proxy Check
 *
 * Gate 2 (VPN): Uses Cloudflare's built-in proxy-detection signals:
 *   - cf.isAnonymous     — Cloudflare anonymous-proxy flag
 *   - cf.isAnonymousVpn  — Cloudflare VPN-specific flag
 *   - cf.isPublicProxy   — Cloudflare public-proxy flag
 *   - cf.botManagement.detectionIds — CF Bot Management threat signals
 *
 * Gate 3 (Known Proxy): Cross-checks ASN + org against the hardcoded
 *   DATACENTER_ASNS list for ASNs not caught by Gate 1's bot-score
 *   path (i.e., the bot score was fine but the ASN is still a proxy host).
 *
 * Zero KV reads, zero external calls. Typical wall-clock: <0.05 ms.
 */

import type { GateResult, PlayerRequest, GateConfig } from "../types.js";
import { DATACENTER_ASNS } from "./ip-type.js";

// ─── CF Bot Management detection IDs that indicate proxy/VPN ────────────────
// These numeric IDs are documented in Cloudflare's Bot Management docs.
// https://developers.cloudflare.com/bots/concepts/bot-score/#detection-ids
const VPN_DETECTION_IDS = new Set<number>([
  // Cloudflare-assigned IDs for anonymous proxies, open proxies, VPN services.
  // The exact IDs are enterprise-tier metadata; we cover the public ones here.
  33, // datacenter IP
  34, // anonymous proxy
  82, // VPN or anonymizer service
  83, // Tor network
]);

// ─── Gate 2: VPN detection ───────────────────────────────────────────────────

export function checkVpn(
  req: PlayerRequest,
  _config: GateConfig,
): GateResult {
  const { cf } = req;

  // Cloudflare's native anonymous-proxy flags — set at the edge by CF Radar.
  if (cf.isAnonymousVpn === "1") {
    return {
      action: "block",
      reason: "BANNED_PROXY_VPN",
      gate: 2,
      detail: `CF isAnonymousVpn flag set for ip=${req.ip}`,
    };
  }

  if (cf.isAnonymous === "1") {
    return {
      action: "block",
      reason: "BANNED_PROXY_VPN",
      gate: 2,
      detail: `CF isAnonymous flag set for ip=${req.ip}`,
    };
  }

  if (cf.isPublicProxy === "1") {
    return {
      action: "block",
      reason: "BANNED_PROXY_VPN",
      gate: 2,
      detail: `CF isPublicProxy flag set for ip=${req.ip}`,
    };
  }

  // Check Bot Management detection IDs for VPN/proxy threat categories.
  const detectionIds = cf.botManagement?.detectionIds;
  if (detectionIds) {
    for (const id of Object.keys(detectionIds)) {
      const numId = parseInt(id, 10);
      if (VPN_DETECTION_IDS.has(numId)) {
        return {
          action: "block",
          reason: "BANNED_PROXY_VPN",
          gate: 2,
          detail: `Bot Management detection ID ${numId} matched for ip=${req.ip}`,
        };
      }
    }
  }

  return { action: "pass", reason: "PASS", gate: 2 };
}

// ─── Gate 3: Known proxy / hosting provider ──────────────────────────────────
// Re-checks ASN against the datacenter set with a slightly different intent:
// Gate 1 also checks this, but Gate 1 runs before the bot-score gate.
// Gate 3 catches IPs whose bot score was high enough to pass Gate 1's score
// check but whose ASN is still in the datacenter block list.
// In practice this acts as a belt-and-suspenders for the ASN check.

// Additional known proxy / anonymizer ASNs beyond the core datacenter set.
const PROXY_ASNS = new Set<number>([
  ...DATACENTER_ASNS,
  // Additional anonymizer / residential-proxy networks
  9009,   // M247 Ltd (proxy provider)
  62567,  // DataPacket (residential proxy reseller)
  51167,  // Contabo GmbH
  47583,  // Hostinger International
  197540, // netcup GmbH
  44477,  // Stark Industries Solutions (known proxy host)
  398705, // Mullvad VPN
  209103, // Surfshark VPN
]);

const PROXY_ORG_PATTERNS: RegExp[] = [
  /\bvpn\b/i,
  /proxy/i,
  /anonymi[sz]er/i,
  /tor\s+(project|exit|relay)/i,
  /nordvpn/i,
  /expressvpn/i,
  /surfshark/i,
  /mullvad/i,
  /perfect\s*privacy/i,
  /protonvpn/i,
  /hide\.me/i,
  /cyberghost/i,
  /ipvanish/i,
];

function isProxyOrg(org: string): boolean {
  for (const re of PROXY_ORG_PATTERNS) {
    if (re.test(org)) return true;
  }
  return false;
}

export function checkKnownProxy(
  req: PlayerRequest,
  _config: GateConfig,
): GateResult {
  const { cf } = req;
  const asn = cf.asn ?? 0;

  if (asn !== 0 && PROXY_ASNS.has(asn)) {
    return {
      action: "block",
      reason: "BANNED_PROXY_KNOWN",
      gate: 3,
      detail: `Known proxy ASN ${asn} (${cf.asOrganization ?? "unknown"}) for ip=${req.ip}`,
    };
  }

  const org = cf.asOrganization ?? "";
  if (org.length > 0 && isProxyOrg(org)) {
    return {
      action: "block",
      reason: "BANNED_PROXY_KNOWN",
      gate: 3,
      detail: `Known proxy org pattern: "${org}" asn=${asn} ip=${req.ip}`,
    };
  }

  return { action: "pass", reason: "PASS", gate: 3 };
}
