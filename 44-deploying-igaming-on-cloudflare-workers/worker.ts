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
 * AcmeToCasino Platform - Main Worker Entry Point
 *
 * This file is the canonical entry point described in Chapter 44 Section 7.
 * It is equivalent to index.ts but uses the security.ts module for its
 * security pipeline rather than inlining the logic — demonstrating the
 * production-ready factored architecture.
 *
 * Route map:
 *   OPTIONS *              → CORS preflight
 *   GET  /health           → Health check (unauthenticated)
 *   GET  / | /play         → Edge-rendered frontend lobby
 *   GET  /api/auth/*       → auth.ts
 *   POST /api/auth/*       → auth.ts
 *   *    /api/games/*      → games.ts
 *   *    /api/wallet/*     → wallet.ts
 *   *    /api/kyc/*        → kyc.ts
 *   *    /api/compliance/* → compliance.ts
 *   *    /api/payments/*   → payments.ts
 *   *    /api/analytics/*  → analytics.ts
 *
 * Security pipeline (per request, before route dispatch):
 *   1. CORS preflight shortcut
 *   2. Health / frontend shortcuts (no security checks applied)
 *   3. Threat score check     → 403 if cf.threat_score > 50
 *   4. Bot score check        → 403 if cf.botManagement.score < 20 AND NOT verifiedBot
 *   5. Jurisdiction block     → 451 if country in BLOCKED_JURISDICTIONS
 *   6. Rate limit check       → 429 if > 200 req/60s per IP (in-memory, no KV)
 *   7. Route dispatch
 *   8. Security headers applied to all responses
 */

import { Env, handleCORS, errorResponse } from './utils.js';
import { handleAuth } from './auth.js';
import { handleGames } from './games.js';
import { handleWallet } from './wallet.js';
import { handleKyc } from './kyc.js';
import { handleCompliance } from './compliance.js';
import { handlePayments } from './payments.js';
import { handleAnalytics } from './analytics.js';
import { serveFrontend } from './frontend.js';
import {
  checkThreatAndBot,
  checkJurisdiction,
  checkRateLimit,
  logSecurityEvent,
  applySecurityHeaders,
} from './security.js';

// ─── Main fetch handler ────────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;

    // ── Step 1: CORS preflight ─────────────────────────────────────────────
    if (request.method === 'OPTIONS') {
      return handleCORS();
    }

    // ── Step 2a: Health check — unauthenticated, no DB, no security checks ──
    if (pathname === '/health') {
      return applySecurityHeaders(buildHealthResponse(env));
    }

    // ── Step 2b: Edge-rendered frontend lobby ─────────────────────────────
    if (pathname === '/' || pathname === '/play') {
      try {
        const frontendResponse = await serveFrontend(env);
        return applySecurityHeaders(frontendResponse);
      } catch (err) {
        console.error('Frontend render error:', err);
        return applySecurityHeaders(errorResponse('Frontend unavailable', 503));
      }
    }

    try {
      // ── Step 3 & 4: Threat score + bot detection ───────────────────────
      const threatResult = checkThreatAndBot(request);
      if (!threatResult.allowed) {
        // Fire-and-forget D1 insert after response is flushed
        ctx.waitUntil(
          logSecurityEvent(request, threatResult.threatType ?? 'security_block', env, 2)
        );
        return applySecurityHeaders(threatResult.response!);
      }

      // ── Step 5: Jurisdiction block ─────────────────────────────────────
      const geoResult = checkJurisdiction(request, pathname);
      if (!geoResult.allowed) {
        ctx.waitUntil(
          logSecurityEvent(request, 'blocked_jurisdiction', env, 1)
        );
        return applySecurityHeaders(geoResult.response!);
      }

      // ── Step 6: In-memory rate limit (no KV writes) ────────────────────
      const rateResult = checkRateLimit(request);
      if (!rateResult.allowed) {
        return applySecurityHeaders(rateResult.response!);
      }

      // ── Step 7: Route dispatch ─────────────────────────────────────────
      let response: Response;

      if (pathname.startsWith('/api/auth')) {
        response = await handleAuth(request, env);
      } else if (pathname.startsWith('/api/games')) {
        response = await handleGames(request, env);
      } else if (pathname.startsWith('/api/wallet')) {
        response = await handleWallet(request, env);
      } else if (pathname.startsWith('/api/kyc')) {
        response = await handleKyc(request, env);
      } else if (pathname.startsWith('/api/compliance')) {
        response = await handleCompliance(request, env);
      } else if (pathname.startsWith('/api/payments')) {
        response = await handlePayments(request, env);
      } else if (pathname.startsWith('/api/analytics')) {
        response = await handleAnalytics(request, env);
      } else {
        response = errorResponse('Not found', 404);
      }

      // ── Step 8: Apply security headers to every API response ───────────
      return applySecurityHeaders(response);

    } catch (err) {
      // Unhandled errors are logged to D1 asynchronously
      console.error('Unhandled worker error:', err);
      ctx.waitUntil(logSecurityEvent(request, 'unhandled_error', env, 3));

      const isDev = env.ENVIRONMENT === 'development';
      const message =
        isDev && err instanceof Error ? err.message : 'Internal server error';

      return applySecurityHeaders(
        new Response(JSON.stringify({ success: false, error: message }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        })
      );
    }
  },
};

// ─── Health check ──────────────────────────────────────────────────────────

function buildHealthResponse(env: Env): Response {
  return new Response(
    JSON.stringify({
      status: 'healthy',
      platform: env.PLATFORM_NAME ?? 'AcmeToCasino Platform',
      environment: env.ENVIRONMENT ?? 'unknown',
      timestamp: new Date().toISOString(),
      version: '1.0.0',
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }
  );
}
