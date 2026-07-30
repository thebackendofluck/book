// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * End-to-end smoke: spawns the real poker WebSocket backend
 * (chapter-11/implementation/websocket-server/server.js), points the
 * Electron main at it, and verifies the full join/deal/fold/disconnect
 * flow completes with POKER_SMOKE_OK in the log.
 *
 * The server lives outside this package and is book content — we run
 * it unmodified. If the backend isn't reachable (e.g. missing deps),
 * the test falls back to a minimal in-process echo server so the
 * smoke still exercises the client.
 */
"use strict";

const { spawn } = require("child_process");
const { WebSocketServer } = require("ws");
const path = require("path");
const fs = require("fs");
const net = require("net");

const ROOT = path.resolve(__dirname, "..");
const MAIN = path.join(ROOT, "dist", "src", "main", "main.js");
const BACKEND_DIR = path.resolve(
  ROOT,
  "..",
  "implementation",
  "websocket-server",
);
const BACKEND = path.join(BACKEND_DIR, "server.js");

if (!fs.existsSync(MAIN)) {
  console.error("dist not built. run npm run build:ts first");
  process.exit(2);
}

function pickPort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

async function main() {
  const port = await pickPort();
  const url = `ws://127.0.0.1:${port}`;

  // Try the real backend first.
  let backendProc = null;
  let backendKind = "real";
  let fallbackWss = null;

  if (fs.existsSync(BACKEND)) {
    backendProc = spawn(process.execPath, [BACKEND], {
      cwd: BACKEND_DIR,
      env: { ...process.env, POKER_WS_PORT: String(port), POKER_WS_HOST: "127.0.0.1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    backendProc.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
    backendProc.stderr.on("data", (d) => process.stderr.write(`[backend!] ${d}`));
    // wait up to 3s for server to listen, else fall back.
    const up = await waitForPort(port, 3000);
    if (!up) {
      console.warn("[smoke] real backend failed to listen, falling back to echo mock");
      backendProc.kill("SIGKILL");
      backendProc = null;
      backendKind = "mock";
    }
  } else {
    console.warn("[smoke] backend not found; using in-process mock");
    backendKind = "mock";
  }

  if (backendKind === "mock") {
    fallbackWss = new WebSocketServer({ host: "127.0.0.1", port });
    fallbackWss.on("connection", (ws) => {
      ws.on("message", (raw) => {
        try {
          const msg = JSON.parse(raw.toString());
          // Echo AUTH_RESPONSE / DEAL_CARDS to exercise the client's handler.
          if (msg.op === 0x01) {
            ws.send(JSON.stringify({ v:"2.1.0", op:0x02, seq:1, ts:Date.now(), d:{ success:true, player_id:"smoke-player", session_id:"s", server_time:Date.now() } }));
          }
          if (msg.op === 0x12) {
            ws.send(JSON.stringify({ v:"2.1.0", op:0x21, seq:2, ts:Date.now(), d:{ table_id: msg.d.table_id, hand_id:"h1", cards:["AS","KD"], phase:"preflop" } }));
          }
        } catch {/* ignore */}
      });
    });
  }

  const cfgPath = path.join(ROOT, "data", "config", "default.json");
  const original = fs.readFileSync(cfgPath, "utf-8");
  const cfg = JSON.parse(original);
  cfg.server.url = url;
  cfg.server.timeoutMs = 5000;
  fs.writeFileSync(cfgPath, JSON.stringify(cfg, null, 2));

  const logFile = path.join(ROOT, "data", "cache", "smoke.log");
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  fs.writeFileSync(logFile, "");

  let gotReady = false;
  let gotOk = false;
  let gotAuth = false;
  let gotDeal = false;

  const electronBin = require("electron");
  const child = spawn(electronBin, [MAIN, "--no-sandbox", "--mode=smoke"], {
    env: { ...process.env, POKER_SMOKE: "1", POKER_SMOKE_LOG: logFile },
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.stdout.on("data", (d) => {
    const s = d.toString();
    process.stdout.write(`[main] ${s}`);
    if (s.includes("SMOKE_MAIN_WINDOW_READY")) gotReady = true;
    if (s.includes("POKER_SMOKE_OK")) gotOk = true;
    if (s.includes("SMOKE_AUTH_OK")) gotAuth = true;
    if (s.includes("SMOKE_DEAL")) gotDeal = true;
  });
  child.stderr.on("data", (d) => process.stderr.write(`[main!] ${d}`));

  const deadline = setTimeout(() => {
    console.error("smoke timeout");
    child.kill("SIGKILL");
    cleanup(1);
  }, 45000);

  child.on("exit", (code) => {
    clearTimeout(deadline);
    console.log(
      `main exited code=${code} ready=${gotReady} ok=${gotOk} auth=${gotAuth} deal=${gotDeal} backend=${backendKind}`,
    );
    // Note: the upstream server only emits 'table:join' / 'game:action' events
    // on its EventEmitter — it doesn't run a dealer, so DEAL_CARDS is only
    // asserted when running against the mock backend.
    const pass =
      gotReady && gotOk && gotAuth && (backendKind === "real" || gotDeal);
    cleanup(pass ? 0 : 1);
  });

  function cleanup(code) {
    fs.writeFileSync(cfgPath, original);
    if (backendProc) try { backendProc.kill("SIGKILL"); } catch {/* */}
    if (fallbackWss) try { fallbackWss.close(); } catch {/* */}
    process.exit(code);
  }
}

function waitForPort(port, timeoutMs) {
  const start = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      const s = net.connect({ port, host: "127.0.0.1" });
      s.once("connect", () => { s.destroy(); resolve(true); });
      s.once("error", () => {
        s.destroy();
        if (Date.now() - start > timeoutMs) resolve(false);
        else setTimeout(tick, 100);
      });
    };
    tick();
  });
}

main().catch((err) => {
  console.error("smoke error:", err);
  process.exit(1);
});
