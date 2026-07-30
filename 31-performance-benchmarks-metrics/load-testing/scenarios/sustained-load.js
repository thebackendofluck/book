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
 * Sustained Load — Steady-State Production Test
 * ==============================================
 * Target: https://acmetocasino-api.teste.workers.dev
 *
 * Verifies that SLOs hold at normal production peak.
 * Default profile: 50 VUs for 10 min (2 min ramp + 6 min steady + 2 min ramp-down).
 * Scale up with VU_SCALE env var for full production validation.
 *
 * Traffic pattern (default):
 *   2 min ramp → 6 min steady (50 VUs) → 2 min ramp-down
 *
 * Covers the four primary player journeys:
 *   1. Casino player  (slots, table games, live casino)
 *   2. Sportsbook player (pre-match and live betting)
 *   3. Wallet-heavy user (deposit, check balance, withdraw)
 *   4. Passive browser (lobby only, no bets)
 *
 * Usage:
 *   k6 run scenarios/sustained-load.js
 *   k6 run --env BASE_URL=https://staging.acmetocasino.com \
 *           --env TEST_PROFILE=stress \
 *           scenarios/sustained-load.js
 */

import http from 'k6/http';
import ws   from 'k6/ws';
import { check, sleep, group } from 'k6';
import { SharedArray }         from 'k6/data';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

import {
  BASE_URL, ENDPOINTS, TEST_DATA, THINK_TIME,
  GLOBAL_THRESHOLDS, ACTIVE_PROFILE, SCENARIO_WEIGHTS,
} from '../config.js';
import {
  login, logout, getBalance, getLobby,
  getGamesByCategory, launchGame, placeCasinoBet,
  placeSportsBet, getOdds, getActiveBets,
  deposit, getActivePromotions, verifyRng, checkHealth,
} from '../helpers/requests.js';
import {
  checkLoginResponse, checkLobbyResponse,
  checkGameLaunchResponse, checkBetPlacementResponse,
  checkBalanceResponse, checkDepositResponse,
  checkOddsResponse, checkRngVerifyResponse,
  activeGameSessions,
} from '../helpers/checks.js';

// ---------------------------------------------------------------------------
// VU scale
// ---------------------------------------------------------------------------

const VU_SCALE = parseFloat(__ENV.VU_SCALE || '1');
const S        = (n) => Math.max(1, Math.round(n * VU_SCALE));

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

const users = new SharedArray('sustained-users', function () {
  const arr = [];
  for (let i = 1; i <= 8000; i++) {
    arr.push({
      email:    `sustained_${i}@load.acmetocasino.com`,
      password: (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
    });
  }
  return arr;
});

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export const options = {
  // Default: 50 total VUs across 4 journeys, 10 min total (VU_SCALE=1)
  // Production: set VU_SCALE=10 for 500 VUs / VU_SCALE=4 for 200 VUs
  scenarios: {
    casino_players: {
      executor:     'ramping-vus',
      startVUs:     0,
      stages: [
        { duration: '2m',  target: S(20) },  // ramp
        { duration: '6m',  target: S(20) },  // hold
        { duration: '2m',  target: 0      },  // ramp down
      ],
      exec:         'casinoPlayerJourney',
      gracefulStop: '2m',
    },
    sportsbook_players: {
      executor:     'ramping-vus',
      startVUs:     0,
      stages: [
        { duration: '2m',  target: S(15) },
        { duration: '6m',  target: S(15) },
        { duration: '2m',  target: 0      },
      ],
      exec:         'sportsbookPlayerJourney',
      gracefulStop: '2m',
    },
    wallet_users: {
      executor:     'ramping-vus',
      startVUs:     0,
      stages: [
        { duration: '2m',  target: S(5)  },
        { duration: '6m',  target: S(5)  },
        { duration: '2m',  target: 0      },
      ],
      exec:         'walletJourney',
      gracefulStop: '2m',
    },
    passive_browsers: {
      executor:     'ramping-vus',
      startVUs:     0,
      stages: [
        { duration: '2m',  target: S(10) },
        { duration: '6m',  target: S(10) },
        { duration: '2m',  target: 0      },
      ],
      exec:         'passiveBrowserJourney',
      gracefulStop: '2m',
    },
  },

  thresholds: Object.assign({}, GLOBAL_THRESHOLDS, ACTIVE_PROFILE.thresholds),
};

// ---------------------------------------------------------------------------
// Casino player journey
// Login → browse lobby → pick category → load game → play rounds → verify RNG
// ---------------------------------------------------------------------------

export function casinoPlayerJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.medium)); return; }

  const { token } = session;

  group('casino: lobby', function () {
    const res = getLobby(token);
    checkLobbyResponse(res);
    sleep(randomIntBetween(...THINK_TIME.medium));
  });

  // Pick a game category with realistic distribution
  const categories = ['slots', 'slots', 'slots', 'table-games', 'live-casino'];
  const category   = randomItem(categories);

  group(`casino: browse ${category}`, function () {
    const res = getGamesByCategory(token, category);
    check(res, { [`${category} list 200`]: (r) => r.status === 200 });
    sleep(randomIntBetween(...THINK_TIME.long));
  });

  // Select a game based on category
  const gameIdPool = {
    'slots':        TEST_DATA.slotGameIds,
    'table-games':  TEST_DATA.tableGameIds,
    'live-casino':  TEST_DATA.liveCasinoGameIds,
  };
  const gameId = randomItem(gameIdPool[category] || TEST_DATA.slotGameIds);

  // 60% actually launch a game
  if (Math.random() < SCENARIO_WEIGHTS.casinoPlayer.launchGame) {
    const launchData = group(`casino: launch ${category}`, function () {
      const res = launchGame(token, gameId, { mode: 'real', platform: 'desktop' });
      return checkGameLaunchResponse(res);
    });

    if (launchData) {
      activeGameSessions.add(1);

      // Play 2-8 rounds
      const rounds = randomIntBetween(2, 8);
      for (let i = 0; i < rounds; i++) {
        group('casino: play round', function () {
          const stake  = randomItem(TEST_DATA.betStakes);
          const betRes = placeCasinoBet(token, launchData.sessionId, stake, {
            lines:     randomIntBetween(10, 25),
            coin_size: randomItem([0.01, 0.05, 0.10, 0.25, 0.50, 1.00]),
          });
          const betId = checkBetPlacementResponse(betRes);

          // 15% verify RNG after a round (auditors / suspicious players)
          if (betId && Math.random() < 0.15) {
            const rngRes = verifyRng(token, betId);
            checkRngVerifyResponse(rngRes);
          }
        });

        sleep(randomIntBetween(3, 8));  // autoplay speed / reading screen
      }

      activeGameSessions.add(-1);
    }
  }

  // Check balance after playing
  if (Math.random() < SCENARIO_WEIGHTS.casinoPlayer.checkBalance) {
    const res = getBalance(token);
    checkBalanceResponse(res);
  }

  // 10% claim a promotion
  if (Math.random() < SCENARIO_WEIGHTS.casinoPlayer.claimBonus) {
    group('casino: promotions', function () {
      getActivePromotions(token);
    });
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.medium));
}

// ---------------------------------------------------------------------------
// Sportsbook player journey
// Login → browse events → check odds → place bet → monitor live
// ---------------------------------------------------------------------------

export function sportsbookPlayerJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.medium)); return; }

  const { token } = session;

  group('sports: event browsing', function () {
    // Load main sportsbook page
    http.get(ENDPOINTS.events, {
      headers: { Authorization: `Bearer ${token}` },
      tags:    { name: 'sports/events' },
    });
    sleep(randomIntBetween(...THINK_TIME.short));

    // Pick an event and browse its markets
    const eventId = randomItem(TEST_DATA.eventIds);
    http.get(ENDPOINTS.eventMarkets(eventId), {
      headers: { Authorization: `Bearer ${token}` },
      tags:    { name: 'sports/markets' },
    });
    sleep(randomIntBetween(...THINK_TIME.medium));
  });

  // Fetch odds for 2-4 markets before deciding
  group('sports: odds comparison', function () {
    const eventId    = randomItem(TEST_DATA.eventIds);
    const marketsToCheck = randomIntBetween(2, 4);
    for (let i = 0; i < marketsToCheck; i++) {
      const res = getOdds(token, eventId, randomItem(TEST_DATA.marketIds));
      checkOddsResponse(res);
      sleep(randomIntBetween(1, 3));
    }
  });

  // 55% place a bet
  if (Math.random() < SCENARIO_WEIGHTS.sportsbookPlayer.placeBet) {
    group('sports: place bet', function () {
      const eventId     = randomItem(TEST_DATA.eventIds);
      const marketId    = randomItem(TEST_DATA.marketIds);
      const selectionId = `sel-${randomIntBetween(1, 10)}`;
      const odds        = [1.30, 1.70, 2.10, 3.00, 4.50, 7.00][randomIntBetween(0, 5)];
      const stake       = randomItem(TEST_DATA.betStakes);

      const betRes = placeSportsBet(token, eventId, marketId, selectionId, odds, stake);
      checkBetPlacementResponse(betRes);
    });
    sleep(randomIntBetween(...THINK_TIME.medium));
  }

  // 35% check their open bets
  if (Math.random() < SCENARIO_WEIGHTS.sportsbookPlayer.checkBalance) {
    http.get(ENDPOINTS.betsActive, {
      headers: { Authorization: `Bearer ${token}` },
      tags:    { name: 'bet/active' },
    });
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.medium));
}

// ---------------------------------------------------------------------------
// Wallet-heavy journey
// Frequent balance checks, deposits, withdrawals — payment system stress
// ---------------------------------------------------------------------------

export function walletJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.medium)); return; }

  const { token } = session;

  group('wallet: initial balance', function () {
    const res = getBalance(token);
    checkBalanceResponse(res);
  });

  // Deposit
  group('wallet: deposit', function () {
    const amount = randomItem(TEST_DATA.depositAmounts);
    const method = randomItem(TEST_DATA.paymentMethods);
    const res    = deposit(token, amount, method);
    checkDepositResponse(res);
    sleep(randomIntBetween(1, 3));
  });

  // Post-deposit balance check
  group('wallet: post-deposit balance', function () {
    const res = getBalance(token);
    checkBalanceResponse(res);
    sleep(randomIntBetween(...THINK_TIME.short));
  });

  // Verify transaction history loads
  group('wallet: transaction history', function () {
    http.get(`${ENDPOINTS.transactions}?page=1&limit=20`, {
      headers: { Authorization: `Bearer ${token}` },
      tags:    { name: 'wallet/transactions' },
    });
  });

  // 25% request a withdrawal
  if (Math.random() < 0.25) {
    group('wallet: withdrawal', function () {
      const amount = randomItem([20, 50, 100]);
      const res    = http.post(
        ENDPOINTS.withdrawal,
        JSON.stringify({ amount, currency: 'EUR', payment_method: 'bank_transfer' }),
        { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, tags: { name: 'wallet/withdrawal' } },
      );
      check(res, { 'withdrawal 200/202': (r) => r.status === 200 || r.status === 202 });
    });
  }

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.medium));
}

// ---------------------------------------------------------------------------
// Passive browser journey
// Simulates logged-in users who browse but don't bet — common cohort
// ---------------------------------------------------------------------------

export function passiveBrowserJourney() {
  const user    = users[__VU % users.length];
  const session = login(user.email, user.password);
  if (!session) { sleep(randomIntBetween(...THINK_TIME.long)); return; }

  const { token } = session;

  group('browse: lobby', function () {
    const res = getLobby(token);
    checkLobbyResponse(res);
    sleep(randomIntBetween(...THINK_TIME.long));
  });

  group('browse: game categories', function () {
    getGamesByCategory(token, 'slots');
    sleep(randomIntBetween(...THINK_TIME.medium));
    getGamesByCategory(token, 'live-casino');
    sleep(randomIntBetween(...THINK_TIME.medium));
  });

  group('browse: promotions', function () {
    getActivePromotions(token);
    sleep(randomIntBetween(...THINK_TIME.long));
  });

  logout(token);
  sleep(randomIntBetween(...THINK_TIME.long));
}

// ---------------------------------------------------------------------------
// Lifecycle hooks
// ---------------------------------------------------------------------------

export function setup() {
  const profile = __ENV.TEST_PROFILE || 'load';
  console.log('='.repeat(60));
  console.log(`  Sustained Load Test  [profile: ${profile}]`);
  console.log(`  Target: ${BASE_URL}`);
  console.log(`  VU Scale: ${VU_SCALE}x`);
  console.log('='.repeat(60));

  if (!checkHealth()) {
    throw new Error('Target health check failed — aborting test');
  }
  return { startTime: Date.now() };
}

export function teardown(data) {
  const mins = ((Date.now() - data.startTime) / 60000).toFixed(1);
  console.log(`Sustained load test complete after ${mins} min.`);
}
