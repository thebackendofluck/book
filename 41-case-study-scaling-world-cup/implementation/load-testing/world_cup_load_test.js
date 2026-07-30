// Companion code for "The Backend of Luck" - Chapter 41, Case Study.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * World Cup Load Test - k6 Script
 * ================================
 * Simulates World Cup Final traffic at 10x normal load with realistic
 * betting patterns, live odds consumption, and bet placement spikes.
 *
 * Models the traffic pattern of a real match:
 *   - Pre-match ramp (45 min before kickoff)
 *   - First half (45 min) with goal-spike events
 *   - Half-time (15 min, moderate activity)
 *   - Second half (45 min) with goal-spike events
 *   - Extra time / penalties (30 min, peak activity)
 *   - Post-match cool-down (30 min)
 *
 * Usage:
 *   k6 run --vus 1000 --duration 3h world_cup_load_test.js
 *   k6 run --env BASE_URL=https://staging.example.com world_cup_load_test.js
 *   k6 run --out influxdb=http://localhost:8086/k6 world_cup_load_test.js
 *
 * Requirements:
 *   - k6 (https://k6.io)
 *   - Target environment with test data seeded
 */

import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep, group } from 'k6';
import { Counter, Rate, Trend, Gauge } from 'k6/metrics';
import { SharedArray } from 'k6/data';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || 'https://api.betting-platform.example.com';
const WS_URL = __ENV.WS_URL || 'wss://ws.betting-platform.example.com';

// Normal baseline: 150 concurrent users
// World Cup Final: 10x = 1500 concurrent users per VU batch
const PEAK_MULTIPLIER = 10;
const BASE_VUS = 150;
const PEAK_VUS = BASE_VUS * PEAK_MULTIPLIER; // 1500

// ---------------------------------------------------------------------------
// Custom Metrics
// ---------------------------------------------------------------------------

const betPlacementDuration = new Trend('bet_placement_duration', true);
const betPlacementRate = new Rate('bet_placement_success');
const oddsRefreshDuration = new Trend('odds_refresh_duration', true);
const wsConnectionDuration = new Trend('ws_connection_duration', true);
const loginDuration = new Trend('login_duration', true);
const cashoutDuration = new Trend('cashout_duration', true);
const betsPlaced = new Counter('bets_placed_total');
const goalsSimulated = new Counter('goals_simulated');
const activeWsConnections = new Gauge('active_ws_connections');

// ---------------------------------------------------------------------------
// Test Data
// ---------------------------------------------------------------------------

const testUsers = new SharedArray('users', function () {
    const users = [];
    for (let i = 1; i <= 5000; i++) {
        users.push({
            username: `loadtest_user_${i}@test.example.com`,
            password: (process.env.LOADTEST_PASSWORD || 'loadtest-user'),
            balance: randomIntBetween(100, 10000),
        });
    }
    return users;
});

const EVENT_ID = 'wc2026-final-001';
const MARKET_IDS = [
    'match-result', '1x2', 'over-under-2.5', 'both-teams-score',
    'correct-score', 'first-goalscorer', 'half-time-result',
    'asian-handicap', 'total-goals', 'next-goal', 'corners',
    'cards', 'player-shots', 'anytime-goalscorer',
];

const SELECTION_ODDS = [1.15, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00, 3.50,
                         4.00, 5.00, 6.00, 8.00, 10.00, 15.00, 25.00];

// ---------------------------------------------------------------------------
// Scenario Configuration - Match Timeline
// ---------------------------------------------------------------------------

export const options = {
    scenarios: {
        // Pre-match: gradual ramp from baseline to 3x
        pre_match: {
            executor: 'ramping-vus',
            startVUs: BASE_VUS,
            stages: [
                { duration: '15m', target: BASE_VUS * 2 },
                { duration: '15m', target: BASE_VUS * 3 },
                { duration: '15m', target: BASE_VUS * 5 },
            ],
            exec: 'preMatchScenario',
            startTime: '0s',
        },
        // First half: 5x-8x with goal spikes to 12x
        first_half: {
            executor: 'ramping-vus',
            startVUs: BASE_VUS * 5,
            stages: [
                { duration: '10m', target: BASE_VUS * 8 },   // Kickoff spike
                { duration: '5m', target: BASE_VUS * 6 },    // Settle
                { duration: '5m', target: PEAK_VUS },        // Goal event
                { duration: '5m', target: BASE_VUS * 7 },    // After goal
                { duration: '10m', target: BASE_VUS * 8 },   // Build-up
                { duration: '5m', target: PEAK_VUS * 1.2 },  // Second goal
                { duration: '5m', target: BASE_VUS * 6 },    // Settle
            ],
            exec: 'liveMatchScenario',
            startTime: '45m',
        },
        // Half-time: moderate activity (3-4x)
        half_time: {
            executor: 'constant-vus',
            vus: BASE_VUS * 4,
            duration: '15m',
            exec: 'halfTimeScenario',
            startTime: '90m',
        },
        // Second half: 6x-10x with more frequent spikes
        second_half: {
            executor: 'ramping-vus',
            startVUs: BASE_VUS * 6,
            stages: [
                { duration: '10m', target: BASE_VUS * 8 },
                { duration: '5m', target: PEAK_VUS * 1.3 },  // Goal
                { duration: '5m', target: BASE_VUS * 7 },
                { duration: '5m', target: PEAK_VUS * 1.5 },  // Equalizer! Max spike
                { duration: '5m', target: BASE_VUS * 8 },
                { duration: '10m', target: PEAK_VUS },        // Final minutes
                { duration: '5m', target: PEAK_VUS * 1.2 },  // Full-time whistle
            ],
            exec: 'liveMatchScenario',
            startTime: '105m',
        },
        // Post-match: rapid cool-down
        post_match: {
            executor: 'ramping-vus',
            startVUs: PEAK_VUS,
            stages: [
                { duration: '10m', target: BASE_VUS * 3 },
                { duration: '10m', target: BASE_VUS },
                { duration: '10m', target: BASE_VUS * 0.5 },
            ],
            exec: 'postMatchScenario',
            startTime: '150m',
        },
        // WebSocket connections (live odds feed)
        live_odds_feed: {
            executor: 'ramping-vus',
            startVUs: BASE_VUS * 2,
            stages: [
                { duration: '45m', target: BASE_VUS * 5 },    // Pre-match
                { duration: '45m', target: PEAK_VUS },         // First half
                { duration: '15m', target: BASE_VUS * 4 },    // Half-time
                { duration: '45m', target: PEAK_VUS * 1.2 },  // Second half
                { duration: '30m', target: BASE_VUS },         // Post-match
            ],
            exec: 'liveOddsFeedScenario',
            startTime: '0s',
        },
    },
    thresholds: {
        http_req_duration: ['p(95)<500', 'p(99)<2000'],
        http_req_failed: ['rate<0.05'],
        bet_placement_duration: ['p(95)<1000', 'p(99)<3000'],
        bet_placement_success: ['rate>0.95'],
        odds_refresh_duration: ['p(95)<200'],
        ws_connection_duration: ['p(95)<300'],
        login_duration: ['p(95)<800'],
    },
};

// ---------------------------------------------------------------------------
// Helper Functions
// ---------------------------------------------------------------------------

function getAuthToken(user) {
    const start = Date.now();
    const res = http.post(`${BASE_URL}/api/v2/auth/login`, JSON.stringify({
        email: user.username,
        password: user.password,
    }), {
        headers: { 'Content-Type': 'application/json' },
        tags: { name: 'login' },
    });

    loginDuration.add(Date.now() - start);

    if (res.status === 200) {
        try {
            return JSON.parse(res.body).token;
        } catch (_) {
            return null;
        }
    }
    return null;
}

function getAuthHeaders(token) {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        'X-Request-ID': `k6-${Date.now()}-${randomIntBetween(1000, 9999)}`,
    };
}

function fetchOdds(token, marketId) {
    const start = Date.now();
    const res = http.get(
        `${BASE_URL}/api/v2/events/${EVENT_ID}/markets/${marketId}/odds`,
        { headers: getAuthHeaders(token), tags: { name: 'fetch_odds' } }
    );
    oddsRefreshDuration.add(Date.now() - start);

    check(res, {
        'odds response 200': (r) => r.status === 200,
        'odds data present': (r) => {
            try { return JSON.parse(r.body).odds !== undefined; }
            catch (_) { return false; }
        },
    });

    return res;
}

function placeBet(token, marketId, stake) {
    const odds = randomItem(SELECTION_ODDS);
    const payload = {
        event_id: EVENT_ID,
        market_id: marketId,
        selection_id: `sel-${randomIntBetween(1, 20)}`,
        odds: odds,
        stake: stake,
        bet_type: randomItem(['single', 'single', 'single', 'accumulator']),
        accept_odds_changes: randomItem([true, false]),
    };

    const start = Date.now();
    const res = http.post(
        `${BASE_URL}/api/v2/bets`,
        JSON.stringify(payload),
        { headers: getAuthHeaders(token), tags: { name: 'place_bet' } }
    );
    const duration = Date.now() - start;

    betPlacementDuration.add(duration);
    const success = res.status === 201 || res.status === 200;
    betPlacementRate.add(success);
    if (success) {
        betsPlaced.add(1);
    }

    check(res, {
        'bet placed successfully': (r) => r.status === 201 || r.status === 200,
        'bet confirmation received': (r) => {
            try { return JSON.parse(r.body).bet_id !== undefined; }
            catch (_) { return false; }
        },
        'bet placement < 1s': (r) => duration < 1000,
    });

    return res;
}

function cashOut(token, betId) {
    const start = Date.now();
    const res = http.post(
        `${BASE_URL}/api/v2/bets/${betId}/cashout`,
        JSON.stringify({ accept_value: true }),
        { headers: getAuthHeaders(token), tags: { name: 'cashout' } }
    );
    cashoutDuration.add(Date.now() - start);

    check(res, {
        'cashout successful': (r) => r.status === 200,
    });

    return res;
}

// ---------------------------------------------------------------------------
// Scenarios
// ---------------------------------------------------------------------------

export function preMatchScenario() {
    const user = testUsers[__VU % testUsers.length];
    const token = getAuthToken(user);
    if (!token) return;

    group('pre_match_browsing', function () {
        // Browse events
        http.get(`${BASE_URL}/api/v2/events/featured`, {
            headers: getAuthHeaders(token),
            tags: { name: 'featured_events' },
        });
        sleep(randomIntBetween(1, 3));

        // Check multiple markets
        const marketCount = randomIntBetween(2, 5);
        for (let i = 0; i < marketCount; i++) {
            fetchOdds(token, randomItem(MARKET_IDS));
            sleep(randomIntBetween(1, 2));
        }

        // 30% of users place a pre-match bet
        if (Math.random() < 0.3) {
            const stake = randomIntBetween(5, 100);
            placeBet(token, randomItem(MARKET_IDS), stake);
        }
    });

    sleep(randomIntBetween(5, 15));
}

export function liveMatchScenario() {
    const user = testUsers[__VU % testUsers.length];
    const token = getAuthToken(user);
    if (!token) return;

    group('live_match_betting', function () {
        // Aggressive odds checking during live play
        for (let i = 0; i < randomIntBetween(3, 8); i++) {
            fetchOdds(token, randomItem(MARKET_IDS));
            sleep(randomIntBetween(0.5, 2));
        }

        // 50% chance of placing a live bet per iteration
        if (Math.random() < 0.5) {
            const stake = randomIntBetween(5, 200);
            const betRes = placeBet(token, randomItem(MARKET_IDS), stake);

            // 20% chance of cash-out attempt on existing bets
            if (Math.random() < 0.2 && betRes.status === 201) {
                try {
                    const betId = JSON.parse(betRes.body).bet_id;
                    sleep(randomIntBetween(10, 60));
                    cashOut(token, betId);
                } catch (_) { /* ignore parse errors */ }
            }
        }

        // Check account balance
        http.get(`${BASE_URL}/api/v2/account/balance`, {
            headers: getAuthHeaders(token),
            tags: { name: 'balance_check' },
        });

        // Check bet history
        if (Math.random() < 0.3) {
            http.get(`${BASE_URL}/api/v2/bets/active`, {
                headers: getAuthHeaders(token),
                tags: { name: 'active_bets' },
            });
        }
    });

    sleep(randomIntBetween(2, 8));
}

export function halfTimeScenario() {
    const user = testUsers[__VU % testUsers.length];
    const token = getAuthToken(user);
    if (!token) return;

    group('half_time_activity', function () {
        // Users check results, stats, place second-half bets
        http.get(`${BASE_URL}/api/v2/events/${EVENT_ID}/statistics`, {
            headers: getAuthHeaders(token),
            tags: { name: 'match_stats' },
        });
        sleep(randomIntBetween(2, 5));

        // Browse second-half markets
        fetchOdds(token, 'half-time-result');
        fetchOdds(token, 'next-goal');
        sleep(randomIntBetween(1, 3));

        // 25% place a half-time bet
        if (Math.random() < 0.25) {
            placeBet(token, randomItem(['next-goal', 'total-goals', 'correct-score']),
                     randomIntBetween(10, 150));
        }

        // Check promotions
        http.get(`${BASE_URL}/api/v2/promotions/active`, {
            headers: getAuthHeaders(token),
            tags: { name: 'promotions' },
        });
    });

    sleep(randomIntBetween(5, 20));
}

export function postMatchScenario() {
    const user = testUsers[__VU % testUsers.length];
    const token = getAuthToken(user);
    if (!token) return;

    group('post_match_activity', function () {
        // Check results
        http.get(`${BASE_URL}/api/v2/events/${EVENT_ID}/result`, {
            headers: getAuthHeaders(token),
            tags: { name: 'match_result' },
        });
        sleep(randomIntBetween(1, 3));

        // Check bet settlements
        http.get(`${BASE_URL}/api/v2/bets/settled?event_id=${EVENT_ID}`, {
            headers: getAuthHeaders(token),
            tags: { name: 'settled_bets' },
        });

        // Check balance (payout verification)
        http.get(`${BASE_URL}/api/v2/account/balance`, {
            headers: getAuthHeaders(token),
            tags: { name: 'balance_check' },
        });

        // 10% request withdrawal
        if (Math.random() < 0.1) {
            http.post(`${BASE_URL}/api/v2/account/withdrawal`, JSON.stringify({
                amount: randomIntBetween(50, 500),
                method: randomItem(['bank_transfer', 'pix', 'paypal']),
            }), {
                headers: getAuthHeaders(token),
                tags: { name: 'withdrawal' },
            });
        }
    });

    sleep(randomIntBetween(5, 30));
}

export function liveOddsFeedScenario() {
    const user = testUsers[__VU % testUsers.length];
    const token = getAuthToken(user);
    if (!token) {
        sleep(5);
        return;
    }

    const start = Date.now();
    const url = `${WS_URL}/v2/odds-feed?token=${token}&event=${EVENT_ID}`;

    const res = ws.connect(url, {}, function (socket) {
        activeWsConnections.add(1);
        wsConnectionDuration.add(Date.now() - start);

        socket.on('open', function () {
            // Subscribe to live odds for multiple markets
            socket.send(JSON.stringify({
                action: 'subscribe',
                channels: MARKET_IDS.map(m => `odds.${EVENT_ID}.${m}`),
            }));
        });

        socket.on('message', function (msg) {
            try {
                const data = JSON.parse(msg);
                check(data, {
                    'ws message has type': (d) => d.type !== undefined,
                    'ws odds update valid': (d) => !d.odds || d.odds > 0,
                });
            } catch (_) { /* ignore parse errors */ }
        });

        socket.on('error', function (e) {
            console.error(`WebSocket error: ${e.error()}`);
        });

        // Keep connection alive for 30-120 seconds
        const connectionDuration = randomIntBetween(30, 120);
        socket.setTimeout(function () {
            socket.close();
        }, connectionDuration * 1000);
    });

    check(res, {
        'ws connection established': (r) => r && r.status === 101,
    });

    activeWsConnections.add(-1);
    sleep(randomIntBetween(5, 15));
}

// ---------------------------------------------------------------------------
// Lifecycle Hooks
// ---------------------------------------------------------------------------

export function setup() {
    console.log('=== World Cup Load Test Starting ===');
    console.log(`Target: ${BASE_URL}`);
    console.log(`Peak VUs: ${PEAK_VUS}`);
    console.log(`Peak Multiplier: ${PEAK_MULTIPLIER}x`);

    // Verify target is reachable
    const healthRes = http.get(`${BASE_URL}/health`);
    if (healthRes.status !== 200) {
        console.error(`Target health check failed: ${healthRes.status}`);
    }

    return { startTime: Date.now() };
}

export function teardown(data) {
    const duration = (Date.now() - data.startTime) / 1000 / 60;
    console.log(`=== World Cup Load Test Complete (${duration.toFixed(1)} min) ===`);
}
