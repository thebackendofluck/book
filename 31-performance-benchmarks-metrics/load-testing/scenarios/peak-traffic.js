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
 * Peak Traffic — Champions League Final Simulation
 * ==================================================
 * Target: https://acmetocasino-api.teste.workers.dev
 *
 * Default profile: 500 VUs burst at peak (VU_SCALE=1).
 * Models the full traffic lifecycle of a high-stakes live match:
 *   1. Pre-match ramp (60 min before kickoff)
 *   2. First half with goal-spike events
 *   3. Half-time burst (review, deposit, place bets)
 *   4. Second half with double-goal spike
 *   5. Extra time / penalty shootout (absolute peak — 500 VUs)
 *   6. Post-match settlement storm
 *   7. Parallel WebSocket live-odds feed throughout
 *
 * Based on observed patterns from Chapter 41 — the 2022 World Cup Final
 * where the platform served 2.3 M concurrent users at peak.
 *
 * Usage:
 *   k6 run scenarios/peak-traffic.js
 *   k6 run --env BASE_URL=https://staging.acmetocasino.com \
 *           --env VU_SCALE=0.1 \
 *           scenarios/peak-traffic.js
 *
 *   # Full World Cup Final simulation (requires distributed k6 execution):
 *   k6 run --env VU_SCALE=100 scenarios/peak-traffic.js
 *
 * VU_SCALE=1   →  ~1 500 peak VUs  (dev / quick validation)
 * VU_SCALE=10  →  ~15 000 peak VUs (staging stress)
 * VU_SCALE=100 →  ~150 000 peak VUs (production dress rehearsal)
 */

import http from 'k6/http';
import ws   from 'k6/ws';
import { check, sleep, group } from 'k6';
import { SharedArray }         from 'k6/data';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

import {
  ENDPOINTS, TEST_DATA, THINK_TIME, GLOBAL_THRESHOLDS,
} from '../config.js';
import {
  login, logout, getBalance, getLobby,
  placeSportsBet, getOdds, getActiveBets, getSettledBets,
  getMatchStatistics, getActivePromotions,
  deposit, withdrawal, checkHealth,
} from '../helpers/requests.js';
import {
  checkLoginResponse, checkBetPlacementResponse, checkOddsResponse,
  checkBalanceResponse, checkDepositResponse, checkWsConnection,
  activeWsConnections, goalsSimulated,
} from '../helpers/checks.js';
import { Counter } from 'k6/metrics';

// ---------------------------------------------------------------------------
// Extra metric for this scenario
// ---------------------------------------------------------------------------

const goalsSimulatedCounter = new Counter('goals_simulated');

// ---------------------------------------------------------------------------
// Scale constants
// ---------------------------------------------------------------------------

const VU_SCALE = parseFloat(__ENV.VU_SCALE || '1');
const B        = (n) => Math.max(1, Math.round(n * VU_SCALE)); // scale helper

// Baseline: 30 VUs represents normal platform load (VU_SCALE=1)
// Peak multipliers model the Champions League final traffic surge:
//   kickoff 2.8x, half-time 3.2x, goal spike +20%, 85th min 4.5x, full-time ~16x = ~480 VUs
// At VU_SCALE=1 the absolute peak (extra time) reaches ~500 VUs
const BASE = B(30);

// ---------------------------------------------------------------------------
// Test users
// ---------------------------------------------------------------------------

const users = new SharedArray('peak-users', function () {
  const arr = [];
  for (let i = 1; i <= 10000; i++) {
    arr.push({
      email:    `peaktest_${i}@load.acmetocasino.com`,
      password: (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
    });
  }
  return arr;
});

// ---------------------------------------------------------------------------
// Scenario configuration
// ---------------------------------------------------------------------------

export const options = {
  scenarios: {
    // -----------------------------------------------------------------------
    // Pre-match ramp: T-60 min → kickoff
    // Traffic grows as players log in, browse markets, place ante-post bets
    // -----------------------------------------------------------------------
    pre_match: {
      executor:   'ramping-vus',
      startVUs:   BASE,
      stages: [
        { duration: '15m', target: BASE * 2   },  // T-45: early arrivals
        { duration: '15m', target: BASE * 3.5 },  // T-30: momentum building
        { duration: '15m', target: BASE * 5   },  // T-15: near kickoff
        { duration: '15m', target: BASE * 8   },  // Kickoff imminent
      ],
      exec:      'preMatchJourney',
      startTime: '0s',
      gracefulStop: '2m',
    },

    // -----------------------------------------------------------------------
    // First half: kickoff → HT whistle  (45 min game-time)
    // Live betting, odds checking, goal spikes at ~23' and ~36'
    // -----------------------------------------------------------------------
    first_half: {
      executor:  'ramping-vus',
      startVUs:  BASE * 5,
      stages: [
        { duration: '5m',  target: BASE * 8   },  // Kickoff surge
        { duration: '8m',  target: BASE * 6   },  // Settle into play
        { duration: '3m',  target: BASE * 12  },  // First goal spike (~23')
        { duration: '4m',  target: BASE * 7   },  // Post-goal settle
        { duration: '5m',  target: BASE * 9   },  // Build to half-time
        { duration: '3m',  target: BASE * 13  },  // Second goal spike (~36')
        { duration: '7m',  target: BASE * 7   },  // Last minutes
        { duration: '10m', target: BASE * 8   },  // HT whistle + delay
      ],
      exec:      'liveMatchJourney',
      startTime: '60m',
      gracefulStop: '3m',
    },

    // -----------------------------------------------------------------------
    // Half-time burst: 15 min
    // Players review stats, check promotions, deposit extra funds, place
    // second-half markets.  Activity is ~3.2x baseline per Chapter 41.
    // -----------------------------------------------------------------------
    half_time: {
      executor:  'constant-vus',
      vus:       BASE * 4,
      duration:  '15m',
      exec:      'halfTimeJourney',
      startTime: '120m',
      gracefulStop: '2m',
    },

    // -----------------------------------------------------------------------
    // Second half: more frequent and higher-intensity spikes
    // Models the Mbappe-brace scenario: two goals in 97 seconds → 2.3 M users
    // -----------------------------------------------------------------------
    second_half: {
      executor:  'ramping-vus',
      startVUs:  BASE * 6,
      stages: [
        { duration: '5m',  target: BASE * 9   },  // Second-half kickoff
        { duration: '8m',  target: BASE * 7   },  // Steady play
        { duration: '2m',  target: BASE * 14  },  // Goal 1 spike (~68')
        { duration: '2m',  target: BASE * 16  },  // Goal 2 spike in 97s
        { duration: '5m',  target: BASE * 9   },  // High-intensity post-goal
        { duration: '5m',  target: BASE * 11  },  // 85th minute peak
        { duration: '5m',  target: BASE * 15  },  // Full-time whistle (5x)
        { duration: '3m',  target: BASE * 12  },  // Settlement begins
      ],
      exec:      'liveMatchJourney',
      startTime: '135m',
      gracefulStop: '3m',
    },

    // -----------------------------------------------------------------------
    // Extra time + penalties (30 min, absolute peak)
    // 2.3 M concurrent, 52 000 rps — every bet needs to be placed
    // -----------------------------------------------------------------------
    extra_time: {
      executor:  'ramping-vus',
      startVUs:  BASE * 12,
      stages: [
        { duration: '10m', target: BASE * 14  },  // Extra time 1
        { duration: '10m', target: BASE * 15  },  // Extra time 2
        { duration: '5m',  target: BASE * 16  },  // Penalty shootout starts
        { duration: '5m',  target: BASE * 15  },  // Post-shootout
      ],
      exec:      'liveMatchJourney',
      startTime: '195m',
      gracefulStop: '3m',
    },

    // -----------------------------------------------------------------------
    // Post-match settlement storm (30 min)
    // Every placed bet resolves simultaneously; payout pipeline stress test
    // -----------------------------------------------------------------------
    post_match: {
      executor:  'ramping-vus',
      startVUs:  BASE * 12,
      stages: [
        { duration: '5m',  target: BASE * 10 },
        { duration: '10m', target: BASE * 6  },
        { duration: '10m', target: BASE * 3  },
        { duration: '5m',  target: BASE      },
      ],
      exec:      'postMatchJourney',
      startTime: '225m',
      gracefulStop: '5m',
    },

    // -----------------------------------------------------------------------
    // Live odds WebSocket feed (runs the entire match)
    // -----------------------------------------------------------------------
    live_ws_feed: {
      executor:  'ramping-vus',
      startVUs:  BASE * 2,
      stages: [
        { duration: '60m',  target: BASE * 4  },  // pre-match
        { duration: '45m',  target: BASE * 8  },  // first half
        { duration: '15m',  target: BASE * 5  },  // half-time
        { duration: '45m',  target: BASE * 10 },  // second half
        { duration: '30m',  target: BASE * 12 },  // extra time
        { duration: '30m',  target: BASE * 3  },  // wind down
      ],
      exec:      'liveOddsWebSocket',
      startTime: '0s',
      gracefulStop: '1m',
    },
  },

  thresholds: Object.assign({}, GLOBAL_THRESHOLDS, {
    // Tighter thresholds for a peak scenario — this is production readiness
    http_req_duration:       ['p(95)<500', 'p(99)<2000'],
    bet_placement_duration:  ['p(95)<1000', 'p(99)<3000'],
    wallet_op_duration:      ['p(95)<800'],
    ws_connect_duration:     ['p(95)<500'],
    http_req_failed:         ['rate<0.01'],
  }),
};

// ---------------------------------------------------------------------------
// Journey: pre-match browsing
// ---------------------------------------------------------------------------

export function preMatchJourney() {
  const user  = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.medium)); return; }

  const { token } = session;

  group('pre_match: initial page load', function () {
    // Parallel: lobby + balance + promotions
    const [lobbyRes, balanceRes] = http.batch([
      { method: 'GET', url: ENDPOINTS.lobby,      params: { headers: { Authorization: `Bearer ${token}` } } },
      { method: 'GET', url: ENDPOINTS.balance,     params: { headers: { Authorization: `Bearer ${token}` } } },
      { method: 'GET', url: ENDPOINTS.promotions,  params: { headers: { Authorization: `Bearer ${token}` } } },
    ]);
    check(lobbyRes,   { 'pre-match lobby 200':  (r) => r.status === 200 });
    check(balanceRes, { 'pre-match balance 200':(r) => r.status === 200 });
    sleep(randomIntBetween(...THINK_TIME.medium));
  });

  group('pre_match: market browsing', function () {
    const eventId  = randomItem(TEST_DATA.eventIds);
    const marketId = randomItem(TEST_DATA.marketIds);

    http.get(ENDPOINTS.eventDetail(eventId), {
      headers: { Authorization: `Bearer ${token}` },
      tags:    { name: 'sports/event' },
    });
    sleep(randomIntBetween(...THINK_TIME.short));

    const oddsRes = getOdds(token, eventId, marketId);
    checkOddsResponse(oddsRes);
    sleep(randomIntBetween(...THINK_TIME.long));
  });

  // 30% of pre-match users place an ante-post bet
  if (Math.random() < 0.30) {
    group('pre_match: bet placement', function () {
      const eventId    = randomItem(TEST_DATA.eventIds);
      const marketId   = randomItem(TEST_DATA.marketIds);
      const selectionId = `sel-${randomIntBetween(1, 8)}`;
      const odds       = [1.50, 2.00, 2.50, 3.00, 4.00, 6.00][randomIntBetween(0, 5)];
      const stake      = randomItem(TEST_DATA.betStakes);

      const betRes = placeSportsBet(token, eventId, marketId, selectionId, odds, stake);
      checkBetPlacementResponse(betRes);
    });
  }

  // 8% deposit before the match
  if (Math.random() < 0.08) {
    group('pre_match: deposit', function () {
      const amount = randomItem(TEST_DATA.depositAmounts);
      const method = randomItem(TEST_DATA.paymentMethods);
      const depRes = deposit(token, amount, method);
      checkDepositResponse(depRes);
    });
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.medium));
}

// ---------------------------------------------------------------------------
// Journey: live match (first half, second half, extra time)
// ---------------------------------------------------------------------------

export function liveMatchJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.short)); return; }

  const { token } = session;
  const eventId   = randomItem(TEST_DATA.eventIds);

  group('live: odds polling burst', function () {
    // Players frantically check odds during live play — 3-8 requests
    const pollCount = randomIntBetween(3, 8);
    for (let i = 0; i < pollCount; i++) {
      const marketId = randomItem(TEST_DATA.marketIds);
      const res      = getOdds(token, eventId, marketId);
      checkOddsResponse(res);
      sleep(randomIntBetween(1, 2));
    }
  });

  // 55% of live users place a bet each iteration
  if (Math.random() < 0.55) {
    group('live: bet placement', function () {
      const marketId    = randomItem(TEST_DATA.marketIds);
      const selectionId = `sel-${randomIntBetween(1, 12)}`;
      const odds        = [1.25, 1.50, 2.00, 2.50, 3.00, 5.00, 8.00][randomIntBetween(0, 6)];
      const stake       = randomItem([1, 2, 5, 10, 20, 50, 100]);

      const betRes  = placeSportsBet(token, eventId, marketId, selectionId, odds, stake);
      const betId   = checkBetPlacementResponse(betRes);

      // 20% immediately attempt cash-out after placing
      if (betId && Math.random() < 0.20) {
        sleep(randomIntBetween(5, 30));
        group('live: cashout', function () {
          const coRes = http.post(
            ENDPOINTS.betCashout(betId),
            JSON.stringify({ accept_value: true }),
            { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, tags: { name: 'bet/cashout' } },
          );
          check(coRes, { 'cashout: 200': (r) => r.status === 200 });
        });
      }
    });
  }

  // Balance check after betting — very common player behaviour
  group('live: balance check', function () {
    const res = getBalance(token);
    checkBalanceResponse(res);
  });

  // 25% check active bet slip
  if (Math.random() < 0.25) {
    http.get(ENDPOINTS.betsActive, {
      headers: { Authorization: `Bearer ${token}` },
      tags:    { name: 'bet/active' },
    });
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.short));
}

// ---------------------------------------------------------------------------
// Journey: half-time
// ---------------------------------------------------------------------------

export function halfTimeJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.medium)); return; }

  const { token } = session;
  const eventId   = randomItem(TEST_DATA.eventIds);

  group('halftime: stats review', function () {
    const statsRes = getMatchStatistics(token, eventId);
    check(statsRes, { 'halftime stats 200': (r) => r.status === 200 });
    sleep(randomIntBetween(...THINK_TIME.medium));
  });

  group('halftime: second-half markets', function () {
    // Browse next-goal and second-half markets
    getOdds(token, eventId, 'next-goal');
    sleep(randomIntBetween(1, 2));
    getOdds(token, eventId, 'total-goals');
  });

  // 25% place a half-time bet
  if (Math.random() < 0.25) {
    group('halftime: bet placement', function () {
      const selectionId = `sel-${randomIntBetween(1, 6)}`;
      const odds        = [1.50, 2.50, 3.50, 5.00][randomIntBetween(0, 3)];
      const stake       = randomItem([5, 10, 20, 50]);
      const betRes = placeSportsBet(token, eventId, 'next-goal', selectionId, odds, stake);
      checkBetPlacementResponse(betRes);
    });
  }

  // 12% deposit more funds during half-time
  if (Math.random() < 0.12) {
    group('halftime: deposit', function () {
      const amount = randomItem([25, 50, 100]);
      const depRes = deposit(token, amount, randomItem(TEST_DATA.paymentMethods));
      checkDepositResponse(depRes);
    });
  }

  group('halftime: promotions', function () {
    const promoRes = getActivePromotions(token);
    check(promoRes, { 'promotions 200': (r) => r.status === 200 });
  });

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.medium));
}

// ---------------------------------------------------------------------------
// Journey: post-match settlement
// ---------------------------------------------------------------------------

export function postMatchJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.medium)); return; }

  const { token } = session;
  const eventId   = randomItem(TEST_DATA.eventIds);

  group('post-match: result check', function () {
    http.get(ENDPOINTS.eventDetail(eventId), {
      headers: { Authorization: `Bearer ${token}` },
      tags:    { name: 'sports/event' },
    });
    sleep(randomIntBetween(1, 3));
  });

  group('post-match: bet settlement check', function () {
    const settledRes = getSettledBets(token, eventId);
    check(settledRes, { 'settled bets 200': (r) => r.status === 200 });
    sleep(randomIntBetween(1, 3));
  });

  group('post-match: balance verification', function () {
    const balanceRes = getBalance(token);
    checkBalanceResponse(balanceRes);
  });

  // 10% request a withdrawal after winnings settle
  if (Math.random() < 0.10) {
    group('post-match: withdrawal', function () {
      const amount    = randomItem([50, 100, 200, 500]);
      const wdRes = withdrawal(token, amount, randomItem(['bank_transfer', 'neteller', 'skrill']));
      check(wdRes, { 'withdrawal initiated': (r) => r.status === 200 || r.status === 202 });
    });
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.long));
}

// ---------------------------------------------------------------------------
// Journey: live WebSocket odds feed
// ---------------------------------------------------------------------------

export function liveOddsWebSocket() {
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
      // Subscribe to the most actively-traded markets
      socket.send(JSON.stringify({
        action:   'subscribe',
        channels: TEST_DATA.marketIds.map((m) => `odds.${eventId}.${m}`),
      }));
    });

    socket.on('message', function (raw) {
      try {
        const msg = JSON.parse(raw);
        checkWsConnection(null, msg);

        // 5% of messages trigger an immediate bet placement (goal-alert behaviour)
        if (msg.type === 'odds_change' && Math.random() < 0.05) {
          goalsSimulatedCounter.add(1);
          // Place a reactive bet synchronously from the WS callback
          const betRes = placeSportsBet(
            token, eventId,
            msg.market_id || randomItem(TEST_DATA.marketIds),
            `sel-${randomIntBetween(1, 8)}`,
            msg.odds || 2.00,
            randomItem([5, 10, 20]),
          );
          checkBetPlacementResponse(betRes);
        }
      } catch (_) { /* ignore */ }
    });

    socket.on('error', function (e) {
      console.error(`WS error VU ${__VU}: ${e.error()}`);
    });

    // Hold the connection for 30–180 seconds
    const holdMs = randomIntBetween(30, 180) * 1000;
    socket.setTimeout(function () { socket.close(); }, holdMs);
  });

  checkWsConnection(res);
  activeWsConnections.add(-1);
  sleep(randomIntBetween(...THINK_TIME.short));
}

// ---------------------------------------------------------------------------
// Lifecycle hooks
// ---------------------------------------------------------------------------

export function setup() {
  console.log('='.repeat(60));
  console.log('  Peak Traffic — Champions League Final Simulation');
  console.log('='.repeat(60));
  console.log(`  Target:       ${ENDPOINTS.health.replace('/health', '')}`);
  console.log(`  VU Scale:     ${VU_SCALE}x  (peak ≈ ${B(30) * 16} VUs, target 500 at VU_SCALE=1)`);
  console.log(`  Total time:   ~255 min`);
  console.log('='.repeat(60));

  if (!checkHealth()) {
    throw new Error('Target health check failed — aborting test');
  }
  return { startTime: Date.now() };
}

export function teardown(data) {
  const mins = ((Date.now() - data.startTime) / 60000).toFixed(1);
  console.log(`Peak traffic simulation complete. Ran for ${mins} min.`);
}
