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
 * AcmeToCasino Platform - Analytics
 *
 * Cloudflare Analytics Engine integration for player and game analytics.
 * Also proxies Cloudflare GraphQL Analytics API to expose Worker metrics
 * to the back-office dashboard without leaking API tokens to the frontend.
 *
 * Endpoints:
 *   GET /api/analytics/metrics      — Worker invocation metrics (GraphQL proxy)
 *   GET /api/analytics/costs        — Monthly cost estimate vs free/paid thresholds
 *   GET /api/analytics/players      — Active player summary (D1)
 *   GET /api/analytics/games        — Top games by wager count (D1)
 *   POST /api/analytics/event       — Record a custom analytics event
 */

import {
  Env,
  successResponse,
  errorResponse,

  unauthorizedResponse,
} from './utils.js';
import { authenticateRequest } from './auth.js';

// ─── Types ─────────────────────────────────────────────────────────────────

interface WorkerMetric {
  workerId: string;
  date: string;
  requests: number;
  errors: number;
  cpuTimeP50: number;
  cpuTimeP99: number;
}

interface CostEstimate {
  plan: 'free' | 'paid';
  requestsThisMonth: number;
  requestAllowance: number;
  requestOverage: number;
  requestCost: number;
  estimatedMonthlyCost: number;
  comparison: {
    vpsMonthly: number;
    savingsVsVps: number;
  };
}

interface AnalyticsEvent {
  eventType: string;
  userId?: number;
  gameId?: string;
  amount?: number;
  currency?: string;
  metadata?: Record<string, unknown>;
}

// Workers being tracked — matches the five workers in the portfolio
const TRACKED_WORKERS = [
  'acmetocasino-api',
  'acmevegas-api',
  'acmegate-api',
  'acmedice-api',
];

// ─── Route handler ──────────────────────────────────────────────────────────

export async function handleAnalytics(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const { method } = request;

  // Public cost endpoint — no auth required for the book demo
  if (method === 'GET' && url.pathname === '/api/analytics/costs') {
    return handleCostEstimate(env);
  }

  // All other analytics endpoints require admin or staff role
  const user = await authenticateRequest(request, env);
  if (!user) return unauthorizedResponse();
  if (user.role !== 'admin' && user.role !== 'staff') {
    return errorResponse('Insufficient permissions', 403);
  }

  if (method === 'GET' && url.pathname === '/api/analytics/metrics') {
    return handleWorkerMetrics(env);
  }

  if (method === 'GET' && url.pathname === '/api/analytics/players') {
    return handlePlayerSummary(env);
  }

  if (method === 'GET' && url.pathname === '/api/analytics/games') {
    return handleTopGames(url, env);
  }

  if (method === 'POST' && url.pathname === '/api/analytics/event') {
    return handleRecordEvent(request, env);
  }

  return errorResponse('Not found', 404);
}

// ─── Worker metrics (Cloudflare GraphQL Analytics proxy) ───────────────────

/**
 * Proxies the Cloudflare GraphQL Analytics API to fetch invocation metrics
 * for all tracked workers. Falls back to simulated metrics when the API
 * is unavailable — keeps the back-office dashboard operational during
 * Cloudflare maintenance windows.
 */
async function handleWorkerMetrics(env: Env): Promise<Response> {
  try {
    const metrics = await fetchWorkerMetrics(env);
    return successResponse({ workers: metrics, fetchedAt: new Date().toISOString() });
  } catch (err) {
    console.warn('GraphQL analytics unavailable, returning simulated metrics:', err);
    const simulated = simulatedMetrics();
    return successResponse({
      workers: simulated,
      fetchedAt: new Date().toISOString(),
      simulated: true,
    });
  }
}

async function fetchWorkerMetrics(env: Env): Promise<WorkerMetric[]> {
  // Cloudflare GraphQL Analytics — queries workersInvocationsAdaptive for the
  // last 24 hours across all tracked workers.
  const query = `{
    viewer {
      accounts(filter: { accountTag_in: ["${env.CF_ZONE_ID}"] }) {
        workersInvocationsAdaptive(
          limit: 100,
          filter: {
            datetime_geq: "${new Date(Date.now() - 86400000).toISOString()}",
            datetime_leq: "${new Date().toISOString()}"
          }
        ) {
          dimensions { scriptName date }
          sum { requests errors }
          quantiles { cpuTimeP50 cpuTimeP99 }
        }
      }
    }
  }`;

  const resp = await fetch('https://api.cloudflare.com/client/v4/graphql', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${env.CF_API_TOKEN}`,
    },
    body: JSON.stringify({ query }),
  });

  if (!resp.ok) {
    throw new Error(`GraphQL API returned ${resp.status}`);
  }

  const json = (await resp.json()) as {
    data?: {
      viewer?: {
        accounts?: Array<{
          workersInvocationsAdaptive?: Array<{
            dimensions: { scriptName: string; date: string };
            sum: { requests: number; errors: number };
            quantiles: { cpuTimeP50: number; cpuTimeP99: number };
          }>;
        }>;
      };
    };
    errors?: unknown[];
  };

  if (json.errors?.length) {
    throw new Error('GraphQL returned errors');
  }

  const rows =
    json.data?.viewer?.accounts?.[0]?.workersInvocationsAdaptive ?? [];

  return rows
    .filter((r) => TRACKED_WORKERS.includes(r.dimensions.scriptName))
    .map((r) => ({
      workerId: r.dimensions.scriptName,
      date: r.dimensions.date,
      requests: r.sum.requests,
      errors: r.sum.errors,
      cpuTimeP50: r.quantiles.cpuTimeP50,
      cpuTimeP99: r.quantiles.cpuTimeP99,
    }));
}

function simulatedMetrics(): WorkerMetric[] {
  const today = new Date().toISOString().split('T')[0];
  return TRACKED_WORKERS.map((workerId) => ({
    workerId,
    date: today,
    requests: Math.floor(Math.random() * 50000) + 10000,
    errors: Math.floor(Math.random() * 20),
    cpuTimeP50: 4 + Math.random() * 3,
    cpuTimeP99: 18 + Math.random() * 12,
  }));
}

// ─── Cost estimate ──────────────────────────────────────────────────────────

/**
 * Estimates monthly Cloudflare costs based on D1-derived request counts.
 * Includes a comparison against a typical VPS deployment to contextualise
 * the cost advantage described in Section 11 of the chapter.
 */
async function handleCostEstimate(env: Env): Promise<Response> {
  const monthStart = new Date();
  monthStart.setDate(1);
  monthStart.setHours(0, 0, 0, 0);

  const result = await env.DB.prepare(
    `SELECT COUNT(*) AS cnt FROM security_events
     WHERE created_at >= ?`
  )
    .bind(monthStart.toISOString())
    .first<{ cnt: number }>();

  // Security events ≈ a proxy for request volume (logged on subset of requests).
  // Real analytics would come from the GraphQL endpoint above.
  const estimatedRequests = (result?.cnt ?? 0) * 50;

  const FREE_ALLOWANCE = 100_000;     // requests/day × ~30 = 3M/month approximation
  const PAID_ALLOWANCE = 10_000_000;  // included in paid plan

  const freeEstimate = buildCostEstimate('free', estimatedRequests, FREE_ALLOWANCE);
  const paidEstimate = buildCostEstimate('paid', estimatedRequests, PAID_ALLOWANCE);

  return successResponse({
    periodStart: monthStart.toISOString(),
    estimatedRequests,
    scenarios: { free: freeEstimate, paid: paidEstimate },
    recommendation:
      estimatedRequests > FREE_ALLOWANCE
        ? 'Upgrade to paid plan ($5/month) — free-tier request allowance exceeded.'
        : 'Free tier sufficient for current traffic.',
  });
}

function buildCostEstimate(
  plan: 'free' | 'paid',
  requests: number,
  allowance: number
): CostEstimate {
  const baseCost = plan === 'paid' ? 5 : 0;
  const requestOverage = Math.max(0, requests - allowance);
  const requestCost = (requestOverage / 1_000_000) * 0.3;
  const estimatedMonthlyCost = baseCost + requestCost;

  // Conservative VPS baseline: $40/month for equivalent capacity
  const vpsMonthly = 40;

  return {
    plan,
    requestsThisMonth: requests,
    requestAllowance: allowance,
    requestOverage,
    requestCost: Math.round(requestCost * 100) / 100,
    estimatedMonthlyCost: Math.round(estimatedMonthlyCost * 100) / 100,
    comparison: {
      vpsMonthly,
      savingsVsVps: Math.round((vpsMonthly - estimatedMonthlyCost) * 100) / 100,
    },
  };
}

// ─── Player summary ─────────────────────────────────────────────────────────

async function handlePlayerSummary(env: Env): Promise<Response> {
  const [totals, statusBreakdown, recent] = await Promise.all([
    env.DB.prepare(
      `SELECT
         COUNT(*)          AS total_players,
         SUM(balance)      AS total_balance,
         AVG(balance)      AS avg_balance
       FROM users`
    ).first<{ total_players: number; total_balance: number; avg_balance: number }>(),

    env.DB.prepare(
      `SELECT status, COUNT(*) AS cnt FROM users GROUP BY status`
    ).all<{ status: string; cnt: number }>(),

    env.DB.prepare(
      `SELECT COUNT(*) AS cnt FROM users
       WHERE created_at >= datetime('now','-24 hours')`
    ).first<{ cnt: number }>(),
  ]);

  return successResponse({
    totalPlayers: totals?.total_players ?? 0,
    totalBalance: Math.round((totals?.total_balance ?? 0) * 100) / 100,
    avgBalance: Math.round((totals?.avg_balance ?? 0) * 100) / 100,
    byStatus: statusBreakdown.results,
    registeredLast24h: recent?.cnt ?? 0,
    asOf: new Date().toISOString(),
  });
}

// ─── Top games ──────────────────────────────────────────────────────────────

async function handleTopGames(url: URL, env: Env): Promise<Response> {
  const limit = Math.min(parseInt(url.searchParams.get('limit') ?? '10', 10), 50);

  const rows = await env.DB.prepare(
    `SELECT
       g.game_id,
       g.name,
       g.provider,
       g.category,
       COUNT(t.id)        AS wager_count,
       SUM(t.amount)      AS total_wagered,
       AVG(t.amount)      AS avg_wager
     FROM games g
     LEFT JOIN transactions t
       ON t.reference_id = g.game_id AND t.type = 'wager'
     WHERE g.is_active = 1
     GROUP BY g.game_id
     ORDER BY wager_count DESC
     LIMIT ?`
  )
    .bind(limit)
    .all<{
      game_id: string;
      name: string;
      provider: string;
      category: string;
      wager_count: number;
      total_wagered: number;
      avg_wager: number;
    }>();

  return successResponse({
    games: rows.results.map((g) => ({
      ...g,
      total_wagered: Math.round((g.total_wagered ?? 0) * 100) / 100,
      avg_wager: Math.round((g.avg_wager ?? 0) * 100) / 100,
    })),
    count: rows.results.length,
    asOf: new Date().toISOString(),
  });
}

// ─── Record custom event ────────────────────────────────────────────────────

/**
 * Records a custom analytics event to the compliance_events table.
 * In a full Analytics Engine deployment, this would write to an AE dataset
 * instead; the D1 approach is used here to stay within the chapter's
 * infrastructure scope while still providing a queryable audit trail.
 */
async function handleRecordEvent(request: Request, env: Env): Promise<Response> {
  let body: AnalyticsEvent;
  try {
    body = (await request.json()) as AnalyticsEvent;
  } catch {
    return errorResponse('Invalid JSON body', 400);
  }

  if (!body.eventType || typeof body.eventType !== 'string') {
    return errorResponse('eventType is required', 422);
  }

  // Prefix with "analytics:" so events are distinguishable from compliance events
  const eventType = `analytics:${body.eventType.slice(0, 64)}`;
  const details = JSON.stringify({
    gameId: body.gameId,
    amount: body.amount,
    currency: body.currency,
    metadata: body.metadata,
    recordedAt: new Date().toISOString(),
  });

  if (body.userId) {
    await env.DB.prepare(
      'INSERT INTO compliance_events (user_id, event_type, details) VALUES (?, ?, ?)'
    )
      .bind(body.userId, eventType, details)
      .run();
  } else {
    // Anonymous event — store in security_events with severity 0
    await env.DB.prepare(
      'INSERT INTO security_events (ip, event_type, details, severity) VALUES (?, ?, ?, 0)'
    )
      .bind('analytics', eventType, details)
      .run();
  }

  return successResponse({ recorded: true, eventType });
}

// ─── Internal helper used by other modules ──────────────────────────────────

/**
 * Lightweight fire-and-forget analytics ping.
 * Wraps a D1 insert that can be handed to ctx.waitUntil.
 */
export async function recordAnalyticsEvent(
  env: Env,
  eventType: string,
  userId: number | null,
  payload: Record<string, unknown>
): Promise<void> {
  try {
    const details = JSON.stringify({ ...payload, recordedAt: new Date().toISOString() });
    if (userId !== null) {
      await env.DB.prepare(
        'INSERT INTO compliance_events (user_id, event_type, details) VALUES (?, ?, ?)'
      )
        .bind(userId, `analytics:${eventType}`, details)
        .run();
    } else {
      await env.DB.prepare(
        'INSERT INTO security_events (ip, event_type, details, severity) VALUES (?, ?, ?, 0)'
      )
        .bind('analytics', `analytics:${eventType}`, details)
        .run();
    }
  } catch (err) {
    // Non-fatal — analytics must not degrade the critical path
    console.error('recordAnalyticsEvent failed:', err);
  }
}
