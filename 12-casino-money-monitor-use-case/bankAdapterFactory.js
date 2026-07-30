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
 * bankAdapterFactory.js — Casino Money Monitor: Bank Adapter Factory
 *
 * Factory that creates the appropriate bank adapter based on authentication scheme.
 * Each European banking partner uses a different authentication method under PSD2:
 *
 *   - OAuth2: Most modern banks (Revolut Business, Wise, Deutsche Bank)
 *   - Certificate (mTLS): Some incumbent banks requiring client certificate auth
 *   - PSD2 AISP: Dedicated Account Information Service Provider connections
 *   - API Key: Simple key-based authentication (rare for production banking)
 *
 * The BankAdapter interface is consistent across all implementations — the
 * ExposureCalculator calls getBalance() and getTransactions() without knowing
 * which authentication scheme is in use.
 *
 * Chapter 12 — Real-Time Cash Flow Management
 * See full implementation: scripts/chapter-12/monitor_money/bank_monitor.js
 */

'use strict';

const https = require('https');
const fs = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// Abstract base adapter
// ---------------------------------------------------------------------------

class BankAdapter {
  /**
   * @param {object} config
   * @param {string} config.bankId - Unique identifier for this bank account
   * @param {string} config.bankName - Human-readable bank name
   * @param {string} config.currency - ISO 4217 currency code
   * @param {string} config.baseUrl - API base URL
   * @param {object} [config.logger] - Logger instance
   */
  constructor(config) {
    if (new.target === BankAdapter) {
      throw new Error('BankAdapter is abstract — use a concrete implementation');
    }
    this.bankId = config.bankId;
    this.bankName = config.bankName;
    this.currency = config.currency;
    this.baseUrl = config.baseUrl;
    this._log = config.logger || console;
    this._balanceCache = null;
    this._balanceCacheExpiry = 0;
    this._cacheSeconds = config.cacheSeconds || 30;
  }

  /**
   * Get the current account balance in cents (minor currency units).
   * @returns {Promise<{ balance: number, currency: string, fetchedAt: string }>}
   */
  async getBalance() {
    throw new Error('getBalance() must be implemented by subclass');
  }

  /**
   * Get recent transactions.
   * @param {Date} fromDate
   * @param {Date} toDate
   * @returns {Promise<Array<{id: string, amount: number, direction: 'credit'|'debit', description: string, timestamp: string}>>}
   */
  async getTransactions(fromDate, toDate) {
    throw new Error('getTransactions() must be implemented by subclass');
  }

  /**
   * Check if the bank API connection is healthy.
   * @returns {Promise<boolean>}
   */
  async healthCheck() {
    try {
      await this.getBalance();
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Return cached balance if still valid; otherwise fetch fresh.
   * @protected
   */
  async _cachedBalance() {
    const now = Date.now();
    if (this._balanceCache && now < this._balanceCacheExpiry) {
      return this._balanceCache;
    }
    const fresh = await this.getBalance();
    this._balanceCache = fresh;
    this._balanceCacheExpiry = now + this._cacheSeconds * 1000;
    return fresh;
  }

  /**
   * Helper: parse amount to cents based on currency precision.
   * @param {number|string} amount
   * @param {string} currency
   * @returns {number}
   * @protected
   */
  _toCents(amount, currency) {
    const zeroDecimalCurrencies = ['JPY', 'KRW', 'VND', 'CLP'];
    const multiplier = zeroDecimalCurrencies.includes(currency) ? 1 : 100;
    return Math.round(parseFloat(amount) * multiplier);
  }
}

// ---------------------------------------------------------------------------
// OAuth2 adapter
// ---------------------------------------------------------------------------

class OAuth2BankAdapter extends BankAdapter {
  constructor(config) {
    super(config);
    this._tokenUrl = config.tokenUrl;
    this._clientId = config.clientId;
    this._clientSecret = config.clientSecret;
    this._scope = config.scope || 'accounts:read transactions:read';
    this._token = null;
    this._tokenExpiry = 0;
  }

  async _getToken() {
    if (this._token && Date.now() < this._tokenExpiry - 60000) {
      return this._token;
    }

    const params = new URLSearchParams({
      grant_type: 'client_credentials',
      client_id: this._clientId,
      client_secret: this._clientSecret,
      scope: this._scope,
    });

    const response = await fetch(this._tokenUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: params.toString(),
    });

    if (!response.ok) {
      throw new Error(`OAuth2 token request failed: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();
    this._token = data.access_token;
    this._tokenExpiry = Date.now() + (data.expires_in || 3600) * 1000;
    return this._token;
  }

  async getBalance() {
    const token = await this._getToken();
    const response = await fetch(`${this.baseUrl}/v1/accounts/${this.bankId}/balance`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    if (!response.ok) {
      throw new Error(`Balance fetch failed for ${this.bankName}: ${response.status}`);
    }

    const data = await response.json();
    return {
      balance: this._toCents(data.balance ?? data.amount, this.currency),
      currency: this.currency,
      fetchedAt: new Date().toISOString(),
    };
  }

  async getTransactions(fromDate, toDate) {
    const token = await this._getToken();
    const params = new URLSearchParams({
      from: fromDate.toISOString(),
      to: toDate.toISOString(),
    });

    const response = await fetch(
      `${this.baseUrl}/v1/accounts/${this.bankId}/transactions?${params}`,
      { headers: { Authorization: `Bearer ${token}` } }
    );

    if (!response.ok) {
      throw new Error(`Transactions fetch failed for ${this.bankName}: ${response.status}`);
    }

    const data = await response.json();
    return (data.transactions || data.items || []).map((tx) => ({
      id: tx.id || tx.transactionId,
      amount: this._toCents(Math.abs(tx.amount), this.currency),
      direction: tx.amount > 0 ? 'credit' : 'debit',
      description: tx.description || tx.narrative || '',
      timestamp: tx.timestamp || tx.bookingDate,
    }));
  }
}

// ---------------------------------------------------------------------------
// Certificate (mTLS) adapter
// ---------------------------------------------------------------------------

class CertificateBankAdapter extends BankAdapter {
  constructor(config) {
    super(config);
    this._certPath = config.certPath;
    this._keyPath = config.keyPath;
    this._caPath = config.caPath;
    this._accountId = config.accountId;
    this._httpsAgent = null;
  }

  _getAgent() {
    if (this._httpsAgent) return this._httpsAgent;
    this._httpsAgent = new https.Agent({
      cert: fs.readFileSync(path.resolve(this._certPath)),
      key: fs.readFileSync(path.resolve(this._keyPath)),
      ca: this._caPath ? fs.readFileSync(path.resolve(this._caPath)) : undefined,
    });
    return this._httpsAgent;
  }

  async getBalance() {
    const { default: nodeFetch } = await import('node-fetch');
    const response = await nodeFetch(
      `${this.baseUrl}/accounts/${this._accountId}/balances`,
      { agent: this._getAgent() }
    );

    if (!response.ok) {
      throw new Error(`Certificate auth balance fetch failed for ${this.bankName}: ${response.status}`);
    }

    const data = await response.json();
    const available = data.balances?.find((b) => b.balanceType === 'AVAILABLE') || data.balances?.[0];
    return {
      balance: this._toCents(available?.balanceAmount?.amount ?? 0, this.currency),
      currency: this.currency,
      fetchedAt: new Date().toISOString(),
    };
  }

  async getTransactions(fromDate, toDate) {
    const { default: nodeFetch } = await import('node-fetch');
    const params = new URLSearchParams({
      dateFrom: fromDate.toISOString().split('T')[0],
      dateTo: toDate.toISOString().split('T')[0],
    });

    const response = await nodeFetch(
      `${this.baseUrl}/accounts/${this._accountId}/transactions?${params}`,
      { agent: this._getAgent() }
    );

    if (!response.ok) {
      throw new Error(`Certificate auth transactions fetch failed for ${this.bankName}: ${response.status}`);
    }

    const data = await response.json();
    return (data.transactions?.booked || []).map((tx) => {
      const amount = parseFloat(tx.transactionAmount?.amount ?? 0);
      return {
        id: tx.transactionId || tx.entryReference,
        amount: this._toCents(Math.abs(amount), this.currency),
        direction: amount > 0 ? 'credit' : 'debit',
        description: tx.remittanceInformationUnstructured || tx.creditorName || '',
        timestamp: tx.bookingDate,
      };
    });
  }
}

// ---------------------------------------------------------------------------
// PSD2 AISP adapter
// ---------------------------------------------------------------------------

class Psd2AispAdapter extends BankAdapter {
  constructor(config) {
    super(config);
    this._consentId = config.consentId;
    this._xRequestId = config.xRequestId;
    this._psuIpAddress = config.psuIpAddress || '127.0.0.1';
    this._apiKey = config.apiKey;
    this._accountId = config.accountId;
  }

  async getBalance() {
    const response = await fetch(
      `${this.baseUrl}/v1/accounts/${this._accountId}/balances`,
      {
        headers: {
          'X-Request-ID': this._xRequestId,
          'Consent-ID': this._consentId,
          'PSU-IP-Address': this._psuIpAddress,
          'X-API-Key': this._apiKey,
        },
      }
    );

    if (!response.ok) {
      throw new Error(`PSD2 AISP balance fetch failed for ${this.bankName}: ${response.status}`);
    }

    const data = await response.json();
    const available = data.balances?.find((b) => b.balanceType === 'interimAvailable') || data.balances?.[0];
    return {
      balance: this._toCents(available?.balanceAmount?.amount ?? 0, this.currency),
      currency: this.currency,
      fetchedAt: new Date().toISOString(),
    };
  }

  async getTransactions(fromDate, toDate) {
    const params = new URLSearchParams({
      dateFrom: fromDate.toISOString().split('T')[0],
      dateTo: toDate.toISOString().split('T')[0],
      bookingStatus: 'booked',
    });

    const response = await fetch(
      `${this.baseUrl}/v1/accounts/${this._accountId}/transactions?${params}`,
      {
        headers: {
          'X-Request-ID': this._xRequestId,
          'Consent-ID': this._consentId,
          'PSU-IP-Address': this._psuIpAddress,
          'X-API-Key': this._apiKey,
        },
      }
    );

    if (!response.ok) {
      throw new Error(`PSD2 AISP transactions fetch failed: ${response.status}`);
    }

    const data = await response.json();
    return (data.transactions?.booked || []).map((tx) => {
      const amount = parseFloat(tx.transactionAmount?.amount ?? 0);
      return {
        id: tx.transactionId,
        amount: this._toCents(Math.abs(amount), this.currency),
        direction: amount > 0 ? 'credit' : 'debit',
        description: tx.remittanceInformationUnstructured || '',
        timestamp: tx.bookingDate,
      };
    });
  }
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

class BankAdapterFactory {
  /**
   * Create a bank adapter from a configuration object.
   *
   * @param {object} config
   * @param {string} config.authScheme - 'oauth2' | 'certificate' | 'psd2_aisp'
   * @param {string} config.bankId
   * @param {string} config.bankName
   * @param {string} config.currency
   * @param {string} config.baseUrl
   * @param {...object} config.authParams - Auth-scheme-specific parameters
   * @returns {BankAdapter}
   */
  static create(config) {
    switch (config.authScheme) {
      case 'oauth2':
        return new OAuth2BankAdapter(config);
      case 'certificate':
      case 'mtls':
        return new CertificateBankAdapter(config);
      case 'psd2_aisp':
        return new Psd2AispAdapter(config);
      default:
        throw new Error(`Unknown bank auth scheme: ${config.authScheme}`);
    }
  }

  /**
   * Create multiple adapters from an array of configurations.
   * Used at application startup to initialise all bank connections.
   *
   * @param {object[]} configs
   * @returns {BankAdapter[]}
   */
  static createAll(configs) {
    return configs.map((c) => BankAdapterFactory.create(c));
  }
}

module.exports = { BankAdapterFactory, BankAdapter, OAuth2BankAdapter, CertificateBankAdapter, Psd2AispAdapter };
