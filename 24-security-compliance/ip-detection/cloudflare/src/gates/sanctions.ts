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
 * Gate 7 — Sanctions / PEP Check
 *
 * Two sub-checks:
 *
 *   A. Country-level OFAC block
 *      KV key: "sanctions:country:<ISO2>"  value: "1"
 *      A full set of OFAC-sanctioned and high-risk jurisdictions is
 *      bootstrapped in wrangler.toml secrets / the Terraform plan.
 *      The in-memory HARDCODED_SANCTIONED_COUNTRIES set provides a
 *      zero-latency fallback that never needs a KV read.
 *
 *   B. Name fuzzy match (PEP / SDN list)
 *      KV key: "sanctions:name:<normalised-token>"  value: "<canonical name>"
 *      Player names are tokenised, normalised (lowercase, diacritics stripped),
 *      and each token is looked up in KV. A match means the player's name
 *      contains a token that appears in the SDN/PEP index.
 *      This is a first-pass filter; full name review happens in the compliance
 *      back-office. The gate returns "review" (not "block") on a name hit so
 *      that false positives are handled by humans.
 *
 * Typical wall-clock:
 *   - Country check: <0.05 ms (in-memory Set lookup, no KV needed)
 *   - Name check:    <1 ms   (1–4 parallel KV reads for name tokens)
 */

import type { Env, GateResult, PlayerRequest, GateConfig } from "../types.js";

// ─── Hardcoded OFAC + high-risk jurisdictions ────────────────────────────────
// Source: OFAC SDN list, EU high-risk third countries, FATF blacklist.
// Maintained in code so that zero-latency decisions are possible even if KV
// is temporarily unavailable (which should never happen but defence-in-depth).
export const HARDCODED_SANCTIONED_COUNTRIES = new Set<string>([
  "CU", // Cuba
  "IR", // Iran
  "KP", // North Korea (DPRK)
  "RU", // Russia (OFAC + EU sanctions)
  "SY", // Syria
  "VE", // Venezuela (OFAC, targeted)
  "BY", // Belarus (EU/US sanctions)
  "MM", // Myanmar
  "SS", // South Sudan
  "SD", // Sudan
  "SO", // Somalia
  "LY", // Libya
  "YE", // Yemen
  "ZW", // Zimbabwe (targeted sanctions)
  "CF", // Central African Republic
  "ML", // Mali
  "NI", // Nicaragua
  "HT", // Haiti
]);

// ─── Name normalisation helpers ───────────────────────────────────────────────

const DIACRITIC_MAP: Record<string, string> = {
  á: "a", à: "a", â: "a", ä: "a", ã: "a", å: "a", æ: "ae",
  é: "e", è: "e", ê: "e", ë: "e",
  í: "i", ì: "i", î: "i", ï: "i",
  ó: "o", ò: "o", ô: "o", ö: "o", õ: "o", ø: "o",
  ú: "u", ù: "u", û: "u", ü: "u",
  ñ: "n", ç: "c", ý: "y", ÿ: "y",
  ß: "ss",
};

function normaliseName(raw: string): string {
  let s = raw.toLowerCase();
  for (const [from, to] of Object.entries(DIACRITIC_MAP)) {
    s = s.replaceAll(from, to);
  }
  // Remove punctuation, keep spaces and alphanumerics.
  s = s.replace(/[^a-z0-9\s]/g, "");
  return s.trim();
}

/**
 * Tokenises a name into an array of normalised tokens, filtering out
 * common prepositions and single-character tokens that generate too many
 * false positives.
 */
function tokeniseName(name: string): string[] {
  const STOP_WORDS = new Set(["de", "la", "le", "di", "da", "van", "von", "bin", "bint", "al", "el"]);
  return normaliseName(name)
    .split(/\s+/)
    .filter((t) => t.length >= 2 && !STOP_WORDS.has(t));
}

// ─── Gate function ────────────────────────────────────────────────────────────

export async function checkSanctions(
  req: PlayerRequest,
  _config: GateConfig,
  env: Env,
): Promise<GateResult> {
  const country = req.cf.country ?? "";

  // ── A. Country-level block ────────────────────────────────────────────────
  if (country.length > 0) {
    // In-memory check first (zero-latency).
    if (HARDCODED_SANCTIONED_COUNTRIES.has(country)) {
      return {
        action: "block",
        reason: "SANCTIONS_MATCH",
        gate: 7,
        detail: `Sanctioned country ${country} (hardcoded list) for ip=${req.ip}`,
      };
    }

    // KV check for dynamically added country sanctions.
    const kvCountry = await env.SANCTIONS_LIST.get(`sanctions:country:${country}`, {
      type: "text",
      cacheTtl: 300,
    });
    if (kvCountry !== null) {
      return {
        action: "block",
        reason: "SANCTIONS_MATCH",
        gate: 7,
        detail: `Sanctioned country ${country} (KV list) for ip=${req.ip}`,
      };
    }
  }

  // ── B. Player name fuzzy match ────────────────────────────────────────────
  const playerName = req.playerName;
  if (!playerName || playerName.trim().length === 0) {
    // No name available (e.g., unauthenticated request) — skip name check.
    return { action: "pass", reason: "PASS", gate: 7 };
  }

  const tokens = tokeniseName(playerName);
  if (tokens.length === 0) {
    return { action: "pass", reason: "PASS", gate: 7 };
  }

  // Look up each token in parallel. Limit to first 5 tokens to bound latency.
  const lookupTokens = tokens.slice(0, 5);
  const kvKeys = lookupTokens.map((t) => `sanctions:name:${t}`);

  const results = await Promise.all(
    kvKeys.map((k) =>
      env.SANCTIONS_LIST.get(k, { type: "text", cacheTtl: 600 })
    ),
  );

  for (let i = 0; i < results.length; i++) {
    const match = results[i];
    if (match !== null) {
      // Name token hit — send to compliance review, not hard block.
      // The compliance team will confirm or dismiss.
      return {
        action: "review",
        reason: "SANCTIONS_MATCH",
        gate: 7,
        detail: `Name token "${lookupTokens[i]}" matched SDN/PEP entry "${match}" for player="${playerName}"`,
      };
    }
  }

  return { action: "pass", reason: "PASS", gate: 7 };
}

// ─── Admin helpers ────────────────────────────────────────────────────────────

/** Add a country to the dynamic sanctions list. */
export async function addSanctionedCountry(
  countryCode: string,
  env: Env,
): Promise<void> {
  await env.SANCTIONS_LIST.put(`sanctions:country:${countryCode.toUpperCase()}`, "1");
}

/** Index a name token into the KV sanctions name index. */
export async function indexSanctionedName(
  canonicalName: string,
  env: Env,
): Promise<void> {
  const tokens = tokeniseName(canonicalName);
  await Promise.all(
    tokens.map((t) =>
      env.SANCTIONS_LIST.put(`sanctions:name:${t}`, canonicalName)
    ),
  );
}
