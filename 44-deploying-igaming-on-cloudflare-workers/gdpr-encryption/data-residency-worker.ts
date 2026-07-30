// Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Jurisdiction-aware data routing for GDPR/UKGC/DGE compliance.
 *
 * Routes player data processing to the correct Cloudflare region based on
 * the player's jurisdiction, using Cloudflare's D1 location hints.
 *
 * GDPR Art.44-49: Restrictions on transfers of personal data to third countries.
 * EU player data must remain in EU datacenters (or countries with adequacy decisions).
 *
 * UK GDPR Art.44-49: Post-Brexit equivalent. UK has separate adequacy decisions.
 * EEA → UK transfer: adequacy decision in place (as of 2024).
 * UK → EEA transfer: UK adequacy regulations in place.
 *
 * LGPD (Brazil) Art.33-36: Cross-border transfer restrictions.
 * Brazil player data can be transferred to countries with adequate protection
 * or under standard contractual clauses (SCCs).
 *
 * DGE (New Jersey Division of Gaming Enforcement): player data must remain
 * in US-regulated infrastructure per N.J.A.C. 13:69O-1.
 *
 * Implementation strategy:
 * We cannot dynamically switch D1 database bindings at runtime — bindings
 * are resolved at Worker deploy time. Instead, we use:
 *
 *   1. Separate Workers per region (EU Worker, UK Worker, US Worker)
 *      each bound to a region-specific D1 database
 *   2. Cloudflare Smart Routing to direct players to the correct Worker
 *      based on cf.country at the edge
 *   3. D1 location_hint at database creation time:
 *      wrangler d1 create acmetocasino-eu-db --location=weur
 *      wrangler d1 create acmetocasino-uk-db --location=weur  (UK/EU overlap)
 *      wrangler d1 create acmetocasino-us-db --location=enam
 *
 * For single-Worker deployments (simpler architecture), use the
 * jurisdiction_hint pattern in wrangler.toml:
 *
 *   [durable_objects.bindings]
 *   # Or set D1 location via wrangler.toml jurisdiction field:
 *   [[d1_databases]]
 *   binding       = "DB"
 *   database_name = "acmetocasino-eu-db"
 *   database_id   = "<id>"
 *   # This restricts all D1 queries to EU datacenters:
 *   # (jurisdiction is set at database creation time, not in wrangler.toml)
 *
 * Cloudflare-specific note on Jurisdiction field:
 *   Adding `jurisdiction = "eu"` to wrangler.toml restricts the Worker itself
 *   to EU datacenters for processing. This is the strongest GDPR control
 *   available at the Workers layer. Set in [env.production] block.
 */

// Cloudflare D1 location hints (set at database creation time)
export const D1_LOCATIONS = {
  EU:   'weur',    // Western Europe — Amsterdam, Paris, Frankfurt
  EEU:  'eeur',    // Eastern Europe — Warsaw, Vienna
  UK:   'weur',    // UK maps to WEUR (nearest EU region post-Brexit)
  US:   'enam',    // Eastern North America — Ashburn, Chicago
  WNAM: 'wnam',    // Western North America — Los Angeles, Seattle
  APAC: 'apac',    // Asia-Pacific — Singapore, Tokyo
  LATAM:'enam',    // Latin America defaults to ENAM until LATAM region available
} as const;

// Country → jurisdiction mapping
// Determines which data residency rules apply
export type JurisdictionZone = 'EU' | 'UK' | 'EEA' | 'US_NJ' | 'BRAZIL' | 'OTHER';

const EU_MEMBER_STATES = new Set([
  'AT','BE','BG','CY','CZ','DE','DK','EE','ES','FI','FR','GR','HR',
  'HU','IE','IT','LT','LU','LV','MT','NL','PL','PT','RO','SE','SI','SK',
]);

const EEA_COUNTRIES = new Set([...EU_MEMBER_STATES, 'IS', 'LI', 'NO']);

export function getJurisdictionZone(countryCode: string): JurisdictionZone {
  if (countryCode === 'GB') return 'UK';
  if (countryCode === 'BR') return 'BRAZIL';
  if (countryCode === 'US') return 'US_NJ';  // Simplified — real impl checks state
  if (EEA_COUNTRIES.has(countryCode)) return 'EU';
  return 'OTHER';
}

/**
 * Determine the appropriate D1 location hint for a given country.
 * Used when creating player-specific databases or when logging data placement.
 */
export function getD1LocationForCountry(countryCode: string): string {
  const zone = getJurisdictionZone(countryCode);
  switch (zone) {
    case 'EU':
      return D1_LOCATIONS.EU;
    case 'UK':
      return D1_LOCATIONS.UK;    // WEUR — UK data in Western Europe
    case 'US_NJ':
      return D1_LOCATIONS.US;
    case 'BRAZIL':
      return D1_LOCATIONS.LATAM; // Brazil: ENAM until CF opens LATAM region
    default:
      return D1_LOCATIONS.EU;    // Default to EU for strictest protection
  }
}

/**
 * Compliance header set for data residency audit trails.
 * Injected into API responses so auditors can verify data placement.
 */
export interface DataResidencyHeaders {
  'X-Data-Jurisdiction': JurisdictionZone;
  'X-Data-Location': string;
  'X-Processing-Region': string;
  'X-Gdpr-Basis': string;
}

export function buildDataResidencyHeaders(
  request: Request,
  zone: JurisdictionZone
): DataResidencyHeaders {
  const cf = (request as Request & { cf?: { colo?: string; country?: string } }).cf;

  const gdprBasis: Record<JurisdictionZone, string> = {
    EU:     'GDPR Art.6(1)(b) — contract performance; Art.6(1)(c) — legal obligation',
    UK:     'UK GDPR Art.6(1)(b) — contract performance',
    EEA:    'GDPR Art.6(1)(b) — contract performance',
    US_NJ:  'NJ Gaming Enabling Act, N.J.A.C. 13:69O-1',
    BRAZIL: 'LGPD Art.7(V) — contract performance; Art.7(IX) — legitimate interest',
    OTHER:  'Contractual necessity',
  };

  return {
    'X-Data-Jurisdiction':  zone,
    'X-Data-Location':      getD1LocationForCountry(cf?.country ?? ''),
    'X-Processing-Region':  cf?.colo ?? 'unknown',
    'X-Gdpr-Basis':         gdprBasis[zone],
  };
}

/**
 * HMAC-based deterministic search token for encrypted PII columns.
 *
 * When email is stored encrypted (non-deterministic AES-GCM), we cannot
 * do WHERE email = ?. Instead, we store a stable HMAC alongside the
 * ciphertext and search by HMAC.
 *
 * This HMAC_KEY must be:
 *   1. Stored as a Workers Secret (separate from ENCRYPTION_KEY)
 *   2. Never rotated (rotating means re-computing all tokens)
 *   3. Not used for anything else
 *
 * The search token is a one-way hash — it reveals nothing about the
 * email itself. Two identical emails produce the same token (deterministic
 * HMAC with a stable key). This allows WHERE email_hash = ? lookups.
 *
 * Set the key with:
 *   npx wrangler secret put HMAC_KEY
 *   # Enter a base64-encoded 32-byte value
 *
 * @param value   - The plaintext value to hash (e.g. 'john@example.com')
 * @param hmacKey - The HMAC key from Workers Secrets (env.HMAC_KEY)
 */
export async function computeSearchToken(value: string, hmacKey: string): Promise<string> {
  const rawKey = Uint8Array.from(atob(hmacKey), c => c.charCodeAt(0));
  const key = await crypto.subtle.importKey(
    'raw',
    rawKey,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );

  const encoded = new TextEncoder().encode(value.toLowerCase().trim());
  const hash = await crypto.subtle.sign('HMAC', key, encoded);

  return btoa(String.fromCharCode(...new Uint8Array(hash)));
}

/**
 * Check whether a cross-border data transfer is permitted under GDPR Art.44-49.
 *
 * Returns the legal mechanism that authorises the transfer, or throws
 * if no valid mechanism exists.
 *
 * EU adequacy decisions as of 2024:
 *   Andorra, Argentina, Canada (commercial orgs), Faroe Islands, Guernsey,
 *   Israel, Isle of Man, Japan, Jersey, New Zealand, Republic of Korea,
 *   Switzerland, UK, Uruguay, US (EU-US Data Privacy Framework).
 *
 * UK adequacy decisions as of 2024:
 *   EEA countries, Andorra, Argentina, Canada, Faroe Islands, Guernsey,
 *   Israel, Isle of Man, Japan, Jersey, New Zealand, Switzerland, Uruguay.
 */
export interface TransferMechanism {
  permitted: boolean;
  mechanism?: string;
  article?: string;
}

const EU_ADEQUACY_COUNTRIES = new Set([
  'AD','AR','CA','FO','GG','IL','IM','JP','JE','NZ','KR',
  'CH','GB','UY','US',  // US via EU-US DPF (2023)
  ...EEA_COUNTRIES,
]);

export function checkTransferPermitted(
  sourceJurisdiction: 'EU' | 'UK',
  destinationCountry: string
): TransferMechanism {
  if (sourceJurisdiction === 'EU') {
    // Transfer within EEA — Art.44 does not apply (no "transfer to third country")
    if (EEA_COUNTRIES.has(destinationCountry)) {
      return {
        permitted: true,
        mechanism: 'Intra-EEA — GDPR applies in destination country',
        article: 'N/A',
      };
    }
    // Adequacy decision
    if (EU_ADEQUACY_COUNTRIES.has(destinationCountry)) {
      return {
        permitted: true,
        mechanism: 'Adequacy decision (European Commission)',
        article: 'GDPR Art.45',
      };
    }
    // For other countries, SCCs are the standard fallback
    return {
      permitted: true,  // Technically permitted with SCCs — operator must execute them
      mechanism: 'Standard Contractual Clauses (SCCs) required',
      article: 'GDPR Art.46(2)(c)',
    };
  }

  // UK source
  if (destinationCountry === 'GB' || EEA_COUNTRIES.has(destinationCountry)) {
    return {
      permitted: true,
      mechanism: 'UK adequacy regulations / EEA adequacy',
      article: 'UK GDPR Art.45',
    };
  }

  return {
    permitted: true,
    mechanism: 'UK International Data Transfer Agreement (IDTA) required',
    article: 'UK GDPR Art.46',
  };
}
