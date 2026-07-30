// Companion code for "The Backend of Luck" - Chapter 24h, Mutual TLS Between Kubernetes Services for iGaming Platforms.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// game-engine/server.js
'use strict';

const https  = require('https');
const http   = require('http');
const express = require('express');
const { CertManager } = require('../mtls');

const certMgr = new CertManager({
  certFile: process.env.TLS_CERT_FILE,
  keyFile:  process.env.TLS_KEY_FILE,
  caFile:   process.env.TLS_CA_BUNDLE,
});

const app = express();
app.use(express.json());

// Health probes over plain HTTP on internal-only port
// (avoids needing certs for liveness check during startup)
const healthApp = express();
healthApp.get('/healthz', (req, res) => res.json({ status: 'ok' }));
healthApp.get('/readyz',  (req, res) => res.json({ status: 'ok' }));
http.createServer(healthApp).listen(8080);

// mTLS HTTPS server
https.createServer(certMgr.serverOptions(), app).listen(8443, () => {
  console.log('[game-engine] mTLS server listening on :8443');
});

// Outbound mTLS agent for calls to wallet-service
const { Agent } = require('https');
const walletAgent = new Agent(certMgr.agentOptions());

// Re-create agent when certs rotate
setInterval(() => {
  walletAgent.options = certMgr.agentOptions();
}, 60_000); // check every minute; in practice cert change triggers the fs.watch
