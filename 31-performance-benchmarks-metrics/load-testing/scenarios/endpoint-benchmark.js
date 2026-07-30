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
 * Endpoint Benchmark — P95 / P99 Latency per Endpoint
 * =====================================================
 * Target: https://acmetocasino-api.teste.workers.dev
 *
 * Tests each AcmeToCasino API endpoint individually under a small but
 * constant load and captures P95 / P99 latency per route.  Use this test
 * to:
 *   - Establish a baseline before a release
 *   - Identify which endpoints regress after a deployment
 *   - Compare Workers edge routing against origin latency
 *   - Satisfy Chapter 32 audit requirement: "all endpoints documented
 *     with measured latency at 50 VUs"
 *
 * Endpoints under test (from spec):
 *   GET  /                                  health check
 *   POST /api/auth/register                 player registration
 *   POST /api/auth/login                    authentication
 *   GET  /api/games                         game catalogue
 *   POST /api/payments/deposit              payment initiation
 *   POST /api/wallet/balance                balance query
 *   GET  /api/compliance/jurisdiction-check compliance gate
 *   GET  /api/analytics                     analytics feed
 *
 * Each endpoint runs as an isolated scenario with its own VU pool and
 * threshold so regressions on one route don't mask another.
 *
 * Usage:
 *   k6 run scenarios/endpoint-benchmark.js
 *   k6 run --env BASE_URL=https://acmetocasino-api.teste.workers.dev \
 *           scenarios/endpoint-benchmark.js
 *
 * Output: per-endpoint P95 / P99 in k6 summary + custom Trend metrics.
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { SharedArray }         from 'k6/data';
import { randomIntBetween }    from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';
import { Trend, Rate, Counter } from 'k6/metrics';

import {
  BASE_URL, HTTP_PARAMS, GLOBAL_THRESHOLDS,
} from '../config.js';
import { login, checkHealth } from '../helpers/requests.js';

// ---------------------------------------------------------------------------
// Per-endpoint latency trends
// ---------------------------------------------------------------------------

const latency = {
  health:              new Trend('bench_health_duration',              true),
  register:            new Trend('bench_register_duration',            true),
  login:               new Trend('bench_login_duration',               true),
  games:               new Trend('bench_games_duration',               true),
  deposit:             new Trend('bench_deposit_duration',             true),
  walletBalance:       new Trend('bench_wallet_balance_duration',      true),
  jurisdictionCheck:   new Trend('bench_jurisdiction_check_duration',  true),
  analytics:           new Trend('bench_analytics_duration',           true),
};

const errorRate = {
  health:            new Rate('bench_health_errors'),
  register:          new Rate('bench_register_errors'),
  login:             new Rate('bench_login_errors'),
  games:             new Rate('bench_games_errors'),
  deposit:           new Rate('bench_deposit_errors'),
  walletBalance:     new Rate('bench_wallet_balance_errors'),
  jurisdictionCheck: new Rate('bench_jurisdiction_check_errors'),
  analytics:         new Rate('bench_analytics_errors'),
};

const requestCount = new Counter('bench_requests_total');

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const BENCH_VUS = parseInt(__ENV.BENCH_VUS || '10', 10);
const BENCH_DURATION = __ENV.BENCH_DURATION || '2m';

// Small pre-registered user pool for endpoints that require auth
const users = new SharedArray('bench-users', function () {
  const arr = [];
  for (let i = 1; i <= 1000; i++) {
    arr.push({
      email:    `bench_${i}@load.acmetocasino.com`,
      password: (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
    });
  }
  return arr;
});

// ---------------------------------------------------------------------------
// Scenario configuration — each endpoint gets its own executor
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // Health / root
    bench_health: {
      executor: 'constant-vus',
      vus:      BENCH_VUS,
      duration: BENCH_DURATION,
      exec:     'benchHealth',
    },

    // POST /api/auth/register  (run after health to avoid parallel registration storms)
    bench_register: {
      executor:  'constant-vus',
      vus:       Math.max(1, Math.floor(BENCH_VUS / 5)),  // lower VUs — registration is expensive
      duration:  BENCH_DURATION,
      exec:      'benchRegister',
      startTime: `${parseInt(BENCH_DURATION) + 10}s`,  // offset after health
    },

    // POST /api/auth/login
    bench_login: {
      executor:  'constant-vus',
      vus:       BENCH_VUS,
      duration:  BENCH_DURATION,
      exec:      'benchLogin',
    },

    // GET /api/games
    bench_games: {
      executor:  'constant-vus',
      vus:       BENCH_VUS,
      duration:  BENCH_DURATION,
      exec:      'benchGames',
    },

    // POST /api/payments/deposit
    bench_deposit: {
      executor:  'constant-vus',
      vus:       BENCH_VUS,
      duration:  BENCH_DURATION,
      exec:      'benchDeposit',
    },

    // POST /api/wallet/balance
    bench_wallet_balance: {
      executor:  'constant-vus',
      vus:       BENCH_VUS,
      duration:  BENCH_DURATION,
      exec:      'benchWalletBalance',
    },

    // GET /api/compliance/jurisdiction-check
    bench_jurisdiction: {
      executor:  'constant-vus',
      vus:       BENCH_VUS,
      duration:  BENCH_DURATION,
      exec:      'benchJurisdictionCheck',
    },

    // GET /api/analytics
    bench_analytics: {
      executor:  'constant-vus',
      vus:       BENCH_VUS,
      duration:  BENCH_DURATION,
      exec:      'benchAnalytics',
    },
  },

  thresholds: {
    // Global SLO baselines
    http_req_failed:                        ['rate<0.01'],

    // Per-endpoint P95 / P99 thresholds
    bench_health_duration:                  ['p(95)<100',   'p(99)<250'],
    bench_register_duration:                ['p(95)<1500',  'p(99)<3000'],
    bench_login_duration:                   ['p(95)<500',   'p(99)<1000'],
    bench_games_duration:                   ['p(95)<300',   'p(99)<600'],
    bench_deposit_duration:                 ['p(95)<800',   'p(99)<2000'],
    bench_wallet_balance_duration:          ['p(95)<200',   'p(99)<500'],
    bench_jurisdiction_check_duration:      ['p(95)<400',   'p(99)<800'],
    bench_analytics_duration:               ['p(95)<500',   'p(99)<1000'],

    // Error rates per endpoint
    bench_health_errors:                    ['rate<0.001'],
    bench_register_errors:                  ['rate<0.05'],
    bench_login_errors:                     ['rate<0.01'],
    bench_games_errors:                     ['rate<0.01'],
    bench_deposit_errors:                   ['rate<0.02'],
    bench_wallet_balance_errors:            ['rate<0.01'],
    bench_jurisdiction_check_errors:        ['rate<0.01'],
    bench_analytics_errors:                 ['rate<0.05'],
  },
};

// ---------------------------------------------------------------------------
// Helper: make an authenticated token for a VU
// ---------------------------------------------------------------------------

let _sessionCache = null;

function getSession() {
  if (_sessionCache) return _sessionCache;
  const user = users[__VU % users.length];
  _sessionCache = login(user.email, user.password);
  return _sessionCache;
}

function authHeader(token) {
  return Object.assign({}, HTTP_PARAMS.headers, {
    Authorization: `Bearer ${token}`,
    'X-Request-ID': `k6-bench-${__VU}-${Date.now()}`,
  });
}

// ---------------------------------------------------------------------------
// Benchmark functions
// ---------------------------------------------------------------------------

/**
 * GET / — health / root endpoint
 */
export function benchHealth() {
  group('bench: GET /', function () {
    const start = Date.now();
    const res = http.get(`${BASE_URL}/`, {
      tags:    { name: 'bench/health', endpoint: 'health' },
      timeout: '10s',
    });
    const dur = Date.now() - start;
    latency.health.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'health: status 200': (r) => r.status === 200,
      'health: body non-empty': (r) => r.body && r.body.length > 0,
    });
    errorRate.health.add(!ok);
  });
  sleep(randomIntBetween(1, 2));
}

/**
 * POST /api/auth/register — new player registration
 */
export function benchRegister() {
  group('bench: POST /api/auth/register', function () {
    const ts = Date.now();
    const payload = JSON.stringify({
      email:      `benchreg_${__VU}_${ts}@load.acmetocasino.com`,
      password:   (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
      first_name: 'Load',
      last_name:  'Tester',
      country:    'MT',
      currency:   'EUR',
      dob:        '1990-01-15',
    });

    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/api/auth/register`,
      payload,
      {
        headers: HTTP_PARAMS.headers,
        tags:    { name: 'bench/register', endpoint: 'register' },
      },
    );
    const dur = Date.now() - start;
    latency.register.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'register: 200 or 201':       (r) => r.status === 200 || r.status === 201,
      'register: player_id in body': (r) => {
        try {
          const b = JSON.parse(r.body);
          return b && (b.player_id || b.user_id || b.id);
        } catch (_) { return false; }
      },
    });
    errorRate.register.add(!ok);
  });
  sleep(randomIntBetween(2, 4));
}

/**
 * POST /api/auth/login — player authentication
 */
export function benchLogin() {
  group('bench: POST /api/auth/login', function () {
    const user = users[__VU % users.length];
    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/api/auth/login`,
      JSON.stringify({ email: user.email, password: user.password }),
      {
        headers: HTTP_PARAMS.headers,
        tags:    { name: 'bench/login', endpoint: 'login' },
      },
    );
    const dur = Date.now() - start;
    latency.login.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'login: status 200':    (r) => r.status === 200,
      'login: token present': (r) => {
        try {
          const b = JSON.parse(r.body);
          return b && (b.token || b.access_token);
        } catch (_) { return false; }
      },
    });
    errorRate.login.add(!ok);
    // Invalidate cache so next iteration re-logs in
    _sessionCache = null;
  });
  sleep(randomIntBetween(1, 3));
}

/**
 * GET /api/games — game catalogue
 */
export function benchGames() {
  group('bench: GET /api/games', function () {
    const session = getSession();
    const token   = session ? session.token : '';

    const start = Date.now();
    const res = http.get(
      `${BASE_URL}/api/games`,
      {
        headers: authHeader(token),
        tags:    { name: 'bench/games', endpoint: 'games' },
      },
    );
    const dur = Date.now() - start;
    latency.games.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'games: status 200':      (r) => r.status === 200,
      'games: array in body':   (r) => {
        try {
          const b = JSON.parse(r.body);
          return b && (Array.isArray(b) || Array.isArray(b.games) || Array.isArray(b.items));
        } catch (_) { return false; }
      },
    });
    errorRate.games.add(!ok);
  });
  sleep(randomIntBetween(1, 2));
}

/**
 * POST /api/payments/deposit — payment initiation
 */
export function benchDeposit() {
  group('bench: POST /api/payments/deposit', function () {
    const session = getSession();
    if (!session) { sleep(2); return; }

    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/api/payments/deposit`,
      JSON.stringify({
        amount:         10,
        currency:       'EUR',
        payment_method: 'card',
        payment_token:  `test-card-token-${__VU % 10}`,
      }),
      {
        headers: authHeader(session.token),
        tags:    { name: 'bench/deposit', endpoint: 'deposit' },
      },
    );
    const dur = Date.now() - start;
    latency.deposit.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'deposit: 200 or 202':       (r) => r.status === 200 || r.status === 202,
      'deposit: transaction_id':   (r) => {
        try {
          const b = JSON.parse(r.body);
          return b && (b.transaction_id || b.payment_id || b.reference);
        } catch (_) { return false; }
      },
    });
    errorRate.deposit.add(!ok);
  });
  sleep(randomIntBetween(2, 4));
}

/**
 * POST /api/wallet/balance — wallet balance query
 */
export function benchWalletBalance() {
  group('bench: POST /api/wallet/balance', function () {
    const session = getSession();
    if (!session) { sleep(1); return; }

    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/api/wallet/balance`,
      JSON.stringify({ currency: 'EUR' }),
      {
        headers: authHeader(session.token),
        tags:    { name: 'bench/wallet-balance', endpoint: 'wallet_balance' },
      },
    );
    const dur = Date.now() - start;
    latency.walletBalance.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'balance: status 200':        (r) => r.status === 200,
      'balance: amount is number':  (r) => {
        try {
          const b = JSON.parse(r.body);
          const amount = b && (b.balance || b.amount || (b.wallet && b.wallet.balance));
          return typeof amount === 'number' && amount >= 0;
        } catch (_) { return false; }
      },
    });
    errorRate.walletBalance.add(!ok);
  });
  sleep(randomIntBetween(1, 2));
}

/**
 * GET /api/compliance/jurisdiction-check — compliance gate
 */
export function benchJurisdictionCheck() {
  group('bench: GET /api/compliance/jurisdiction-check', function () {
    const session = getSession();
    const token   = session ? session.token : '';

    const start = Date.now();
    const res = http.get(
      `${BASE_URL}/api/compliance/jurisdiction-check`,
      {
        headers: authHeader(token),
        tags:    { name: 'bench/jurisdiction', endpoint: 'jurisdiction_check' },
      },
    );
    const dur = Date.now() - start;
    latency.jurisdictionCheck.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'jurisdiction: status 200 or 403': (r) => r.status === 200 || r.status === 403,
      'jurisdiction: body present':      (r) => r.body && r.body.length > 0,
    });
    errorRate.jurisdictionCheck.add(!ok);
  });
  sleep(randomIntBetween(1, 3));
}

/**
 * GET /api/analytics — analytics feed
 */
export function benchAnalytics() {
  group('bench: GET /api/analytics', function () {
    const session = getSession();
    const token   = session ? session.token : '';

    const start = Date.now();
    const res = http.get(
      `${BASE_URL}/api/analytics`,
      {
        headers: authHeader(token),
        tags:    { name: 'bench/analytics', endpoint: 'analytics' },
      },
    );
    const dur = Date.now() - start;
    latency.analytics.add(dur);
    requestCount.add(1);

    const ok = check(res, {
      'analytics: status 200 or 401': (r) => r.status === 200 || r.status === 401,
      'analytics: body present':      (r) => r.body && r.body.length > 0,
    });
    errorRate.analytics.add(!ok);
  });
  sleep(randomIntBetween(1, 3));
}

// ---------------------------------------------------------------------------
// Lifecycle hooks
// ---------------------------------------------------------------------------

export function setup() {
  console.log('='.repeat(70));
  console.log('  Endpoint Benchmark — AcmeToCasino API');
  console.log('='.repeat(70));
  console.log(`  Target:    ${BASE_URL}`);
  console.log(`  VUs/endpoint: ${BENCH_VUS}`);
  console.log(`  Duration:  ${BENCH_DURATION} per endpoint`);
  console.log('');
  console.log('  Endpoints:');
  console.log('    GET  /');
  console.log('    POST /api/auth/register');
  console.log('    POST /api/auth/login');
  console.log('    GET  /api/games');
  console.log('    POST /api/payments/deposit');
  console.log('    POST /api/wallet/balance');
  console.log('    GET  /api/compliance/jurisdiction-check');
  console.log('    GET  /api/analytics');
  console.log('='.repeat(70));

  if (!checkHealth()) {
    throw new Error('Target health check failed — aborting benchmark');
  }
  return { startTime: Date.now() };
}

export function teardown(data) {
  const mins = ((Date.now() - data.startTime) / 60000).toFixed(1);
  console.log(`\nEndpoint benchmark complete after ${mins} min.`);
  console.log('');
  console.log('Review these metrics for P95 / P99 per endpoint:');
  console.log('  bench_health_duration');
  console.log('  bench_register_duration');
  console.log('  bench_login_duration');
  console.log('  bench_games_duration');
  console.log('  bench_deposit_duration');
  console.log('  bench_wallet_balance_duration');
  console.log('  bench_jurisdiction_check_duration');
  console.log('  bench_analytics_duration');
}
