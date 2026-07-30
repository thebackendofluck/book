// Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * exposureCalculator.js — Casino Money Monitor: Exposure Calculator
 *
 * Calculates the ratio of potential payouts to available reserves.
 * Runs on a configurable polling interval (default: 60 seconds).
 *
 * Exposure formula:
 *   exposure% = (pending_withdrawals + active_bet_payouts) / available_reserves * 100
 *
 * Where:
 *   - pending_withdrawals: withdrawals in PROCESSING or ACCEPTED state
 *   - active_bet_payouts: maximum possible payout from all open bets
 *   - available_reserves: sum of all bank account balances minus settlement holds
 *
 * Risk classification:
 *   SAFE      0  – 50%   Normal operations
 *   WARNING   50 – 65%   Monitoring increased
 *   ELEVATED  65 – 75%   Finance team notified
 *   CRITICAL  75 – 85%   Management escalation
 *   EMERGENCY 85 – 95%   Deposit suspension
 *   SHUTDOWN  95%+       Maintenance mode activated
 *
 * Chapter 12 — Real-Time Cash Flow Management
 * See full implementation: scripts/chapter-12/monitor_money/exposure_calculator.js
 */

'use strict';

const EventEmitter = require('events');

const RISK_BANDS = [
  { name: 'SAFE',      min: 0,  max: 50 },
  { name: 'WARNING',   min: 50, max: 65 },
  { name: 'ELEVATED',  min: 65, max: 75 },
  { name: 'CRITICAL',  min: 75, max: 85 },
  { name: 'EMERGENCY', min: 85, max: 95 },
  { name: 'SHUTDOWN',  min: 95, max: Infinity },
];

class ExposureCalculator extends EventEmitter {
  /**
   * @param {object} options
   * @param {object[]} options.bankAdapters - Array of BankAdapter instances
   * @param {object} options.walletStore - Data store with pending withdrawals/open bets
   * @param {number} [options.intervalSeconds=60] - Calculation interval
   * @param {number} [options.reserveBufferPct=5] - Safety buffer % to subtract from reserves
   * @param {object} [options.redisClient] - Redis for caching last snapshot
   * @param {object} [options.logger] - Logger instance
   */
  constructor(options = {}) {
    super();
    this._adapters = options.bankAdapters || [];
    this._wallet = options.walletStore;
    this._intervalSeconds = options.intervalSeconds || 60;
    this._bufferPct = options.reserveBufferPct || 5;
    this._redis = options.redisClient || null;
    this._log = options.logger || console;
    this._timer = null;
    this._lastSnapshot = null;
    this._isRunning = false;
  }

  // ---------------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------------

  start() {
    if (this._isRunning) return;
    this._isRunning = true;
    this._log.info(`ExposureCalculator started — interval ${this._intervalSeconds}s`);

    // Calculate immediately, then on interval
    this._calculate();
    this._timer = setInterval(() => this._calculate(), this._intervalSeconds * 1000);
  }

  stop() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    this._isRunning = false;
    this._log.info('ExposureCalculator stopped');
  }

  /**
   * Get the most recent snapshot (may be null if not yet calculated).
   * @returns {object|null}
   */
  getLastSnapshot() {
    return this._lastSnapshot;
  }

  // ---------------------------------------------------------------------------
  // Calculation
  // ---------------------------------------------------------------------------

  async _calculate() {
    try {
      const [reserves, liabilities] = await Promise.all([
        this._fetchReserves(),
        this._fetchLiabilities(),
      ]);

      const availableReserves = Math.max(0, reserves.total - reserves.settlementHolds);
      const bufferedReserves = availableReserves * (1 - this._bufferPct / 100);
      const potentialPayouts = liabilities.pendingWithdrawals + liabilities.activeBetPayouts;

      const exposurePercent = bufferedReserves > 0
        ? (potentialPayouts / bufferedReserves) * 100
        : 100;

      const riskBand = this._classifyRisk(exposurePercent);

      const snapshot = {
        exposurePercent: Math.min(exposurePercent, 100),
        riskBand: riskBand.name,
        availableReserves,
        bufferedReserves,
        potentialPayouts,
        pendingWithdrawals: liabilities.pendingWithdrawals,
        activeBetPayouts: liabilities.activeBetPayouts,
        settlementHolds: reserves.settlementHolds,
        bankBreakdown: reserves.breakdown,
        calculatedAt: new Date().toISOString(),
        intervalSeconds: this._intervalSeconds,
      };

      this._lastSnapshot = snapshot;

      if (this._redis) {
        await this._redis.setex('exposure:last_snapshot', 300, JSON.stringify(snapshot))
          .catch((err) => this._log.error('Redis snapshot store failed:', err));
      }

      this.emit('snapshot', snapshot);
      this._log.info(
        `Exposure: ${exposurePercent.toFixed(2)}% [${riskBand.name}] ` +
        `reserves=${this._formatAmount(availableReserves)} ` +
        `payouts=${this._formatAmount(potentialPayouts)}`
      );
    } catch (err) {
      this._log.error('Exposure calculation failed:', err);
      this.emit('error', err);
    }
  }

  // ---------------------------------------------------------------------------
  // Data fetchers
  // ---------------------------------------------------------------------------

  async _fetchReserves() {
    const results = await Promise.allSettled(
      this._adapters.map(async (adapter) => {
        const balance = await adapter.getBalance();
        return {
          bankId: adapter.bankId,
          bankName: adapter.bankName,
          balance: balance.balance,
          currency: balance.currency,
        };
      })
    );

    const breakdown = [];
    let total = 0;

    for (const result of results) {
      if (result.status === 'fulfilled') {
        breakdown.push(result.value);
        total += result.value.balance;
      } else {
        this._log.error('Bank balance fetch failed:', result.reason);
        this.emit('bank:error', { error: result.reason });
      }
    }

    // Settlement holds: funds that are reserved but not yet settled
    const settlementHolds = this._wallet
      ? await this._wallet.getSettlementHoldsCents()
      : 0;

    return { total, settlementHolds, breakdown };
  }

  async _fetchLiabilities() {
    if (!this._wallet) {
      return { pendingWithdrawals: 0, activeBetPayouts: 0 };
    }

    const [pendingWithdrawals, activeBetPayouts] = await Promise.all([
      this._wallet.getPendingWithdrawalsCents(),
      this._wallet.getActiveBetMaxPayoutsCents(),
    ]);

    return { pendingWithdrawals, activeBetPayouts };
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  _classifyRisk(exposurePercent) {
    return RISK_BANDS.find((b) => exposurePercent >= b.min && exposurePercent < b.max)
      || RISK_BANDS[RISK_BANDS.length - 1];
  }

  _formatAmount(cents) {
    return `EUR ${(cents / 100).toLocaleString('en-GB', { maximumFractionDigits: 2 })}`;
  }

  /**
   * What-if analysis: estimate exposure if a large jackpot hits.
   *
   * @param {number} jackpotAmountCents
   * @returns {object} Projected snapshot
   */
  projectJackpotScenario(jackpotAmountCents) {
    if (!this._lastSnapshot) {
      throw new Error('No snapshot available — run start() first');
    }

    const projected = this._lastSnapshot.potentialPayouts + jackpotAmountCents;
    const projectedExposure = this._lastSnapshot.bufferedReserves > 0
      ? (projected / this._lastSnapshot.bufferedReserves) * 100
      : 100;

    return {
      scenario: 'jackpot_hit',
      jackpotAmountCents,
      baseExposurePercent: this._lastSnapshot.exposurePercent,
      projectedExposurePercent: Math.min(projectedExposure, 100),
      projectedRiskBand: this._classifyRisk(projectedExposure).name,
      wouldTriggerShutdown: projectedExposure >= 95,
      calculatedAt: new Date().toISOString(),
    };
  }
}

module.exports = { ExposureCalculator, RISK_BANDS };
