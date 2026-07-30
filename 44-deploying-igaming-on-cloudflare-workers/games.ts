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
 * AcmeToCasino Platform - Game Catalog and Launch
 * Game listing, filtering, launch token generation, provider integration
 */

import {
  Env,
  successResponse,
  errorResponse,
  internalErrorResponse,
  parseJSON,
} from './utils.js';
import { authenticateRequest, UserRow } from './auth.js';

// ─── Types ─────────────────────────────────────────────────────────────────

export interface GameRow {
  id: number;
  game_id: string;
  provider: string;
  name: string;
  category: string;
  type: 'slots' | 'table' | 'live' | 'instant';
  rtp: number | null;
  mobile_compatible: number; // SQLite boolean (0/1)
  jurisdictions: string | null;  // JSON array of allowed country codes
  currencies: string | null;     // JSON array of supported currencies
  thumbnail_url: string | null;
  is_active: number;
  created_at: string;
}

export interface PublicGame {
  id: number;
  gameId: string;
  provider: string;
  name: string;
  category: string;
  type: string;
  rtp: number | null;
  mobileCompatible: boolean;
  thumbnailUrl: string | null;
}

interface GameFilters {
  category?: string | null;
  provider?: string | null;
  search?: string | null;
  userCountry?: string | null;
  userCurrency?: string | null;
}

interface LaunchBody {
  gameId: string;
  mode: 'real' | 'fun';
}

// ─── Route handler ─────────────────────────────────────────────────────────

export async function handleGames(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const { method } = request;

  // Public game listing (no auth required for browsing)
  if (method === 'GET' && url.pathname === '/api/games') {
    return handleGetGames(request, env);
  }

  // All other game routes require authentication
  const user = await authenticateRequest(request, env);
  if (!user) return errorResponse('Unauthorized', 401);

  if (method === 'GET' && url.pathname === '/api/games/categories') {
    return handleGetCategories(env);
  }

  if (method === 'GET' && url.pathname === '/api/games/providers') {
    return handleGetProviders(env);
  }

  if (method === 'POST' && url.pathname === '/api/games/launch') {
    return handleGameLaunch(request, env, user);
  }

  const gameMatch = url.pathname.match(/^\/api\/games\/([^/]+)$/);
  if (method === 'GET' && gameMatch) {
    return handleGetGame(gameMatch[1], env);
  }

  return errorResponse('Route not found', 404);
}

// ─── Get games list ────────────────────────────────────────────────────────

async function handleGetGames(request: Request, env: Env): Promise<Response> {
  try {
    const url = new URL(request.url);
    const filters: GameFilters = {
      category: url.searchParams.get('category'),
      provider: url.searchParams.get('provider'),
      search: url.searchParams.get('search'),
      userCountry: url.searchParams.get('country'),
      userCurrency: url.searchParams.get('currency'),
    };

    // Check cache first
    const cacheKey = `games:${JSON.stringify(filters)}`;
    const cached = await env.CACHE.get(cacheKey);
    if (cached) {
      return new Response(cached, {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const games = await queryGames(filters, env);
    const publicGames = games.map(toPublicGame);

    const responseBody = JSON.stringify({ success: true, data: { games: publicGames } });
    await env.CACHE.put(cacheKey, responseBody, { expirationTtl: 300 });

    return new Response(responseBody, { headers: { 'Content-Type': 'application/json' } });
  } catch (err) {
    console.error('Get games error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Get single game ───────────────────────────────────────────────────────

async function handleGetGame(gameId: string, env: Env): Promise<Response> {
  try {
    const cacheKey = `game:${gameId}`;
    const cached = await env.CACHE.get(cacheKey);
    if (cached) {
      return new Response(cached, { headers: { 'Content-Type': 'application/json' } });
    }

    const game = await env.DB.prepare(
      'SELECT * FROM games WHERE game_id = ? AND is_active = 1'
    )
      .bind(gameId)
      .first<GameRow>();

    if (!game) return errorResponse('Game not found', 404);

    const body = JSON.stringify({ success: true, data: { game: toPublicGame(game) } });
    await env.CACHE.put(cacheKey, body, { expirationTtl: 3600 });

    return new Response(body, { headers: { 'Content-Type': 'application/json' } });
  } catch (err) {
    console.error('Get game error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Categories & providers ────────────────────────────────────────────────

async function handleGetCategories(env: Env): Promise<Response> {
  try {
    const cached = await env.CACHE.get('game_categories');
    if (cached) {
      return new Response(cached, { headers: { 'Content-Type': 'application/json' } });
    }

    const result = await env.DB.prepare(
      'SELECT DISTINCT category FROM games WHERE is_active = 1 ORDER BY category'
    ).all<{ category: string }>();

    const categories = result.results.map((r) => r.category);
    const body = JSON.stringify({ success: true, data: { categories } });
    await env.CACHE.put('game_categories', body, { expirationTtl: 3600 });

    return new Response(body, { headers: { 'Content-Type': 'application/json' } });
  } catch (err) {
    console.error('Get categories error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

async function handleGetProviders(env: Env): Promise<Response> {
  try {
    const cached = await env.CACHE.get('game_providers');
    if (cached) {
      return new Response(cached, { headers: { 'Content-Type': 'application/json' } });
    }

    const result = await env.DB.prepare(
      'SELECT DISTINCT provider FROM games WHERE is_active = 1 ORDER BY provider'
    ).all<{ provider: string }>();

    const providers = result.results.map((r) => r.provider);
    const body = JSON.stringify({ success: true, data: { providers } });
    await env.CACHE.put('game_providers', body, { expirationTtl: 3600 });

    return new Response(body, { headers: { 'Content-Type': 'application/json' } });
  } catch (err) {
    console.error('Get providers error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Game launch ───────────────────────────────────────────────────────────

async function handleGameLaunch(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<LaunchBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  if (!body.gameId) return errorResponse('gameId is required');
  if (!body.mode || !['real', 'fun'].includes(body.mode)) {
    return errorResponse('mode must be "real" or "fun"');
  }

  try {
    const game = await env.DB.prepare(
      'SELECT * FROM games WHERE game_id = ? AND is_active = 1'
    )
      .bind(body.gameId)
      .first<GameRow>();

    if (!game) return errorResponse('Game not found', 404);

    // Check jurisdiction availability
    if (game.jurisdictions && user.country) {
      const allowed: string[] = JSON.parse(game.jurisdictions);
      if (allowed.length > 0 && !allowed.includes(user.country)) {
        return errorResponse('This game is not available in your region', 403);
      }
    }

    // Balance check for real money play
    if (body.mode === 'real') {
      if (user.balance <= 0) {
        return errorResponse('Insufficient balance. Please make a deposit.', 402);
      }
      if (user.status !== 'active') {
        return errorResponse('Account is not eligible for real money play', 403);
      }
    }

    // Generate launch token
    const sessionToken = await generateLaunchToken(user, game);

    // Call game provider API
    const launchResult = await callProviderLaunch(game.provider, {
      gameId: game.game_id,
      userId: String(user.id),
      sessionToken,
      currency: user.currency,
      language: user.language,
      mode: body.mode,
    }, env);

    // Cache active game session
    await env.CACHE.put(
      `game_session:${sessionToken}`,
      JSON.stringify({ userId: user.id, gameId: game.game_id, provider: game.provider }),
      { expirationTtl: 3600 }
    );

    return successResponse({
      launchUrl: launchResult.launchUrl,
      sessionId: launchResult.sessionId,
    });
  } catch (err) {
    console.error('Game launch error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Internal helpers ──────────────────────────────────────────────────────

async function queryGames(filters: GameFilters, env: Env): Promise<GameRow[]> {
  let query = 'SELECT * FROM games WHERE is_active = 1';
  const params: (string | number)[] = [];

  if (filters.category) {
    query += ' AND category = ?';
    params.push(filters.category);
  }
  if (filters.provider) {
    query += ' AND provider = ?';
    params.push(filters.provider);
  }
  if (filters.search) {
    query += ' AND name LIKE ?';
    params.push(`%${filters.search}%`);
  }
  if (filters.userCountry) {
    query += " AND (jurisdictions IS NULL OR jurisdictions = '[]' OR jurisdictions LIKE ?)";
    params.push(`%"${filters.userCountry}"%`);
  }
  if (filters.userCurrency) {
    query += " AND (currencies IS NULL OR currencies = '[]' OR currencies LIKE ?)";
    params.push(`%"${filters.userCurrency}"%`);
  }

  query += ' ORDER BY name ASC LIMIT 200';

  const stmt = env.DB.prepare(query);
  const bound = params.length > 0 ? stmt.bind(...params) : stmt;
  const result = await bound.all<GameRow>();
  return result.results;
}

async function generateLaunchToken(user: UserRow, game: GameRow): Promise<string> {
  const payload = {
    userId: user.id,
    gameId: game.game_id,
    provider: game.provider,
    timestamp: Date.now(),
    exp: Date.now() + 30 * 60 * 1000, // 30 min
  };
  const randomBytes = crypto.getRandomValues(new Uint8Array(8));
  const nonce = Array.from(randomBytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  return `${btoa(JSON.stringify(payload))}.${nonce}`;
}

async function callProviderLaunch(
  provider: string,
  params: {
    gameId: string;
    userId: string;
    sessionToken: string;
    currency: string;
    language: string;
    mode: string;
  },
  _env: Env
): Promise<{ launchUrl: string; sessionId: string }> {
  // In production, each provider has its own integration.
  // This stub returns a structured launch URL following the provider pattern.
  const sessionId = `acme_${provider}_${Date.now()}`;
  const launchUrl = `https://games.${provider}.com/launch?token=${params.sessionToken}&currency=${params.currency}&lang=${params.language}&mode=${params.mode}`;

  return { launchUrl, sessionId };
}

function toPublicGame(row: GameRow): PublicGame {
  return {
    id: row.id,
    gameId: row.game_id,
    provider: row.provider,
    name: row.name,
    category: row.category,
    type: row.type,
    rtp: row.rtp,
    mobileCompatible: row.mobile_compatible === 1,
    thumbnailUrl: row.thumbnail_url,
  };
}
