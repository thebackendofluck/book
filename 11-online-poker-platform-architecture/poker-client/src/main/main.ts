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
 * poker.exe entry point (Electron main process).
 */
import { app, BrowserWindow, ipcMain, crashReporter } from "electron";
import * as path from "path";
import * as fs from "fs";
import { connect, Connection } from "../../lib/network/connect";
import { pinCertificate } from "../../lib/security/pinning";
import { verifyAssetIntegrity } from "../../lib/graphics/render";

// Multi-entry dispatch: the same exe acts as poker / updater / crash-reporter
// depending on argv[1]. This keeps bin/ small and signable as one binary.
const mode = process.argv.find((a) => a.startsWith("--mode="))?.split("=")[1];
const noConnect = process.argv.includes("--no-connect");

if (mode === "updater" || mode === "crash-reporter") {
  if (mode === "updater") require("../updater/updater");
  else require("../crash-reporter/crash-reporter");
} else {
  app.whenReady().then(() => createWindow().catch((err) => {
    console.error("startup failed:", err);
    app.exit(1);
  }));
}

interface AppConfig {
  server: { url: string; timeoutMs: number; heartbeatMs: number };
  tls: { minVersion: "TLSv1.3"; pinnedSha256: string; pinSource?: string };
  updater: { feedUrl: string; checkIntervalMs: number; publicKeyPath?: string };
  crashReporter: { submitUrl: string; uploadByDefault: boolean };
  ui: { seats: number; theme: string };
}

/** Minimal subset of the server protocol opcodes (see chapter-11/implementation/websocket-server/protocol.js). */
const OP = {
  AUTH_REQUEST:      0x01,
  AUTH_RESPONSE:     0x02,
  HEARTBEAT_PING:    0x04,
  HEARTBEAT_PONG:    0x05,
  TABLE_JOIN:        0x12,
  TABLE_LEAVE:       0x13,
  DEAL_CARDS:        0x21,
  ACTION_RESPONSE:   0x23,
  ERROR:             0xFF,
} as const;
const PROTOCOL_VERSION = "2.1.0";
let seqCounter = 0;

function envelope(op: number, data: unknown): Record<string, unknown> {
  return {
    v: PROTOCOL_VERSION,
    op,
    seq: ++seqCounter,
    ts: Date.now(),
    d: data,
  };
}

function projectRoot(): string {
  let dir = __dirname;
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(dir, "package.json"))) return dir;
    dir = path.dirname(dir);
  }
  return app.getAppPath();
}

function loadConfig(): AppConfig {
  const configPath = path.join(projectRoot(), "data", "config", "default.json");
  const raw = fs.readFileSync(configPath, "utf-8");
  return JSON.parse(raw) as AppConfig;
}

async function createWindow(): Promise<void> {
  const cfg = loadConfig();
  const root = projectRoot();

  // Integrity-defence: verify shipped assets haven't been tampered with.
  const assetCheck = verifyAssetIntegrity(path.join(root, "data", "assets"));
  if (!assetCheck.ok) {
    console.warn(
      "[poker] asset integrity check FAILED",
      JSON.stringify(assetCheck),
    );
  } else {
    console.log("[poker] asset integrity OK");
  }

  crashReporter.start({
    productName: "poker-client",
    submitURL: cfg.crashReporter.submitUrl,
    uploadToServer: cfg.crashReporter.uploadByDefault,
    compress: true,
  });

  try {
    const host = new URL(cfg.server.url).hostname;
    pinCertificate(host, cfg.tls.pinnedSha256);
  } catch (err) {
    console.error("pin registration failed:", err);
  }

  const win = new BrowserWindow({
    width: 1024,
    height: 720,
    title: "Poker Client",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const indexPath = path.join(root, "src", "renderer", "index.html");
  await win.loadFile(indexPath);

  ipcMain.handle("get-config", () => cfg);
  ipcMain.handle("poker:connect", async (_evt, url: string) => {
    try {
      const conn = await connect(url, {
        pinnedSha256: cfg.tls.pinnedSha256,
        minTlsVersion: "TLSv1.3",
        timeoutMs: cfg.server.timeoutMs,
      });
      conn.close();
      return { ok: true };
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
  });

  // --- Smoke / headless modes ---
  if (mode === "smoke" || process.env.POKER_SMOKE === "1") {
    const logPath = process.env.POKER_SMOKE_LOG;
    const log = (line: string) => {
      console.log(line);
      if (logPath) {
        try { fs.appendFileSync(logPath, line + "\n"); } catch { /* best-effort */ }
      }
    };
    log("SMOKE_MAIN_WINDOW_READY");

    if (noConnect) {
      log("POKER_SMOKE_OK no-connect mode");
      setTimeout(() => app.quit(), 500);
      return;
    }

    // Full protocol walk: auth → join → deal → fold → leave.
    runProtocolSmoke(cfg, log).finally(() => setTimeout(() => app.quit(), 250));
  }
}

async function runProtocolSmoke(cfg: AppConfig, log: (s: string) => void): Promise<void> {
  let conn: Connection | undefined;
  const events: string[] = [];
  try {
    conn = await connect(cfg.server.url, {
      pinnedSha256: /^wss:/.test(cfg.server.url) ? cfg.tls.pinnedSha256 : undefined,
      minTlsVersion: cfg.tls.minVersion,
      timeoutMs: cfg.server.timeoutMs,
    });
    log("SMOKE_CONNECTED");

    conn.socket.on("message", (raw: Buffer) => {
      try {
        const msg = JSON.parse(raw.toString()) as { op: number };
        events.push(`op=${msg.op}`);
        if (msg.op === OP.AUTH_RESPONSE) log("SMOKE_AUTH_OK");
        if (msg.op === OP.DEAL_CARDS)    log("SMOKE_DEAL");
        if (msg.op === OP.ERROR)         log(`SMOKE_ERROR ${raw.toString().slice(0,120)}`);
      } catch {/* ignore */}
    });

    // Build a minimal JWT body — the server parses the payload without verification.
    const jwtBody = Buffer.from(JSON.stringify({ sub: "smoke-player" })).toString("base64");
    conn.send(envelope(OP.AUTH_REQUEST, {
      token: `hdr.${jwtBody}.sig`,
      device_fingerprint: "smoke-device",
      client_version: "0.1.0",
    }));
    await delay(300);
    conn.send(envelope(OP.TABLE_JOIN, { table_id: "smoke-table", buy_in: 100 }));
    await delay(200);
    conn.send(envelope(OP.ACTION_RESPONSE, {
      table_id: "smoke-table",
      hand_id: "h1",
      player_id: "smoke-player",
      action: "fold",
      amount: 0,
      client_timestamp: Date.now(),
    }));
    await delay(200);
    conn.send(envelope(OP.TABLE_LEAVE, { table_id: "smoke-table" }));
    await delay(150);

    log(`SMOKE_EVENTS ${events.join(",")}`);
    log("POKER_SMOKE_OK");
  } catch (err) {
    log(`SMOKE_FAIL ${(err as Error).message}`);
  } finally {
    try { conn?.close(); } catch {/* */}
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
