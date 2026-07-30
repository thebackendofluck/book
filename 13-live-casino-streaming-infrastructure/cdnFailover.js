// Companion code for "The Backend of Luck" - Chapter 13, Live Casino Streaming Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * cdnFailover.js — Multi-CDN Client-Side Failover
 *
 * Client-side CDN failover manager for HLS live casino streams.
 * Maintains an ordered list of CDN endpoints and automatically demotes
 * failing providers, promoting them again after recovery.
 *
 * Failover triggers:
 *   - HTTP 5xx response on segment or manifest fetch
 *   - Connection timeout (> 2s for manifests, > 5s for segments)
 *   - Consecutive failures exceeding FAILURE_THRESHOLD
 *
 * Recovery:
 *   - Demoted CDNs are probed every RECOVERY_PROBE_INTERVAL_MS
 *   - A successful health probe promotes the CDN back to its original position
 *
 * Chapter 13 — Live Casino Streaming Infrastructure
 */

'use strict';

const DEFAULT_FAILURE_THRESHOLD = 3;
const DEFAULT_RECOVERY_PROBE_INTERVAL_MS = 30_000;
const DEFAULT_TIMEOUT_MS = 5_000;

class MultiCdnFailoverManager {
  #endpoints;
  #activeIndex;
  #failureCounts;
  #recoveryTimer;
  #log;

  /**
   * @param {Array<{provider: string, url: string}>} endpoints - Ordered list (primary first)
   * @param {object} [options]
   * @param {number} [options.failureThreshold=3]
   * @param {number} [options.recoveryProbeIntervalMs=30000]
   * @param {number} [options.timeoutMs=5000]
   * @param {object} [options.logger]
   */
  constructor(endpoints, options = {}) {
    if (!endpoints || endpoints.length === 0) {
      throw new Error('At least one CDN endpoint is required');
    }
    this.#endpoints = [...endpoints];
    this.#activeIndex = 0;
    this.#failureCounts = new Map(endpoints.map((e) => [e.url, 0]));
    this.#log = options.logger || console;
    this._failureThreshold = options.failureThreshold || DEFAULT_FAILURE_THRESHOLD;
    this._probeInterval = options.recoveryProbeIntervalMs || DEFAULT_RECOVERY_PROBE_INTERVAL_MS;
    this._timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;

    // Start recovery probe
    this.#recoveryTimer = setInterval(() => this.probeRecovery(), this._probeInterval);
  }

  /**
   * Fetch a URL through the CDN failover chain.
   * Automatically retries on the next CDN if the current one fails.
   *
   * @param {string} path - Stream path (e.g. /live/table42/index.m3u8)
   * @param {RequestInit} [fetchOptions]
   * @returns {Promise<Response>}
   */
  async fetch(path, fetchOptions = {}) {
    const startIndex = this.#activeIndex;
    let lastError;

    // Try from active CDN through the end of the list
    for (let i = startIndex; i < this.#endpoints.length; i++) {
      const ep = this.#endpoints[i];
      const url = `${ep.url}${path}`;

      try {
        const response = await this._fetchWithTimeout(url, fetchOptions);

        if (response.ok) {
          // Success — reset failure count for this CDN
          this.#failureCounts.set(ep.url, 0);
          if (i !== this.#activeIndex) {
            this.#log.info(`CDN ${ep.provider} succeeded — maintaining active index at ${i}`);
            this.#activeIndex = i;
          }
          return response;
        }

        throw new Error(`HTTP ${response.status} from CDN ${ep.provider}`);
      } catch (err) {
        lastError = err;
        const failures = (this.#failureCounts.get(ep.url) || 0) + 1;
        this.#failureCounts.set(ep.url, failures);

        this.#log.warn(
          `CDN ${ep.provider} failure ${failures}/${this._failureThreshold}: ${err.message}`
        );

        if (failures >= this._failureThreshold && i === this.#activeIndex) {
          this.#log.error(`CDN ${ep.provider} exceeded failure threshold — demoting`);
          this.#activeIndex = Math.min(i + 1, this.#endpoints.length - 1);
        }
      }
    }

    // All CDNs failed
    throw new Error(`All CDN endpoints failed. Last error: ${lastError?.message}`);
  }

  /**
   * Fetch a manifest file (.m3u8) with appropriate timeout.
   * Manifests need faster timeouts than segments to avoid stale playlist delivery.
   */
  async fetchManifest(path) {
    return this.fetch(path, {
      signal: AbortSignal.timeout(Math.min(this._timeoutMs, 2000)), // Manifest: max 2s
      headers: { 'Cache-Control': 'no-cache' },
    });
  }

  /**
   * Fetch a media segment (.ts / .m4s).
   */
  async fetchSegment(path) {
    return this.fetch(path, {
      signal: AbortSignal.timeout(this._timeoutMs),
    });
  }

  /**
   * Periodically probe demoted CDNs for recovery.
   * Called automatically by internal timer.
   */
  async probeRecovery() {
    for (let i = 0; i < this.#activeIndex; i++) {
      const ep = this.#endpoints[i];
      try {
        const response = await this._fetchWithTimeout(
          `${ep.url}/health`,
          { signal: AbortSignal.timeout(1_000) }
        );

        if (response.ok) {
          this.#log.info(`CDN ${ep.provider} recovered — promoting to index ${i}`);
          this.#activeIndex = i;
          this.#failureCounts.set(ep.url, 0);
          break;
        }
      } catch {
        // Still down — keep probing on next interval
      }
    }
  }

  /**
   * Get the URL for the currently active CDN.
   * @returns {string}
   */
  get activeCdnUrl() {
    return this.#endpoints[this.#activeIndex]?.url ?? '';
  }

  /**
   * Get current CDN health status.
   * @returns {Array<object>}
   */
  getStatus() {
    return this.#endpoints.map((ep, idx) => ({
      provider: ep.provider,
      url: ep.url,
      isActive: idx === this.#activeIndex,
      failureCount: this.#failureCounts.get(ep.url) || 0,
    }));
  }

  /**
   * Manually force failover to next CDN.
   * Used when a stream error is detected by the player but before the
   * failure count threshold is reached.
   */
  forceFailover() {
    if (this.#activeIndex < this.#endpoints.length - 1) {
      const current = this.#endpoints[this.#activeIndex];
      this.#activeIndex++;
      const next = this.#endpoints[this.#activeIndex];
      this.#log.warn(`Forced failover from ${current.provider} to ${next.provider}`);
      return next;
    }
    this.#log.error('Forced failover requested but no more CDN endpoints available');
    return null;
  }

  destroy() {
    clearInterval(this.#recoveryTimer);
  }

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  async _fetchWithTimeout(url, fetchOptions = {}) {
    if (typeof fetch !== 'undefined') {
      return fetch(url, fetchOptions);
    }
    // Node.js < 18 fallback: dynamic import
    const { default: nodeFetch } = await import('node-fetch');
    return nodeFetch(url, fetchOptions);
  }
}

module.exports = { MultiCdnFailoverManager };
