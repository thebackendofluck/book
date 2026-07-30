// Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// service-monitor.js — Monitors all critical URLs, services, and TCP ports
// Stores results in Redis for the UptimePanel

const fs = require("fs");
const http = require("http");
const https = require("https");
const net = require("net");
const { createClient } = require("redis");

const REDIS_URL = process.env.REDIS_URL || "redis://127.0.0.1:6382";
const CHECK_INTERVAL = 30000; // 30 seconds

// Internal endpoints (e.g. pfSense) that present a self-signed or private-CA
// cert are marked `skipTLS: true`. Rather than disabling verification
// (rejectUnauthorized: false, which accepts ANY certificate — including one
// from an attacker on the LAN), trust that cert only if the operator points
// SERVICE_MONITOR_CA_BUNDLE at the internal CA's PEM file. Without it,
// verification stays on and self-signed targets simply report down.
const CA_BUNDLE_PATH = process.env.SERVICE_MONITOR_CA_BUNDLE;
let customCA = null;
if (CA_BUNDLE_PATH) {
  try {
    customCA = fs.readFileSync(CA_BUNDLE_PATH);
  } catch (e) {
    console.error(`[MONITOR] Failed to read SERVICE_MONITOR_CA_BUNDLE (${CA_BUNDLE_PATH}):`, e.message);
  }
}

// ── Services to monitor ──────────────────────
const SERVICES = [
  // Production (203.0.113.1)
  { name: "Casino API", url: "https://new.acmetocasino.com/api/v2/health", type: "http", category: "production", critical: true },
  { name: "Casino Frontend", url: "https://new.acmetocasino.com", type: "http", category: "production", critical: true },
  { name: "Book Site (EN)", url: "https://thebackendofluck.com", type: "http", category: "production", critical: false },
  { name: "Book Site (PT)", url: "https://portrasdasorte.com.br", type: "http", category: "production", critical: false },
  { name: "Test Platform", url: "https://test.acmetocasino.com", type: "http", category: "production", critical: false },
  { name: "Dev Platform", url: "https://new.acmetocasino.com", type: "http", category: "production", critical: false },

  // K3s Services (internal)
  // K3s Casino Prod checked via production URL (new.acmetocasino.com) in production section
  { name: "K3s Staging", url: "http://10.0.10.211", type: "http", category: "k3s", headers: {"Host": "staging.acmetocasino.com"}, critical: false },
  { name: "K3s Preview (B/G)", url: "http://10.0.10.211", type: "http", category: "k3s", headers: {"Host": "preview.acmetocasino.com"}, critical: false },  // 502 expected when no active rollout
  { name: "ArgoCD", host: "10.0.10.210", port: 443, type: "tcp", category: "k3s", critical: false },
  { name: "Grafana", url: "http://10.0.10.211/api/health", type: "http", category: "k3s", headers: {"Host": "grafana.acmetocasino.com"}, critical: false },
  { name: "GitSearch", url: "http://10.0.10.211", type: "http", category: "k3s", headers: {"Host": "gitsearch.acmetocasino.com"}, critical: false },

  // DevSecOps Tools
  { name: "GitLab CE", url: "http://10.0.0.11:8929", type: "http", category: "devsecops", critical: true },
  { name: "DefectDojo", url: "http://10.0.10.211", type: "http", category: "devsecops", headers: {"Host": "defectdojo.acmetocasino.com"}, critical: false },
  { name: "Dashboard", url: "http://10.0.0.11:3080/api/uptime/summary", type: "http", category: "devsecops", critical: true },

  // Databases & Caches
  // PostgreSQL on 203.0.113.1 binds 127.0.0.1 — monitored via SSH in failover-collector
  // Redis on 203.0.113.1 binds 127.0.0.1 — monitored via SSH in failover-collector
  { name: "Patroni HA", host: "10.0.0.11", port: 15000, type: "tcp", category: "database", critical: false },
  { name: "Redis (dashboard)", host: "127.0.0.1", port: 6382, type: "tcp", category: "database", critical: true },

  // Infrastructure
  { name: "Nginx Proxy Manager", url: "http://10.0.10.209:81", type: "http", category: "infra", critical: true },
  { name: "pfSense API", url: "https://10.0.10.1/api/v2/status/system", type: "http", category: "infra", headers: {"X-API-Key": process.env.PFSENSE_API_KEY || ""}, skipTLS: true, critical: true },
  { name: "Wazuh Dashboard", host: "10.0.10.26", port: 443, type: "tcp", category: "infra", critical: true },
  { name: "ChromaDB (book)", host: "10.0.10.251", port: 8102, type: "tcp", category: "infra", critical: false },
  { name: "Search API (book)", host: "10.0.10.251", port: 8300, type: "tcp", category: "infra", critical: false },
  { name: "LocalStack", host: "10.0.0.11", port: 4567, type: "tcp", category: "infra", critical: false },

  // K3s Cluster Nodes
  { name: "K3s Master 001", host: "10.0.10.42", port: 6443, type: "tcp", category: "k3s-nodes", critical: true },
  { name: "K3s Master 002", host: "10.0.10.43", port: 6443, type: "tcp", category: "k3s-nodes", critical: true },
  { name: "K3s Master 003", host: "10.0.10.44", port: 6443, type: "tcp", category: "k3s-nodes", critical: true },
  { name: "K3s Worker 001", host: "10.0.10.45", port: 10250, type: "tcp", category: "k3s-nodes", critical: false },
  { name: "K3s Worker 002", host: "10.0.10.46", port: 10250, type: "tcp", category: "k3s-nodes", critical: false },
  { name: "K3s Worker 003", host: "10.0.10.47", port: 10250, type: "tcp", category: "k3s-nodes", critical: false },
];

// ── Check functions ──────────────────────────

function checkHTTP(service) {
  return new Promise((resolve) => {
    const start = Date.now();
    const urlObj = new URL(service.url);
    const mod = urlObj.protocol === "https:" ? https : http;
    const opts = {
      hostname: urlObj.hostname,
      port: urlObj.port || (urlObj.protocol === "https:" ? 443 : 80),
      path: urlObj.pathname + urlObj.search,
      method: "GET",
      timeout: 10000,
      headers: { "User-Agent": "ServiceMonitor/1.0", ...(service.headers || {}) },
    };
    if (service.skipTLS) {
      if (customCA) {
        opts.ca = customCA;
      } else {
        console.error(`[MONITOR] ${service.name}: skipTLS requested but SERVICE_MONITOR_CA_BUNDLE is not set; certificate verification stays ON`);
      }
    }

    const req = mod.request(opts, (res) => {
      const latency = Date.now() - start;
      const up = res.statusCode < 500 || (res.statusCode === 502 && service.headers && service.headers["Host"]);  // 502 through Kong = Kong is up
      res.resume();
      resolve({ up, latency, status: res.statusCode });
    });
    req.on("error", () => resolve({ up: false, latency: Date.now() - start, status: 0 }));
    req.on("timeout", () => { req.destroy(); resolve({ up: false, latency: 10000, status: 0 }); });
    req.end();
  });
}

function checkTCP(service) {
  return new Promise((resolve) => {
    const start = Date.now();
    const socket = new net.Socket();
    socket.setTimeout(5000);
    socket.on("connect", () => {
      const latency = Date.now() - start;
      socket.destroy();
      resolve({ up: true, latency, status: "open" });
    });
    socket.on("error", () => resolve({ up: false, latency: Date.now() - start, status: "closed" }));
    socket.on("timeout", () => { socket.destroy(); resolve({ up: false, latency: 5000, status: "timeout" }); });
    socket.connect(service.port, service.host);
  });
}

// ── Main loop ────────────────────────────────

async function runChecks(redis) {
  const results = [
  // DevSecOps Tools (added session 2026-04-10)
  // { name: "Nexus Repository", url: "http://10.0.10.211", type: "http", category: "devsecops", headers: {"Host": "nexus.acmetocasino.com"}, critical: false },
  { name: "Nexus NodePort", host: "10.0.10.42", port: 31229, type: "tcp", category: "devsecops", critical: true },
  { name: "GitLab Runner", host: "10.0.0.11", port: 8929, type: "tcp", category: "devsecops", critical: true },
  { name: "GitLab SSH", host: "10.0.0.11", port: 2224, type: "tcp", category: "devsecops", critical: false },

  // External Sites (HTTPS)
  // { name: "GitLab (external)", url: "https://gitlab.acmetocasino.com", type: "http", category: "production", critical: false },
  { name: "Dashboard (external)", url: "https://dashboard.acmetocasino.com", type: "http", category: "production", critical: true },
  // { name: "Preview (external)", url: "https://preview.acmetocasino.com", type: "http", category: "production", critical: false },

  // Database & Cache
  // { name: "PostgreSQL (casino001)", host: "10.0.10.24", port: 5432, type: "tcp", category: "database", critical: false },
  { name: "MinIO S3", host: "10.0.0.11", port: 9000, type: "tcp", category: "database", critical: false },

  // Monitoring
  // { name: "Prometheus (ops-host)", host: "10.0.0.11", port: 9090, type: "tcp", category: "infra", critical: false },
  // { name: "InfluxDB (ops-host)", host: "10.0.0.11", port: 8086, type: "tcp", category: "infra", critical: false },

  // K3s Services
  { name: "Kong Gateway", host: "10.0.10.211", port: 80, type: "tcp", category: "k3s", critical: true },
  { name: "MetalLB VIP", host: "10.0.10.210", port: 443, type: "tcp", category: "k3s", critical: true },
];
  const checks = SERVICES.map(async (svc) => {
    const result = svc.type === "http" ? await checkHTTP(svc) : await checkTCP(svc);
    results.push({
      name: svc.name,
      type: svc.type,
      category: svc.category,
      critical: svc.critical,
      target: svc.url || svc.host + ":" + svc.port,
      up: result.up,
      latency: result.latency,
      status: result.status,
      timestamp: new Date().toISOString(),
    });
  });

  await Promise.all(checks);

  // Store current state
  await redis.set("services:status", JSON.stringify(results), { EX: 60 });

  // Store history (1 entry per check cycle, keep 24h)
  const summary = {
    ts: Date.now(),
    total: results.length,
    up: results.filter(r => r.up).length,
    down: results.filter(r => !r.up).length,
    critical_down: results.filter(r => !r.up && r.critical).length,
    details: results.filter(r => !r.up).map(r => r.name),
  };
  await redis.zAdd("services:history", { score: Date.now(), value: JSON.stringify(summary) });
  await redis.zRemRangeByScore("services:history", 0, Date.now() - 86400000);

  // Log
  const downList = results.filter(r => !r.up);
  if (downList.length > 0) {
    console.log("[MONITOR] DOWN:", downList.map(r => r.name).join(", "));
  } else {
    console.log("[MONITOR] All", results.length, "services UP");
  }
}

async function main() {
  const redis = createClient({ url: REDIS_URL });
  redis.on("error", (err) => console.error("Redis:", err.message));
  await redis.connect();
  console.log("Service Monitor started —", SERVICES.length, "services tracked");

  const loop = async () => { try { await runChecks(redis); } catch (e) { console.error("check:", e.message); } };
  await loop();
  setInterval(loop, CHECK_INTERVAL);
}

main().catch(console.error);
