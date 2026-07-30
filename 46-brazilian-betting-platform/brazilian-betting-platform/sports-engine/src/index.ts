// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// ---------------------------------------------------------------------------
// BetBR Sports Engine — Cloudflare Worker entry point
//
// Cron (*/30 * * * *) → fetch API-Football data → store in KV
// HTTP GET /api/*    → read from KV → return JSON
// ---------------------------------------------------------------------------
import type {
  Env,
  LiveFixturesCache,
  UpcomingFixturesCache,
  StandingsCache,
  ScorersCache,
  OddsCache,
  LiveStatsCache,
  PredictionsCache,
} from './types';
import {
  fetchLiveFixtures,
  fetchUpcomingFixtures,
  fetchStandings,
  fetchSerieBStandings,
  fetchTopScorers,
  fetchSerieBTopScorers,
  fetchOdds,
  fetchLiveFixturesV3,
  fetchPredictions,
} from './api-football';
import { FALLBACK_LIVE, FALLBACK_UPCOMING, FALLBACK_STANDINGS, FALLBACK_SCORERS } from './fallback';

// ---------------------------------------------------------------------------
// CORS headers — origin whitelist
// ---------------------------------------------------------------------------
const ALLOWED_ORIGINS = [
  'https://bet-brazil.cloud-acmetocasino.com',
  'https://new.acmetocasino.com',
  'https://thebackendofluck.com',
  'https://www.thebackendofluck.com',
  'https://portrasdasorte.com.br',
  'https://www.portrasdasorte.com.br',
];

// Security headers applied to every response. This is a JSON-only API, so the
// CSP locks down every resource type — nothing should ever be loaded from here.
const SECURITY_HEADERS: Record<string, string> = {
  'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'",
  'Referrer-Policy': 'no-referrer',
};

function corsHeaders(request: Request): Record<string, string> {
  const origin = request.headers.get('Origin') || '';
  const allowedOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Vary': 'Origin',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    ...SECURITY_HEADERS,
  };
}

function json(body: unknown, status = 200, request?: Request): Response {
  const headers = request
    ? corsHeaders(request)
    : { 'Content-Type': 'application/json', ...SECURITY_HEADERS };
  return new Response(JSON.stringify(body), { status, headers });
}

// ---------------------------------------------------------------------------
// KV keys
// ---------------------------------------------------------------------------
const KV = {
  LIVE: 'live_fixtures',
  UPCOMING: 'upcoming_fixtures',
  STANDINGS: 'standings',
  STANDINGS_SERIE_B: 'standings_serie_b',
  SCORERS: 'top_scorers',
  SCORERS_SERIE_B: 'top_scorers_serie_b',
  FALLBACK_LIVE: 'fallback_live',
  FALLBACK_UPCOMING: 'fallback_upcoming',
  ODDS: 'odds',
  LIVE_STATS: 'live_stats',
  PREDICTIONS: 'predictions',
  CRON_COUNTER: 'cron_counter',
} as const;

// ---------------------------------------------------------------------------
// KV read helpers with fallback chaining
// ---------------------------------------------------------------------------
async function getFromKV<T>(env: Env, key: string, hardFallback: T): Promise<T> {
  try {
    const cached = await env.SPORTS_DATA.get<T>(key, 'json');
    if (cached !== null) return cached;
  } catch (err) {
    console.error(`[kv] Error reading "${key}":`, err);
  }
  return hardFallback;
}

// ---------------------------------------------------------------------------
// Cron counter — used to throttle expensive endpoints
// Returns the new counter value (incremented and wrapped at 1000).
// ---------------------------------------------------------------------------
async function incrementCronCounter(env: Env): Promise<number> {
  try {
    const raw = await env.SPORTS_DATA.get(KV.CRON_COUNTER);
    const current = raw ? parseInt(raw, 10) : 0;
    const next = isNaN(current) ? 1 : (current % 1000) + 1;
    await env.SPORTS_DATA.put(KV.CRON_COUNTER, String(next));
    return next;
  } catch {
    return 1;
  }
}

// ---------------------------------------------------------------------------
// Helper: extract upcoming fixture IDs from KV for use by odds/predictions
// ---------------------------------------------------------------------------
async function getUpcomingFixtureIds(env: Env): Promise<number[]> {
  try {
    const cached = await env.SPORTS_DATA.get<UpcomingFixturesCache>(KV.UPCOMING, 'json');
    if (cached?.fixtures) {
      return cached.fixtures.map((f) => f.id);
    }
  } catch {
    // fall through
  }
  return [];
}

// ---------------------------------------------------------------------------
// Cron: update all sports data
// ---------------------------------------------------------------------------
async function updateSportsData(env: Env): Promise<void> {
  console.log('[cron] Starting sports data refresh at', new Date().toISOString());

  const counter = await incrementCronCounter(env);
  console.log(`[cron] Run counter: ${counter}`);

  // Always-run updates (every cron)
  const alwaysResults = await Promise.allSettled([
    updateLiveFixtures(env),
    updateUpcomingFixtures(env),
    updateStandings(env),
    updateSerieBStandings(env),
    updateTopScorers(env),
    updateSerieBTopScorers(env),
    updateLiveStats(env),
  ]);

  const alwaysLabels = ['live', 'upcoming', 'standings', 'standings_serie_b', 'scorers', 'scorers_serie_b', 'live_stats'];
  alwaysResults.forEach((r, i) => {
    if (r.status === 'rejected') {
      console.error(`[cron] ${alwaysLabels[i]} update failed:`, r.reason);
    } else {
      console.log(`[cron] ${alwaysLabels[i]} update ok`);
    }
  });

  // Odds: every 2nd cron run
  if (counter % 2 === 0) {
    try {
      await updateOdds(env);
      console.log('[cron] odds update ok');
    } catch (err) {
      console.error('[cron] odds update failed:', err);
    }
  } else {
    console.log(`[cron] odds skipped (counter=${counter}, runs every 2nd)`);
  }

  // Predictions: every 4th cron run
  if (counter % 4 === 0) {
    try {
      await updatePredictions(env);
      console.log('[cron] predictions update ok');
    } catch (err) {
      console.error('[cron] predictions update failed:', err);
    }
  } else {
    console.log(`[cron] predictions skipped (counter=${counter}, runs every 4th)`);
  }
}

async function updateLiveFixtures(env: Env): Promise<void> {
  const fresh = await fetchLiveFixtures(env);
  if (fresh) {
    await env.SPORTS_DATA.put(KV.LIVE, JSON.stringify(fresh), { expirationTtl: 900 }); // 15 min TTL
    console.log(`[kv] Stored ${fresh.fixtures.length} live fixtures`);
  } else {
    // Preserve any existing fallback if already stored; write built-in otherwise
    const existing = await env.SPORTS_DATA.get(KV.LIVE, 'json');
    if (!existing) {
      await env.SPORTS_DATA.put(KV.LIVE, JSON.stringify(FALLBACK_LIVE), { expirationTtl: 900 });
      console.log('[kv] No API data — stored built-in fallback for live_fixtures');
    }
  }
}

async function updateUpcomingFixtures(env: Env): Promise<void> {
  const fresh = await fetchUpcomingFixtures(env);
  if (fresh) {
    await env.SPORTS_DATA.put(KV.UPCOMING, JSON.stringify(fresh), { expirationTtl: 1800 }); // 30 min TTL
    console.log(`[kv] Stored ${fresh.fixtures.length} upcoming fixtures`);
  } else {
    const existing = await env.SPORTS_DATA.get(KV.UPCOMING, 'json');
    if (!existing) {
      await env.SPORTS_DATA.put(KV.UPCOMING, JSON.stringify(FALLBACK_UPCOMING), { expirationTtl: 1800 });
      console.log('[kv] No API data — stored built-in fallback for upcoming_fixtures');
    }
  }
}

async function updateStandings(env: Env): Promise<void> {
  const fresh = await fetchStandings(env);
  if (fresh) {
    // Standings change slowly; keep for 6 hours
    await env.SPORTS_DATA.put(KV.STANDINGS, JSON.stringify(fresh), { expirationTtl: 21600 });
    console.log(`[kv] Stored ${fresh.standings.length} Brasileirão A standings rows`);
  }
}

async function updateSerieBStandings(env: Env): Promise<void> {
  const fresh = await fetchSerieBStandings(env);
  if (fresh) {
    await env.SPORTS_DATA.put(KV.STANDINGS_SERIE_B, JSON.stringify(fresh), { expirationTtl: 21600 });
    console.log(`[kv] Stored ${fresh.standings.length} Serie B standings rows`);
  }
}

async function updateTopScorers(env: Env): Promise<void> {
  const fresh = await fetchTopScorers(env);
  if (fresh) {
    await env.SPORTS_DATA.put(KV.SCORERS, JSON.stringify(fresh), { expirationTtl: 21600 });
    console.log(`[kv] Stored ${fresh.scorers.length} Brasileirão A top scorers`);
  }
}

async function updateSerieBTopScorers(env: Env): Promise<void> {
  const fresh = await fetchSerieBTopScorers(env);
  if (fresh) {
    await env.SPORTS_DATA.put(KV.SCORERS_SERIE_B, JSON.stringify(fresh), { expirationTtl: 21600 });
    console.log(`[kv] Stored ${fresh.scorers.length} Serie B top scorers`);
  }
}

async function updateLiveStats(env: Env): Promise<void> {
  const fresh = await fetchLiveFixturesV3(env);
  if (fresh.source === 'api') {
    await env.SPORTS_DATA.put(KV.LIVE_STATS, JSON.stringify(fresh), { expirationTtl: 300 }); // 5 min TTL
    console.log(`[kv] Stored live stats for ${Object.keys(fresh.stats).length} fixtures`);
  }
}

async function updateOdds(env: Env): Promise<void> {
  const fixtureIds = await getUpcomingFixtureIds(env);
  const fresh = await fetchOdds(env, fixtureIds);
  if (fresh.source === 'api') {
    await env.SPORTS_DATA.put(KV.ODDS, JSON.stringify(fresh), { expirationTtl: 1800 }); // 30 min TTL
    console.log(`[kv] Stored odds for ${fresh.odds.length} fixtures`);
  }
}

async function updatePredictions(env: Env): Promise<void> {
  const fixtureIds = await getUpcomingFixtureIds(env);
  const fresh = await fetchPredictions(env, fixtureIds);
  if (fresh.source === 'api') {
    await env.SPORTS_DATA.put(KV.PREDICTIONS, JSON.stringify(fresh), { expirationTtl: 21600 }); // 6 hour TTL
    console.log(`[kv] Stored predictions for ${fresh.predictions.length} fixtures`);
  }
}

// ---------------------------------------------------------------------------
// HTTP request handler
// ---------------------------------------------------------------------------
async function handleRequest(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders(request) });
  }

  if (request.method !== 'GET') {
    return json({ error: 'Method not allowed' }, 405, request);
  }

  const path = url.pathname;

  // GET /api/fixtures/live
  if (path === '/api/fixtures/live') {
    const data = await getFromKV<LiveFixturesCache>(env, KV.LIVE, FALLBACK_LIVE);
    return json(data, 200, request);
  }

  // GET /api/fixtures/upcoming
  if (path === '/api/fixtures/upcoming') {
    const data = await getFromKV<UpcomingFixturesCache>(env, KV.UPCOMING, FALLBACK_UPCOMING);
    return json(data, 200, request);
  }

  // GET /api/standings
  if (path === '/api/standings') {
    const data = await getFromKV<StandingsCache>(env, KV.STANDINGS, FALLBACK_STANDINGS);
    return json(data, 200, request);
  }

  // GET /api/standings/serie-b
  if (path === '/api/standings/serie-b') {
    const fallback: StandingsCache = { standings: [], leagueId: 72, season: new Date().getFullYear(), updated_at: null, source: 'fallback' };
    const data = await getFromKV<StandingsCache>(env, KV.STANDINGS_SERIE_B, fallback);
    return json(data, 200, request);
  }

  // GET /api/scorers
  if (path === '/api/scorers') {
    const data = await getFromKV<ScorersCache>(env, KV.SCORERS, FALLBACK_SCORERS);
    return json(data, 200, request);
  }

  // GET /api/scorers/serie-b
  if (path === '/api/scorers/serie-b') {
    const fallback: ScorersCache = { scorers: [], leagueId: 72, season: new Date().getFullYear(), updated_at: null, source: 'fallback' };
    const data = await getFromKV<ScorersCache>(env, KV.SCORERS_SERIE_B, fallback);
    return json(data, 200, request);
  }

  // GET /api/odds
  if (path === '/api/odds') {
    const fallback: OddsCache = { odds: [], updated_at: null, source: 'fallback' };
    const data = await getFromKV<OddsCache>(env, KV.ODDS, fallback);
    return json(data, 200, request);
  }

  // GET /api/live-stats
  if (path === '/api/live-stats') {
    const fallback: LiveStatsCache = { stats: {}, updated_at: null, source: 'fallback' };
    const data = await getFromKV<LiveStatsCache>(env, KV.LIVE_STATS, fallback);
    return json(data, 200, request);
  }

  // GET /api/predictions
  if (path === '/api/predictions') {
    const fallback: PredictionsCache = { predictions: [], updated_at: null, source: 'fallback' };
    const data = await getFromKV<PredictionsCache>(env, KV.PREDICTIONS, fallback);
    return json(data, 200, request);
  }

  // POST-ish (GET with token) /api/admin/refresh — manually trigger the cron update job.
  // Protected by a shared token to avoid hammering the upstream API.
  if (path === '/api/admin/refresh') {
    const token = url.searchParams.get('token');
    if (!env.REFRESH_TOKEN || token !== env.REFRESH_TOKEN) {
      return json({ error: 'unauthorized' }, 401, request);
    }
    await updateSportsData(env);
    return json({ status: 'refresh_triggered', time: new Date().toISOString() }, 200, request);
  }

  // GET /api/health
  if (path === '/api/health') {
    return json({
      status: 'ok',
      engine: 'betbr-sports-engine v1.1.0',
      time: new Date().toISOString(),
      endpoints: [
        '/api/fixtures/live',
        '/api/fixtures/upcoming',
        '/api/standings',
        '/api/standings/serie-b',
        '/api/scorers',
        '/api/scorers/serie-b',
        '/api/odds',
        '/api/live-stats',
        '/api/predictions',
      ],
    }, 200, request);
  }

  // Root — list available endpoints
  return json({
    engine: 'betbr-sports-engine v1.1.0',
    endpoints: {
      live: '/api/fixtures/live',
      upcoming: '/api/fixtures/upcoming',
      standings: '/api/standings',
      standings_serie_b: '/api/standings/serie-b',
      scorers: '/api/scorers',
      scorers_serie_b: '/api/scorers/serie-b',
      odds: '/api/odds',
      live_stats: '/api/live-stats',
      predictions: '/api/predictions',
      health: '/api/health',
    },
  }, 200, request);
}

// ---------------------------------------------------------------------------
// Worker export
// ---------------------------------------------------------------------------
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return handleRequest(request, env);
  },

  async scheduled(_event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(updateSportsData(env));
  },
};
