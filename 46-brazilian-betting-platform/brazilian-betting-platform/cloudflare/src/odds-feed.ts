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
 * Live Odds Feed Worker
 *
 * Provides a Server-Sent Events (SSE) endpoint for real-time odds updates.
 * Brazilian sports categories receive highest cache priority and fastest
 * update cadence.
 *
 * Architecture:
 *  - GET /api/odds/live       → SSE stream: pushes cached snapshots
 *  - GET /api/odds/:marketId  → Single market snapshot from KV cache
 *  - POST /api/odds/refresh   → Authenticated AWS snapshot publication
 *  - POST /api/odds/suspend   → Internal: suspend a market (SIGAP integrity alert)
 *
 * Cache strategy:
 *  - ODDS_CACHE KV namespace: key = `market:{marketId}`.
 *  - The authoritative AWS odds service pushes immutable, versioned snapshots.
 *  - Source timestamps are enforced on both publication and reads. The cache is
 *    never used as the authoritative price at bet placement.
 *
 * SIGAP integrity alerts (Art. 42, Portaria 827/2023): if SIGAP issues an
 * alert for a specific event, the Worker automatically suspends the market
 * and stops accepting new bets.
 */

import type { Env, OddsMarket, SportCategory } from './types.js';

type OddsFeedEnv = Pick<Env, 'ODDS_CACHE'> & {
  ODDS_PUBLISHER_HMAC_SECRET: string;
};

interface VersionedOddsMarket extends OddsMarket {
  snapshotVersion: number;
  sourceTimestamp: string;
}

interface OddsSnapshotPush {
  version: number;
  generatedAt: string;
  markets: OddsMarket[];
}

// ── Brazilian sports priority configuration ──────────────────────────────────

const BRAZILIAN_SPORTS: Set<SportCategory> = new Set([
  'brasileirao-serie-a',
  'brasileirao-serie-b',
  'copa-do-brasil',
  'libertadores',
  'sul-americana',
  'ufc-mma',
]);

/** TTL in seconds for KV odds cache entries. */
const CACHE_TTL: Record<'brazilian' | 'other', number> = {
  brazilian: 60,
  other:     120,
};

const FRESHNESS_MS: Record<'brazilian' | 'other', number> = {
  brazilian: 10_000,
  other:     30_000,
};

const MAX_PUBLICATION_AGE_MS = 60_000;
const MAX_FUTURE_SKEW_MS = 5_000;
const REPLAY_WINDOW_SECONDS = 120;

/** SSE heartbeat interval in milliseconds. */
const SSE_HEARTBEAT_MS = 5_000;

/** Maximum SSE connection duration (Cloudflare Workers limit: 100s wall clock). */
const SSE_MAX_DURATION_MS = 90_000;

// ── Worker export ─────────────────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: OddsFeedEnv, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = normalizeOddsPath(url.pathname);

    // ── Live SSE stream ──────────────────────────────────────────────────
    if (path === '/odds/live') {
      return handleLiveStream(request, env, ctx);
    }

    // ── Single market snapshot ───────────────────────────────────────────
    if (path.startsWith('/odds/') && request.method === 'GET') {
      const marketId = path.slice('/odds/'.length);
      return handleMarketSnapshot(marketId, env);
    }

    // ── Internal: refresh cache from upstream ────────────────────────────
    if (path === '/odds/refresh' && request.method === 'POST') {
      return handleOddsRefresh(request, env);
    }

    // ── Internal: suspend market (SIGAP integrity alert) ─────────────────
    if (path === '/odds/suspend' && request.method === 'POST') {
      return handleMarketSuspension(request, env);
    }

    // ── List active markets ───────────────────────────────────────────────
    if (path === '/odds' && request.method === 'GET') {
      return handleListMarkets(url, env);
    }

    return new Response('Not found', { status: 404 });
  },
};

// ── SSE stream handler ────────────────────────────────────────────────────────

function handleLiveStream(
  request: Request,
  env: OddsFeedEnv,
  _ctx: ExecutionContext
): Response {
  // Browsers send an Accept: text/event-stream header for SSE
  const accept = request.headers.get('Accept') ?? '';
  if (!accept.includes('text/event-stream')) {
    return new Response('SSE requires Accept: text/event-stream', { status: 406 });
  }

  // Optional: filter by sport category
  const url      = new URL(request.url);
  const sport    = url.searchParams.get('sport') as SportCategory | null;
  const lastId   = url.searchParams.get('lastEventId') ?? '0';

  // TransformStream is the Workers-native way to produce streaming responses
  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const writer = writable.getWriter();
  const enc    = new TextEncoder();

  const startTime = Date.now();
  let  eventId    = parseInt(lastId, 10) || 0;

  // Non-blocking: pump events to the stream
  (async () => {
    try {
      // Initial connection acknowledgement
      await writer.write(enc.encode(`: connected\n\n`));

      while (Date.now() - startTime < SSE_MAX_DURATION_MS) {
        const markets = await getMarketsForSSE(env, sport);

        for (const market of markets) {
          eventId++;
          const data = JSON.stringify(market);
          const chunk = `id: ${eventId}\nevent: odds_update\ndata: ${data}\n\n`;
          await writer.write(enc.encode(chunk));
        }

        // Heartbeat comment to keep the connection alive (proxies may close idle streams)
        await writer.write(enc.encode(`: heartbeat\n\n`));

        // Wait before next poll
        await sleep(SSE_HEARTBEAT_MS);
      }

      // Graceful close: tell the client to reconnect
      await writer.write(enc.encode(`event: reconnect\ndata: max_duration_reached\n\n`));
    } catch {
      // Client disconnected or write error — close silently
    } finally {
      try { await writer.close(); } catch { /* already closed */ }
    }
  })();

  return new Response(readable as unknown as BodyInit, {
    status:  200,
    headers: {
      'Content-Type':                'text/event-stream; charset=utf-8',
      'Cache-Control':               'no-cache, no-store',
      'Connection':                  'keep-alive',
      'X-Accel-Buffering':           'no',    // disable nginx buffering
      'Access-Control-Allow-Origin': 'https://acmetocasino.bet.br',
    },
  });
}

// ── Single market snapshot ─────────────────────────────────────────────────────

async function handleMarketSnapshot(marketId: string, env: OddsFeedEnv): Promise<Response> {
  if (!marketId || marketId.length > 64) {
    return new Response(JSON.stringify({ error: 'Invalid marketId' }), {
      status: 400, headers: { 'Content-Type': 'application/json' },
    });
  }

  const cached = await env.ODDS_CACHE.get(`market:${marketId}`);
  if (!cached) {
    return new Response(JSON.stringify({ error: 'Market not found' }), {
      status: 404, headers: { 'Content-Type': 'application/json' },
    });
  }

  try {
    const snapshot = JSON.parse(cached) as VersionedOddsMarket;
    if (!isSnapshotFresh(snapshot)) {
      return new Response(JSON.stringify({ error: 'Odds snapshot is stale' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json', 'Retry-After': '1' },
      });
    }
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid odds snapshot' }), {
      status: 503, headers: { 'Content-Type': 'application/json' },
    });
  }

  return new Response(cached, {
    status:  200,
    headers: {
      'Content-Type':  'application/json',
      'Cache-Control': 'no-cache',
    },
  });
}

// ── Market listing ─────────────────────────────────────────────────────────────

async function handleListMarkets(url: URL, env: OddsFeedEnv): Promise<Response> {
  const sport  = url.searchParams.get('sport') as SportCategory | null;
  const limit  = Math.min(parseInt(url.searchParams.get('limit') ?? '50', 10), 100);

  // List KV keys for markets (prefix scan)
  const { keys } = await env.ODDS_CACHE.list({ prefix: 'market:', limit });

  const markets: OddsMarket[] = [];
  for (const key of keys) {
    const value = await env.ODDS_CACHE.get(key.name);
    if (!value) continue;
    try {
      const market = JSON.parse(value) as OddsMarket;
      if (sport && market.sport !== sport) continue;
      if (!isSnapshotFresh(market as VersionedOddsMarket)) continue;
      markets.push(market);
    } catch { continue; }
  }

  // Sort: Brazilian sports first, then by start time
  markets.sort((a, b) => {
    const aBrazilian = BRAZILIAN_SPORTS.has(a.sport) ? 0 : 1;
    const bBrazilian = BRAZILIAN_SPORTS.has(b.sport) ? 0 : 1;
    if (aBrazilian !== bBrazilian) return aBrazilian - bBrazilian;
    return new Date(a.startTime).getTime() - new Date(b.startTime).getTime();
  });

  return new Response(JSON.stringify({ markets, total: markets.length }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
}

// ── Odds refresh (internal) ───────────────────────────────────────────────────

async function handleOddsRefresh(request: Request, env: OddsFeedEnv): Promise<Response> {
  const rawBody = await request.text();
  const authenticationError = await authenticatePublisher(request, rawBody, env);
  if (authenticationError) return authenticationError;

  let body: OddsSnapshotPush | null = null;
  try {
    body = JSON.parse(rawBody) as OddsSnapshotPush;
  } catch {
    return new Response('Invalid JSON', { status: 400 });
  }

  if (!body || !Number.isSafeInteger(body.version) || body.version <= 0) {
    return new Response('positive integer version required', { status: 422 });
  }
  if (!Array.isArray(body.markets) || body.markets.length === 0) {
    return new Response('non-empty markets array required', { status: 422 });
  }

  const generatedAtMs = Date.parse(body.generatedAt);
  const now = Date.now();
  if (!Number.isFinite(generatedAtMs)
      || now - generatedAtMs > MAX_PUBLICATION_AGE_MS
      || generatedAtMs - now > MAX_FUTURE_SKEW_MS) {
    return new Response('snapshot generatedAt is outside the freshness window', { status: 422 });
  }

  for (const market of body.markets) {
    if (!market.id || !market.eventId || !market.sport) {
      return new Response('each market requires id, eventId and sport', { status: 422 });
    }

    const cached = await env.ODDS_CACHE.get(`market:${market.id}`);
    if (!cached) continue;
    try {
      const current = JSON.parse(cached) as Partial<VersionedOddsMarket>;
      if (typeof current.snapshotVersion === 'number' && current.snapshotVersion >= body.version) {
        return new Response(JSON.stringify({
          error: 'stale snapshot version',
          marketId: market.id,
          currentVersion: current.snapshotVersion,
        }), { status: 409, headers: { 'Content-Type': 'application/json' } });
      }
    } catch {
      return new Response('cached snapshot is invalid', { status: 503 });
    }
  }

  let updated = 0;
  for (const market of body.markets) {
    const isBrazilian = BRAZILIAN_SPORTS.has(market.sport);
    const ttl         = isBrazilian ? CACHE_TTL.brazilian : CACHE_TTL.other;
    const snapshot: VersionedOddsMarket = {
      ...market,
      updatedAt: body.generatedAt,
      snapshotVersion: body.version,
      sourceTimestamp: body.generatedAt,
    };

    await env.ODDS_CACHE.put(`market:${market.id}`, JSON.stringify(snapshot), {
      expirationTtl: ttl,
    });
    updated++;
  }

  return new Response(JSON.stringify({ updated, version: body.version }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
}

// ── Market suspension (SIGAP integrity alert) ─────────────────────────────────

/**
 * Suspend a market in response to a SIGAP integrity alert.
 * Article 42 of Portaria SPA/MF 827/2023 requires immediate suspension of
 * betting on events flagged by the regulator's integrity monitoring system.
 *
 * This is a state-mutating internal endpoint: it carries the same HMAC
 * publisher authentication as /odds/refresh so a forged request cannot
 * suspend live markets.
 */
async function handleMarketSuspension(request: Request, env: OddsFeedEnv): Promise<Response> {
  const rawBody = await request.text();
  const authenticationError = await authenticatePublisher(request, rawBody, env);
  if (authenticationError) return authenticationError;

  let body: { marketId?: string; reason?: string } | null = null;
  try {
    body = JSON.parse(rawBody) as { marketId?: string; reason?: string };
  } catch {
    return new Response('Invalid JSON', { status: 400 });
  }

  if (!body?.marketId) {
    return new Response('marketId is required', { status: 422 });
  }

  const cached = await env.ODDS_CACHE.get(`market:${body.marketId}`);
  if (!cached) {
    return new Response(JSON.stringify({ error: 'Market not found' }), {
      status: 404, headers: { 'Content-Type': 'application/json' },
    });
  }

  const market = JSON.parse(cached) as OddsMarket;
  market.suspended       = true;
  market.suspendedReason = (body.reason as OddsMarket['suspendedReason']) ?? 'sigap_integrity_alert';
  market.updatedAt       = new Date().toISOString();

  // Write back with no TTL expiration — suspended markets stay suspended until
  // an explicit re-activation call (not implemented in this example).
  await env.ODDS_CACHE.put(`market:${body.marketId}`, JSON.stringify(market));

  return new Response(JSON.stringify({ suspended: true, marketId: body.marketId }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
}

// ── SSE helper ────────────────────────────────────────────────────────────────

async function getMarketsForSSE(env: OddsFeedEnv, sport: SportCategory | null): Promise<OddsMarket[]> {
  const prefix = 'market:';
  const { keys } = await env.ODDS_CACHE.list({ prefix, limit: 50 });
  const markets: OddsMarket[] = [];

  for (const key of keys) {
    const value = await env.ODDS_CACHE.get(key.name);
    if (!value) continue;
    try {
      const market = JSON.parse(value) as OddsMarket;
      if (sport && market.sport !== sport) continue;
      if (!market.suspended && isSnapshotFresh(market as VersionedOddsMarket)) markets.push(market);
    } catch { continue; }
  }

  return markets;
}

function normalizeOddsPath(pathname: string): string {
  if (pathname === '/api/odds') return '/odds';
  if (pathname.startsWith('/api/odds/')) return pathname.slice('/api'.length);
  return pathname;
}

function isSnapshotFresh(snapshot: VersionedOddsMarket): boolean {
  if (!Number.isSafeInteger(snapshot.snapshotVersion) || snapshot.snapshotVersion <= 0) return false;
  const sourceTime = Date.parse(snapshot.sourceTimestamp);
  if (!Number.isFinite(sourceTime)) return false;
  const policy = BRAZILIAN_SPORTS.has(snapshot.sport) ? 'brazilian' : 'other';
  const age = Date.now() - sourceTime;
  return age >= -MAX_FUTURE_SKEW_MS && age <= FRESHNESS_MS[policy];
}

async function authenticatePublisher(
  request: Request,
  rawBody: string,
  env: OddsFeedEnv
): Promise<Response | null> {
  const timestamp = request.headers.get('X-Odds-Timestamp') ?? '';
  const nonce = request.headers.get('X-Odds-Nonce') ?? '';
  const signature = request.headers.get('X-Odds-Signature') ?? '';
  const timestampSeconds = Number(timestamp);

  if (!/^\d{10}$/.test(timestamp) || !/^[A-Za-z0-9_-]{16,128}$/.test(nonce)
      || !/^[a-fA-F0-9]{64}$/.test(signature)) {
    return new Response('missing or malformed publisher authentication', { status: 401 });
  }

  const skew = Math.abs(Date.now() - timestampSeconds * 1_000);
  if (!Number.isFinite(timestampSeconds) || skew > MAX_PUBLICATION_AGE_MS) {
    return new Response('publisher timestamp outside replay window', { status: 401 });
  }

  const replayKey = `publisher-replay:${nonce}`;
  if (await env.ODDS_CACHE.get(replayKey)) {
    return new Response('publisher request already processed', { status: 409 });
  }

  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(env.ODDS_PUBLISHER_HMAC_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['verify']
  );
  const signatureBytes = new Uint8Array(signature.match(/.{2}/g)!.map(byte => parseInt(byte, 16)));
  const valid = await crypto.subtle.verify(
    'HMAC',
    key,
    signatureBytes,
    encoder.encode(`${timestamp}.${nonce}.${rawBody}`)
  );
  if (!valid) return new Response('invalid publisher signature', { status: 401 });

  await env.ODDS_CACHE.put(replayKey, timestamp, { expirationTtl: REPLAY_WINDOW_SECONDS });
  return null;
}

// ── Sleep (Workers-compatible) ────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}
