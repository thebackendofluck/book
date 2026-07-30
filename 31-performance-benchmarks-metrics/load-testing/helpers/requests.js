// Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Reusable HTTP request helpers — iGaming platform
 * =================================================
 * All API calls live here. Scenarios import these functions rather than
 * constructing raw http.* calls inline. This keeps scenario scripts readable
 * and centralises URL and header management.
 *
 * Every function returns the raw k6 Response object so callers can run
 * additional checks or extract data as needed.
 */

import http from 'k6/http';
import { ENDPOINTS, HTTP_PARAMS } from '../config.js';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';
import {
  loginDuration,
  lobbyLoadDuration,
  gameLaunchDuration,
  betPlacementDuration,
  betPlacementSuccess,
  walletOpDuration,
  walletOpSuccess,
  rngVerifyDuration,
  oddsRefreshDuration,
  wsConnectDuration,
} from './checks.js';

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Build authenticated request params, merging defaults with any extras.
 * @param {string} token  - Bearer token
 * @param {object} extras - Additional params (tags, headers, timeout …)
 */
export function authParams(token, extras = {}) {
  const merged = Object.assign({}, HTTP_PARAMS, extras);
  merged.headers = Object.assign({}, HTTP_PARAMS.headers, extras.headers || {}, {
    'Authorization':  `Bearer ${token}`,
    'X-Request-ID':   `k6-${__VU}-${Date.now()}-${randomIntBetween(1000, 9999)}`,
    'X-Correlation-ID': `k6-${__ITER}-${__VU}`,
  });
  return merged;
}

/**
 * Measure a request and add the duration to a Trend metric.
 * @param {Function} fn       - Function that issues the request and returns the response
 * @param {object}   trend    - k6 Trend metric to record to
 * @param {object}   [rate]   - Optional k6 Rate metric; records success (status < 400)
 * @returns {Response}
 */
function measured(fn, trend, rate) {
  const start = Date.now();
  const res = fn();
  trend.add(Date.now() - start);
  if (rate) rate.add(res.status < 400);
  return res;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/**
 * Login with email/password.
 * Returns { token, refreshToken, playerId } or null on failure.
 */
export function login(email, password) {
  const res = measured(
    () => http.post(
      ENDPOINTS.login,
      JSON.stringify({ email, password }),
      Object.assign({}, HTTP_PARAMS, { tags: { name: 'auth/login' } }),
    ),
    loginDuration,
  );

  if (res.status !== 200) return null;
  try {
    const body = JSON.parse(res.body);
    return {
      token:        body.token || body.access_token,
      refreshToken: body.refresh_token,
      playerId:     body.player_id || body.user_id,
    };
  } catch (_) {
    return null;
  }
}

/**
 * Logout and invalidate the session.
 */
export function logout(token) {
  return http.post(
    ENDPOINTS.logout,
    null,
    authParams(token, { tags: { name: 'auth/logout' } }),
  );
}

/**
 * Register a new player account.
 * `playerData` should include: email, password, firstName, lastName,
 * dateOfBirth, country, currency.
 */
export function register(playerData) {
  return http.post(
    ENDPOINTS.register,
    JSON.stringify(playerData),
    Object.assign({}, HTTP_PARAMS, { tags: { name: 'auth/register' } }),
  );
}

/**
 * Refresh an expiring JWT without re-authenticating.
 */
export function refreshToken(refreshToken) {
  return http.post(
    ENDPOINTS.refreshToken,
    JSON.stringify({ refresh_token: refreshToken }),
    Object.assign({}, HTTP_PARAMS, { tags: { name: 'auth/refresh' } }),
  );
}

// ---------------------------------------------------------------------------
// Account
// ---------------------------------------------------------------------------

export function getProfile(token) {
  return http.get(
    ENDPOINTS.profile,
    authParams(token, { tags: { name: 'account/profile' } }),
  );
}

export function getBalance(token) {
  return http.get(
    ENDPOINTS.balance,
    authParams(token, { tags: { name: 'account/balance' } }),
  );
}

export function getResponsibleGamingLimits(token) {
  return http.get(
    ENDPOINTS.limits,
    authParams(token, { tags: { name: 'account/limits' } }),
  );
}

export function getTransactionHistory(token, page = 1, limit = 20) {
  return http.get(
    `${ENDPOINTS.history}?page=${page}&limit=${limit}`,
    authParams(token, { tags: { name: 'account/history' } }),
  );
}

// ---------------------------------------------------------------------------
// Wallet
// ---------------------------------------------------------------------------

/**
 * Initiate a deposit.
 * @param {string} token
 * @param {number} amount      - Amount in EUR
 * @param {string} method      - e.g. 'card', 'trustly'
 * @param {string} [currency]  - Default 'EUR'
 */
export function deposit(token, amount, method, currency = 'EUR') {
  return measured(
    () => http.post(
      ENDPOINTS.deposit,
      JSON.stringify({
        amount,
        currency,
        payment_method: method,
        // Card details are tokenised; use the test token for load testing
        payment_token: `test-card-token-${randomIntBetween(1, 10)}`,
      }),
      authParams(token, { tags: { name: 'wallet/deposit' } }),
    ),
    walletOpDuration,
    walletOpSuccess,
  );
}

/**
 * Request a withdrawal.
 */
export function withdrawal(token, amount, method, currency = 'EUR') {
  return measured(
    () => http.post(
      ENDPOINTS.withdrawal,
      JSON.stringify({ amount, currency, payment_method: method }),
      authParams(token, { tags: { name: 'wallet/withdrawal' } }),
    ),
    walletOpDuration,
    walletOpSuccess,
  );
}

export function getWalletTransactions(token, page = 1) {
  return http.get(
    `${ENDPOINTS.transactions}?page=${page}&limit=20`,
    authParams(token, { tags: { name: 'wallet/transactions' } }),
  );
}

// ---------------------------------------------------------------------------
// Casino lobby
// ---------------------------------------------------------------------------

/**
 * Load the full casino lobby including featured games and banners.
 */
export function getLobby(token) {
  return measured(
    () => http.get(
      ENDPOINTS.lobby,
      authParams(token, { tags: { name: 'casino/lobby' } }),
    ),
    lobbyLoadDuration,
  );
}

/**
 * Load games for a specific category.
 * @param {string} category - e.g. 'slots', 'table-games', 'live-casino'
 */
export function getGamesByCategory(token, category) {
  return http.get(
    ENDPOINTS.gameList(category),
    authParams(token, { tags: { name: 'casino/game-list' } }),
  );
}

export function getGameDetail(token, gameId) {
  return http.get(
    ENDPOINTS.gameDetail(gameId),
    authParams(token, { tags: { name: 'casino/game-detail' } }),
  );
}

/**
 * Launch a game — returns a session URL and session ID.
 * @param {string} token
 * @param {string} gameId
 * @param {object} opts   - { mode: 'real'|'demo', platform: 'desktop'|'mobile' }
 */
export function launchGame(token, gameId, opts = {}) {
  return measured(
    () => http.post(
      ENDPOINTS.gameLaunch(gameId),
      JSON.stringify({
        mode:     opts.mode     || 'real',
        platform: opts.platform || 'desktop',
        language: opts.language || 'en',
        currency: opts.currency || 'EUR',
      }),
      authParams(token, { tags: { name: 'casino/game-launch' } }),
    ),
    gameLaunchDuration,
  );
}

/**
 * Heartbeat for an active game session. Providers require periodic pings
 * to keep sessions alive; use every 30-60 s.
 */
export function pingGameSession(token, sessionId) {
  return http.put(
    ENDPOINTS.gameSession(sessionId),
    JSON.stringify({ action: 'heartbeat' }),
    authParams(token, { tags: { name: 'casino/session-ping' } }),
  );
}

// ---------------------------------------------------------------------------
// Bet placement (casino rounds + sportsbook)
// ---------------------------------------------------------------------------

/**
 * Place a casino bet (spin / round).
 * @param {string} token
 * @param {string} sessionId  - Active game session ID
 * @param {number} stake      - Bet amount in EUR
 * @param {object} metadata   - Game-specific data (lines, coin size, etc.)
 */
export function placeCasinoBet(token, sessionId, stake, metadata = {}) {
  return measured(
    () => http.post(
      ENDPOINTS.betPlace,
      JSON.stringify({
        session_id: sessionId,
        stake,
        currency: 'EUR',
        bet_type:  'casino_round',
        metadata,
      }),
      authParams(token, { tags: { name: 'bet/casino-place' } }),
    ),
    betPlacementDuration,
    betPlacementSuccess,
  );
}

/**
 * Place a sportsbook bet.
 * @param {string} token
 * @param {string} eventId
 * @param {string} marketId
 * @param {string} selectionId
 * @param {number} odds
 * @param {number} stake
 * @param {object} opts       - { betType, acceptOddsChanges }
 */
export function placeSportsBet(token, eventId, marketId, selectionId, odds, stake, opts = {}) {
  return measured(
    () => http.post(
      ENDPOINTS.betPlace,
      JSON.stringify({
        event_id:           eventId,
        market_id:          marketId,
        selection_id:       selectionId,
        odds,
        stake,
        currency:           'EUR',
        bet_type:           opts.betType || 'single',
        accept_odds_changes: opts.acceptOddsChanges !== false,
      }),
      authParams(token, { tags: { name: 'bet/sports-place' } }),
    ),
    betPlacementDuration,
    betPlacementSuccess,
  );
}

/**
 * Simulate bet settlement. This is typically called by the platform
 * automatically; in load tests we call it directly to verify the
 * settlement pipeline under load.
 */
export function settleBet(token, betId, outcome) {
  return http.post(
    ENDPOINTS.betSettle(betId),
    JSON.stringify({ outcome }),
    authParams(token, { tags: { name: 'bet/settle' } }),
  );
}

/**
 * Cash out a live bet at current value.
 */
export function cashOutBet(token, betId) {
  return measured(
    () => http.post(
      ENDPOINTS.betCashout(betId),
      JSON.stringify({ accept_value: true }),
      authParams(token, { tags: { name: 'bet/cashout' } }),
    ),
    betPlacementDuration,
    betPlacementSuccess,
  );
}

export function getActiveBets(token) {
  return http.get(
    ENDPOINTS.betsActive,
    authParams(token, { tags: { name: 'bet/active' } }),
  );
}

export function getSettledBets(token, eventId) {
  const qs = eventId ? `?event_id=${eventId}` : '';
  return http.get(
    `${ENDPOINTS.betsSettled}${qs}`,
    authParams(token, { tags: { name: 'bet/settled' } }),
  );
}

// ---------------------------------------------------------------------------
// Sportsbook / odds
// ---------------------------------------------------------------------------

export function getInPlayEvents(token) {
  return http.get(
    ENDPOINTS.inPlay,
    authParams(token, { tags: { name: 'sports/in-play' } }),
  );
}

export function getEventDetail(token, eventId) {
  return http.get(
    ENDPOINTS.eventDetail(eventId),
    authParams(token, { tags: { name: 'sports/event' } }),
  );
}

export function getEventMarkets(token, eventId) {
  return http.get(
    ENDPOINTS.eventMarkets(eventId),
    authParams(token, { tags: { name: 'sports/markets' } }),
  );
}

/**
 * Fetch current odds for a specific market — called frequently during live play.
 */
export function getOdds(token, eventId, marketId) {
  return measured(
    () => http.get(
      ENDPOINTS.eventOdds(eventId, marketId),
      authParams(token, { tags: { name: 'sports/odds' } }),
    ),
    oddsRefreshDuration,
  );
}

export function getMatchStatistics(token, eventId) {
  return http.get(
    ENDPOINTS.eventStats(eventId),
    authParams(token, { tags: { name: 'sports/stats' } }),
  );
}

// ---------------------------------------------------------------------------
// RNG verification
// ---------------------------------------------------------------------------

/**
 * Verify a completed round's RNG outcome.
 * Called after game rounds to confirm the cryptographic proof chain.
 */
export function verifyRng(token, roundId) {
  return measured(
    () => http.get(
      ENDPOINTS.rngVerify(roundId),
      authParams(token, { tags: { name: 'rng/verify' } }),
    ),
    rngVerifyDuration,
  );
}

/**
 * Retrieve the RNG audit log. Tests the audit API under concurrent load.
 */
export function getRngAuditLog(token, limit = 10) {
  return http.get(
    `${ENDPOINTS.rngAudit}?limit=${limit}`,
    authParams(token, { tags: { name: 'rng/audit' } }),
  );
}

// ---------------------------------------------------------------------------
// Promotions
// ---------------------------------------------------------------------------

export function getActivePromotions(token) {
  return http.get(
    ENDPOINTS.promotions,
    authParams(token, { tags: { name: 'promo/list' } }),
  );
}

export function claimBonus(token, promoId) {
  return http.post(
    ENDPOINTS.bonusClaim(promoId),
    null,
    authParams(token, { tags: { name: 'promo/claim' } }),
  );
}

// ---------------------------------------------------------------------------
// Health checks
// ---------------------------------------------------------------------------

/**
 * Lightweight health probe — use in setup() to confirm target is reachable.
 * Returns true if healthy.
 */
export function checkHealth() {
  const res = http.get(ENDPOINTS.health, {
    timeout: '10s',
    tags: { name: 'internal/health' },
  });
  return res.status === 200;
}

export function checkReadiness() {
  const res = http.get(ENDPOINTS.readiness, {
    timeout: '10s',
    tags: { name: 'internal/readiness' },
  });
  return res.status === 200;
}

// ---------------------------------------------------------------------------
// Batch helpers
// ---------------------------------------------------------------------------

/**
 * Fetch odds for multiple markets in a single k6 batch call.
 * More efficient than sequential calls; simulates browser parallelism.
 */
export function batchFetchOdds(token, eventId, marketIds) {
  const requests = marketIds.map((marketId) => ({
    method: 'GET',
    url:    ENDPOINTS.eventOdds(eventId, marketId),
    params: authParams(token, { tags: { name: 'sports/odds-batch' } }),
  }));
  return http.batch(requests);
}

/**
 * Parallel lobby + balance load — typical first action after login.
 */
export function batchInitialLoad(token) {
  return http.batch([
    {
      method: 'GET',
      url:    ENDPOINTS.lobby,
      params: authParams(token, { tags: { name: 'casino/lobby' } }),
    },
    {
      method: 'GET',
      url:    ENDPOINTS.balance,
      params: authParams(token, { tags: { name: 'account/balance' } }),
    },
    {
      method: 'GET',
      url:    ENDPOINTS.promotions,
      params: authParams(token, { tags: { name: 'promo/list' } }),
    },
  ]);
}
