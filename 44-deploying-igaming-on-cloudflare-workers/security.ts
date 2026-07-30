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
 * AcmeToCasino Platform - Security Middleware
 *
 * WAF-layer enforcement using Cloudflare-native signals:
 *   - Threat score screening    (cf.threat_score, range 0-100)
 *   - Bot detection             (cf.botManagement.score, range 1-99)
 *   - Geo-blocking              (cf.country — no external API needed)
 *   - In-memory rate limiting   (per-isolate Map — zero KV writes)
 *   - CORS enforcement
 *   - Security response headers (HSTS, CSP, X-Frame-Options…)
 *   - D1 security event logging via ctx.waitUntil
 *
 * Design principle: every check in this module uses data injected by
 * Cloudflare's network layer before the request reaches the Worker.
 * No external lookups, no additional latency.
 */

import { Env, getClientIP } from './utils.js';

// ─── Types ─────────────────────────────────────────────────────────────────

export interface SecurityCheckResult {
  allowed: boolean;
  response?: Response;
  threatType?: string;
}

interface RateEntry {
  count: number;
  reset: number;
}

// Extend globalThis for the per-isolate rate-limit map.
// Workers instances share no memory, so this is intentionally per-isolate.
declare global {
  // eslint-disable-next-line no-var
  var _rateMap: Map<string, RateEntry> | undefined;
}

// ─── Jurisdiction configuration ─────────────────────────────────────────────

/**
 * Structured jurisdiction table — single source of truth.
 * The BLOCKED_JURISDICTIONS Set used by the router is derived from this config;
 * the config is not derived from the Set.
 */
export interface JurisdictionRule {
  status: 'blocked' | 'restricted' | 'allowed';
  reason?: string;
  license?: string;
  requirements?: string[];
}

export const JURISDICTION_CONFIG: Record<string, JurisdictionRule> = {
  // ── Blocked ─────────────────────────────────────────────────────────────
  US: { status: 'blocked', reason: 'Federal Wire Act — state-by-state licensing required' },
  CN: { status: 'blocked', reason: 'All online gambling prohibited' },
  KP: { status: 'blocked', reason: 'OFAC sanctions' },
  IR: { status: 'blocked', reason: 'OFAC sanctions' },
  SY: { status: 'blocked', reason: 'OFAC sanctions' },
  CU: { status: 'blocked', reason: 'OFAC sanctions' },
  MM: { status: 'blocked', reason: 'Political exclusion — operator policy' },
  AF: { status: 'blocked', reason: 'Political exclusion — operator policy' },
  SA: { status: 'blocked', reason: 'Islamic law prohibition' },
  AE: { status: 'blocked', reason: 'Islamic law prohibition — federal level' },
  SG: { status: 'blocked', reason: 'Remote Gambling Act 2014' },
  IL: { status: 'blocked', reason: 'Prohibits online gambling operators' },

  // ── Restricted ──────────────────────────────────────────────────────────
  AU: {
    status: 'restricted',
    requirements: [
      'Australian Communications and Media Authority (ACMA) compliance',
      'No credit card deposits',
      'Australian Interactive Gambling Act obligations',
    ],
  },
  FR: {
    status: 'restricted',
    requirements: [
      'ANJ (Autorité Nationale des Jeux) licence required',
      'Player limits enforced by regulation',
      'ARJEL exclusion register integration',
    ],
  },
  NL: {
    status: 'restricted',
    requirements: [
      'KSA (Kansspelautoriteit) licence required',
      'CRUKS player cooling-off register integration mandatory',
      'Dutch residents require local entity',
    ],
  },
  SE: {
    status: 'restricted',
    requirements: [
      'Spelinspektionen licence required',
      'Spelpaus.se self-exclusion register integration mandatory',
      'SEK currency mandatory for Swedish residents',
    ],
  },
  IN: {
    status: 'restricted',
    requirements: [
      'State-by-state licensing — no federal framework',
      'UPI/net banking only',
      'FEMA restrictions on international remittances',
    ],
  },
  ZA: {
    status: 'restricted',
    requirements: [
      'National Gambling Board licence required',
      'FICA anti-money-laundering compliance',
    ],
  },

  // ── Allowed ─────────────────────────────────────────────────────────────
  GB: {
    status: 'allowed',
    license: 'UKGC',
    requirements: [
      'Responsible gambling tools mandatory (deposit limits, cool-off, self-exclusion)',
      'Source of funds verification above £2,000',
      'GAMSTOP national self-exclusion register integration',
    ],
  },
  MT: {
    status: 'allowed',
    license: 'MGA',
    requirements: ['Player fund segregation', 'MGA B2C licence required'],
  },
  BR: {
    status: 'allowed',
    license: 'SIGAP',
    requirements: ['BRL currency only', 'CPF (tax ID) verification mandatory', 'Pix preferred'],
  },
  DE: {
    status: 'allowed',
    license: 'GGL',
    requirements: [
      'Glücksspielbehörde (GGL) licence required',
      '€1,000/month deposit cap across all operators',
      '5-second cooldown between game rounds',
      'OASIS self-exclusion register integration',
    ],
  },
  CW: {
    status: 'allowed',
    license: 'Curacao eGaming',
    requirements: ['Curacao Master Licence or sub-licence', 'Basic KYC for deposits above €2,000'],
  },
  CY: {
    status: 'allowed',
    license: 'CySEC',
    requirements: ['Cyprus Securities and Exchange Commission licence', 'GDPR compliance'],
  },
  PT: {
    status: 'allowed',
    license: 'SRIJ',
    requirements: ['Serviço de Regulação e Inspeção de Jogos licence', 'EUR currency'],
  },
  ES: {
    status: 'allowed',
    license: 'DGOJ',
    requirements: [
      'Dirección General de Ordenación del Juego licence',
      'RGIAJ self-exclusion register integration',
    ],
  },
  IT: {
    status: 'allowed',
    license: 'ADM',
    requirements: [
      'Agenzia delle Dogane e dei Monopoli licence',
      'AAMS/ADM self-exclusion register',
    ],
  },
};

// O(1) lookup set derived from config — used in the hot request path
export const BLOCKED_JURISDICTIONS = new Set(
  Object.entries(JURISDICTION_CONFIG)
    .filter(([, v]) => v.status === 'blocked')
    .map(([k]) => k)
);

// Countries requiring enhanced due diligence at KYC level
export const HIGH_RISK_JURISDICTIONS = new Set([
  'RU', 'UA', 'BY', 'VN', 'ID', 'PK', 'NG', 'KE', 'VE',
]);

export function isBlockedJurisdiction(country: string): boolean {
  return BLOCKED_JURISDICTIONS.has(country);
}

export function isHighRisk(country: string): boolean {
  return HIGH_RISK_JURISDICTIONS.has(country);
}

export function getJurisdictionRule(country: string): JurisdictionRule | null {
  return JURISDICTION_CONFIG[country] ?? null;
}

// ─── Security header constants ──────────────────────────────────────────────

const SECURITY_HEADERS: Record<string, string> = {
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains; preload',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'X-XSS-Protection': '1; mode=block',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
  'Content-Security-Policy': [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",   // inline scripts needed for edge-rendered lobby
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "connect-src 'self' https://api.cloudflare.com",
    "frame-ancestors 'none'",
  ].join('; '),
};

/**
 * Applies security headers to an existing Response, returning a new Response
 * with the merged header set. Called at the tail of the main fetch handler.
 */
export function applySecurityHeaders(response: Response): Response {
  const headers = new Headers(response.headers);
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) {
    headers.set(k, v);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

// ─── Bot and threat detection ───────────────────────────────────────────────

interface CfProperties {
  threat_score?: number;
  country?: string;
  botManagement?: {
    score?: number;
    verifiedBot?: boolean;
    ja3Hash?: string;
  };
}

/**
 * Runs the security pipeline described in the chapter's request lifecycle
 * diagram (steps 4–6). Returns SecurityCheckResult so the caller can decide
 * whether to continue processing or return the blocked response.
 */
export function checkThreatAndBot(request: Request): SecurityCheckResult {
  const cf = (request as Request & { cf?: CfProperties }).cf;

  // Step 4: Cloudflare threat score > 50 = known malicious source
  const threatScore = cf?.threat_score ?? 0;
  if (threatScore > 50) {
    return {
      allowed: false,
      threatType: 'high_threat_score',
      response: new Response(JSON.stringify({ success: false, error: 'Access denied' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    };
  }

  // Step 5: Bot score < 20 AND not a verified search engine crawler
  const botScore = cf?.botManagement?.score ?? 100;
  const isVerifiedBot = cf?.botManagement?.verifiedBot ?? false;
  if (botScore < 20 && !isVerifiedBot) {
    return {
      allowed: false,
      threatType: 'bot_detected',
      response: new Response(JSON.stringify({ success: false, error: 'Access denied' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    };
  }

  return { allowed: true };
}

/**
 * Jurisdiction gate — step 6 in the request lifecycle.
 * Returns a 451 response for blocked countries; null for allowed or restricted.
 *
 * Compliance routes (/api/compliance) are exempted so the jurisdiction-check
 * endpoint works from blocked countries — allowing the frontend to display
 * a correct "not available in your region" message.
 */
export function checkJurisdiction(request: Request, pathname: string): SecurityCheckResult {
  const cf = (request as Request & { cf?: CfProperties }).cf;
  const country = cf?.country ?? 'unknown';

  // Exemption: compliance routes must work from anywhere
  if (pathname.startsWith('/api/compliance')) {
    return { allowed: true };
  }

  if (isBlockedJurisdiction(country)) {
    const rule = JURISDICTION_CONFIG[country];
    const body = JSON.stringify({
      success: false,
      error: 'This service is not available in your region.',
      country,
      reason: rule?.reason ?? 'Regulatory restriction',
    });
    return {
      allowed: false,
      threatType: 'blocked_jurisdiction',
      response: new Response(body, {
        status: 451,
        headers: { 'Content-Type': 'application/json' },
      }),
    };
  }

  return { allowed: true };
}

// ─── In-memory rate limiting ────────────────────────────────────────────────

/**
 * Per-isolate in-memory rate limiter.
 *
 * Uses globalThis._rateMap instead of KV to avoid consuming free-tier KV write
 * quota (1,000 writes/day). The trade-off is per-isolate tracking only — no
 * cross-isolate consistency. This is acceptable for abuse prevention; distributed
 * attacks should be handled by Cloudflare WAF rules.
 *
 * Limit: 200 requests per 60-second window per IP address.
 */
export function checkRateLimit(request: Request): SecurityCheckResult {
  const ip = getClientIP(request);

  if (!globalThis._rateMap) {
    globalThis._rateMap = new Map<string, RateEntry>();
  }

  const now = Date.now();
  const entry = globalThis._rateMap.get(ip) ?? { count: 0, reset: now + 60_000 };

  // Reset window if expired
  if (now > entry.reset) {
    entry.count = 0;
    entry.reset = now + 60_000;
  }

  entry.count++;
  globalThis._rateMap.set(ip, entry);

  // Periodic cleanup — prevent unbounded map growth in long-lived isolates
  if (globalThis._rateMap.size > 1_000) {
    for (const [k, v] of globalThis._rateMap) {
      if (now > v.reset) globalThis._rateMap.delete(k);
    }
  }

  if (entry.count > 200) {
    return {
      allowed: false,
      threatType: 'rate_limit_exceeded',
      response: new Response(
        JSON.stringify({ success: false, error: 'Rate limit exceeded. Retry after 60 seconds.' }),
        {
          status: 429,
          headers: {
            'Content-Type': 'application/json',
            'Retry-After': '60',
          },
        }
      ),
    };
  }

  return { allowed: true };
}

// ─── Security event logging ─────────────────────────────────────────────────

/**
 * Writes a security event to the D1 security_events table.
 * Must be called via ctx.waitUntil so the insert does not block the response.
 *
 * Severity mapping:
 *   1 = informational (rate limit, bot block)
 *   2 = warning (geo block, threat score)
 *   3 = critical (unhandled error, signature mismatch)
 */
export async function logSecurityEvent(
  request: Request,
  eventType: string,
  env: Env,
  severity = 1
): Promise<void> {
  try {
    const cf = (request as Request & { cf?: CfProperties }).cf;
    const details = JSON.stringify({
      url: request.url,
      method: request.method,
      country: cf?.country ?? 'unknown',
      threatScore: cf?.threat_score ?? 0,
      botScore: cf?.botManagement?.score ?? null,
      ja3Hash: cf?.botManagement?.ja3Hash ?? null,
      userAgent: request.headers.get('User-Agent'),
      cfRay: request.headers.get('CF-RAY'),
    });

    await env.DB.prepare(
      'INSERT INTO security_events (ip, event_type, details, severity) VALUES (?, ?, ?, ?)'
    )
      .bind(getClientIP(request), eventType, details, severity)
      .run();
  } catch (err) {
    // Non-fatal — logging must never degrade the critical response path
    console.error('Failed to log security event:', err);
  }
}

// ─── CORS ───────────────────────────────────────────────────────────────────

const CORS_HEADERS: Record<string, string> = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With',
  'Access-Control-Max-Age': '86400',
};

export function handleCORSPreflight(): Response {
  return new Response(null, { status: 200, headers: CORS_HEADERS });
}
