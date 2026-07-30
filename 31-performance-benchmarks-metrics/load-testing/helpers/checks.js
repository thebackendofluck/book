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
 * Custom checks and metrics — iGaming platform
 * ============================================
 * Centralised k6 metric definitions and check functions for gambling-specific
 * assertions. Import metrics here; check functions keep scenario scripts free
 * of boilerplate assertion logic.
 */

import { check } from 'k6';
import { Counter, Rate, Trend, Gauge } from 'k6/metrics';

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------
// Trends record durations (ms). Rates record success/failure ratios.
// Counters accumulate totals. Gauges track instantaneous values.

// Latency trends (true = report in milliseconds)
export const loginDuration        = new Trend('login_duration',         true);
export const lobbyLoadDuration    = new Trend('lobby_load_duration',    true);
export const gameLaunchDuration   = new Trend('game_launch_duration',   true);
export const betPlacementDuration = new Trend('bet_placement_duration', true);
export const walletOpDuration     = new Trend('wallet_op_duration',     true);
export const rngVerifyDuration    = new Trend('rng_verify_duration',    true);
export const oddsRefreshDuration  = new Trend('odds_refresh_duration',  true);
export const wsConnectDuration    = new Trend('ws_connect_duration',    true);
export const settlementDuration   = new Trend('settlement_duration',    true);
export const cashoutDuration      = new Trend('cashout_duration',       true);

// Success rates
export const loginSuccess         = new Rate('login_success');
export const betPlacementSuccess  = new Rate('bet_placement_success');
export const walletOpSuccess      = new Rate('wallet_op_success');
export const gameLoadSuccess      = new Rate('game_launch_success');
export const rngVerifySuccess     = new Rate('rng_verify_success');

// Business counters (total across the test run)
export const betsPlaced           = new Counter('bets_placed_total');
export const betsSettled          = new Counter('bets_settled_total');
export const depositsInitiated    = new Counter('deposits_initiated_total');
export const withdrawalsInitiated = new Counter('withdrawals_initiated_total');
export const gameSessionsStarted  = new Counter('game_sessions_started_total');
export const cashoutAttempts      = new Counter('cashout_attempts_total');

// Instantaneous gauges
export const activeWsConnections  = new Gauge('active_ws_connections');
export const activeGameSessions   = new Gauge('active_game_sessions');

// ---------------------------------------------------------------------------
// Generic check helpers
// ---------------------------------------------------------------------------

/**
 * Assert an HTTP response was successful (2xx) and optionally validate the
 * body with a predicate. Logs a warning on failure.
 *
 * @param {Response}  res          - k6 HTTP response
 * @param {string}    label        - Human-readable label for check output
 * @param {Function}  [bodyCheck]  - Optional (body: object) => boolean
 * @returns {boolean}
 */
export function assertOk(res, label, bodyCheck) {
  const checks = {
    [`${label}: status 2xx`]: (r) => r.status >= 200 && r.status < 300,
  };

  if (bodyCheck) {
    checks[`${label}: body valid`] = (r) => {
      try {
        return bodyCheck(JSON.parse(r.body));
      } catch (_) {
        return false;
      }
    };
  }

  return check(res, checks);
}

/**
 * Attempt to parse JSON from a response body.
 * Returns the parsed object, or null if parsing fails (avoids test crashes).
 */
export function parseBody(res) {
  try {
    return JSON.parse(res.body);
  } catch (_) {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Domain-specific check functions
// ---------------------------------------------------------------------------

/**
 * Validate a login response.
 * Records loginSuccess rate and returns the parsed session data or null.
 */
export function checkLoginResponse(res) {
  const ok = check(res, {
    'login: status 200':       (r) => r.status === 200,
    'login: token present':    (r) => {
      const b = parseBody(r);
      return b && (b.token || b.access_token);
    },
    'login: player_id present':(r) => {
      const b = parseBody(r);
      return b && (b.player_id || b.user_id);
    },
  });
  loginSuccess.add(ok);
  return ok ? parseBody(res) : null;
}

/**
 * Validate a lobby response.
 * Returns true when the response contains usable game data.
 */
export function checkLobbyResponse(res) {
  return check(res, {
    'lobby: status 200':         (r) => r.status === 200,
    'lobby: has games array':    (r) => {
      const b = parseBody(r);
      return b && Array.isArray(b.games || b.items);
    },
    'lobby: not empty':          (r) => {
      const b = parseBody(r);
      const games = b && (b.games || b.items);
      return Array.isArray(games) && games.length > 0;
    },
  });
}

/**
 * Validate a game launch response.
 * Records gameLoadSuccess and returns the session URL on success.
 */
export function checkGameLaunchResponse(res) {
  const ok = check(res, {
    'game launch: status 200':       (r) => r.status === 200,
    'game launch: session_url present': (r) => {
      const b = parseBody(r);
      return b && (b.session_url || b.url || b.launch_url);
    },
    'game launch: session_id present': (r) => {
      const b = parseBody(r);
      return b && (b.session_id || b.game_session_id);
    },
  });
  gameLoadSuccess.add(ok);
  if (!ok) return null;

  const b = parseBody(res);
  return {
    sessionUrl: b.session_url || b.url || b.launch_url,
    sessionId:  b.session_id  || b.game_session_id,
  };
}

/**
 * Validate a bet placement response.
 * Records betPlacementSuccess and the total bets counter.
 * Returns the bet ID on success, null on failure.
 */
export function checkBetPlacementResponse(res) {
  const ok = check(res, {
    'bet: status 200/201':    (r) => r.status === 200 || r.status === 201,
    'bet: bet_id in body':    (r) => {
      const b = parseBody(r);
      return b && (b.bet_id || b.id || b.coupon_id);
    },
    'bet: accepted status':   (r) => {
      const b = parseBody(r);
      const status = b && (b.status || b.bet_status);
      return ['accepted', 'placed', 'open', 'pending'].includes((status || '').toLowerCase());
    },
  });
  betPlacementSuccess.add(ok);
  if (ok) betsPlaced.add(1);

  if (!ok) return null;
  const b = parseBody(res);
  return b.bet_id || b.id || b.coupon_id;
}

/**
 * Validate a bet settlement response.
 */
export function checkBetSettlementResponse(res) {
  const ok = check(res, {
    'settle: status 200':       (r) => r.status === 200,
    'settle: outcome confirmed': (r) => {
      const b = parseBody(r);
      return b && b.outcome !== undefined;
    },
  });
  if (ok) betsSettled.add(1);
  return ok;
}

/**
 * Validate a wallet deposit initiation.
 * Records walletOpSuccess and depositsInitiated.
 */
export function checkDepositResponse(res) {
  const ok = check(res, {
    'deposit: status 200/202': (r) => r.status === 200 || r.status === 202,
    'deposit: transaction_id': (r) => {
      const b = parseBody(r);
      return b && (b.transaction_id || b.payment_id || b.reference);
    },
  });
  walletOpSuccess.add(ok);
  if (ok) depositsInitiated.add(1);
  return ok ? parseBody(res) : null;
}

/**
 * Validate a withdrawal request.
 */
export function checkWithdrawalResponse(res) {
  const ok = check(res, {
    'withdrawal: status 200/202': (r) => r.status === 200 || r.status === 202,
    'withdrawal: reference':      (r) => {
      const b = parseBody(r);
      return b && (b.reference || b.withdrawal_id || b.transaction_id);
    },
  });
  walletOpSuccess.add(ok);
  if (ok) withdrawalsInitiated.add(1);
  return ok;
}

/**
 * Validate a balance response and return the balance value.
 */
export function checkBalanceResponse(res) {
  check(res, {
    'balance: status 200':       (r) => r.status === 200,
    'balance: amount is number': (r) => {
      const b = parseBody(r);
      const amount = b && (b.balance || b.amount || (b.wallet && b.wallet.balance));
      return typeof amount === 'number' && amount >= 0;
    },
    'balance: currency present': (r) => {
      const b = parseBody(r);
      return b && (b.currency || (b.wallet && b.wallet.currency));
    },
  });

  const b = parseBody(res);
  if (!b) return 0;
  return b.balance || b.amount || (b.wallet && b.wallet.balance) || 0;
}

/**
 * Validate an odds response.
 */
export function checkOddsResponse(res) {
  return check(res, {
    'odds: status 200':      (r) => r.status === 200,
    'odds: data present':    (r) => {
      const b = parseBody(r);
      return b && (b.odds !== undefined || Array.isArray(b.selections));
    },
    'odds: positive values': (r) => {
      const b = parseBody(r);
      if (!b) return false;
      const selections = b.selections || [];
      return selections.every((s) => !s.odds || s.odds > 1.0);
    },
  });
}

/**
 * Validate an RNG verification response.
 * Returns the verification result object or null.
 */
export function checkRngVerifyResponse(res) {
  const ok = check(res, {
    'rng: status 200':          (r) => r.status === 200,
    'rng: verified field':      (r) => {
      const b = parseBody(r);
      return b && b.verified !== undefined;
    },
    'rng: seed_hash present':   (r) => {
      const b = parseBody(r);
      return b && b.seed_hash;
    },
    'rng: outcome matches':     (r) => {
      const b = parseBody(r);
      return b && b.verified === true;
    },
  });
  rngVerifySuccess.add(ok);
  return ok ? parseBody(res) : null;
}

/**
 * Validate a WebSocket connection and an incoming odds message.
 * @param {Response}  wsRes  - Result from ws.connect()
 * @param {object}    [msg]  - Parsed message object (optional)
 */
export function checkWsConnection(wsRes, msg) {
  check(wsRes, {
    'ws: connection established': (r) => r && r.status === 101,
  });

  if (msg) {
    check(msg, {
      'ws message: has type field':    (m) => m && m.type !== undefined,
      'ws message: valid odds update': (m) => !m.odds || (typeof m.odds === 'number' && m.odds > 1.0),
    });
  }
}

/**
 * Validate a cash-out response.
 */
export function checkCashoutResponse(res) {
  const ok = check(res, {
    'cashout: status 200':      (r) => r.status === 200,
    'cashout: amount present':  (r) => {
      const b = parseBody(r);
      return b && typeof (b.amount || b.cashout_value) === 'number';
    },
  });
  cashoutAttempts.add(1);
  return ok;
}

// ---------------------------------------------------------------------------
// Threshold helper
// ---------------------------------------------------------------------------

/**
 * Build a merged thresholds object combining global defaults with
 * scenario-specific overrides.
 *
 * @param {object} globalThresholds
 * @param {object} scenarioThresholds
 * @returns {object}
 */
export function mergeThresholds(globalThresholds, scenarioThresholds) {
  return Object.assign({}, globalThresholds, scenarioThresholds);
}
