// Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * ===========================================================================
 * Full Platform Load Test - k6 Script
 * ===========================================================================
 *
 * Chapter 42 - Complete Platform Architecture
 * Simulates full gambling platform at 2x expected peak load
 *
 * Test Scenarios:
 *   1. Concurrent player sessions (login/play/logout lifecycle)
 *   2. Game round execution (slots, table games, live casino)
 *   3. Payment processing (deposits, withdrawals)
 *   4. API gateway throughput
 *   5. Real-time WebSocket connections
 *
 * GLI-11 Compliance:
 *   - RNG service must maintain <10ms p99 under load
 *   - Game rounds must complete within 500ms
 *   - Payment processing must not exceed 30s
 *   - Platform must handle 2x peak without degradation
 *
 * Prerequisites:
 *   brew install k6  (or: docker run -i grafana/k6 run -)
 *
 * Usage:
 *   k6 run full-platform-load-test.js
 *   k6 run --vus 500 --duration 10m full-platform-load-test.js
 *   k6 run --out influxdb=http://localhost:8086/k6 full-platform-load-test.js
 *
 * ===========================================================================
 */

import http from 'k6/http';
import ws from 'k6/ws';
import { check, group, sleep } from 'k6';
import { Counter, Rate, Trend, Gauge } from 'k6/metrics';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const WS_URL = __ENV.WS_URL || 'ws://localhost:8080';
const API_KEY = __ENV.API_KEY || 'test-api-key';

// Platform expected peaks (per the capacity planning in Ch42)
const EXPECTED_PEAK_CCU = 5000;        // Concurrent Connected Users
const EXPECTED_PEAK_GAME_RPS = 2000;   // Game rounds per second
const EXPECTED_PEAK_PAYMENT_RPS = 100; // Payment transactions per second

// Test at 2x peak
const LOAD_MULTIPLIER = 2;

// ---------------------------------------------------------------------------
// Custom Metrics
// ---------------------------------------------------------------------------

// Game Engine metrics
const gameRoundDuration = new Trend('game_round_duration_ms', true);
const gameRoundSuccess = new Rate('game_round_success_rate');
const gameRoundsTotal = new Counter('game_rounds_total');

// Payment metrics
const paymentDuration = new Trend('payment_duration_ms', true);
const paymentSuccess = new Rate('payment_success_rate');
const paymentsTotal = new Counter('payments_total');

// RNG metrics
const rngLatency = new Trend('rng_latency_ms', true);
const rngSuccess = new Rate('rng_success_rate');

// Session metrics
const sessionDuration = new Trend('session_duration_ms', true);
const activeSessionsGauge = new Gauge('active_sessions');

// API Gateway metrics
const apiLatency = new Trend('api_gateway_latency_ms', true);
const apiErrors = new Counter('api_gateway_errors');

// ---------------------------------------------------------------------------
// Test Options
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // Scenario 1: Player sessions (ramp up to 2x peak CCU)
    player_sessions: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: Math.floor(EXPECTED_PEAK_CCU * LOAD_MULTIPLIER * 0.25) },
        { duration: '3m', target: Math.floor(EXPECTED_PEAK_CCU * LOAD_MULTIPLIER * 0.5) },
        { duration: '5m', target: EXPECTED_PEAK_CCU * LOAD_MULTIPLIER },
        { duration: '10m', target: EXPECTED_PEAK_CCU * LOAD_MULTIPLIER },  // Sustained peak
        { duration: '3m', target: Math.floor(EXPECTED_PEAK_CCU * LOAD_MULTIPLIER * 0.5) },
        { duration: '2m', target: 0 },
      ],
      exec: 'playerSession',
      tags: { scenario: 'player_sessions' },
    },

    // Scenario 2: Game rounds (constant high throughput)
    game_rounds: {
      executor: 'constant-arrival-rate',
      rate: EXPECTED_PEAK_GAME_RPS * LOAD_MULTIPLIER,
      timeUnit: '1s',
      duration: '20m',
      preAllocatedVUs: 500,
      maxVUs: 2000,
      exec: 'gameRound',
      startTime: '2m',  // Start after session ramp
      tags: { scenario: 'game_rounds' },
    },

    // Scenario 3: Payment processing
    payments: {
      executor: 'constant-arrival-rate',
      rate: EXPECTED_PEAK_PAYMENT_RPS * LOAD_MULTIPLIER,
      timeUnit: '1s',
      duration: '15m',
      preAllocatedVUs: 50,
      maxVUs: 200,
      exec: 'paymentFlow',
      startTime: '3m',
      tags: { scenario: 'payments' },
    },

    // Scenario 4: API gateway stress
    api_stress: {
      executor: 'ramping-arrival-rate',
      startRate: 100,
      timeUnit: '1s',
      stages: [
        { duration: '5m', target: 500 },
        { duration: '10m', target: 1000 },
        { duration: '5m', target: 200 },
      ],
      preAllocatedVUs: 200,
      maxVUs: 1000,
      exec: 'apiStress',
      startTime: '1m',
      tags: { scenario: 'api_stress' },
    },
  },

  thresholds: {
    // Overall HTTP thresholds
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    http_req_failed: ['rate<0.01'],  // <1% error rate

    // Game Engine: Must be fast for real-time gaming
    game_round_duration_ms: ['p(95)<300', 'p(99)<500'],
    game_round_success_rate: ['rate>0.99'],

    // Payments: Can be slower but must be reliable
    payment_duration_ms: ['p(95)<5000', 'p(99)<15000'],
    payment_success_rate: ['rate>0.995'],

    // RNG: Must be extremely fast (GLI-11)
    rng_latency_ms: ['p(99)<10', 'avg<5'],
    rng_success_rate: ['rate>0.999'],

    // API Gateway
    api_gateway_latency_ms: ['p(95)<200', 'p(99)<500'],
  },
};

// ---------------------------------------------------------------------------
// Helper Functions
// ---------------------------------------------------------------------------

function getHeaders(token) {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY,
    'Authorization': token ? `Bearer ${token}` : '',
    'X-Request-ID': `k6-${Date.now()}-${randomIntBetween(1, 999999)}`,
  };
}

function generatePlayerId() {
  return `PLR-LOAD-${randomIntBetween(100000, 999999)}`;
}

const GAME_TYPES = ['slots', 'blackjack', 'roulette', 'baccarat', 'video-poker'];
const CURRENCIES = ['EUR', 'GBP', 'USD', 'SEK'];
const JURISDICTIONS = ['MGA', 'UKGC', 'SGA', 'DGA'];

// ---------------------------------------------------------------------------
// Scenario 1: Player Session Lifecycle
// ---------------------------------------------------------------------------

export function playerSession() {
  const playerId = generatePlayerId();
  const sessionStart = Date.now();

  group('Player Session Lifecycle', () => {
    // Step 1: Login
    let loginRes;
    group('Login', () => {
      loginRes = http.post(`${BASE_URL}/api/v1/auth/login`, JSON.stringify({
        player_id: playerId,
        credentials: { type: 'api_key', key: 'load-test-key' },
      }), { headers: getHeaders(), tags: { operation: 'login' } });

      check(loginRes, {
        'login: status 200': (r) => r.status === 200,
        'login: has token': (r) => {
          try { return JSON.parse(r.body).token !== undefined; } catch { return false; }
        },
      });
    });

    const token = loginRes.status === 200
      ? (JSON.parse(loginRes.body || '{}').token || 'mock-token')
      : 'mock-token';

    // Step 2: Get player profile
    group('Get Profile', () => {
      const res = http.get(`${BASE_URL}/api/v1/players/${playerId}/profile`, {
        headers: getHeaders(token),
        tags: { operation: 'get_profile' },
      });
      check(res, { 'profile: status 200': (r) => r.status === 200 });
    });

    // Step 3: Browse game lobby
    group('Game Lobby', () => {
      const res = http.get(`${BASE_URL}/api/v1/games/lobby`, {
        headers: getHeaders(token),
        tags: { operation: 'game_lobby' },
      });
      check(res, { 'lobby: status 200': (r) => r.status === 200 });
    });

    // Step 4: Play multiple game rounds
    const numRounds = randomIntBetween(3, 15);
    group('Play Games', () => {
      for (let i = 0; i < numRounds; i++) {
        const gameType = randomItem(GAME_TYPES);
        const bet = randomIntBetween(1, 50) * 0.5;

        const roundStart = Date.now();
        const res = http.post(`${BASE_URL}/api/v1/games/${gameType}/play`, JSON.stringify({
          player_id: playerId,
          bet_amount: bet,
          currency: 'EUR',
          game_variant: 'standard',
        }), {
          headers: getHeaders(token),
          tags: { operation: 'game_play', game_type: gameType },
          timeout: '5s',
        });

        const roundDuration = Date.now() - roundStart;
        gameRoundDuration.add(roundDuration);
        gameRoundsTotal.add(1);
        gameRoundSuccess.add(res.status === 200 ? 1 : 0);

        check(res, {
          'game: status 200': (r) => r.status === 200,
          'game: has result': (r) => {
            try { return JSON.parse(r.body).result !== undefined; } catch { return false; }
          },
          'game: duration < 500ms': () => roundDuration < 500,
        });

        sleep(randomIntBetween(2, 8));  // Think time between rounds
      }
    });

    // Step 5: Check balance
    group('Check Balance', () => {
      const res = http.get(`${BASE_URL}/api/v1/wallet/${playerId}/balance`, {
        headers: getHeaders(token),
        tags: { operation: 'check_balance' },
      });
      check(res, { 'balance: status 200': (r) => r.status === 200 });
    });

    // Step 6: Logout
    group('Logout', () => {
      const res = http.post(`${BASE_URL}/api/v1/auth/logout`, JSON.stringify({
        player_id: playerId,
      }), {
        headers: getHeaders(token),
        tags: { operation: 'logout' },
      });
      check(res, { 'logout: status 200': (r) => r.status === 200 || r.status === 204 });
    });

    const totalDuration = Date.now() - sessionStart;
    sessionDuration.add(totalDuration);
  });
}

// ---------------------------------------------------------------------------
// Scenario 2: Game Round (High Throughput)
// ---------------------------------------------------------------------------

export function gameRound() {
  const playerId = generatePlayerId();
  const gameType = randomItem(GAME_TYPES);
  const bet = randomIntBetween(1, 100) * 0.25;

  const start = Date.now();

  const res = http.post(`${BASE_URL}/api/v1/games/${gameType}/play`, JSON.stringify({
    player_id: playerId,
    bet_amount: bet,
    currency: randomItem(CURRENCIES),
    game_variant: 'standard',
    session_id: `SESS-${randomIntBetween(1, 100000)}`,
  }), {
    headers: getHeaders(),
    tags: { operation: 'game_round', game_type: gameType },
    timeout: '2s',
  });

  const duration = Date.now() - start;
  gameRoundDuration.add(duration);
  gameRoundsTotal.add(1);
  gameRoundSuccess.add(res.status === 200 ? 1 : 0);

  check(res, {
    'round: status 200': (r) => r.status === 200,
    'round: < 300ms p95 target': () => duration < 300,
  });

  // Also test RNG endpoint directly
  const rngStart = Date.now();
  const rngRes = http.post(`${BASE_URL}/api/v1/rng/integer`, JSON.stringify({
    lower: 0,
    upper: 99,
    purpose: 'load_test_game_outcome',
  }), {
    headers: getHeaders(),
    tags: { operation: 'rng_generate' },
    timeout: '1s',
  });

  const rngDur = Date.now() - rngStart;
  rngLatency.add(rngDur);
  rngSuccess.add(rngRes.status === 200 ? 1 : 0);

  check(rngRes, {
    'rng: status 200': (r) => r.status === 200,
    'rng: < 10ms p99 target': () => rngDur < 10,
  });
}

// ---------------------------------------------------------------------------
// Scenario 3: Payment Flow
// ---------------------------------------------------------------------------

export function paymentFlow() {
  const playerId = generatePlayerId();
  const amount = randomIntBetween(10, 500);
  const currency = randomItem(CURRENCIES);

  group('Payment Flow', () => {
    // Deposit
    group('Deposit', () => {
      const start = Date.now();

      const res = http.post(`${BASE_URL}/api/v1/payments/deposit`, JSON.stringify({
        player_id: playerId,
        amount: amount,
        currency: currency,
        payment_method: randomItem(['card', 'bank_transfer', 'pix']),
        jurisdiction: randomItem(JURISDICTIONS),
        idempotency_key: `IDP-${Date.now()}-${randomIntBetween(1, 999999)}`,
      }), {
        headers: getHeaders(),
        tags: { operation: 'deposit' },
        timeout: '30s',
      });

      const duration = Date.now() - start;
      paymentDuration.add(duration);
      paymentsTotal.add(1);
      paymentSuccess.add(res.status === 200 || res.status === 202 ? 1 : 0);

      check(res, {
        'deposit: success': (r) => r.status === 200 || r.status === 202,
        'deposit: < 15s': () => duration < 15000,
      });
    });

    sleep(randomIntBetween(1, 3));

    // Check balance
    group('Post-Deposit Balance', () => {
      const res = http.get(`${BASE_URL}/api/v1/wallet/${playerId}/balance`, {
        headers: getHeaders(),
        tags: { operation: 'balance_check' },
      });
      check(res, { 'balance: status 200': (r) => r.status === 200 });
    });

    // Withdrawal (30% of deposits)
    if (Math.random() < 0.3) {
      sleep(1);
      group('Withdrawal', () => {
        const withdrawAmount = randomIntBetween(5, Math.min(amount, 200));

        const start = Date.now();
        const res = http.post(`${BASE_URL}/api/v1/payments/withdraw`, JSON.stringify({
          player_id: playerId,
          amount: withdrawAmount,
          currency: currency,
          payment_method: 'bank_transfer',
          jurisdiction: randomItem(JURISDICTIONS),
          idempotency_key: `IDP-W-${Date.now()}-${randomIntBetween(1, 999999)}`,
        }), {
          headers: getHeaders(),
          tags: { operation: 'withdrawal' },
          timeout: '30s',
        });

        const duration = Date.now() - start;
        paymentDuration.add(duration);
        paymentsTotal.add(1);
        paymentSuccess.add(res.status === 200 || res.status === 202 ? 1 : 0);

        check(res, {
          'withdrawal: success': (r) => r.status === 200 || r.status === 202,
        });
      });
    }
  });
}

// ---------------------------------------------------------------------------
// Scenario 4: API Gateway Stress
// ---------------------------------------------------------------------------

export function apiStress() {
  const endpoints = [
    { method: 'GET', path: '/api/v1/games/lobby', name: 'lobby' },
    { method: 'GET', path: '/api/v1/games/categories', name: 'categories' },
    { method: 'GET', path: `/api/v1/players/${generatePlayerId()}/profile`, name: 'profile' },
    { method: 'GET', path: '/api/v1/promotions/active', name: 'promotions' },
    { method: 'GET', path: '/health', name: 'health' },
    { method: 'GET', path: '/api/v1/games/popular', name: 'popular' },
    { method: 'GET', path: '/api/v1/jackpots/current', name: 'jackpots' },
  ];

  const endpoint = randomItem(endpoints);
  const start = Date.now();

  let res;
  if (endpoint.method === 'GET') {
    res = http.get(`${BASE_URL}${endpoint.path}`, {
      headers: getHeaders(),
      tags: { operation: endpoint.name },
      timeout: '5s',
    });
  }

  const duration = Date.now() - start;
  apiLatency.add(duration);

  if (res && res.status >= 500) {
    apiErrors.add(1);
  }

  check(res, {
    'api: not 5xx': (r) => r.status < 500,
    'api: < 200ms p95': () => duration < 200,
  });
}

// ---------------------------------------------------------------------------
// Setup & Teardown
// ---------------------------------------------------------------------------

export function setup() {
  console.log('=== Full Platform Load Test ===');
  console.log(`Base URL: ${BASE_URL}`);
  console.log(`Target: ${LOAD_MULTIPLIER}x peak load`);
  console.log(`Expected Peak CCU: ${EXPECTED_PEAK_CCU}`);
  console.log(`Test CCU: ${EXPECTED_PEAK_CCU * LOAD_MULTIPLIER}`);
  console.log(`Expected Peak Game RPS: ${EXPECTED_PEAK_GAME_RPS}`);
  console.log(`Test Game RPS: ${EXPECTED_PEAK_GAME_RPS * LOAD_MULTIPLIER}`);

  // Verify platform is reachable
  const healthRes = http.get(`${BASE_URL}/health`, { timeout: '10s' });
  if (healthRes.status !== 200) {
    console.warn(`Platform health check failed: ${healthRes.status}`);
  }

  return { startTime: Date.now() };
}

export function teardown(data) {
  const totalDuration = (Date.now() - data.startTime) / 1000;
  console.log(`\n=== Load Test Complete (${totalDuration.toFixed(0)}s) ===`);
}
