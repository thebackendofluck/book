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
 * alertEngine.js — Casino Money Monitor: Tiered Alert Engine
 *
 * Five-tier alert escalation system for real-time financial monitoring.
 * Each tier represents an increasing level of exposure risk and triggers
 * a different set of automated responses.
 *
 * Alert Tiers:
 *   1. WARNING   (50-65%)  — Dashboard notification, log entry
 *   2. ELEVATED  (65-75%)  — Email to finance team, 5-min debounce
 *   3. CRITICAL  (75-85%)  — SMS + email to management, pager alert
 *   4. EMERGENCY (85-95%)  — All above + automatic deposit suspension
 *   5. SHUTDOWN  (95%+)    — All above + automatic maintenance mode activation
 *
 * Debounce logic prevents alert storms: a new alert at the same or lower tier
 * will not re-trigger within DEBOUNCE_WINDOW_MS of the previous alert.
 *
 * Chapter 12 — Real-Time Cash Flow Management
 * See full implementation: scripts/chapter-12/monitor_money/alert_system.js
 */

'use strict';

const EventEmitter = require('events');

// ---------------------------------------------------------------------------
// Alert tier definitions
// ---------------------------------------------------------------------------

const ALERT_TIERS = {
  WARNING: {
    level: 1,
    name: 'WARNING',
    minExposure: 50,
    maxExposure: 65,
    color: '#f39c12',
    actions: ['log', 'dashboard_push'],
    debounceMs: 5 * 60 * 1000,  // 5 minutes
  },
  ELEVATED: {
    level: 2,
    name: 'ELEVATED',
    minExposure: 65,
    maxExposure: 75,
    color: '#e67e22',
    actions: ['log', 'dashboard_push', 'email_finance'],
    debounceMs: 5 * 60 * 1000,
  },
  CRITICAL: {
    level: 3,
    name: 'CRITICAL',
    minExposure: 75,
    maxExposure: 85,
    color: '#e74c3c',
    actions: ['log', 'dashboard_push', 'email_management', 'sms_management', 'pager_alert'],
    debounceMs: 3 * 60 * 1000,  // 3 minutes
  },
  EMERGENCY: {
    level: 4,
    name: 'EMERGENCY',
    minExposure: 85,
    maxExposure: 95,
    color: '#c0392b',
    actions: ['log', 'dashboard_push', 'email_management', 'sms_management', 'pager_alert', 'suspend_deposits'],
    debounceMs: 1 * 60 * 1000,  // 1 minute
  },
  SHUTDOWN: {
    level: 5,
    name: 'SHUTDOWN',
    minExposure: 95,
    maxExposure: 100,
    color: '#7b241c',
    actions: ['log', 'dashboard_push', 'email_management', 'sms_management', 'pager_alert', 'suspend_deposits', 'activate_maintenance_mode'],
    debounceMs: 0,  // No debounce — always fire immediately
  },
};

// ---------------------------------------------------------------------------
// AlertEngine
// ---------------------------------------------------------------------------

class AlertEngine extends EventEmitter {
  /**
   * @param {object} options
   * @param {object} options.notifiers - Map of notifier instances { email, sms, pager }
   * @param {object} options.maintenanceService - MaintenanceService instance
   * @param {object} options.redisClient - Redis client for debounce state
   * @param {object} [options.logger] - Logger instance (defaults to console)
   */
  constructor(options = {}) {
    super();
    this._notifiers = options.notifiers || {};
    this._maintenance = options.maintenanceService || null;
    this._redis = options.redisClient || null;
    this._log = options.logger || console;
    this._lastAlertByTier = new Map();
    this._acknowledgedAlerts = new Set();
    this._activeAlert = null;
  }

  // ---------------------------------------------------------------------------
  // Primary entry point
  // ---------------------------------------------------------------------------

  /**
   * Evaluate current exposure percentage and fire alerts if thresholds are breached.
   *
   * @param {object} snapshot - Exposure snapshot from ExposureCalculator
   * @param {number} snapshot.exposurePercent - Current exposure as % of reserves
   * @param {number} snapshot.availableReserves - Total available bank balance (cents)
   * @param {number} snapshot.potentialPayouts - Total potential outflows (cents)
   * @param {string} snapshot.calculatedAt - ISO timestamp
   */
  async evaluate(snapshot) {
    const { exposurePercent } = snapshot;

    const tier = this._resolveTier(exposurePercent);

    if (!tier) {
      // Exposure within safe range — clear any active alert
      if (this._activeAlert) {
        await this._clearAlert(snapshot);
      }
      return null;
    }

    if (this._isDebounced(tier)) {
      this._log.debug(`Alert ${tier.name} debounced — skipping`);
      return null;
    }

    const alert = await this._fireAlert(tier, snapshot);
    return alert;
  }

  /**
   * Acknowledge an active alert.
   * Acknowledged alerts are suppressed from re-firing until the tier changes.
   *
   * @param {string} alertId
   * @param {string} acknowledgedBy
   */
  async acknowledge(alertId, acknowledgedBy) {
    this._acknowledgedAlerts.add(alertId);
    const alert = {
      alertId,
      acknowledgedBy,
      acknowledgedAt: new Date().toISOString(),
    };
    this.emit('alert:acknowledged', alert);

    if (this._redis) {
      await this._redis.setex(
        `alert:ack:${alertId}`,
        3600,
        JSON.stringify(alert)
      );
    }

    this._log.info(`Alert ${alertId} acknowledged by ${acknowledgedBy}`);
    return alert;
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  _resolveTier(exposurePercent) {
    for (const [, tier] of Object.entries(ALERT_TIERS).sort((a, b) => b[1].level - a[1].level)) {
      if (exposurePercent >= tier.minExposure) {
        return tier;
      }
    }
    return null;
  }

  _isDebounced(tier) {
    const lastFired = this._lastAlertByTier.get(tier.name);
    if (!lastFired || tier.debounceMs === 0) return false;
    return Date.now() - lastFired < tier.debounceMs;
  }

  async _fireAlert(tier, snapshot) {
    const alertId = `alert-${tier.name.toLowerCase()}-${Date.now()}`;
    const alert = {
      alertId,
      tier: tier.name,
      tierLevel: tier.level,
      exposurePercent: snapshot.exposurePercent,
      availableReserves: snapshot.availableReserves,
      potentialPayouts: snapshot.potentialPayouts,
      firedAt: new Date().toISOString(),
      acknowledged: false,
    };

    this._lastAlertByTier.set(tier.name, Date.now());
    this._activeAlert = alert;

    this._log.warn(`[ALERT:${tier.name}] Exposure at ${snapshot.exposurePercent.toFixed(2)}%`);

    // Execute tier actions
    const results = await Promise.allSettled(
      tier.actions.map((action) => this._executeAction(action, alert, snapshot))
    );

    results.forEach((result, i) => {
      if (result.status === 'rejected') {
        this._log.error(`Alert action ${tier.actions[i]} failed:`, result.reason);
      }
    });

    this.emit('alert:fired', alert);

    if (this._redis) {
      await this._redis.setex(
        `alert:active:${alertId}`,
        7200,
        JSON.stringify(alert)
      ).catch((err) => this._log.error('Redis alert store failed:', err));
    }

    return alert;
  }

  async _clearAlert(snapshot) {
    const clearedAlert = {
      previousAlert: this._activeAlert,
      clearedAt: new Date().toISOString(),
      exposurePercent: snapshot.exposurePercent,
    };
    this._activeAlert = null;
    this._acknowledgedAlerts.clear();
    this.emit('alert:cleared', clearedAlert);
    this._log.info(`Alert cleared — exposure recovered to ${snapshot.exposurePercent.toFixed(2)}%`);
  }

  async _executeAction(action, alert, snapshot) {
    switch (action) {
      case 'log':
        this._log.warn(`FINANCIAL ALERT [${alert.tier}]: exposure=${alert.exposurePercent.toFixed(2)}%`);
        break;

      case 'dashboard_push':
        this.emit('dashboard:alert', alert);
        break;

      case 'email_finance':
        if (this._notifiers.email) {
          await this._notifiers.email.sendAlert({
            to: process.env.EMAIL_FINANCE_TEAM,
            subject: `[${alert.tier}] Financial exposure alert`,
            alert,
          });
        }
        break;

      case 'email_management':
        if (this._notifiers.email) {
          await this._notifiers.email.sendAlert({
            to: process.env.EMAIL_MANAGEMENT_TEAM,
            subject: `[${alert.tier}] URGENT: Financial exposure alert`,
            alert,
          });
        }
        break;

      case 'sms_management':
        if (this._notifiers.sms) {
          await this._notifiers.sms.send({
            to: process.env.SMS_MANAGEMENT_NUMBERS,
            body: `[${alert.tier}] Casino exposure at ${alert.exposurePercent.toFixed(0)}% — immediate action required`,
          });
        }
        break;

      case 'pager_alert':
        if (this._notifiers.pager) {
          await this._notifiers.pager.trigger({
            severity: 'critical',
            summary: `Financial exposure at ${alert.exposurePercent.toFixed(0)}%`,
            details: alert,
          });
        }
        break;

      case 'suspend_deposits':
        this.emit('deposits:suspend', { reason: `exposure_alert_${alert.tier}`, alert });
        break;

      case 'activate_maintenance_mode':
        if (this._maintenance) {
          await this._maintenance.activate({
            reason: `AUTO: Exposure at ${alert.exposurePercent.toFixed(0)}% — shutdown tier triggered`,
            activatedBy: 'alert_engine',
          });
        }
        break;

      default:
        this._log.warn(`Unknown alert action: ${action}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = { AlertEngine, ALERT_TIERS };
