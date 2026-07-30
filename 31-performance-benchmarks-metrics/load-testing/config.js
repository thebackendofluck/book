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
 * k6 Load Testing Framework — Shared Configuration
 * =================================================
 * Central configuration for all iGaming load test scenarios.
 * Every setting here can be overridden via environment variables.
 *
 * Environment variables:
 *   BASE_URL      - HTTP base URL of the platform API
 *   WS_URL        - WebSocket base URL (live odds feed)
 *   TEST_PROFILE  - smoke | load | stress | spike | soak (default: load)
 *   VU_SCALE      - multiplier applied to all VU counts   (default: 1)
 *   TARGET_RPS    - target requests-per-second ceiling     (default: unlimited)
 */

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const BASE_URL = __ENV.BASE_URL || 'https://acmetocasino-api.teste.workers.dev';
export const WS_URL   = __ENV.WS_URL   || 'wss://acmetocasino-api.teste.workers.dev';

export const ENDPOINTS = {
  // Health (Workers route — no prefix)
  health:   `${BASE_URL}/`,
  readiness:`${BASE_URL}/health`,

  // Auth
  login:        `${BASE_URL}/api/auth/login`,
  logout:       `${BASE_URL}/api/auth/logout`,
  register:     `${BASE_URL}/api/auth/register`,
  refreshToken: `${BASE_URL}/api/auth/token/refresh`,

  // Player account
  profile:      `${BASE_URL}/api/account/profile`,
  balance:      `${BASE_URL}/api/wallet/balance`,
  limits:       `${BASE_URL}/api/account/limits`,
  history:      `${BASE_URL}/api/account/history`,

  // Wallet
  deposit:      `${BASE_URL}/api/payments/deposit`,
  withdrawal:   `${BASE_URL}/api/payments/withdrawal`,
  transactions: `${BASE_URL}/api/wallet/transactions`,

  // Games
  lobby:           `${BASE_URL}/api/games`,
  lobbyCategories: `${BASE_URL}/api/games/categories`,
  gameList:        (category) => `${BASE_URL}/api/games?category=${category}&limit=50`,
  gameDetail:      (gameId)   => `${BASE_URL}/api/games/${gameId}`,
  gameLaunch:      (gameId)   => `${BASE_URL}/api/games/${gameId}/launch`,
  gameSession:     (sessionId) => `${BASE_URL}/api/games/sessions/${sessionId}`,

  // Bets (casino rounds / sportsbook)
  betPlace:    `${BASE_URL}/api/bets`,
  betDetail:   (betId)  => `${BASE_URL}/api/bets/${betId}`,
  betSettle:   (betId)  => `${BASE_URL}/api/bets/${betId}/settle`,
  betCashout:  (betId)  => `${BASE_URL}/api/bets/${betId}/cashout`,
  betsActive:  `${BASE_URL}/api/bets/active`,
  betsSettled: `${BASE_URL}/api/bets/settled`,

  // Sportsbook
  events:       `${BASE_URL}/api/events`,
  eventDetail:  (eventId) => `${BASE_URL}/api/events/${eventId}`,
  eventMarkets: (eventId) => `${BASE_URL}/api/events/${eventId}/markets`,
  eventOdds:    (eventId, marketId) => `${BASE_URL}/api/events/${eventId}/markets/${marketId}/odds`,
  eventStats:   (eventId) => `${BASE_URL}/api/events/${eventId}/statistics`,
  inPlay:       `${BASE_URL}/api/events/in-play`,

  // Compliance
  jurisdictionCheck: `${BASE_URL}/api/compliance/jurisdiction-check`,

  // Analytics
  analytics:    `${BASE_URL}/api/analytics`,

  // RNG / fairness
  rngVerify:    (roundId) => `${BASE_URL}/api/rng/verify/${roundId}`,
  rngAudit:     `${BASE_URL}/api/rng/audit`,

  // Promotions
  promotions: `${BASE_URL}/api/promotions/active`,
  bonusClaim: (promoId) => `${BASE_URL}/api/promotions/${promoId}/claim`,

  // WebSocket
  oddsFeed: (token, eventId) => `${WS_URL}/ws/odds?token=${token}&event=${eventId}`,
  gameFeed: (token, sessionId) => `${WS_URL}/ws/game?token=${token}&session=${sessionId}`,
};

// ---------------------------------------------------------------------------
// Test profiles
// ---------------------------------------------------------------------------
// Each profile maps to a stages array (ramping-vus) and a set of thresholds.
// VU_SCALE multiplies every vus/target value — useful for scaling a smoke
// test up to a full stress test without editing files.

const VU_SCALE = parseFloat(__ENV.VU_SCALE || '1');

function scale(n) {
  return Math.max(1, Math.round(n * VU_SCALE));
}

export const PROFILES = {
  /**
   * Smoke — minimal load, verify scripts run end-to-end.
   * Use before every test session to confirm nothing is broken.
   */
  smoke: {
    stages: [
      { duration: '1m',  target: scale(5) },
      { duration: '3m',  target: scale(5) },
      { duration: '1m',  target: scale(0) },
    ],
    thresholds: {
      http_req_failed:   ['rate<0.05'],
      http_req_duration: ['p(95)<2000'],
    },
  },

  /**
   * Load — normal production peak. Validates that SLOs hold
   * at expected traffic levels.
   */
  load: {
    stages: [
      { duration: '5m',  target: scale(500) },
      { duration: '20m', target: scale(500) },
      { duration: '5m',  target: scale(0)   },
    ],
    thresholds: {
      http_req_failed:      ['rate<0.01'],
      http_req_duration:    ['p(95)<500', 'p(99)<1500'],
      bet_placement_duration: ['p(95)<1000'],
      wallet_op_duration:   ['p(95)<800'],
      game_launch_duration: ['p(95)<2000'],
      ws_connect_duration:  ['p(95)<500'],
    },
  },

  /**
   * Stress — above peak. Finds the breaking point and verifies
   * graceful degradation rather than hard failure.
   */
  stress: {
    stages: [
      { duration: '5m',  target: scale(500)  },
      { duration: '5m',  target: scale(1000) },
      { duration: '5m',  target: scale(2000) },
      { duration: '5m',  target: scale(3000) },
      { duration: '10m', target: scale(3000) },
      { duration: '5m',  target: scale(0)    },
    ],
    thresholds: {
      http_req_failed:   ['rate<0.05'],   // allow higher errors at stress
      http_req_duration: ['p(95)<2000', 'p(99)<5000'],
      bet_placement_duration: ['p(95)<3000'],
    },
  },

  /**
   * Spike — sudden surge modelling a goal, match-start, or
   * breaking news event. Tests auto-scaling responsiveness.
   */
  spike: {
    stages: [
      { duration: '1m',  target: scale(100)  },  // baseline
      { duration: '30s', target: scale(5000) },  // spike
      { duration: '3m',  target: scale(5000) },  // sustain
      { duration: '1m',  target: scale(100)  },  // recover
      { duration: '1m',  target: scale(0)    },
    ],
    thresholds: {
      http_req_failed:   ['rate<0.05'],
      http_req_duration: ['p(95)<3000'],
    },
  },

  /**
   * Soak — long-duration stability test. Catches memory leaks,
   * connection pool exhaustion, and slow degradation.
   */
  soak: {
    stages: [
      { duration: '5m',  target: scale(300)  },  // ramp up
      { duration: '4h',  target: scale(300)  },  // hold
      { duration: '5m',  target: scale(0)    },  // ramp down
    ],
    thresholds: {
      http_req_failed:   ['rate<0.01'],
      http_req_duration: ['p(95)<500', 'p(99)<1500'],
    },
  },
};

// Resolve active profile (default: load)
const PROFILE_NAME = __ENV.TEST_PROFILE || 'load';
export const ACTIVE_PROFILE = PROFILES[PROFILE_NAME] || PROFILES.load;

// ---------------------------------------------------------------------------
// Global thresholds (merged into every scenario's options)
// ---------------------------------------------------------------------------

export const GLOBAL_THRESHOLDS = {
  // HTTP
  http_req_failed:      ['rate<0.01'],
  http_req_duration:    ['p(95)<500', 'p(99)<1500'],

  // Custom gambling metrics (defined in helpers/checks.js)
  bet_placement_duration:  ['p(95)<1000', 'p(99)<3000'],
  bet_placement_success:   ['rate>0.99'],
  wallet_op_duration:      ['p(95)<800', 'p(99)<2000'],
  wallet_op_success:       ['rate>0.999'],  // payments must not fail
  game_launch_duration:    ['p(95)<2000', 'p(99)<4000'],
  game_launch_success:     ['rate>0.99'],
  rng_verify_duration:     ['p(95)<200'],
  ws_connect_duration:     ['p(95)<500'],
  login_duration:          ['p(95)<800'],
  lobby_load_duration:     ['p(95)<300'],
  odds_refresh_duration:   ['p(95)<200'],
};

// ---------------------------------------------------------------------------
// Scenario weights (probability a virtual user runs each action)
// ---------------------------------------------------------------------------

export const SCENARIO_WEIGHTS = {
  // Casino player journey
  casinoPlayer: {
    viewLobby:     0.95,  // almost everyone browses
    launchGame:    0.60,  // 60% actually open a game
    placeBet:      0.50,  // 50% bet per iteration
    checkBalance:  0.40,
    viewHistory:   0.15,
    claimBonus:    0.10,
    deposit:       0.08,
    withdraw:      0.03,
  },

  // Sportsbook player journey
  sportsbookPlayer: {
    browseLobby:   0.90,
    checkOdds:     0.80,
    placeBet:      0.55,
    checkBalance:  0.35,
    cashOut:       0.15,
    deposit:       0.10,
    withdraw:      0.04,
  },
};

// ---------------------------------------------------------------------------
// Static test data
// ---------------------------------------------------------------------------

export const TEST_DATA = {
  // Game IDs must exist in the target environment's test seed data
  slotGameIds: [
    'game-starburst-001',
    'game-book-of-dead-002',
    'game-gonzo-003',
    'game-reactoonz-004',
    'game-jammin-jars-005',
  ],
  tableGameIds: [
    'game-blackjack-classic-001',
    'game-european-roulette-002',
    'game-baccarat-mini-003',
    'game-texas-holdem-004',
  ],
  liveCasinoGameIds: [
    'game-live-blackjack-vip-001',
    'game-live-roulette-001',
    'game-live-baccarat-001',
    'game-crazy-time-001',
  ],

  // Event IDs for sportsbook testing
  eventIds: [
    'evt-football-premier-001',
    'evt-football-laliga-002',
    'evt-tennis-wimbledon-001',
    'evt-basketball-nba-001',
  ],

  // Market IDs
  marketIds: [
    'match-result',
    '1x2',
    'over-under-2.5',
    'both-teams-score',
    'asian-handicap',
    'total-goals',
    'next-goal',
    'anytime-goalscorer',
    'correct-score',
    'first-goalscorer',
  ],

  // Deposit amounts in EUR
  depositAmounts: [10, 20, 25, 50, 100, 200],

  // Bet stakes in EUR
  betStakes: [1, 2, 5, 10, 20, 25, 50, 100],

  // Payment methods
  paymentMethods: ['card', 'bank_transfer', 'neteller', 'skrill', 'paysafecard', 'trustly'],
};

// ---------------------------------------------------------------------------
// HTTP request defaults
// ---------------------------------------------------------------------------

export const HTTP_PARAMS = {
  timeout: '30s',
  headers: {
    'Content-Type': 'application/json',
    'Accept':        'application/json',
    'X-Client':      'k6-load-test',
    'X-Environment': __ENV.TEST_PROFILE || 'load',
  },
};

// ---------------------------------------------------------------------------
// Timing constants (seconds)
// ---------------------------------------------------------------------------

export const THINK_TIME = {
  short:    [1, 3],    // between rapid consecutive requests
  medium:   [3, 8],    // between logical steps in a journey
  long:     [8, 20],   // between major actions (reading odds, deciding)
  idle:     [20, 60],  // watching a live game, not actively betting
};
