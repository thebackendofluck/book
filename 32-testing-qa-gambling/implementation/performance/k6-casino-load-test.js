// Companion code for "The Backend of Luck" - Chapter 32, Testing and QA in Gambling.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * k6 Load Test Suite for iGaming Casino Platform
 * =================================================
 * Simulates 50K+ concurrent users across realistic casino workflows:
 *   - Player registration and login
 *   - Deposit via multiple payment methods
 *   - Slot gameplay (spin cycles)
 *   - Live dealer table join/bet/leave
 *   - Sports betting (pre-match and live)
 *   - Withdrawal processing
 *   - Account/history queries
 *
 * Usage:
 *   k6 run k6-casino-load-test.js --env BASE_URL=https://api.casino.com
 *   k6 run k6-casino-load-test.js --env BASE_URL=https://api.casino.com --env SCENARIO=peak_event
 *   k6 cloud k6-casino-load-test.js  # Run on k6 Cloud
 *
 * Scenarios:
 *   - baseline:    Normal traffic (5K concurrent)
 *   - peak:        Saturday evening peak (25K concurrent)
 *   - peak_event:  Major sporting event (50K+ concurrent)
 *   - stress:      Stress test ramp to 75K
 *   - soak:        8-hour endurance test at 10K
 */

import http from 'k6/http';
import { check, group, sleep, fail } from 'k6';
import { Counter, Rate, Trend, Gauge } from 'k6/metrics';
import { SharedArray } from 'k6/data';
import { randomIntBetween, randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE_URL = __ENV.BASE_URL || 'https://api.staging-casino.com';
const SCENARIO = __ENV.SCENARIO || 'peak';
const API_VERSION = __ENV.API_VERSION || 'v1';

// Custom metrics
const loginDuration = new Trend('casino_login_duration', true);
const spinDuration = new Trend('casino_spin_duration', true);
const depositDuration = new Trend('casino_deposit_duration', true);
const withdrawalDuration = new Trend('casino_withdrawal_duration', true);
const betPlaceDuration = new Trend('casino_bet_place_duration', true);
const liveDealerJoinDuration = new Trend('casino_live_dealer_join_duration', true);
const gameErrors = new Counter('casino_game_errors');
const paymentErrors = new Counter('casino_payment_errors');
const successRate = new Rate('casino_success_rate');
const activePlayersGauge = new Gauge('casino_active_players');

// Test data
const testPlayers = new SharedArray('players', function () {
    const players = [];
    for (let i = 0; i < 100000; i++) {
        players.push({
            username: `loadtest_player_${i}`,
            password: `${__ENV.LOADTEST_PASSWORD || "loadtest-user"}`,
            email: `player${i}@loadtest.casino.com`,
            currency: randomItem(['USD', 'EUR', 'GBP', 'CAD', 'AUD']),
        });
    }
    return players;
});

const slotGames = [
    'starburst', 'book-of-dead', 'gonzo-quest', 'mega-moolah',
    'sweet-bonanza', 'gates-of-olympus', 'big-bass-bonanza',
    'wolf-gold', 'reactoonz', 'dead-or-alive-2',
];

const liveTableIds = [
    'blackjack-a', 'blackjack-b', 'blackjack-vip',
    'roulette-eu-1', 'roulette-eu-2', 'roulette-immersive',
    'baccarat-1', 'baccarat-speed',
    'game-show-crazy-time', 'game-show-monopoly',
];

const paymentMethods = ['visa', 'mastercard', 'skrill', 'neteller', 'paysafecard', 'crypto_btc'];

// ---------------------------------------------------------------------------
// Scenario Definitions
// ---------------------------------------------------------------------------

export const options = {
    scenarios: {
        // Baseline: Normal weekday traffic
        baseline: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '2m', target: 1000 },
                { duration: '5m', target: 5000 },
                { duration: '10m', target: 5000 },
                { duration: '2m', target: 0 },
            ],
            exec: 'casinoWorkflow',
            tags: { scenario: 'baseline' },
            ...(SCENARIO !== 'baseline' && { startTime: '99h' }), // disable if not selected
        },

        // Peak: Saturday evening
        peak: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '3m', target: 5000 },
                { duration: '5m', target: 15000 },
                { duration: '10m', target: 25000 },
                { duration: '15m', target: 25000 },
                { duration: '5m', target: 10000 },
                { duration: '2m', target: 0 },
            ],
            exec: 'casinoWorkflow',
            tags: { scenario: 'peak' },
            ...(SCENARIO !== 'peak' && { startTime: '99h' }),
        },

        // Peak Event: Champions League Final, Super Bowl, etc.
        peak_event: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '5m', target: 10000 },
                { duration: '5m', target: 30000 },
                { duration: '5m', target: 50000 },
                { duration: '20m', target: 50000 },  // Sustained peak
                { duration: '5m', target: 55000 },    // Spike during halftime
                { duration: '10m', target: 50000 },
                { duration: '5m', target: 20000 },
                { duration: '5m', target: 0 },
            ],
            exec: 'sportsBettingWorkflow',
            tags: { scenario: 'peak_event' },
            ...(SCENARIO !== 'peak_event' && { startTime: '99h' }),
        },

        // Stress: Find breaking point
        stress: {
            executor: 'ramping-vus',
            startVUs: 0,
            stages: [
                { duration: '5m', target: 10000 },
                { duration: '5m', target: 25000 },
                { duration: '5m', target: 50000 },
                { duration: '5m', target: 75000 },
                { duration: '10m', target: 75000 },
                { duration: '5m', target: 0 },
            ],
            exec: 'casinoWorkflow',
            tags: { scenario: 'stress' },
            ...(SCENARIO !== 'stress' && { startTime: '99h' }),
        },

        // Soak: 8-hour endurance
        soak: {
            executor: 'constant-vus',
            vus: 10000,
            duration: '8h',
            exec: 'casinoWorkflow',
            tags: { scenario: 'soak' },
            ...(SCENARIO !== 'soak' && { startTime: '99h' }),
        },
    },

    thresholds: {
        // SLA thresholds
        http_req_duration: ['p(95)<2000', 'p(99)<5000'],
        http_req_failed: ['rate<0.01'],    // < 1% error rate
        casino_success_rate: ['rate>0.99'],
        casino_spin_duration: ['p(95)<500', 'p(99)<1000'],
        casino_login_duration: ['p(95)<1500'],
        casino_deposit_duration: ['p(95)<3000'],
        casino_bet_place_duration: ['p(95)<300'],  // Betting must be fast
        casino_game_errors: ['count<100'],
        casino_payment_errors: ['count<10'],
    },
};

// ---------------------------------------------------------------------------
// Helper Functions
// ---------------------------------------------------------------------------

function getHeaders(token) {
    const headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Request-ID': `k6-${__VU}-${__ITER}-${Date.now()}`,
    };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
}

function apiUrl(path) {
    return `${BASE_URL}/api/${API_VERSION}${path}`;
}

function checkResponse(res, name) {
    const success = check(res, {
        [`${name} status 2xx`]: (r) => r.status >= 200 && r.status < 300,
        [`${name} response time < 5s`]: (r) => r.timings.duration < 5000,
        [`${name} has body`]: (r) => r.body && r.body.length > 0,
    });
    successRate.add(success);
    if (!success) {
        gameErrors.add(1);
    }
    return success;
}

// ---------------------------------------------------------------------------
// Main Casino Workflow
// ---------------------------------------------------------------------------

export function casinoWorkflow() {
    const player = testPlayers[__VU % testPlayers.length];
    let token = null;

    // 1. Login
    group('01_login', function () {
        const loginPayload = JSON.stringify({
            username: player.username,
            password: player.password,
        });

        const res = http.post(apiUrl('/auth/login'), loginPayload, {
            headers: getHeaders(),
            tags: { name: 'login' },
        });

        loginDuration.add(res.timings.duration);

        if (checkResponse(res, 'login')) {
            try {
                const body = JSON.parse(res.body);
                token = body.token || body.access_token;
            } catch (e) {
                gameErrors.add(1);
            }
        }
    });

    if (!token) {
        // Try registration if login fails
        token = `simulated_token_${__VU}`;
    }

    sleep(randomIntBetween(1, 3));

    // 2. Check balance
    group('02_check_balance', function () {
        const res = http.get(apiUrl('/wallet/balance'), {
            headers: getHeaders(token),
            tags: { name: 'balance' },
        });
        checkResponse(res, 'balance');
    });

    sleep(randomIntBetween(1, 2));

    // 3. Deposit (30% of users)
    if (Math.random() < 0.3) {
        group('03_deposit', function () {
            const depositPayload = JSON.stringify({
                amount: randomItem([10, 20, 50, 100, 200, 500]),
                currency: player.currency,
                method: randomItem(paymentMethods),
                bonus_code: Math.random() < 0.2 ? 'WELCOME100' : undefined,
            });

            const res = http.post(apiUrl('/payments/deposit'), depositPayload, {
                headers: getHeaders(token),
                tags: { name: 'deposit' },
            });

            depositDuration.add(res.timings.duration);

            if (!checkResponse(res, 'deposit')) {
                paymentErrors.add(1);
            }
        });

        sleep(randomIntBetween(2, 5));
    }

    // 4. Game session (main activity)
    const activity = weightedRandom([
        { value: 'slots', weight: 50 },
        { value: 'live_dealer', weight: 25 },
        { value: 'sports', weight: 15 },
        { value: 'browse', weight: 10 },
    ]);

    if (activity === 'slots') {
        group('04_slots_session', function () {
            const game = randomItem(slotGames);

            // Open game
            const openRes = http.get(apiUrl(`/games/slots/${game}/launch`), {
                headers: getHeaders(token),
                tags: { name: 'game_launch' },
            });
            checkResponse(openRes, 'game_launch');

            sleep(randomIntBetween(2, 5));

            // Spin cycle (5-50 spins per session)
            const numSpins = randomIntBetween(5, 50);
            for (let i = 0; i < numSpins; i++) {
                const spinPayload = JSON.stringify({
                    game_id: game,
                    bet_amount: randomItem([0.10, 0.20, 0.50, 1.00, 2.00, 5.00]),
                    lines: 20,
                    autoplay: i > 5 && Math.random() < 0.3,
                });

                const spinRes = http.post(apiUrl(`/games/slots/${game}/spin`), spinPayload, {
                    headers: getHeaders(token),
                    tags: { name: 'slot_spin' },
                });

                spinDuration.add(spinRes.timings.duration);
                checkResponse(spinRes, 'slot_spin');

                // Variable think time between spins
                if (Math.random() < 0.7) {
                    sleep(randomIntBetween(1, 3)); // Normal play
                } else {
                    sleep(randomIntBetween(5, 15)); // Checking wins, pausing
                }
            }
        });
    } else if (activity === 'live_dealer') {
        group('04_live_dealer_session', function () {
            const table = randomItem(liveTableIds);

            // Join table
            const joinRes = http.post(apiUrl(`/live/${table}/join`), '{}', {
                headers: getHeaders(token),
                tags: { name: 'live_join' },
            });

            liveDealerJoinDuration.add(joinRes.timings.duration);
            checkResponse(joinRes, 'live_join');

            sleep(randomIntBetween(3, 10));

            // Place bets (5-20 rounds)
            const numRounds = randomIntBetween(5, 20);
            for (let i = 0; i < numRounds; i++) {
                const betPayload = JSON.stringify({
                    table_id: table,
                    bets: generateLiveBets(table),
                });

                const betRes = http.post(apiUrl(`/live/${table}/bet`), betPayload, {
                    headers: getHeaders(token),
                    tags: { name: 'live_bet' },
                });

                betPlaceDuration.add(betRes.timings.duration);
                checkResponse(betRes, 'live_bet');

                // Wait for round to complete (live dealer rounds take 30-90 seconds)
                sleep(randomIntBetween(15, 45));

                // Check result
                http.get(apiUrl(`/live/${table}/result`), {
                    headers: getHeaders(token),
                    tags: { name: 'live_result' },
                });
            }

            // Leave table
            http.post(apiUrl(`/live/${table}/leave`), '{}', {
                headers: getHeaders(token),
                tags: { name: 'live_leave' },
            });
        });
    } else if (activity === 'sports') {
        sportsBettingFlow(token, player);
    } else {
        // Browse lobby
        group('04_browse', function () {
            http.get(apiUrl('/games/lobby'), {
                headers: getHeaders(token),
                tags: { name: 'lobby' },
            });

            sleep(randomIntBetween(3, 10));

            http.get(apiUrl('/games/categories'), {
                headers: getHeaders(token),
                tags: { name: 'categories' },
            });

            sleep(randomIntBetween(2, 8));

            // Search
            http.get(apiUrl('/games/search?q=mega&limit=20'), {
                headers: getHeaders(token),
                tags: { name: 'game_search' },
            });
        });
    }

    sleep(randomIntBetween(2, 10));

    // 5. Withdrawal (5% of users)
    if (Math.random() < 0.05) {
        group('05_withdrawal', function () {
            const withdrawPayload = JSON.stringify({
                amount: randomItem([20, 50, 100, 200, 500, 1000]),
                currency: player.currency,
                method: randomItem(['bank_transfer', 'visa', 'skrill', 'crypto_btc']),
            });

            const res = http.post(apiUrl('/payments/withdraw'), withdrawPayload, {
                headers: getHeaders(token),
                tags: { name: 'withdrawal' },
            });

            withdrawalDuration.add(res.timings.duration);

            if (!checkResponse(res, 'withdrawal')) {
                paymentErrors.add(1);
            }
        });
    }

    // 6. Check history (20% of users)
    if (Math.random() < 0.2) {
        group('06_history', function () {
            http.get(apiUrl('/player/history?type=game&limit=50'), {
                headers: getHeaders(token),
                tags: { name: 'game_history' },
            });

            http.get(apiUrl('/player/history?type=transaction&limit=20'), {
                headers: getHeaders(token),
                tags: { name: 'tx_history' },
            });
        });
    }

    activePlayersGauge.add(__VU);
}

// ---------------------------------------------------------------------------
// Sports Betting Workflow (for peak_event scenario)
// ---------------------------------------------------------------------------

export function sportsBettingWorkflow() {
    const player = testPlayers[__VU % testPlayers.length];
    const token = `simulated_token_${__VU}`;

    sportsBettingFlow(token, player);
}

function sportsBettingFlow(token, player) {
    group('sports_betting', function () {
        // Get live events
        const eventsRes = http.get(apiUrl('/sports/events?status=live&sport=football'), {
            headers: getHeaders(token),
            tags: { name: 'live_events' },
        });
        checkResponse(eventsRes, 'live_events');

        sleep(randomIntBetween(2, 8));

        // Get odds for specific event
        const eventId = `event_${randomIntBetween(1, 100)}`;
        const oddsRes = http.get(apiUrl(`/sports/events/${eventId}/odds`), {
            headers: getHeaders(token),
            tags: { name: 'event_odds' },
        });
        checkResponse(oddsRes, 'event_odds');

        sleep(randomIntBetween(1, 5));

        // Place bet (single or accumulator)
        const betType = Math.random() < 0.6 ? 'single' : 'accumulator';
        const betPayload = JSON.stringify({
            type: betType,
            selections: betType === 'single'
                ? [{ event_id: eventId, market: '1x2', outcome: randomItem(['home', 'draw', 'away']), odds: (1.5 + Math.random() * 8).toFixed(2) }]
                : Array.from({ length: randomIntBetween(2, 6) }, (_, i) => ({
                    event_id: `event_${randomIntBetween(1, 100)}`,
                    market: randomItem(['1x2', 'over_under', 'btts']),
                    outcome: randomItem(['home', 'draw', 'away', 'over', 'under', 'yes', 'no']),
                    odds: (1.5 + Math.random() * 5).toFixed(2),
                })),
            stake: randomItem([1, 2, 5, 10, 20, 50]),
            currency: player.currency,
        });

        const betRes = http.post(apiUrl('/sports/bets'), betPayload, {
            headers: getHeaders(token),
            tags: { name: 'place_bet' },
        });

        betPlaceDuration.add(betRes.timings.duration);
        checkResponse(betRes, 'place_bet');

        sleep(randomIntBetween(10, 60));

        // Cash out check (30% of users)
        if (Math.random() < 0.3) {
            const cashoutRes = http.get(apiUrl('/sports/bets/active?cashout=available'), {
                headers: getHeaders(token),
                tags: { name: 'cashout_check' },
            });
            checkResponse(cashoutRes, 'cashout_check');

            // Actually cash out (10% of those who check)
            if (Math.random() < 0.1) {
                const cashoutPayload = JSON.stringify({
                    bet_id: `bet_${randomIntBetween(1, 10000)}`,
                    cashout_amount: (5 + Math.random() * 95).toFixed(2),
                });

                http.post(apiUrl('/sports/bets/cashout'), cashoutPayload, {
                    headers: getHeaders(token),
                    tags: { name: 'cashout_execute' },
                });
            }
        }

        // Live score updates (polling)
        for (let i = 0; i < randomIntBetween(3, 10); i++) {
            http.get(apiUrl(`/sports/events/${eventId}/live`), {
                headers: getHeaders(token),
                tags: { name: 'live_score' },
            });
            sleep(randomIntBetween(5, 15));
        }
    });
}

// ---------------------------------------------------------------------------
// Utility Functions
// ---------------------------------------------------------------------------

function weightedRandom(items) {
    const totalWeight = items.reduce((sum, item) => sum + item.weight, 0);
    let random = Math.random() * totalWeight;
    for (const item of items) {
        random -= item.weight;
        if (random <= 0) return item.value;
    }
    return items[items.length - 1].value;
}

function generateLiveBets(tableId) {
    if (tableId.startsWith('blackjack')) {
        return [{ type: 'main', amount: randomItem([5, 10, 25, 50, 100]) }];
    }
    if (tableId.startsWith('roulette')) {
        const numBets = randomIntBetween(1, 8);
        const bets = [];
        const betTypes = ['straight', 'split', 'red', 'black', 'even', 'odd', 'dozen', 'column'];
        for (let i = 0; i < numBets; i++) {
            bets.push({
                type: randomItem(betTypes),
                number: randomIntBetween(0, 36),
                amount: randomItem([1, 2, 5, 10, 25]),
            });
        }
        return bets;
    }
    if (tableId.startsWith('baccarat')) {
        return [{
            type: randomItem(['player', 'banker', 'tie']),
            amount: randomItem([10, 25, 50, 100]),
        }];
    }
    return [{ type: 'main', amount: randomItem([5, 10, 25]) }];
}

// ---------------------------------------------------------------------------
// Lifecycle Hooks
// ---------------------------------------------------------------------------

export function handleSummary(data) {
    const summary = {
        timestamp: new Date().toISOString(),
        scenario: SCENARIO,
        base_url: BASE_URL,
        metrics: {
            total_requests: data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0,
            avg_response_time: data.metrics.http_req_duration ? data.metrics.http_req_duration.values.avg : 0,
            p95_response_time: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(95)'] : 0,
            p99_response_time: data.metrics.http_req_duration ? data.metrics.http_req_duration.values['p(99)'] : 0,
            error_rate: data.metrics.http_req_failed ? data.metrics.http_req_failed.values.rate : 0,
            peak_vus: data.metrics.vus_max ? data.metrics.vus_max.values.max : 0,
        },
        thresholds: data.root_group ? data.root_group.checks : {},
    };

    return {
        'stdout': textSummary(data, { indent: ' ', enableColors: true }),
        'results/k6-summary.json': JSON.stringify(summary, null, 2),
    };
}

function textSummary(data) {
    let output = '\n========================================\n';
    output += `Casino Load Test Summary (${SCENARIO})\n`;
    output += '========================================\n';

    if (data.metrics.http_reqs) {
        output += `Total Requests:    ${data.metrics.http_reqs.values.count}\n`;
    }
    if (data.metrics.http_req_duration) {
        output += `Avg Response Time: ${data.metrics.http_req_duration.values.avg.toFixed(0)}ms\n`;
        output += `P95 Response Time: ${data.metrics.http_req_duration.values['p(95)'].toFixed(0)}ms\n`;
        output += `P99 Response Time: ${data.metrics.http_req_duration.values['p(99)'].toFixed(0)}ms\n`;
    }
    if (data.metrics.http_req_failed) {
        output += `Error Rate:        ${(data.metrics.http_req_failed.values.rate * 100).toFixed(2)}%\n`;
    }
    if (data.metrics.vus_max) {
        output += `Peak VUs:          ${data.metrics.vus_max.values.max}\n`;
    }
    output += '========================================\n';

    return output;
}
