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
 * registries.ts
 * -------------
 * National gambling self-exclusion registry connectors.
 *
 * Each registry exposes a single check(playerId, jurisdiction) call.
 * Results are cached in KV for 15 minutes to avoid hammering external APIs
 * and to stay within free-tier KV read limits.
 *
 * Registries implemented (as documented stubs):
 *   - GamStop   — United Kingdom (UKGC)
 *   - Spelpaus  — Sweden (Spelinspektionen)
 *   - ROFUS     — Denmark (Spillemyndigheden)
 *   - Brazil    — Pending: SIGAE stub (Brazil regulation, 2024)
 */

export type Jurisdiction = "GB" | "SE" | "DK" | "BR" | string;

export interface ExclusionCheckResult {
  registry: string;
  jurisdiction: Jurisdiction;
  isExcluded: boolean;
  expiresAt?: string;    // ISO date if exclusion has an end date
  checkedAt: string;
  source: "LIVE" | "CACHE" | "STUB";
  errorMessage?: string;
}

// ---------------------------------------------------------------------------
// GamStop (UK)
// ---------------------------------------------------------------------------

/**
 * GamStop — UK national self-exclusion scheme.
 * Production: POST https://api.gamstop.co.uk/v1/exclusions/check
 * Required header: X-API-KEY: <operator-key>
 *
 * This stub returns excluded=false unless the playerId contains "excluded".
 */
export async function checkGamStop(
  playerId: string,
  _apiKey?: string,
): Promise<ExclusionCheckResult> {
  // Stub — replace with real fetch in production:
  // const res = await fetch("https://api.gamstop.co.uk/v1/exclusions/check", {
  //   method: "POST",
  //   headers: { "Content-Type": "application/json", "X-API-KEY": apiKey ?? "" },
  //   body: JSON.stringify({ playerId }),
  // });
  // const data = await res.json();
  // return { registry: "GamStop", jurisdiction: "GB", isExcluded: data.selfExcluded, ... };

  const isExcluded = playerId.toLowerCase().includes("excluded");

  return {
    registry: "GamStop",
    jurisdiction: "GB",
    isExcluded,
    checkedAt: new Date().toISOString(),
    source: "STUB",
  };
}

// ---------------------------------------------------------------------------
// Spelpaus (Sweden)
// ---------------------------------------------------------------------------

/**
 * Spelpaus — Swedish national self-exclusion register.
 * Production: GET https://api.spelpaus.se/v1/check?pid=<personnummer>
 * Required header: Authorization: Bearer <token>
 *
 * Uses Swedish personnummer (national ID) as the player identifier.
 */
export async function checkSpelpaus(
  playerId: string,
  _apiToken?: string,
): Promise<ExclusionCheckResult> {
  const isExcluded = playerId.toLowerCase().includes("excluded");

  return {
    registry: "Spelpaus",
    jurisdiction: "SE",
    isExcluded,
    checkedAt: new Date().toISOString(),
    source: "STUB",
  };
}

// ---------------------------------------------------------------------------
// ROFUS (Denmark)
// ---------------------------------------------------------------------------

/**
 * ROFUS — Register Over Frivilligt Udelukkede Spillere (Denmark).
 * Production: POST https://rofus.nu/api/v2/check
 * Required: operator license number + CPR (Danish national ID)
 */
export async function checkRofus(
  playerId: string,
  _licenseNumber?: string,
): Promise<ExclusionCheckResult> {
  const isExcluded = playerId.toLowerCase().includes("excluded");

  return {
    registry: "ROFUS",
    jurisdiction: "DK",
    isExcluded,
    checkedAt: new Date().toISOString(),
    source: "STUB",
  };
}

// ---------------------------------------------------------------------------
// Brazil SIGAE (stub — regulation pending full implementation)
// ---------------------------------------------------------------------------

/**
 * Brazil SIGAE — Sistema de Gestão de Agentes de Apostas Esportivas.
 * The self-exclusion API was pending public documentation as of Q1 2025.
 * This stub always returns not-excluded until the official endpoint is published.
 *
 * Monitor: https://www.gov.br/seae/pt-br/apostas-esportivas
 */
export async function checkBrazil(
  _playerId: string,
  _apiKey?: string,
): Promise<ExclusionCheckResult> {
  return {
    registry: "SIGAE-BR",
    jurisdiction: "BR",
    isExcluded: false,
    checkedAt: new Date().toISOString(),
    source: "STUB",
    errorMessage: "Brazil SIGAE self-exclusion API not yet publicly available",
  };
}

// ---------------------------------------------------------------------------
// Registry map — maps jurisdiction to check function
// ---------------------------------------------------------------------------

export type RegistryCheckFn = (
  playerId: string,
  credential?: string,
) => Promise<ExclusionCheckResult>;

export const REGISTRY_MAP: Record<Jurisdiction, RegistryCheckFn | undefined> = {
  GB: checkGamStop,
  SE: checkSpelpaus,
  DK: checkRofus,
  BR: checkBrazil,
};

/**
 * Run the appropriate registry check for a given jurisdiction.
 * Falls back to a "not implemented" result for unknown jurisdictions.
 */
export async function checkRegistry(
  playerId: string,
  jurisdiction: Jurisdiction,
  credential?: string,
): Promise<ExclusionCheckResult> {
  const checkFn = REGISTRY_MAP[jurisdiction];

  if (!checkFn) {
    return {
      registry: "UNKNOWN",
      jurisdiction,
      isExcluded: false,
      checkedAt: new Date().toISOString(),
      source: "STUB",
      errorMessage: `No registry connector implemented for jurisdiction: ${jurisdiction}`,
    };
  }

  return checkFn(playerId, credential);
}
