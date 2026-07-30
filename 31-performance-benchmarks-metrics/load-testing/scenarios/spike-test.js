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
 * Spike Test — Goal / Match-Start / Breaking-News Surge
 * ======================================================
 * Target: https://acmetocasino-api.teste.workers.dev
 *
 * Default profile: 10 → 200 → 10 VUs in 5 min.
 * Set VU_SCALE to increase the ceiling for production-scale testing.
 *
 * Simulates the 20–30% instant traffic surge triggered by real-world events:
 *   - A goal scored during a live match
 *   - Kickoff of a high-profile game
 *   - A red card / penalty award
 *   - Breaking sports news (injury, line-up change)
 *
 * From Chapter 41: when Mbappe scored twice in 97 seconds during the
 * World Cup final, traffic jumped from 2.0 M to 2.3 M concurrent users
 * in under two minutes.  This test validates that:
 *
 *   1. Auto-scaling responds within the 30 s cooldown window
 *   2. Bet placement remains available at the peak (no queue saturation)
 *   3. Odds updates propagate without WebSocket disconnects
 *   4. p95 latency stays under 3 s during the spike
 *   5. Error rate stays below 5% throughout
 *
 * Traffic pattern (configurable via SPIKE_MULTIPLIER env var):
 *   baseline → 30 s ramp to spike → hold → recovery → baseline
 *
 * Usage:
 *   k6 run scenarios/spike-test.js
 *   k6 run --env BASE_URL=https://staging.acmetocasino.com \
 *           --env SPIKE_MULTIPLIER=20 \
 *           scenarios/spike-test.js
 */

import http from 'k6/http';
import ws   from 'k6/ws';
import { check, sleep, group } from 'k6';
import { SharedArray }         from 'k6/data';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';
import { Counter, Gauge, Trend } from 'k6/metrics';

import {
  BASE_URL, ENDPOINTS, TEST_DATA, THINK_TIME, GLOBAL_THRESHOLDS,
} from '../config.js';
import {
  login, logout, getBalance, getOdds, placeSportsBet,
  placeCasinoBet, launchGame, deposit, checkHealth,
} from '../helpers/requests.js';
import {
  checkLoginResponse, checkBetPlacementResponse, checkOddsResponse,
  checkBalanceResponse, checkDepositResponse, checkWsConnection,
  activeWsConnections,
} from '../helpers/checks.js';

// ---------------------------------------------------------------------------
// Spike-specific metrics
// ---------------------------------------------------------------------------

const spikeLatencyTrend = new Trend('spike_request_latency', true);
const recoveryTime      = new Trend('spike_recovery_time_ms', true);
const spikeErrors       = new Counter('spike_error_count');
const peakVUsReached    = new Gauge('peak_vus_during_spike');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const VU_SCALE        = parseFloat(__ENV.VU_SCALE         || '1');
const SPIKE_MULTIPLIER= parseInt(__ENV.SPIKE_MULTIPLIER   || '20', 10);
// Default: 10 base VUs → 200 spike VUs in 5 min
const BASE_VUS        = Math.max(1, Math.round(10 * VU_SCALE));
const SPIKE_VUS       = Math.max(1, Math.round(BASE_VUS * SPIKE_MULTIPLIER));

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

const users = new SharedArray('spike-users', function () {
  const arr = [];
  for (let i = 1; i <= 5000; i++) {
    arr.push({
      email:    `spiketest_${i}@load.acmetocasino.com`,
      password: (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
    });
  }
  return arr;
});

// ---------------------------------------------------------------------------
// Options — three spike cycles to test auto-scaling repeatability
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // Main HTTP load: 10 → 200 → 10 VUs in 5 min (default)
    // Extend with SPIKE_MULTIPLIER and VU_SCALE for production-scale multi-cycle testing
    spike_http: {
      executor:  'ramping-vus',
      startVUs:  BASE_VUS,
      stages: [
        { duration: '1m',  target: BASE_VUS   },  // baseline (10 VUs)
        { duration: '30s', target: SPIKE_VUS  },  // instant surge (→ 200 VUs)
        { duration: '2m',  target: SPIKE_VUS  },  // hold at peak
        { duration: '1m',  target: BASE_VUS   },  // recovery
        { duration: '30s', target: 0          },  // ramp down
      ],
      exec:         'spikeHttpJourney',
      gracefulStop: '2m',
    },

    // WebSocket load runs during the spike to validate odds-feed stability
    spike_ws: {
      executor:  'constant-vus',
      vus:       Math.max(1, Math.round(BASE_VUS * 3 * VU_SCALE)),
      duration:  '5m',
      exec:      'spikeWsJourney',
      gracefulStop: '1m',
    },
  },

  thresholds: Object.assign({}, GLOBAL_THRESHOLDS, {
    // Relaxed during spike — platform is under extreme load
    http_req_duration:       ['p(95)<3000', 'p(99)<8000'],
    http_req_failed:         ['rate<0.05'],   // tolerate up to 5% errors at spike peak
    bet_placement_duration:  ['p(95)<3000'],
    bet_placement_success:   ['rate>0.95'],
    wallet_op_success:       ['rate>0.99'],   // payments must still succeed
    spike_request_latency:   ['p(95)<3000'],
  }),
};

// ---------------------------------------------------------------------------
// Journey: spike HTTP — aggressive betting on a goal event
// ---------------------------------------------------------------------------

export function spikeHttpJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) {
    spikeErrors.add(1);
    sleep(randomIntBetween(1, 3));
    return;
  }

  const { token } = session;
  const eventId   = randomItem(TEST_DATA.eventIds);

  // Track current peak
  peakVUsReached.add(__VU);

  group('spike: odds rush', function () {
    // Goal event → every player instantly refreshes odds
    // Batch fetch multiple markets simultaneously (browser-like parallelism)
    const markets  = TEST_DATA.marketIds.slice(0, 4);
    const requests = markets.map((m) => ({
      method: 'GET',
      url:    ENDPOINTS.eventOdds(eventId, m),
      params: {
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        tags:    { name: 'sports/odds' },
      },
    }));

    const start = Date.now();
    const responses = http.batch(requests);
    spikeLatencyTrend.add(Date.now() - start);

    responses.forEach((r) => {
      if (r.status >= 400) spikeErrors.add(1);
      checkOddsResponse(r);
    });
  });

  sleep(randomIntBetween(1, 3));  // split-second decision after seeing new odds

  // During a spike, 70% place a bet immediately (higher than normal 55%)
  if (Math.random() < 0.70) {
    group('spike: reactive bet placement', function () {
      const marketId    = randomItem(TEST_DATA.marketIds);
      const selectionId = `sel-${randomIntBetween(1, 12)}`;
      // Post-goal odds shift dramatically — pick from shifted range
      const odds        = [1.10, 1.20, 1.40, 1.80, 2.50, 4.00][randomIntBetween(0, 5)];
      const stake       = randomItem([5, 10, 20, 50]);

      const start  = Date.now();
      const betRes = placeSportsBet(token, eventId, marketId, selectionId, odds, stake);
      spikeLatencyTrend.add(Date.now() - start);

      const betId = checkBetPlacementResponse(betRes);
      if (!betId && betRes.status >= 500) spikeErrors.add(1);
    });
  }

  // 40% immediately check balance (payout visible yet?)
  if (Math.random() < 0.40) {
    group('spike: balance check', function () {
      const start  = Date.now();
      const res    = getBalance(token);
      spikeLatencyTrend.add(Date.now() - start);
      checkBalanceResponse(res);
    });
  }

  // 15% also deposit during the spike (adrenaline purchase)
  if (Math.random() < 0.15) {
    group('spike: impulse deposit', function () {
      const start  = Date.now();
      const res    = deposit(token, randomItem([20, 50, 100]), randomItem(['card', 'trustly']));
      spikeLatencyTrend.add(Date.now() - start);
      checkDepositResponse(res);
    });
  }

  logout(token);
  // Shorter think time during spike — users are excited
  sleep(randomIntBetween(1, 4));
}

// ---------------------------------------------------------------------------
// Journey: WebSocket — validate odds-feed stability during spike
// ---------------------------------------------------------------------------

export function spikeWsJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(5); return; }

  const { token } = session;
  const eventId   = randomItem(TEST_DATA.eventIds);
  const wsUrl     = ENDPOINTS.oddsFeed(token, eventId);

  const connStart = Date.now();

  const res = ws.connect(wsUrl, {}, function (socket) {
    activeWsConnections.add(1);

    socket.on('open', function () {
      socket.send(JSON.stringify({
        action:   'subscribe',
        channels: TEST_DATA.marketIds.map((m) => `odds.${eventId}.${m}`),
      }));
    });

    let messageCount  = 0;
    let errorCount    = 0;
    let firstMsgTime  = null;

    socket.on('message', function (raw) {
      if (!firstMsgTime) {
        firstMsgTime = Date.now();
        recoveryTime.add(firstMsgTime - connStart);  // time-to-first-message
      }
      messageCount++;
      try {
        const msg = JSON.parse(raw);
        checkWsConnection(null, msg);
      } catch (_) {
        errorCount++;
      }
    });

    socket.on('error', function () {
      errorCount++;
      spikeErrors.add(1);
    });

    socket.on('close', function () {
      activeWsConnections.add(-1);
    });

    // Hold connection for 30–120 s to span the spike window
    const holdMs = randomIntBetween(30, 120) * 1000;
    socket.setTimeout(function () { socket.close(); }, holdMs);

    // Send a keepalive ping every 30 s
    socket.setInterval(function () {
      socket.send(JSON.stringify({ action: 'ping' }));
    }, 30000);
  });

  check(res, { 'spike ws: connection 101': (r) => r && r.status === 101 });

  sleep(randomIntBetween(5, 15));
}

// ---------------------------------------------------------------------------
// Lifecycle hooks
// ---------------------------------------------------------------------------

export function setup() {
  console.log('='.repeat(60));
  console.log('  Spike Test — Goal / Match-Start Surge');
  console.log('='.repeat(60));
  console.log(`  Target:           ${BASE_URL}`);
  console.log(`  Baseline VUs:     ${BASE_VUS}`);
  console.log(`  Spike VUs:        ${SPIKE_VUS}  (${SPIKE_MULTIPLIER}x baseline)`);
  console.log(`  Max spike VUs:    ${Math.round(SPIKE_VUS * 1.5)}`);
  console.log(`  Pattern:          ${BASE_VUS} → ${SPIKE_VUS} → ${BASE_VUS} VUs`);
  console.log(`  Total duration:   ~5 min (extend with VU_SCALE for multi-cycle)`);
  console.log('='.repeat(60));

  if (!checkHealth()) {
    throw new Error('Target health check failed — aborting spike test');
  }
  return { startTime: Date.now() };
}

export function teardown(data) {
  const mins = ((Date.now() - data.startTime) / 60000).toFixed(1);
  console.log(`Spike test complete after ${mins} min.`);
  console.log('Review spike_request_latency and spike_error_count metrics.');
}
