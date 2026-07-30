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
 * AcmeToCasino Platform - Authentication
 * JWT-based auth, session management via KV, registration/login handlers
 */

import {
  Env,
  JWTPayload,
  signJWT,
  verifyJWT,
  hashPassword,
  verifyPassword,
  successResponse,
  errorResponse,
  unauthorizedResponse,
  internalErrorResponse,
  jsonResponse,
  parseJSON,
  getClientIP,
} from './utils.js';

// ─── Types ─────────────────────────────────────────────────────────────────

export interface UserRow {
  id: number;
  email: string;
  username: string;
  password_hash: string;
  first_name: string | null;
  last_name: string | null;
  date_of_birth: string | null;
  country: string | null;
  currency: string;
  language: string;
  status: 'active' | 'inactive' | 'suspended' | 'self_excluded';
  balance: number;
  role: string;
  created_at: string;
  updated_at: string;
}

export interface PublicUser {
  id: number;
  email: string;
  username: string;
  firstName: string | null;
  lastName: string | null;
  country: string | null;
  currency: string;
  language: string;
  status: string;
  balance: number;
  role: string;
}

interface RegisterBody {
  email: string;
  username: string;
  password: string;
  firstName?: string;
  lastName?: string;
  dateOfBirth?: string;
  country?: string;
  currency?: string;
  language?: string;
}

interface LoginBody {
  email: string;
  password: string;
}

interface TokenPair {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

// ─── Route handler ─────────────────────────────────────────────────────────

export async function handleAuth(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const { method } = request;

  if (method === 'POST' && url.pathname === '/api/auth/register') {
    return handleRegister(request, env);
  }
  if (method === 'POST' && url.pathname === '/api/auth/login') {
    return handleLogin(request, env);
  }
  if (method === 'POST' && url.pathname === '/api/auth/refresh') {
    return handleRefresh(request, env);
  }
  if (method === 'POST' && url.pathname === '/api/auth/logout') {
    return handleLogout(request, env);
  }
  if (method === 'GET' && url.pathname === '/api/auth/me') {
    return handleMe(request, env);
  }

  return errorResponse('Route not found', 404);
}

// ─── Register ──────────────────────────────────────────────────────────────

async function handleRegister(request: Request, env: Env): Promise<Response> {
  const body = await parseJSON<RegisterBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  const errors = validateRegister(body);
  if (errors.length > 0) {
    return jsonResponse({ success: false, errors }, 422);
  }

  try {
    // Check existing email/username
    const existing = await env.DB.prepare(
      'SELECT id FROM users WHERE email = ? OR username = ?'
    )
      .bind(body.email.toLowerCase(), body.username.toLowerCase())
      .first<{ id: number }>();

    if (existing) {
      return errorResponse('Email or username already in use', 409);
    }

    const passwordHash = await hashPassword(body.password);

    const result = await env.DB.prepare(
      `INSERT INTO users
         (email, username, password_hash, first_name, last_name, date_of_birth,
          country, currency, language, status, balance, role)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0.00, 'player')`
    )
      .bind(
        body.email.toLowerCase(),
        body.username.toLowerCase(),
        passwordHash,
        body.firstName ?? null,
        body.lastName ?? null,
        body.dateOfBirth ?? null,
        body.country ?? null,
        body.currency ?? 'EUR',
        body.language ?? 'en'
      )
      .run();

    const userId = result.meta.last_row_id as number;
    const user = await getUserById(userId, env);
    if (!user) return internalErrorResponse(env);

    const tokens = await generateTokens(user, env);
    await storeSession(user.id, tokens.refreshToken, env);

    return jsonResponse({ success: true, data: { user: toPublicUser(user), tokens } }, 201);
  } catch (err) {
    console.error('Register error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Login ─────────────────────────────────────────────────────────────────

async function handleLogin(request: Request, env: Env): Promise<Response> {
  const body = await parseJSON<LoginBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  if (!body.email || !body.password) {
    return jsonResponse({ success: false, errors: ['email and password are required'] }, 422);
  }

  // Brute-force rate limit: 5 attempts per IP per 15 min
  const ip = getClientIP(request);
  const limitKey = `login_attempts:${ip}`;
  const attempts = parseInt((await env.CACHE.get(limitKey)) ?? '0', 10);
  if (attempts >= 5) {
    return errorResponse('Too many login attempts. Try again later.', 429);
  }

  try {
    const user = await env.DB.prepare(
      'SELECT * FROM users WHERE email = ?'
    )
      .bind(body.email.toLowerCase())
      .first<UserRow>();

    const valid = user ? await verifyPassword(body.password, user.password_hash) : false;

    if (!user || !valid) {
      await env.CACHE.put(limitKey, String(attempts + 1), { expirationTtl: 900 });
      return unauthorizedResponse();
    }

    if (user.status !== 'active') {
      return errorResponse(`Account is ${user.status}`, 403);
    }

    // Clear attempt counter on success
    await env.CACHE.delete(limitKey);

    // Update last login
    await env.DB.prepare('UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = ?')
      .bind(user.id)
      .run();

    const tokens = await generateTokens(user, env);
    await storeSession(user.id, tokens.refreshToken, env);

    return successResponse({ user: toPublicUser(user), tokens });
  } catch (err) {
    console.error('Login error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Refresh token ─────────────────────────────────────────────────────────

async function handleRefresh(request: Request, env: Env): Promise<Response> {
  const body = await parseJSON<{ refreshToken: string }>(request);
  if (!body?.refreshToken) return errorResponse('refreshToken required');

  const payload = await verifyJWT(body.refreshToken, env.JWT_SECRET);
  if (!payload) return unauthorizedResponse();

  const session = await env.SESSIONS.get(`session:${payload.userId}`);
  if (!session || session !== body.refreshToken) return unauthorizedResponse();

  const user = await getUserById(payload.userId, env);
  if (!user || user.status !== 'active') return unauthorizedResponse();

  const tokens = await generateTokens(user, env);
  await storeSession(user.id, tokens.refreshToken, env);

  return successResponse({ tokens });
}

// ─── Logout ────────────────────────────────────────────────────────────────

async function handleLogout(request: Request, env: Env): Promise<Response> {
  const user = await authenticateRequest(request, env);
  if (!user) return unauthorizedResponse();

  await env.SESSIONS.delete(`session:${user.id}`);
  await env.CACHE.delete(`user:${user.id}`);

  return successResponse({ message: 'Logged out successfully' });
}

// ─── Me ────────────────────────────────────────────────────────────────────

async function handleMe(request: Request, env: Env): Promise<Response> {
  const user = await authenticateRequest(request, env);
  if (!user) return unauthorizedResponse();
  return successResponse({ user: toPublicUser(user) });
}

// ─── Shared auth utilities ─────────────────────────────────────────────────

export async function authenticateRequest(
  request: Request,
  env: Env
): Promise<UserRow | null> {
  const authHeader = request.headers.get('Authorization');
  if (!authHeader?.startsWith('Bearer ')) return null;

  const token = authHeader.substring(7);
  const payload = await verifyJWT(token, env.JWT_SECRET);
  if (!payload) return null;

  // Verify session still active
  const session = await env.SESSIONS.get(`session:${payload.userId}`);
  if (!session) return null;

  return getUserById(payload.userId, env);
}

export async function getUserById(id: number, env: Env): Promise<UserRow | null> {
  const cacheKey = `user:${id}`;
  const cached = await env.CACHE.get(cacheKey);
  if (cached) {
    try {
      return JSON.parse(cached) as UserRow;
    } catch {
      // cache miss; fall through
    }
  }

  const user = await env.DB.prepare('SELECT * FROM users WHERE id = ?')
    .bind(id)
    .first<UserRow>();

  if (user) {
    await env.CACHE.put(cacheKey, JSON.stringify(user), { expirationTtl: 300 });
  }

  return user ?? null;
}

async function generateTokens(user: UserRow, env: Env): Promise<TokenPair> {
  const now = Math.floor(Date.now() / 1000);

  const accessPayload: JWTPayload = {
    userId: user.id,
    email: user.email,
    role: user.role,
    exp: now + 3600, // 1 hour
    iat: now,
    iss: 'acmetocasino-api',
  };

  const refreshPayload: JWTPayload = {
    userId: user.id,
    email: user.email,
    role: user.role,
    exp: now + 30 * 24 * 3600, // 30 days
    iat: now,
    iss: 'acmetocasino-api',
  };

  const [accessToken, refreshToken] = await Promise.all([
    signJWT(accessPayload, env.JWT_SECRET),
    signJWT(refreshPayload, env.JWT_SECRET),
  ]);

  return { accessToken, refreshToken, expiresIn: 3600 };
}

async function storeSession(userId: number, refreshToken: string, env: Env): Promise<void> {
  await env.SESSIONS.put(`session:${userId}`, refreshToken, {
    expirationTtl: 30 * 24 * 3600,
  });
}

export function toPublicUser(user: UserRow): PublicUser {
  return {
    id: user.id,
    email: user.email,
    username: user.username,
    firstName: user.first_name,
    lastName: user.last_name,
    country: user.country,
    currency: user.currency,
    language: user.language,
    status: user.status,
    balance: user.balance,
    role: user.role,
  };
}

// ─── Validation ────────────────────────────────────────────────────────────

function validateRegister(body: RegisterBody): string[] {
  const errors: string[] = [];

  if (!body.email || body.email.length > 254 || !body.email.includes('@') || !/^[^@\s]{1,64}@[^@\s]{1,253}$/.test(body.email)) {
    errors.push('Valid email is required');
  }
  if (!body.username || body.username.length < 3 || body.username.length > 32) {
    errors.push('Username must be 3–32 characters');
  }
  if (!body.password || body.password.length < 8) {
    errors.push('Password must be at least 8 characters');
  }
  if (body.currency && !/^[A-Z]{3}$/.test(body.currency)) {
    errors.push('Currency must be a 3-letter ISO code');
  }
  if (body.country && !/^[A-Z]{2}$/.test(body.country)) {
    errors.push('Country must be a 2-letter ISO code');
  }

  return errors;
}
