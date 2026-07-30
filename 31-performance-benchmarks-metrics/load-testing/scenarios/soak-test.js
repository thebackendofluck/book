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
 * Soak Test — Long-Duration Stability
 * =====================================
 * Target: https://acmetocasino-api.teste.workers.dev
 *
 * Default profile: 20 VUs for 30 min.
 * Set SOAK_HOURS to extend (e.g., SOAK_HOURS=4 for the full 4-hour soak).
 *
 * Runs a moderate, constant load to surface issues that only
 * manifest over time:
 *   - Memory leaks in application servers
 *   - Database connection pool exhaustion
 *   - File descriptor limits
 *   - Redis key-space growth and eviction drift
 *   - JWT token expiry / rotation bugs
 *   - Slow incremental response-time degradation
 *   - Background job queue depth accumulation
 *
 * From Chapter 32: "A load test must show that the platform sustains
 * 100,000+ concurrent bets without degradation."  The soak test is how
 * you validate the without-degradation part.
 *
 * SLO gate: p95 latency at minute 240 must not exceed p95 at minute 10
 * by more than 20%.  Anything larger indicates accumulated resource
 * exhaustion and must be resolved before production deployment.
 *
 * Usage:
 *   k6 run scenarios/soak-test.js
 *   k6 run --env SOAK_HOURS=8 scenarios/soak-test.js   # overnight run
 *   k6 run --env VU_SCALE=0.1 scenarios/soak-test.js  # quick 24-min smoke
 */

import http from 'k6/http';
import ws   from 'k6/ws';
import { check, sleep, group } from 'k6';
import { SharedArray }         from 'k6/data';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';
import { Counter, Rate, Trend, Gauge }  from 'k6/metrics';

import {
  BASE_URL, ENDPOINTS, TEST_DATA, THINK_TIME, GLOBAL_THRESHOLDS,
} from '../config.js';
import {
  login, logout, getBalance, getLobby,
  getGamesByCategory, launchGame, placeCasinoBet,
  placeSportsBet, getOdds, getActiveBets, getSettledBets,
  deposit, verifyRng, getActivePromotions,
  getTransactionHistory, pingGameSession, checkHealth,
  refreshToken as refreshTokenReq,
} from '../helpers/requests.js';
import {
  checkLoginResponse, checkLobbyResponse, checkGameLaunchResponse,
  checkBetPlacementResponse, checkBalanceResponse, checkDepositResponse,
  checkOddsResponse, checkRngVerifyResponse, checkWsConnection,
  activeWsConnections, activeGameSessions,
} from '../helpers/checks.js';

// ---------------------------------------------------------------------------
// Soak-specific metrics
// ---------------------------------------------------------------------------
// These allow graphing degradation over time in Grafana / k6 Cloud

const latencyP95Early  = new Trend('soak_latency_p95_early',  true);
const latencyP95Late   = new Trend('soak_latency_p95_late',   true);
const sessionRenewals  = new Counter('soak_token_renewals');
const connectionErrors = new Counter('soak_connection_errors');
const degradationAlert = new Rate('soak_degradation_detected');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const VU_SCALE   = parseFloat(__ENV.VU_SCALE   || '1');
const SOAK_HOURS = parseFloat(__ENV.SOAK_HOURS || '0.5');  // default: 30 min
const SOAK_MINS  = Math.round(SOAK_HOURS * 60);
const S          = (n) => Math.max(1, Math.round(n * VU_SCALE));

// Default: 20 VUs for 30 min.  Scale up with VU_SCALE for production soak.
const SOAK_VUS = S(20);

// ---------------------------------------------------------------------------
// Users — larger pool for a 4-hour run to avoid hot usernames
// ---------------------------------------------------------------------------

const users = new SharedArray('soak-users', function () {
  const arr = [];
  for (let i = 1; i <= 15000; i++) {
    arr.push({
      email:    `soaktest_${i}@load.acmetocasino.com`,
      password: (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
    });
  }
  return arr;
});

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    casino_soak: {
      executor:     'constant-vus',
      vus:          Math.round(SOAK_VUS * 0.45),  // 45% casino
      duration:     `${SOAK_MINS + 10}m`,
      exec:         'casinoSoakJourney',
      startTime:    '5m',
      gracefulStop: '5m',
    },
    sportsbook_soak: {
      executor:     'constant-vus',
      vus:          Math.round(SOAK_VUS * 0.35),  // 35% sportsbook
      duration:     `${SOAK_MINS + 10}m`,
      exec:         'sportsbookSoakJourney',
      startTime:    '5m',
      gracefulStop: '5m',
    },
    wallet_soak: {
      executor:     'constant-vus',
      vus:          Math.round(SOAK_VUS * 0.10),  // 10% wallet operations
      duration:     `${SOAK_MINS + 10}m`,
      exec:         'walletSoakJourney',
      startTime:    '5m',
      gracefulStop: '5m',
    },
    ws_soak: {
      executor:     'constant-vus',
      vus:          Math.round(SOAK_VUS * 0.10),  // 10% WebSocket
      duration:     `${SOAK_MINS + 10}m`,
      exec:         'wsSoakJourney',
      startTime:    '5m',
      gracefulStop: '2m',
    },
    // Ramp in / out so we don't crash the target at t=0
    ramp_up: {
      executor:     'ramping-vus',
      startVUs:     0,
      stages:       [{ duration: '5m', target: SOAK_VUS }],
      exec:         'casinoSoakJourney',
      startTime:    '0s',
      gracefulStop: '1m',
    },
    ramp_down: {
      executor:     'ramping-vus',
      startVUs:     SOAK_VUS,
      stages:       [{ duration: '5m', target: 0 }],
      exec:         'casinoSoakJourney',
      startTime:    `${SOAK_MINS + 15}m`,
      gracefulStop: '1m',
    },
  },

  thresholds: Object.assign({}, GLOBAL_THRESHOLDS, {
    // Same thresholds as load test — they must hold for the full duration
    http_req_duration:      ['p(95)<500', 'p(99)<1500'],
    http_req_failed:        ['rate<0.01'],
    bet_placement_duration: ['p(95)<1000'],
    wallet_op_success:      ['rate>0.999'],
    game_launch_success:    ['rate>0.99'],
    // Soak-specific: monitor for late-test degradation
    soak_latency_p95_late:  ['p(95)<600'],  // allow 20% headroom above normal SLO
  }),
};

// ---------------------------------------------------------------------------
// Helper: detect whether we're in the early or late phase of the soak
// ---------------------------------------------------------------------------

function isLatePhase() {
  // __ITER increments per VU iteration.  A rough heuristic: after 1000 iters
  // we're deep enough into the run to be measuring for degradation.
  return __ITER > 1000;
}

// ---------------------------------------------------------------------------
// Helper: refresh JWT before it expires
// VUs in a soak test will have long-lived sessions that need renewal
// ---------------------------------------------------------------------------

function ensureFreshToken(session) {
  if (!session) return null;
  // Tokens expire after 1 hour; refresh if iteration count suggests > 55 min
  if (session.refreshToken && __ITER > 0 && __ITER % 200 === 0) {
    const res = refreshTokenReq(session.refreshToken);
    if (res.status === 200) {
      sessionRenewals.add(1);
      try {
        const body = JSON.parse(res.body);
        return Object.assign({}, session, {
          token: body.token || body.access_token,
        });
      } catch (_) {}
    }
  }
  return session;
}

// ---------------------------------------------------------------------------
// Casino soak journey
// Full slot / table-game loop with session heartbeats and RNG verification
// ---------------------------------------------------------------------------

export function casinoSoakJourney() {
  const user    = users[__VU % users.length];
  let   session = login(user.email, user.password);
  if (!session) {
    connectionErrors.add(1);
    sleep(randomIntBetween(...THINK_TIME.medium));
    return;
  }

  session = ensureFreshToken(session);
  const { token } = session;

  group('casino soak: lobby load', function () {
    const start  = Date.now();
    const res    = getLobby(token);
    const dur    = Date.now() - start;

    if (isLatePhase()) {
      latencyP95Late.add(dur);
      degradationAlert.add(dur > 600); // flag if above 600 ms late in soak
    } else {
      latencyP95Early.add(dur);
    }

    checkLobbyResponse(res);
    sleep(randomIntBetween(...THINK_TIME.medium));
  });

  const category = randomItem(['slots', 'slots', 'table-games', 'live-casino']);
  const gameId   = randomItem(
    category === 'slots'        ? TEST_DATA.slotGameIds :
    category === 'table-games'  ? TEST_DATA.tableGameIds :
                                  TEST_DATA.liveCasinoGameIds,
  );

  const launchData = group('casino soak: game launch', function () {
    const res = launchGame(token, gameId, { mode: 'real' });
    return checkGameLaunchResponse(res);
  });

  if (!launchData) {
    logout(token);
    sleep(randomIntBetween(...THINK_TIME.medium));
    return;
  }

  activeGameSessions.add(1);

  // Play multiple rounds — longer sessions surface memory leaks
  const rounds = randomIntBetween(3, 12);
  for (let i = 0; i < rounds; i++) {
    group('casino soak: play round', function () {
      const stake   = randomItem(TEST_DATA.betStakes);
      const betRes  = placeCasinoBet(token, launchData.sessionId, stake);
      const betId   = checkBetPlacementResponse(betRes);

      // Heartbeat the session every round
      pingGameSession(token, launchData.sessionId);

      // 10% verify RNG (simulates responsible-gambling audit tools)
      if (betId && Math.random() < 0.10) {
        const rngRes = verifyRng(token, betId);
        checkRngVerifyResponse(rngRes);
      }
    });

    sleep(randomIntBetween(3, 10));
  }

  activeGameSessions.add(-1);

  group('casino soak: post-session', function () {
    getBalance(token);
    // 5% browse history (connection pool pressure on read replicas)
    if (Math.random() < 0.05) {
      getTransactionHistory(token);
    }
  });

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.medium));
}

// ---------------------------------------------------------------------------
// Sportsbook soak journey
// Repeated odds polling + bet placement over multiple events
// ---------------------------------------------------------------------------

export function sportsbookSoakJourney() {
  const user    = users[__VU % users.length];
  let   session = login(user.email, user.password);
  if (!session) {
    connectionErrors.add(1);
    sleep(randomIntBetween(...THINK_TIME.medium));
    return;
  }

  session = ensureFreshToken(session);
  const { token } = session;

  const eventId = randomItem(TEST_DATA.eventIds);

  group('sports soak: odds browsing', function () {
    const start  = Date.now();
    const res    = getOdds(token, eventId, randomItem(TEST_DATA.marketIds));
    const dur    = Date.now() - start;

    if (isLatePhase()) latencyP95Late.add(dur);
    else latencyP95Early.add(dur);

    checkOddsResponse(res);
    sleep(randomIntBetween(...THINK_TIME.short));
  });

  // Multiple odds refreshes — realistic polling behaviour
  const pollRounds = randomIntBetween(2, 5);
  for (let i = 0; i < pollRounds; i++) {
    getOdds(token, eventId, randomItem(TEST_DATA.marketIds));
    sleep(randomIntBetween(2, 5));
  }

  if (Math.random() < 0.50) {
    group('sports soak: bet placement', function () {
      const selectionId = `sel-${randomIntBetween(1, 10)}`;
      const odds        = [1.50, 2.00, 2.50, 3.50, 5.00][randomIntBetween(0, 4)];
      const betRes = placeSportsBet(
        token, eventId,
        randomItem(TEST_DATA.marketIds),
        selectionId, odds,
        randomItem(TEST_DATA.betStakes),
      );
      checkBetPlacementResponse(betRes);
    });
  }

  // Periodic settled bets check (connection pool test on historical data queries)
  if (Math.random() < 0.20) {
    getSettledBets(token);
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.medium));
}

// ---------------------------------------------------------------------------
// Wallet soak journey
// Steady payment operations — validates connection pool at the PSP gateway
// ---------------------------------------------------------------------------

export function walletSoakJourney() {
  const user    = users[__VU % users.length];
  let   session = login(user.email, user.password);
  if (!session) {
    connectionErrors.add(1);
    sleep(randomIntBetween(...THINK_TIME.medium));
    return;
  }

  session = ensureFreshToken(session);
  const { token } = session;

  group('wallet soak: balance', function () {
    const start = Date.now();
    const res   = getBalance(token);
    const dur   = Date.now() - start;
    if (isLatePhase()) latencyP95Late.add(dur);
    else latencyP95Early.add(dur);
    checkBalanceResponse(res);
    sleep(randomIntBetween(1, 2));
  });

  // Alternate between deposit and transaction history
  if (Math.random() < 0.60) {
    group('wallet soak: deposit', function () {
      const res = deposit(
        token,
        randomItem(TEST_DATA.depositAmounts),
        randomItem(TEST_DATA.paymentMethods),
      );
      checkDepositResponse(res);
    });
  } else {
    group('wallet soak: tx history', function () {
      getTransactionHistory(token);
    });
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.long));
}

// ---------------------------------------------------------------------------
// WebSocket soak journey
// Long-lived connections — detects fd / socket exhaustion over time
// ---------------------------------------------------------------------------

export function wsSoakJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) {
    connectionErrors.add(1);
    sleep(10);
    return;
  }

  const { token } = session;
  const eventId   = randomItem(TEST_DATA.eventIds);
  const wsUrl     = ENDPOINTS.oddsFeed(token, eventId);

  const res = ws.connect(wsUrl, {}, function (socket) {
    activeWsConnections.add(1);

    socket.on('open', function () {
      socket.send(JSON.stringify({
        action:   'subscribe',
        channels: TEST_DATA.marketIds.slice(0, 6).map((m) => `odds.${eventId}.${m}`),
      }));
    });

    socket.on('message', function (raw) {
      try {
        checkWsConnection(null, JSON.parse(raw));
      } catch (_) {}
    });

    socket.on('error', function () {
      connectionErrors.add(1);
      activeWsConnections.add(-1);
    });

    socket.on('close', function () {
      activeWsConnections.add(-1);
    });

    // Long hold: 60–300 s (simulates a full betting session on a match half)
    const holdMs = randomIntBetween(60, 300) * 1000;
    socket.setTimeout(function () { socket.close(); }, holdMs);

    // Periodic ping to keep connection alive and detect silent drops
    socket.setInterval(function () {
      socket.send(JSON.stringify({ action: 'ping' }));
    }, 30000);
  });

  check(res, { 'ws soak: connected': (r) => r && r.status === 101 });
  if (!res || res.status !== 101) connectionErrors.add(1);

  sleep(randomIntBetween(5, 20));
}

// ---------------------------------------------------------------------------
// Lifecycle hooks
// ---------------------------------------------------------------------------

export function setup() {
  console.log('='.repeat(60));
  console.log(`  Soak Test — ${SOAK_HOURS}h Stability Run`);
  console.log('='.repeat(60));
  console.log(`  Target:       ${BASE_URL}`);
  console.log(`  VU Scale:     ${VU_SCALE}x`);
  console.log(`  Concurrent VUs: ~${SOAK_VUS}`);
  console.log(`  Duration:     ${SOAK_MINS + 20} min`);
  console.log('');
  console.log('  Monitor for:');
  console.log('  - soak_latency_p95_late > soak_latency_p95_early + 20%');
  console.log('  - soak_connection_errors creeping up over time');
  console.log('  - active_game_sessions / active_ws_connections plateauing');
  console.log('='.repeat(60));

  if (!checkHealth()) {
    throw new Error('Target health check failed — aborting soak test');
  }
  return { startTime: Date.now() };
}

export function teardown(data) {
  const mins = ((Date.now() - data.startTime) / 60000).toFixed(1);
  console.log(`Soak test complete after ${mins} min.`);
  console.log('');
  console.log('Post-soak checklist:');
  console.log('  [ ] Compare soak_latency_p95_late vs soak_latency_p95_early');
  console.log('  [ ] Check soak_connection_errors for upward trend');
  console.log('  [ ] Inspect application memory usage graphs');
  console.log('  [ ] Verify database connection pool size stayed bounded');
  console.log('  [ ] Check Redis eviction counters');
  console.log('  [ ] Review soak_token_renewals for auth service stability');
}
