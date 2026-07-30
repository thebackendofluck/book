// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Brazilian Betting Platform — API Gateway Worker
 *
 * Entry point for all client traffic on *.acmetocasino.bet.br.
 * Responsibilities:
 *  1. Enforce Brazil-only geolocation (CF-Country: BR)
 *  2. CORS preflight handling
 *  3. JWT validation on protected routes
 *  4. Per-CPF rate limiting backed by KV (RATE_LIMITS namespace)
 *  5. Structured request logging (ctx.waitUntil, non-blocking)
 *  6. Reverse-proxy routing to downstream service Workers
 *
 * No business logic lives here — only the security and dispatch pipeline.
 */

import type { Env, BrazilRequest, JWTPayload, RateLimitResult } from './types.js';
import { forwardToCore } from './utils/origin.js';

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const req = request as BrazilRequest;
    const url = new URL(req.url);
    const requestId = generateRequestId();

    // ── 1. OPTIONS preflight ─────────────────────────────────────────────────
    if (req.method === 'OPTIONS') {
      return corsPreflightResponse();
    }

    // ── 2. Root service info (no auth, no geo check) ─────────────────────────
    if (url.pathname === '/') {
      return new Response(
        JSON.stringify({
          service: 'api-gateway',
          status: 'operational',
          version: '1.0.0',
          platform: 'AcmetoCasino',
          domain: 'cloud-acmetocasino.com',
          environment: env.ENVIRONMENT ?? 'production',
          endpoints: ['/health', '/api/auth/login', '/api/auth/register', '/api/bets', '/api/wallet', '/api/session', '/api/kyc'],
          documentation: 'https://thebackendofluck.com',
        }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Request-Id': requestId,
            ...corsHeaders(),
          },
        }
      );
    }

    // ── 3. Health check (no auth, no geo check) ──────────────────────────────
    if (url.pathname === '/health') {
      return new Response(
        JSON.stringify({ status: 'ok', region: req.cf?.colo ?? 'unknown', requestId }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Request-Id': requestId,
            ...corsHeaders(),
          },
        }
      );
    }

    // ── 3. Brazil-only geolocation enforcement ───────────────────────────────
    const country = req.cf?.country ?? 'unknown';
    if (country !== 'BR') {
      ctx.waitUntil(logEvent(env, {
        type:      'geo_block',
        requestId,
        url:       req.url,
        country,
        ip:        getClientIP(req),
        timestamp: new Date().toISOString(),
      }));
      return errorResponse('Service not available in your region.', 451, requestId);
    }

    // ── 4. Threat score check (Cloudflare Bot Management) ────────────────────
    const threatScore = req.cf?.threatScore ?? 0;
    if (threatScore > 50) {
      ctx.waitUntil(logEvent(env, {
        type:       'threat_block',
        requestId,
        threatScore,
        ip:         getClientIP(req),
        timestamp:  new Date().toISOString(),
      }));
      return errorResponse('Request blocked.', 403, requestId);
    }

    const botScore = req.cf?.botManagement?.score ?? 100;
    const isVerifiedBot = req.cf?.botManagement?.verifiedBot ?? false;
    if (botScore < 20 && !isVerifiedBot) {
      return errorResponse('Automated requests not permitted.', 403, requestId);
    }

    // ── 5. Route dispatch ─────────────────────────────────────────────────────
    try {
      let response: Response;

      if (url.pathname.startsWith('/api/pix')) {
        response = await routeToService(env.PIX_WEBHOOK_SVC, req, requestId);
      } else if (url.pathname.startsWith('/api/odds')) {
        response = await routeToService(env.ODDS_FEED_SVC, req, requestId);
      } else if (url.pathname.startsWith('/api/auth')) {
        response = await handleAuth(req, env, requestId);
      } else if (url.pathname.startsWith('/api/')) {
        // All other /api/* routes require JWT authentication + rate limiting
        const authResult = await validateJWT(req, env);
        if (!authResult.valid) {
          return errorResponse('Unauthorized.', 401, requestId);
        }

        const rl = await checkRateLimit(env, authResult.payload!.cpf, 120, 60);
        if (!rl.allowed) {
          return rateLimitResponse(rl, requestId);
        }

        response = await dispatchApiRoute(req, env, authResult.payload!, requestId);
      } else {
        response = errorResponse('Not found.', 404, requestId);
      }

      // Attach request ID and CORS headers to every response
      return addResponseHeaders(response, requestId);
    } catch (err) {
      ctx.waitUntil(logEvent(env, {
        type:      'internal_error',
        requestId,
        error:     err instanceof Error ? err.message : String(err),
        url:       req.url,
        timestamp: new Date().toISOString(),
      }));
      return errorResponse('Internal server error.', 500, requestId);
    }
  },
};

// ── Route dispatch ────────────────────────────────────────────────────────────

async function dispatchApiRoute(
  req: BrazilRequest,
  env: Env,
  player: JWTPayload,
  requestId: string
): Promise<Response> {
  const url = new URL(req.url);

  if (url.pathname.startsWith('/api/bets')) {
    return handleBets(req, env, player, requestId);
  }
  if (url.pathname.startsWith('/api/wallet')) {
    return handleWallet(req, env, player, requestId);
  }
  if (url.pathname.startsWith('/api/session')) {
    return handleSession(req, env, player, requestId);
  }
  if (url.pathname.startsWith('/api/kyc')) {
    return handleKyc(req, env, player, requestId);
  }

  return errorResponse('Route not found.', 404, requestId);
}

// ── Auth (unauthenticated routes) ─────────────────────────────────────────────

async function handleAuth(
  req: BrazilRequest,
  env: Env,
  requestId: string
): Promise<Response> {
  const url  = new URL(req.url);
  const body = await parseJSON<Record<string, unknown>>(req);

  if (!body) {
    return errorResponse('Invalid JSON body.', 400, requestId);
  }

  if (url.pathname === '/api/auth/login') {
    return handleLogin(body, env, req, requestId);
  }
  if (url.pathname === '/api/auth/register') {
    return handleRegister(body, env, req, requestId);
  }
  if (url.pathname === '/api/auth/refresh') {
    return handleTokenRefresh(body, env, requestId);
  }

  return errorResponse('Unknown auth route.', 404, requestId);
}

async function handleLogin(
  body: Record<string, unknown>,
  env: Env,
  req: BrazilRequest,
  requestId: string
): Promise<Response> {
  const cpf      = typeof body.cpf === 'string' ? body.cpf.replace(/\D/g, '') : '';
  const password = typeof body.password === 'string' ? body.password : '';

  if (!cpf || !password) {
    return errorResponse('CPF e senha são obrigatórios.', 400, requestId);
  }

  // Per-CPF login brute-force limit: 5 attempts per 15 minutes
  const rl = await checkRateLimit(env, `login:${cpf}`, 5, 900);
  if (!rl.allowed) {
    return rateLimitResponse(rl, requestId);
  }

  const player = await env.DB.prepare(
    'SELECT id, cpf, email, password_hash, status FROM players WHERE cpf = ?'
  ).bind(cpf).first<{
    id: string; cpf: string; email: string; password_hash: string; status: string;
  }>();

  if (!player) {
    return errorResponse('CPF ou senha inválidos.', 401, requestId);
  }

  if (player.status !== 'active') {
    return errorResponse('Conta suspensa ou bloqueada.', 403, requestId);
  }

  const passwordValid = await verifyPassword(password, player.password_hash, env.ENCRYPTION_KEY);
  if (!passwordValid) {
    return errorResponse('CPF ou senha inválidos.', 401, requestId);
  }

  const token = await signJWT(
    {
      sub:   player.id,
      cpf:   player.cpf,
      email: player.email,
      role:  'player',
      exp:   Math.floor(Date.now() / 1000) + 3600,
      iat:   Math.floor(Date.now() / 1000),
      iss:   env.JWT_ISSUER,
      colo:  req.cf?.colo,
    },
    env.JWT_SECRET
  );

  // Persist session in KV for server-side revocation capability
  await env.PLAYER_SESSIONS.put(
    `session:${player.id}`,
    JSON.stringify({ playerId: player.id, issuedAt: Date.now() }),
    { expirationTtl: 3600 }
  );

  return jsonResponse({ token, playerId: player.id }, 200, requestId);
}

async function handleRegister(
  body: Record<string, unknown>,
  env: Env,
  _req: BrazilRequest,
  requestId: string
): Promise<Response> {
  const cpf      = typeof body.cpf === 'string' ? body.cpf.replace(/\D/g, '') : '';
  const email    = typeof body.email === 'string' ? body.email : '';
  const password = typeof body.password === 'string' ? body.password : '';
  const fullName = typeof body.fullName === 'string' ? body.fullName : '';

  if (!cpf || !email || !password || !fullName) {
    return errorResponse('Todos os campos são obrigatórios.', 400, requestId);
  }

  // Inline CPF validation (11 digits + mod-11) to avoid worker import cost
  if (!isValidCpf(cpf)) {
    return errorResponse('CPF inválido.', 422, requestId);
  }

  const existing = await env.DB.prepare(
    'SELECT id FROM players WHERE cpf = ? OR email = ?'
  ).bind(cpf, email).first();

  if (existing) {
    return errorResponse('CPF ou e-mail já cadastrado.', 409, requestId);
  }

  const playerId    = generateUUID();
  const passwordHash = await hashPassword(password, env.ENCRYPTION_KEY);

  await env.DB.prepare(
    `INSERT INTO players (id, cpf, email, full_name, password_hash, status, kyc_status, created_at)
     VALUES (?, ?, ?, ?, ?, 'active', 'pending', ?)`
  ).bind(playerId, cpf, email, fullName, passwordHash, new Date().toISOString()).run();

  return jsonResponse({ playerId }, 201, requestId);
}

async function handleTokenRefresh(
  body: Record<string, unknown>,
  env: Env,
  requestId: string
): Promise<Response> {
  const oldToken = typeof body.token === 'string' ? body.token : '';
  if (!oldToken) {
    return errorResponse('Token is required.', 400, requestId);
  }

  const payload = await verifyJWT(oldToken, env.JWT_SECRET);
  if (!payload) {
    return errorResponse('Token inválido ou expirado.', 401, requestId);
  }

  // Honor server-side session revocation: a valid-but-revoked token must not
  // be exchangeable for a fresh one (mirrors validateJWT).
  const session = await env.PLAYER_SESSIONS.get(`session:${payload.sub}`);
  if (!session) {
    return errorResponse('Sessão revogada. Faça login novamente.', 401, requestId);
  }

  const newToken = await signJWT(
    { ...payload, exp: Math.floor(Date.now() / 1000) + 3600, iat: Math.floor(Date.now() / 1000) },
    env.JWT_SECRET
  );

  return jsonResponse({ token: newToken }, 200, requestId);
}

// ── Bets handler ──────────────────────────────────────────────────────────────

async function handleBets(
  req: BrazilRequest,
  env: Env,
  player: JWTPayload,
  requestId: string
): Promise<Response> {
  if (req.method !== 'GET' && req.method !== 'POST') {
    return errorResponse('Method not allowed.', 405, requestId);
  }

  // Bet validation, wallet reservation, persistence and regulatory event
  // preparation are one authoritative transaction in the AWS core.
  return forwardClientRequestToCore(req, env, player, requestId);
}

// ── Wallet handler ────────────────────────────────────────────────────────────

async function handleWallet(
  req: BrazilRequest,
  env: Env,
  player: JWTPayload,
  requestId: string
): Promise<Response> {
  if (req.method === 'GET') {
    return forwardClientRequestToCore(req, env, player, requestId);
  }

  if (req.method === 'POST') {
    const url  = new URL(req.url);

    if (url.pathname === '/api/wallet/deposit') {
      const body = await parseJSON<{ amountBRL: number }>(req);
      if (!body || body.amountBRL <= 0) {
        return errorResponse('Valor inválido.', 400, requestId);
      }
      const amountCentavos = Math.round(body.amountBRL * 100);

      // Generate PIX QR code via the PIX service. This is an internal-only
      // call: sign it so the pix-webhook /qrcode endpoint can reject any
      // request that did not originate from this gateway.
      const pixBody = JSON.stringify({ playerId: player.sub, amountCentavos });
      const pixResp = await env.PIX_WEBHOOK_SVC.fetch(new Request('https://pix/qrcode', {
        method: 'POST',
        headers: await signInternalHeaders(pixBody, env.GATEWAY_INTERNAL_HMAC_SECRET),
        body: pixBody,
      }));
      return pixResp;
    }

    if (url.pathname === '/api/wallet/withdraw' || url.pathname === '/api/wallet/limits') {
      return forwardClientRequestToCore(req, env, player, requestId);
    }
  }

  return errorResponse('Method not allowed.', 405, requestId);
}

// ── Session handler ───────────────────────────────────────────────────────────

async function handleSession(
  req: BrazilRequest,
  env: Env,
  player: JWTPayload,
  requestId: string
): Promise<Response> {
  const sessionId   = env.BETTING_SESSION.idFromName(`${player.sub}:${env.JWT_ISSUER}`);
  const sessionStub = env.BETTING_SESSION.get(sessionId);

  if (req.method === 'GET') {
    const resp = await sessionStub.fetch(new Request('https://session/state'));
    return addResponseHeaders(resp, requestId);
  }

  if (req.method === 'DELETE') {
    const resp = await sessionStub.fetch(new Request('https://session/end', { method: 'POST' }));
    // Revoke KV session
    await env.PLAYER_SESSIONS.delete(`session:${player.sub}`);
    return addResponseHeaders(resp, requestId);
  }

  return errorResponse('Method not allowed.', 405, requestId);
}

// ── KYC handler ───────────────────────────────────────────────────────────────

async function handleKyc(
  req: BrazilRequest,
  env: Env,
  player: JWTPayload,
  requestId: string
): Promise<Response> {
  if (req.method === 'POST') {
    const contentType = req.headers.get('Content-Type') ?? '';
    if (!contentType.includes('multipart/form-data')) {
      return errorResponse('Content-Type must be multipart/form-data.', 415, requestId);
    }

    const formData = await req.formData();
    const file     = formData.get('document') as File | null;
    const docType  = formData.get('documentType') as string | null;

    if (!file || !docType) {
      return errorResponse('document e documentType são obrigatórios.', 400, requestId);
    }

    const allowedTypes = ['RG', 'CNH', 'PASSAPORTE'];
    if (!allowedTypes.includes(docType.toUpperCase())) {
      return errorResponse(`documentType deve ser um de: ${allowedTypes.join(', ')}`, 422, requestId);
    }

    const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, '_');
    const objectKey = `${player.sub}/${docType.toLowerCase()}/${requestId}_${safeName}`;

    await env.KYC_DOCUMENTS.put(objectKey, await file.arrayBuffer(), {
      httpMetadata: { contentType: file.type },
      customMetadata: {
        playerId:     player.sub,
        cpf:          player.cpf,
        documentType: docType,
        uploadedAt:   new Date().toISOString(),
      },
    });

    // R2 stores the encrypted intake object; PAM/KYC status remains
    // authoritative in the AWS core. Only the object reference crosses.
    return forwardToCore(env, {
      method: 'POST',
      path: '/api/kyc/intake',
      requestId,
      playerId: player.sub,
      idempotencyKey: req.headers.get('Idempotency-Key') ?? requestId,
      contentType: 'application/json',
      body: JSON.stringify({
        objectKey,
        documentType: docType.toUpperCase(),
        fileName: safeName,
        contentType: file.type,
        size: file.size,
      }),
    });
  }

  if (req.method === 'GET') {
    return forwardClientRequestToCore(req, env, player, requestId);
  }

  return errorResponse('Method not allowed.', 405, requestId);
}

async function forwardClientRequestToCore(
  req: Request,
  env: Env,
  player: JWTPayload,
  requestId: string
): Promise<Response> {
  const url = new URL(req.url);
  const hasBody = req.method !== 'GET' && req.method !== 'HEAD';
  const body = hasBody ? await req.arrayBuffer() : undefined;

  return forwardToCore(env, {
    method: req.method,
    path: `${url.pathname}${url.search}`,
    requestId,
    playerId: player.sub,
    idempotencyKey: req.headers.get('Idempotency-Key') ?? requestId,
    contentType: req.headers.get('Content-Type') ?? undefined,
    body,
  });
}

// ── Service routing ────────────────────────────────────────────────────────────

/**
 * Forward a request to a downstream service Worker, injecting the requestId
 * header so downstream logs correlate to gateway logs.
 */
async function routeToService(
  service: Fetcher,
  req: Request,
  requestId: string
): Promise<Response> {
  const forwarded = new Request(req.url, {
    method:  req.method,
    headers: new Headers(req.headers),
    body:    req.method !== 'GET' && req.method !== 'HEAD' ? req.body : undefined,
  });
  forwarded.headers.set('X-Request-Id', requestId);
  return service.fetch(forwarded);
}

// ── Internal service signing ───────────────────────────────────────────────────

/**
 * Build signed headers for an internal gateway→service call. The signature
 * covers `timestamp.nonce.rawBody` (same canonical form as the odds publisher
 * authentication) so the receiving Worker can reject anything not minted here.
 */
async function signInternalHeaders(rawBody: string, secret: string): Promise<Headers> {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce     = generateUUID();
  const signature = await hmacHex(secret, `${timestamp}.${nonce}.${rawBody}`);
  return new Headers({
    'Content-Type':          'application/json',
    'X-Internal-Timestamp':  timestamp,
    'X-Internal-Nonce':      nonce,
    'X-Internal-Signature':  signature,
  });
}

async function hmacHex(secret: string, value: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(value));
  return Array.from(new Uint8Array(sig), b => b.toString(16).padStart(2, '0')).join('');
}

// ── JWT helpers ───────────────────────────────────────────────────────────────

interface AuthResult {
  valid: boolean;
  payload?: JWTPayload;
}

async function validateJWT(req: BrazilRequest, env: Env): Promise<AuthResult> {
  const authHeader = req.headers.get('Authorization');
  if (!authHeader?.startsWith('Bearer ')) {
    return { valid: false };
  }

  const token   = authHeader.slice(7);
  const payload = await verifyJWT(token, env.JWT_SECRET);

  if (!payload) return { valid: false };

  // Confirm the session has not been server-side revoked
  const session = await env.PLAYER_SESSIONS.get(`session:${payload.sub}`);
  if (!session) return { valid: false };

  return { valid: true, payload };
}

async function signJWT(payload: JWTPayload, secret: string): Promise<string> {
  const enc    = new TextEncoder();
  const header = base64UrlEncode(enc.encode(JSON.stringify({ alg: 'HS256', typ: 'JWT' })));
  const body   = base64UrlEncode(enc.encode(JSON.stringify(payload)));
  const input  = `${header}.${body}`;

  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(input));
  return `${input}.${base64UrlEncode(new Uint8Array(sig))}`;
}

async function verifyJWT(token: string, secret: string): Promise<JWTPayload | null> {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;

    const [header, body, sig] = parts;
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']
    );

    const valid = await crypto.subtle.verify(
      'HMAC', key, base64UrlDecode(sig), enc.encode(`${header}.${body}`)
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

// ── Password helpers (PBKDF2 + AES-GCM envelope) ─────────────────────────────

async function hashPassword(password: string, _encKey: string): Promise<string> {
  const enc   = new TextEncoder();
  const salt  = crypto.getRandomValues(new Uint8Array(16));
  const km    = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits  = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt, iterations: 600_000, hash: 'SHA-256' }, km, 256
  );
  const combined = new Uint8Array(16 + 32);
  combined.set(salt, 0);
  combined.set(new Uint8Array(bits), 16);
  return base64UrlEncode(combined);
}

async function verifyPassword(password: string, hash: string, _encKey: string): Promise<boolean> {
  try {
    const combined = base64UrlDecode(hash);
    const salt     = combined.slice(0, 16);
    const stored   = combined.slice(16);
    const enc      = new TextEncoder();
    const km       = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
    const bits     = await crypto.subtle.deriveBits(
      { name: 'PBKDF2', salt, iterations: 600_000, hash: 'SHA-256' }, km, 256
    );
    const derived  = new Uint8Array(bits);
    if (derived.length !== stored.length) return false;
    let diff = 0;
    for (let i = 0; i < derived.length; i++) diff |= derived[i] ^ stored[i];
    return diff === 0;
  } catch {
    return false;
  }
}

// ── Rate limiting ─────────────────────────────────────────────────────────────

async function checkRateLimit(
  env: Env,
  key: string,
  limit: number,
  windowSecs: number
): Promise<RateLimitResult> {
  const kvKey = `rl:${key}`;
  const raw   = await env.RATE_LIMITS.get(kvKey);
  const count = raw ? parseInt(raw, 10) : 0;

  if (count >= limit) {
    return { allowed: false, remaining: 0, retryAfter: windowSecs, limit };
  }

  await env.RATE_LIMITS.put(kvKey, String(count + 1), { expirationTtl: windowSecs });
  return { allowed: true, remaining: limit - count - 1, retryAfter: 0, limit };
}

// ── Logging ───────────────────────────────────────────────────────────────────

async function logEvent(env: Env, event: Record<string, unknown>): Promise<void> {
  try {
    await env.DB.prepare(
      `INSERT INTO request_log (id, event_type, payload, created_at)
       VALUES (?, ?, ?, ?)`
    ).bind(
      generateUUID(),
      String(event.type ?? 'unknown'),
      JSON.stringify(event),
      new Date().toISOString()
    ).run();
  } catch {
    // Log failures must never propagate to the response path
  }
}

// ── Response helpers ──────────────────────────────────────────────────────────

function jsonResponse(data: unknown, status: number, requestId: string): Response {
  return new Response(JSON.stringify({ success: status < 400, data, requestId }), {
    status,
    headers: {
      'Content-Type':  'application/json',
      'X-Request-Id':  requestId,
      ...corsHeaders(),
    },
  });
}

function errorResponse(message: string, status: number, requestId = ''): Response {
  return new Response(
    JSON.stringify({ success: false, error: message, requestId }),
    {
      status,
      headers: { 'Content-Type': 'application/json', 'X-Request-Id': requestId, ...corsHeaders() },
    }
  );
}

function rateLimitResponse(rl: RateLimitResult, requestId: string): Response {
  return new Response(
    JSON.stringify({ success: false, error: 'Too many requests.', requestId }),
    {
      status: 429,
      headers: {
        'Content-Type':  'application/json',
        'X-Request-Id':  requestId,
        'Retry-After':   String(rl.retryAfter),
        'X-RateLimit-Limit': String(rl.limit),
        'X-RateLimit-Remaining': '0',
        ...corsHeaders(),
      },
    }
  );
}

function corsPreflightResponse(): Response {
  return new Response(null, { status: 204, headers: corsHeaders() });
}

function corsHeaders(): Record<string, string> {
  return {
    'Access-Control-Allow-Origin':  'https://acmetocasino.bet.br',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Request-Id',
    'Access-Control-Max-Age':       '86400',
  };
}

function addResponseHeaders(response: Response, requestId: string): Response {
  const headers = new Headers(response.headers);
  headers.set('X-Request-Id', requestId);
  headers.set('X-Content-Type-Options', 'nosniff');
  headers.set('X-Frame-Options', 'DENY');
  headers.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  headers.set('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'");
  // Merge CORS headers
  for (const [k, v] of Object.entries(corsHeaders())) {
    if (!headers.has(k)) headers.set(k, v);
  }
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function generateRequestId(): string {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function generateUUID(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}

function getClientIP(req: Request): string {
  return req.headers.get('CF-Connecting-IP') ?? 'unknown';
}

async function parseJSON<T>(req: Request): Promise<T | null> {
  try { return (await req.json()) as T; } catch { return null; }
}

function base64UrlEncode(data: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < data.byteLength; i++) bin += String.fromCharCode(data[i]);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function base64UrlDecode(str: string): Uint8Array {
  const b   = atob(str.replace(/-/g, '+').replace(/_/g, '/'));
  const out = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
  return out;
}

/** Inline CPF mod-11 validator (avoids cross-worker module import). */
function isValidCpf(cpf: string): boolean {
  if (cpf.length !== 11) return false;
  if (/^(\d)\1{10}$/.test(cpf)) return false;
  const check = (len: number): number => {
    let s = 0;
    for (let i = 0; i < len; i++) s += parseInt(cpf[i], 10) * (len + 1 - i);
    const r = s % 11;
    return r < 2 ? 0 : 11 - r;
  };
  return check(9) === parseInt(cpf[9], 10) && check(10) === parseInt(cpf[10], 10);
}
