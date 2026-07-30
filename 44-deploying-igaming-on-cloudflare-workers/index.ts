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
 * AcmeToCasino Platform - Cloudflare Worker Entry Point
 * Main API gateway: routing, CORS, rate limiting, security monitoring
 */

import { Env, handleCORS, errorResponse, getClientIP } from './utils.js';
import { handleAuth } from './auth.js';
import { handleGames } from './games.js';
import { handleWallet } from './wallet.js';
import { handleKyc } from './kyc.js';
import { handleCompliance, isBlockedJurisdiction } from './compliance.js';
import { serveFrontend } from './frontend.js';

// ─── Main fetch handler ────────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return handleCORS();
    }

    // Health check (unauthenticated)
    if (url.pathname === '/health') {
      return healthCheck(env);
    }

    // Frontend landing page with game lobby
    if (url.pathname === '/' || url.pathname === '/play') {
      return serveFrontend(env);
    }

    try {
      // ── Security layer ──────────────────────────────────────────────────

      // Check Cloudflare threat score
      const cf = (request as Request & { cf?: { threat_score?: number; country?: string; botManagement?: { score?: number; verifiedBot?: boolean } } }).cf;
      const threatScore = cf?.threat_score ?? 0;
      if (threatScore > 50) {
        ctx.waitUntil(logSecurityEvent(request, 'high_threat_score', env));
        return errorResponse('Access denied', 403);
      }

      // Block bots (allow verified search-engine bots)
      const botScore = cf?.botManagement?.score ?? 100;
      const isVerifiedBot = cf?.botManagement?.verifiedBot ?? false;
      if (botScore < 20 && !isVerifiedBot) {
        return errorResponse('Access denied', 403);
      }

      // Jurisdiction block (skip for compliance routes so the check endpoint works)
      const country = cf?.country ?? 'unknown';
      if (!url.pathname.startsWith('/api/compliance') && isBlockedJurisdiction(country)) {
        return errorResponse('This service is not available in your region.', 451);
      }

      // ── Rate limiting ───────────────────────────────────────────────────

      const ip = getClientIP(request);
      const rateLimitKey = `rate:${ip}`;
      const currentCount = parseInt(
        (await env.CACHE.get(rateLimitKey)) ?? '0', 10
      );

      if (currentCount >= 200) {
        return new Response('Rate limit exceeded', {
          status: 429,
          headers: {
            'Retry-After': '60',
            'Content-Type': 'application/json',
          },
        });
      }

      // Increment counter (fire and forget)
      ctx.waitUntil(
        env.CACHE.put(rateLimitKey, String(currentCount + 1), { expirationTtl: 60 })
      );

      // ── Route dispatch ──────────────────────────────────────────────────

      const path = url.pathname;

      if (path.startsWith('/api/auth')) {
        return await handleAuth(request, env);
      }

      if (path.startsWith('/api/games')) {
        return await handleGames(request, env);
      }

      if (path.startsWith('/api/wallet')) {
        return await handleWallet(request, env);
      }

      if (path.startsWith('/api/kyc')) {
        return await handleKyc(request, env);
      }

      if (path.startsWith('/api/compliance')) {
        return await handleCompliance(request, env);
      }

      return errorResponse('Not found', 404);

    } catch (err) {
      console.error('Unhandled worker error:', err);
      ctx.waitUntil(logSecurityEvent(request, 'unhandled_error', env));

      const isDev = env.ENVIRONMENT === 'development';
      return new Response(
        JSON.stringify({
          success: false,
          error: isDev && err instanceof Error ? err.message : 'Internal server error',
        }),
        { status: 500, headers: { 'Content-Type': 'application/json' } }
      );
    }
  },
};

// ─── Health check ──────────────────────────────────────────────────────────

function healthCheck(env: Env): Response {
  return new Response(
    JSON.stringify({
      status: 'healthy',
      platform: env.PLATFORM_NAME ?? 'AcmeToCasino Platform',
      environment: env.ENVIRONMENT ?? 'unknown',
      timestamp: new Date().toISOString(),
      version: '1.0.0',
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } }
  );
}

// ─── Security event logging ────────────────────────────────────────────────

async function logSecurityEvent(
  request: Request,
  eventType: string,
  env: Env
): Promise<void> {
  try {
    const cf = (request as Request & { cf?: { threat_score?: number; country?: string } }).cf;
    await env.DB.prepare(
      'INSERT INTO security_events (ip, event_type, details) VALUES (?, ?, ?)'
    )
      .bind(
        getClientIP(request),
        eventType,
        JSON.stringify({
          url: request.url,
          method: request.method,
          country: cf?.country,
          threatScore: cf?.threat_score,
          userAgent: request.headers.get('User-Agent'),
          cfRay: request.headers.get('CF-RAY'),
        })
      )
      .run();
  } catch (err) {
    // Non-fatal — log to console if DB write fails
    console.error('Failed to log security event:', err);
  }
}
