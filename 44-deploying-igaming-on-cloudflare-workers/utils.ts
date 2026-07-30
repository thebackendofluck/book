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
 * AcmeToCasino Platform - Shared Utilities
 * CORS, error handling, request/response helpers
 */

export interface Env {
  DB: D1Database;
  CACHE: KVNamespace;
  SESSIONS: KVNamespace;
  STORAGE: R2Bucket;
  JWT_SECRET: string;
  ENCRYPTION_KEY: string;
  CF_API_TOKEN: string;
  CF_ZONE_ID: string;
  CF_ACCESS_AUDIENCE: string;
  ENVIRONMENT: string;
  PLATFORM_NAME: string;
  // Game provider API keys
  NETENT_API_KEY: string;
  MICROGAMING_API_KEY: string;
  PLAYNG0_API_KEY: string;
  EVOLUTION_API_KEY: string;
  PRAGMATIC_API_KEY: string;
  // Payment processor keys
  PAYMENT_PROCESSOR_KEY: string;
  // Remote HSM proxy API (ops-host — see remote-hsm/)
  // Set via: npx wrangler secret put HSM_API_URL
  //          npx wrangler secret put HSM_API_KEY
  HSM_API_URL: string;   // https://hsm-api.acmetocasino.com
  HSM_API_KEY: string;   // 32-byte hex key, matches HSM_API_KEY on ops-host
}

export interface ApiResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  errors?: string[];
}

// ─── CORS ──────────────────────────────────────────────────────────────────

const CORS_HEADERS: Record<string, string> = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With',
  'Access-Control-Max-Age': '86400',
};

export function handleCORS(): Response {
  return new Response(null, { status: 200, headers: CORS_HEADERS });
}

export function addCORSHeaders(response: Response): Response {
  const newHeaders = new Headers(response.headers);
  for (const [key, value] of Object.entries(CORS_HEADERS)) {
    newHeaders.set(key, value);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: newHeaders,
  });
}

// ─── JSON Response Helpers ─────────────────────────────────────────────────

export function jsonResponse<T>(data: ApiResponse<T>, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS_HEADERS },
  });
}

export function successResponse<T>(data: T, status = 200): Response {
  return jsonResponse<T>({ success: true, data }, status);
}

export function errorResponse(message: string, status = 400): Response {
  return jsonResponse({ success: false, error: message }, status);
}

export function validationErrorResponse(errors: string[]): Response {
  return jsonResponse({ success: false, errors }, 422);
}

export function notFoundResponse(resource = 'Resource'): Response {
  return errorResponse(`${resource} not found`, 404);
}

export function unauthorizedResponse(): Response {
  return errorResponse('Unauthorized', 401);
}

export function internalErrorResponse(env: Env, message?: string): Response {
  const msg =
    env.ENVIRONMENT === 'development' && message ? message : 'Internal server error';
  return jsonResponse({ success: false, error: msg }, 500);
}

// ─── Rate Limiting ─────────────────────────────────────────────────────────

export class RateLimiter {
  private kv: KVNamespace;

  constructor(kv: KVNamespace) {
    this.kv = kv;
  }

  async check(
    key: string,
    limit: number,
    windowSecs: number
  ): Promise<{ allowed: boolean; remaining: number; retryAfter: number }> {
    const kvKey = `ratelimit:${key}`;
    const raw = await this.kv.get(kvKey);
    const count = raw ? parseInt(raw, 10) : 0;

    if (count >= limit) {
      return { allowed: false, remaining: 0, retryAfter: windowSecs };
    }

    await this.kv.put(kvKey, String(count + 1), { expirationTtl: windowSecs });
    return { allowed: true, remaining: limit - count - 1, retryAfter: 0 };
  }
}

// ─── JWT ───────────────────────────────────────────────────────────────────

export interface JWTPayload {
  userId: number;
  email: string;
  role: string;
  exp: number;
  iat: number;
  iss: string;
}

export async function signJWT(payload: JWTPayload, secret: string): Promise<string> {
  const encoder = new TextEncoder();
  const header = base64UrlEncode(encoder.encode(JSON.stringify({ alg: 'HS256', typ: 'JWT' })));
  const body = base64UrlEncode(encoder.encode(JSON.stringify(payload)));
  const signingInput = `${header}.${body}`;

  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );

  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(signingInput));
  return `${signingInput}.${base64UrlEncode(new Uint8Array(signature))}`;
}

export async function verifyJWT(token: string, secret: string): Promise<JWTPayload | null> {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;

    const [header, body, sig] = parts;
    const signingInput = `${header}.${body}`;

    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify']
    );

    const sigBytes = base64UrlDecode(sig);
    const valid = await crypto.subtle.verify(
      'HMAC',
      key,
      sigBytes,
      encoder.encode(signingInput)
    );

    if (!valid) return null;

    const payload: JWTPayload = JSON.parse(
      new TextDecoder().decode(base64UrlDecode(body))
    );

    if (payload.exp < Math.floor(Date.now() / 1000)) return null;

    return payload;
  } catch {
    return null;
  }
}

// ─── Base64 URL ────────────────────────────────────────────────────────────

export function base64UrlEncode(data: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < data.byteLength; i++) {
    binary += String.fromCharCode(data[i]);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

export function base64UrlDecode(str: string): Uint8Array {
  const padded = str.replace(/-/g, '+').replace(/_/g, '/');
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// ─── Crypto helpers ────────────────────────────────────────────────────────

export async function hashPassword(password: string): Promise<string> {
  const encoder = new TextEncoder();
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    encoder.encode(password),
    'PBKDF2',
    false,
    ['deriveBits']
  );
  const derived = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt, iterations: 600000, hash: 'SHA-256' },
    keyMaterial,
    256
  );
  const combined = new Uint8Array(salt.length + derived.byteLength);
  combined.set(salt, 0);
  combined.set(new Uint8Array(derived), salt.length);
  return base64UrlEncode(combined);
}

export async function verifyPassword(password: string, hash: string): Promise<boolean> {
  try {
    const combined = base64UrlDecode(hash);
    const salt = combined.slice(0, 16);
    const storedDerived = combined.slice(16);

    const encoder = new TextEncoder();
    const keyMaterial = await crypto.subtle.importKey(
      'raw',
      encoder.encode(password),
      'PBKDF2',
      false,
      ['deriveBits']
    );
    const derived = await crypto.subtle.deriveBits(
      { name: 'PBKDF2', salt, iterations: 600000, hash: 'SHA-256' },
      keyMaterial,
      256
    );

    const newDerived = new Uint8Array(derived);
    if (newDerived.length !== storedDerived.length) return false;

    let diff = 0;
    for (let i = 0; i < newDerived.length; i++) {
      diff |= newDerived[i] ^ storedDerived[i];
    }
    return diff === 0;
  } catch {
    return false;
  }
}

// ─── IP / Geo helpers ──────────────────────────────────────────────────────

export function getClientIP(request: Request): string {
  return request.headers.get('CF-Connecting-IP') ?? 'unknown';
}

export function getCountry(request: Request): string {
  return (request as Request & { cf?: { country?: string } }).cf?.country ?? 'unknown';
}

// ─── Request parsing ───────────────────────────────────────────────────────

export async function parseJSON<T>(request: Request): Promise<T | null> {
  try {
    return (await request.json()) as T;
  } catch {
    return null;
  }
}
