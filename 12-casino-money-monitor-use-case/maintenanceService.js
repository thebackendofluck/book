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
 * maintenanceService.js — Casino Money Monitor: Maintenance Mode Service
 *
 * Manages the operational state of the casino payment systems.
 * Maintenance mode suspends all player-facing payment operations:
 * deposits, withdrawals, and new bet placements.
 *
 * Supports three activation modes:
 *   1. Manual — triggered by an operator via the dashboard or API
 *   2. Automatic — triggered by the AlertEngine when SHUTDOWN tier is reached
 *   3. Scheduled — pre-configured windows for planned maintenance
 *
 * Emergency bypass: in exceptional circumstances (e.g. regulator audit),
 * a hardcoded bypass token can override maintenance mode for read-only operations.
 *
 * Chapter 12 — Real-Time Cash Flow Management
 * See full implementation: scripts/chapter-12/monitor_money/maintenance_mode.js
 */

'use strict';

const EventEmitter = require('events');
const crypto = require('crypto');

const MAINTENANCE_STATE = {
  ACTIVE: 'ACTIVE',
  INACTIVE: 'INACTIVE',
  SCHEDULED: 'SCHEDULED',
};

const SUSPENSION_REASON = {
  MANUAL: 'MANUAL',
  AUTO_EXPOSURE: 'AUTO_EXPOSURE',
  SCHEDULED: 'SCHEDULED',
  EMERGENCY: 'EMERGENCY',
};

class MaintenanceService extends EventEmitter {
  /**
   * @param {object} options
   * @param {object} options.store - Persistent state store (database or Redis)
   * @param {object} [options.notifier] - Notification service for player-facing messaging
   * @param {object} [options.logger] - Logger instance
   */
  constructor(options = {}) {
    super();
    this._store = options.store;
    this._notifier = options.notifier || null;
    this._log = options.logger || console;
    this._state = MAINTENANCE_STATE.INACTIVE;
    this._activeRecord = null;
    this._scheduledWindows = [];
    this._scheduleTimer = null;
  }

  // ---------------------------------------------------------------------------
  // Activation / Deactivation
  // ---------------------------------------------------------------------------

  /**
   * Activate maintenance mode.
   *
   * @param {object} params
   * @param {string} params.reason - Human-readable reason (logged and displayed)
   * @param {string} params.activatedBy - Operator username or 'alert_engine'
   * @param {string} [params.suspensionType] - SUSPENSION_REASON value
   * @param {number} [params.durationMinutes] - Auto-deactivate after N minutes (0 = indefinite)
   * @returns {Promise<object>} Maintenance record
   */
  async activate(params) {
    if (this._state === MAINTENANCE_STATE.ACTIVE) {
      this._log.warn('Maintenance mode already active — updating reason');
    }

    const record = {
      id: this._generateId(),
      state: MAINTENANCE_STATE.ACTIVE,
      suspensionType: params.suspensionType || SUSPENSION_REASON.MANUAL,
      reason: params.reason,
      activatedBy: params.activatedBy,
      activatedAt: new Date().toISOString(),
      scheduledDeactivationAt: params.durationMinutes
        ? new Date(Date.now() + params.durationMinutes * 60000).toISOString()
        : null,
      deactivatedAt: null,
      deactivatedBy: null,
    };

    this._state = MAINTENANCE_STATE.ACTIVE;
    this._activeRecord = record;

    if (this._store) {
      await this._store.set('maintenance:current', record);
    }

    this._log.warn(`[MAINTENANCE] Activated by ${params.activatedBy}: ${params.reason}`);
    this.emit('maintenance:activated', record);

    // Player-facing notification
    if (this._notifier) {
      await this._notifier.broadcastMaintenanceMessage({
        type: 'MAINTENANCE_START',
        message: 'The payment system is currently undergoing maintenance. Please try again later.',
        scheduledEnd: record.scheduledDeactivationAt,
      }).catch((err) => this._log.error('Maintenance notification failed:', err));
    }

    // Auto-deactivate timer
    if (params.durationMinutes && params.durationMinutes > 0) {
      setTimeout(() => this.deactivate({ reason: 'Scheduled duration elapsed', activatedBy: 'system' }),
        params.durationMinutes * 60000);
    }

    return record;
  }

  /**
   * Deactivate maintenance mode.
   *
   * @param {object} params
   * @param {string} params.reason
   * @param {string} params.activatedBy
   * @returns {Promise<object|null>}
   */
  async deactivate(params) {
    if (this._state !== MAINTENANCE_STATE.ACTIVE) {
      this._log.info('Maintenance mode not active — nothing to deactivate');
      return null;
    }

    const record = {
      ...this._activeRecord,
      state: MAINTENANCE_STATE.INACTIVE,
      deactivatedAt: new Date().toISOString(),
      deactivatedBy: params.activatedBy,
      deactivationReason: params.reason,
    };

    this._state = MAINTENANCE_STATE.INACTIVE;
    this._activeRecord = null;

    if (this._store) {
      await this._store.set('maintenance:current', null);
      await this._store.rpush('maintenance:history', JSON.stringify(record));
    }

    this._log.info(`[MAINTENANCE] Deactivated by ${params.activatedBy}: ${params.reason}`);
    this.emit('maintenance:deactivated', record);

    if (this._notifier) {
      await this._notifier.broadcastMaintenanceMessage({
        type: 'MAINTENANCE_END',
        message: 'Payment services have been restored. Thank you for your patience.',
      }).catch((err) => this._log.error('Maintenance clear notification failed:', err));
    }

    return record;
  }

  // ---------------------------------------------------------------------------
  // Scheduled windows
  // ---------------------------------------------------------------------------

  /**
   * Schedule a maintenance window.
   *
   * @param {object} window
   * @param {Date} window.startAt
   * @param {Date} window.endAt
   * @param {string} window.reason
   * @param {string} window.scheduledBy
   * @returns {string} Window ID
   */
  scheduleWindow(window) {
    const id = this._generateId();
    const entry = { id, ...window };
    this._scheduledWindows.push(entry);
    this._startScheduleChecker();
    this._log.info(`Maintenance window scheduled: ${window.startAt} to ${window.endAt}`);
    return id;
  }

  cancelWindow(windowId) {
    const idx = this._scheduledWindows.findIndex((w) => w.id === windowId);
    if (idx !== -1) {
      this._scheduledWindows.splice(idx, 1);
      return true;
    }
    return false;
  }

  _startScheduleChecker() {
    if (this._scheduleTimer) return;
    this._scheduleTimer = setInterval(() => this._checkSchedules(), 30000); // every 30s
  }

  async _checkSchedules() {
    const now = new Date();
    for (const window of this._scheduledWindows) {
      const startAt = new Date(window.startAt);
      const endAt = new Date(window.endAt);

      if (now >= startAt && now < endAt && this._state !== MAINTENANCE_STATE.ACTIVE) {
        await this.activate({
          reason: window.reason,
          activatedBy: window.scheduledBy,
          suspensionType: SUSPENSION_REASON.SCHEDULED,
        });
      } else if (now >= endAt && this._state === MAINTENANCE_STATE.ACTIVE &&
                 this._activeRecord?.suspensionType === SUSPENSION_REASON.SCHEDULED) {
        await this.deactivate({
          reason: 'Scheduled maintenance window ended',
          activatedBy: 'scheduler',
        });
      }
    }
    // Clean up past windows
    this._scheduledWindows = this._scheduledWindows.filter((w) => new Date(w.endAt) > now);
  }

  // ---------------------------------------------------------------------------
  // Status
  // ---------------------------------------------------------------------------

  /**
   * Check if maintenance is currently active.
   * Used by API middleware to gate payment endpoints.
   *
   * @param {string} [bypassToken] - Emergency bypass token
   * @returns {boolean}
   */
  isActive(bypassToken) {
    if (bypassToken && this._validateBypassToken(bypassToken)) {
      return false; // Bypass granted
    }
    return this._state === MAINTENANCE_STATE.ACTIVE;
  }

  getStatus() {
    return {
      state: this._state,
      activeRecord: this._activeRecord,
      scheduledWindows: this._scheduledWindows,
      checkedAt: new Date().toISOString(),
    };
  }

  async getHistory(limit = 20) {
    if (!this._store) return [];
    const raw = await this._store.lrange('maintenance:history', -limit, -1);
    return raw.map((r) => JSON.parse(r));
  }

  // ---------------------------------------------------------------------------
  // Express middleware
  // ---------------------------------------------------------------------------

  /**
   * Express middleware: block payment endpoints during maintenance.
   *
   * @returns {Function} Express middleware
   */
  middleware() {
    return (req, res, next) => {
      const bypassToken = req.headers['x-maintenance-bypass'];
      if (this.isActive(bypassToken)) {
        return res.status(503).json({
          error: 'SERVICE_UNAVAILABLE',
          message: 'Payment services are temporarily unavailable for maintenance.',
          retryAfter: this._activeRecord?.scheduledDeactivationAt || null,
        });
      }
      return next();
    };
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  _generateId() {
    return `maint-${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;
  }

  _validateBypassToken(token) {
    const expected = process.env.MAINTENANCE_BYPASS_TOKEN;
    if (!expected) return false;
    // Constant-time comparison to prevent timing attacks
    try {
      return crypto.timingSafeEqual(Buffer.from(token), Buffer.from(expected));
    } catch {
      return false;
    }
  }
}

module.exports = { MaintenanceService, MAINTENANCE_STATE, SUSPENSION_REASON };
