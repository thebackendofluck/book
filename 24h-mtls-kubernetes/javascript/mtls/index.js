// Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// mtls/index.js
'use strict';

const fs = require('fs');
const https = require('https');
const tls = require('tls');

/**
 * CertManager watches cert files and provides a dynamic SecureContext
 * that updates atomically when certificates are renewed.
 */
class CertManager {
  constructor({ certFile, keyFile, caFile }) {
    this.certFile = certFile;
    this.keyFile  = keyFile;
    this.caFile   = caFile;
    this._ctx     = null;
    this._reload();
    this._watch();
  }

  _reload() {
    try {
      const ctx = tls.createSecureContext({
        cert: fs.readFileSync(this.certFile),
        key:  fs.readFileSync(this.keyFile),
        ca:   fs.readFileSync(this.caFile),
        minVersion: 'TLSv1.3',
      });
      this._ctx = ctx;
      console.log('[mtls] Certificates reloaded from', this.certFile);
    } catch (err) {
      console.error('[mtls] Certificate reload failed:', err.message);
    }
  }

  _watch() {
    const reload = () => {
      // Debounce: cert-manager writes atomically via rename
      setTimeout(() => this._reload(), 100);
    };

    for (const file of [this.certFile, this.keyFile, this.caFile]) {
      fs.watch(file, { persistent: false }, (event) => {
        if (event === 'change' || event === 'rename') reload();
      });
    }
  }

  getSecureContext() {
    return this._ctx;
  }

  /**
   * Returns server options for https.createServer()
   * requestCert + rejectUnauthorized enforces mutual TLS.
   */
  serverOptions() {
    return {
      SNICallback: (servername, cb) => cb(null, this._ctx),
      requestCert: true,
      rejectUnauthorized: true,
      ca: fs.readFileSync(this.caFile),
      minVersion: 'TLSv1.3',
    };
  }

  /**
   * Returns agent options for outbound HTTPS requests (mTLS client).
   */
  agentOptions() {
    return {
      cert: fs.readFileSync(this.certFile),
      key:  fs.readFileSync(this.keyFile),
      ca:   fs.readFileSync(this.caFile),
      minVersion: 'TLSv1.3',
    };
  }
}

module.exports = { CertManager };
