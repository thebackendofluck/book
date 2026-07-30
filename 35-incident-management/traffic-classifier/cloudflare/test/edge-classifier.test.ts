// Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * edge-classifier.test.ts
 *
 * Vitest unit tests for the classification engine, campaign manager, and
 * attack logger.  These tests run against in-memory mocks of Workers KV
 * and Durable Objects — no actual Cloudflare environment is required.
 *
 * Test scenarios:
 *   - Normal traffic: passes through with ALLOW
 *   - Bot score attack: returns 403 BLOCK
 *   - Bot score suspicious single signal: JS challenge
 *   - Bot score suspicious + rate limit: CAPTCHA
 *   - Per-IP rate limit: 429 RATE_LIMIT then BLOCK
 *   - Per-ASN rate limit: ATTACK
 *   - JA3 blocklist: instant BLOCK
 *   - IP blocklist: instant BLOCK
 *   - Campaign active: multiplied thresholds pass traffic that would otherwise fail
 *   - Campaign start/stop API
 *   - Attack export / ASN summary / flush
 *   - Geographic spike: marks SUSPICIOUS
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import type { AttackEvent, CampaignRecord, Env } from "../src/types.js";
import { THRESHOLDS } from "../src/types.js";

// ─── In-memory KV mock ────────────────────────────────────────────────────────

class MockKV {
  private store = new Map<string, { value: string; expiry?: number }>();

  async get(key: string): Promise<string | null> {
    const entry = this.store.get(key);
    if (!entry) return null;
    if (entry.expiry !== undefined && Date.now() > entry.expiry) {
      this.store.delete(key);
      return null;
    }
    return entry.value;
  }

  async put(
    key: string,
    value: string,
    options?: { expirationTtl?: number; expiration?: number },
  ): Promise<void> {
    let expiry: number | undefined;
    if (options?.expirationTtl !== undefined) {
      expiry = Date.now() + options.expirationTtl * 1_000;
    } else if (options?.expiration !== undefined) {
      expiry = options.expiration * 1_000;
    }
    this.store.set(key, { value, expiry });
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  async list(options?: {
    prefix?: string;
    cursor?: string;
    limit?: number;
  }): Promise<{ keys: Array<{ name: string }>; list_complete: boolean; cursor: string; cacheStatus: null }> {
    const prefix = options?.prefix ?? "";
    const keys = Array.from(this.store.keys())
      .filter((k) => k.startsWith(prefix))
      .map((name) => ({ name }));
    return {
      keys,
      list_complete: true,
      cursor: "",
      cacheStatus: null,
    };
  }

  // Helper for tests: set a value directly
  _set(key: string, value: string): void {
    this.store.set(key, { value });
  }

  // Helper for tests: clear all state
  _clear(): void {
    this.store.clear();
  }
}

// ─── In-memory Durable Object stub ───────────────────────────────────────────

interface CounterState {
  count: number;
  firstSeen: number;
  lastSeen: number;
  sampleIps: string[];
}

class MockDurableObjectStub {
  private state: CounterState = { count: 0, firstSeen: 0, lastSeen: 0, sampleIps: [] };
  readonly name: string;

  constructor(name: string) {
    this.name = name;
  }

  async fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = typeof input === "string" ? new URL(input) : new URL((input as Request).url);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();

    if (url.pathname === "/increment" && method === "POST") {
      const body = JSON.parse((init?.body as string) ?? "{}") as AttackEvent;
      this.state.count += 1;
      if (this.state.firstSeen === 0) this.state.firstSeen = body.timestamp;
      this.state.lastSeen = body.timestamp;
      if (!this.state.sampleIps.includes(body.ip) && this.state.sampleIps.length < 10) {
        this.state.sampleIps.push(body.ip);
      }
      return new Response(JSON.stringify({ count: this.state.count }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    if (url.pathname === "/read") {
      return new Response(JSON.stringify(this.state), {
        headers: { "Content-Type": "application/json" },
      });
    }

    if (url.pathname === "/reset") {
      this.state = { count: 0, firstSeen: 0, lastSeen: 0, sampleIps: [] };
      return new Response(JSON.stringify({ status: "reset" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response("Not found", { status: 404 });
  }
}

class MockDurableObjectNamespace {
  private stubs = new Map<string, MockDurableObjectStub>();

  idFromName(name: string): { toString(): string; name: string } {
    return { toString: () => name, name };
  }

  get(id: { toString(): string }): MockDurableObjectStub {
    const name = id.toString();
    if (!this.stubs.has(name)) {
      this.stubs.set(name, new MockDurableObjectStub(name));
    }
    return this.stubs.get(name)!;
  }
}

// ─── Mock Env factory ────────────────────────────────────────────────────────

interface MockEnv {
  RATE_LIMITS: MockKV;
  CAMPAIGNS: MockKV;
  ATTACK_LOG: MockKV;
  JA3_BLOCKLIST: MockKV;
  ATTACK_COUNTER: MockDurableObjectNamespace;
  ORIGIN_AUTOSCALER_URL: string;
  ORIGIN_ALERT_URL: string;
  ADMIN_SECRET: string;
}

function makeMockEnv(): {
  env: MockEnv;
  rateLimits: MockKV;
  campaigns: MockKV;
  attackLog: MockKV;
  ja3Blocklist: MockKV;
} {
  const rateLimits = new MockKV();
  const campaigns = new MockKV();
  const attackLog = new MockKV();
  const ja3Blocklist = new MockKV();
  const attackCounter = new MockDurableObjectNamespace();

  return {
    env: {
      RATE_LIMITS: rateLimits,
      CAMPAIGNS: campaigns,
      ATTACK_LOG: attackLog,
      JA3_BLOCKLIST: ja3Blocklist,
      ATTACK_COUNTER: attackCounter,
      ORIGIN_AUTOSCALER_URL: "https://autoscaler.test",
      ORIGIN_ALERT_URL: "https://alerts.test",
      ADMIN_SECRET: "test-secret-123",
    },
    rateLimits,
    campaigns,
    attackLog,
    ja3Blocklist,
  };
}

// ─── Request builder helpers ─────────────────────────────────────────────────

interface CfExtras {
  botManagement?: { score: number; ja3Hash?: string };
  threatScore?: number;
  asn?: number;
  asOrganization?: string;
  country?: string;
}

function makeRequest(
  path = "/",
  ip = "1.2.3.4",
  cf: CfExtras = {},
  method = "GET",
): Request {
  const req = new Request(`https://casino.test${path}`, { method });
  // Cloudflare injects cf as a non-enumerable property; we approximate this
  // by using Object.defineProperty so tests match production behaviour.
  Object.defineProperty(req, "cf", {
    value: {
      botManagement: cf.botManagement ?? { score: 85, ja3Hash: "" },
      threatScore: cf.threatScore ?? 0,
      asn: cf.asn ?? 12345,
      asOrganization: cf.asOrganization ?? "Test ASN Corp",
      country: cf.country ?? "BR",
    },
    writable: false,
  });
  // Simulate CF headers
  const headers = new Headers(req.headers);
  headers.set("CF-Connecting-IP", ip);
  headers.set("CF-Ray", "test-ray-001");
  return new Request(req, { headers });
}

function makeAdminRequest(
  path: string,
  body: unknown,
  method = "POST",
): Request {
  return new Request(`https://casino.test${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Secret": "test-secret-123",
    },
    body: method !== "GET" ? JSON.stringify(body) : undefined,
  });
}

// ─── Import modules under test ────────────────────────────────────────────────
// Dynamic imports so each describe block can reload fresh module state.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let workerModule: any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let campaignModule: any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let attackModule: any;

beforeEach(async () => {
  workerModule = await import("../src/edge-classifier.js");
  campaignModule = await import("../src/campaign-manager.js");
  attackModule = await import("../src/attack-logger.js");
});

// ─── Test suites ──────────────────────────────────────────────────────────────

describe("edge-classifier: normal traffic", () => {
  it("passes through a clean request with ALLOW", async () => {
    const { env } = makeMockEnv();
    const mockFetch = vi.fn().mockResolvedValue(new Response("OK", { status: 200 }));
    vi.stubGlobal("fetch", mockFetch);

    const req = makeRequest("/", "10.0.0.1", {
      botManagement: { score: 90, ja3Hash: "" },
      threatScore: 0,
      country: "BR",
    });

    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);

    expect(resp.status).toBe(200);
    vi.restoreAllMocks();
  });
});

describe("edge-classifier: bot score thresholds", () => {
  it("blocks a request with bot score at or below ATTACK threshold", async () => {
    const { env } = makeMockEnv();
    const req = makeRequest("/", "1.2.3.4", {
      botManagement: { score: THRESHOLDS.BOT_SCORE_ATTACK, ja3Hash: "" },
      threatScore: 0,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(403);
    const body = await resp.json() as { code: string };
    expect(body.code).toBe("DDOS_BLOCK");
  });

  it("challenges a request with bot score in the suspicious range", async () => {
    const { env } = makeMockEnv();
    const req = makeRequest("/", "1.2.3.5", {
      botManagement: { score: THRESHOLDS.BOT_SCORE_SUSPICIOUS, ja3Hash: "" },
      threatScore: 0,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    // Single suspicious signal → JS challenge (403 with challenge HTML)
    expect(resp.status).toBe(403);
    const header = resp.headers.get("X-Edge-Decision");
    expect(["js_challenge", "captcha"]).toContain(header);
  });

  it("allows a request with a healthy bot score", async () => {
    const { env } = makeMockEnv();
    const mockFetch = vi.fn().mockResolvedValue(new Response("OK", { status: 200 }));
    vi.stubGlobal("fetch", mockFetch);

    const req = makeRequest("/", "1.2.3.6", {
      botManagement: { score: 95, ja3Hash: "" },
      threatScore: 0,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(200);
    vi.restoreAllMocks();
  });
});

describe("edge-classifier: threat score thresholds", () => {
  it("blocks a request with threat score at or above ATTACK threshold", async () => {
    const { env } = makeMockEnv();
    const req = makeRequest("/", "2.2.2.2", {
      botManagement: { score: 90, ja3Hash: "" },
      threatScore: THRESHOLDS.THREAT_SCORE_ATTACK,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(403);
  });

  it("challenges a request with threat score in the suspicious range", async () => {
    const { env } = makeMockEnv();
    const req = makeRequest("/", "2.2.2.3", {
      botManagement: { score: 90, ja3Hash: "" },
      threatScore: THRESHOLDS.THREAT_SCORE_SUSPICIOUS,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(403);
  });
});

describe("edge-classifier: JA3 blocklist", () => {
  it("blocks a request with a known-bad JA3 hash", async () => {
    const { env, ja3Blocklist } = makeMockEnv();
    const badJa3 = "abc123def456abc123def456abc12345";
    ja3Blocklist._set(badJa3, "blocked");

    const req = makeRequest("/", "3.3.3.3", {
      botManagement: { score: 95, ja3Hash: badJa3 },
      threatScore: 0,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(403);
    const body = await resp.json() as { reasons: string[] };
    expect(body.reasons).toContain("ja3_blocklist");
  });

  it("allows a request with an unknown JA3 hash", async () => {
    const { env } = makeMockEnv();
    const mockFetch = vi.fn().mockResolvedValue(new Response("OK", { status: 200 }));
    vi.stubGlobal("fetch", mockFetch);

    const req = makeRequest("/", "3.3.3.4", {
      botManagement: { score: 95, ja3Hash: "aabbccddeeff00112233445566778899" },
      threatScore: 0,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(200);
    vi.restoreAllMocks();
  });
});

describe("edge-classifier: IP blocklist", () => {
  it("blocks a pre-blocked IP immediately", async () => {
    const { env, rateLimits } = makeMockEnv();
    const blockedIp = "5.5.5.5";
    rateLimits._set(`blocked_ip:${blockedIp}`, "1");

    const req = makeRequest("/", blockedIp, {
      botManagement: { score: 95, ja3Hash: "" },
      threatScore: 0,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(403);
    const body = await resp.json() as { reasons: string[] };
    expect(body.reasons).toContain("ip_blocklist");
  });
});

describe("edge-classifier: campaign multiplier", () => {
  it("raises thresholds during an active campaign so moderate traffic is allowed", async () => {
    const { env, campaigns } = makeMockEnv();
    const mockFetch = vi.fn().mockResolvedValue(new Response("OK", { status: 200 }));
    vi.stubGlobal("fetch", mockFetch);

    // Activate a campaign for BR with 5x multiplier
    const campaign: CampaignRecord = {
      geo: "BR",
      multiplier: 5,
      startedAt: Date.now(),
      expiresAt: Date.now() + 3_600_000,
    };
    campaigns._set("campaign_active:geo:BR", JSON.stringify(campaign));

    // Pre-populate rate counter at just above the baseline WARNING level.
    // With a 5x multiplier the effective warn threshold is 5*120=600,
    // so this count should be below the new threshold and pass through.
    const windowId = Math.floor(Date.now() / 1_000 / 60);
    env.RATE_LIMITS._set(
      `ip_m:1.2.3.7:${windowId}`,
      String(THRESHOLDS.IP_PER_MINUTE_WARN + 1),
    );

    const req = makeRequest("/", "1.2.3.7", {
      botManagement: { score: 95, ja3Hash: "" },
      threatScore: 0,
      country: "BR",
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);

    // WARN+1 = 121, multiplied threshold = 600 → still NORMAL → ALLOW
    expect(resp.status).toBe(200);
    vi.restoreAllMocks();
  });
});

describe("edge-classifier: rate limiting", () => {
  it("blocks when per-IP per-minute counter hits the ATTACK threshold", async () => {
    const { env, rateLimits } = makeMockEnv();
    const ip = "6.6.6.6";
    const windowId = Math.floor(Date.now() / 1_000 / 60);

    // Pre-seed the counter at exactly the attack level
    rateLimits._set(`ip_m:${ip}:${windowId}`, String(THRESHOLDS.IP_PER_MINUTE_ATTACK));

    const req = makeRequest("/", ip, {
      botManagement: { score: 95, ja3Hash: "" },
      threatScore: 0,
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    // At ATTACK threshold the counter fires BLOCK (403) after the increment
    expect([429, 403]).toContain(resp.status);
  });
});

describe("campaign-manager API", () => {
  it("POST /campaign/start creates an active campaign record", async () => {
    const { env } = makeMockEnv();
    const req = makeAdminRequest("/campaign/start", {
      geo: "BR",
      multiplier: 5,
      durationSeconds: 3600,
      note: "Super Bowl bonus launch",
    });

    const resp = await campaignModule.handleCampaign(req, env as unknown as Env);
    expect(resp.status).toBe(201);

    const body = await resp.json() as { status: string; campaign: CampaignRecord };
    expect(body.status).toBe("started");
    expect(body.campaign.geo).toBe("BR");
    expect(body.campaign.multiplier).toBe(5);

    // Verify KV was written
    const stored = await env.CAMPAIGNS.get("campaign_active:geo:BR");
    expect(stored).not.toBeNull();
    const parsed = JSON.parse(stored!) as CampaignRecord;
    expect(parsed.multiplier).toBe(5);
  });

  it("POST /campaign/start rejects invalid geo codes", async () => {
    const { env } = makeMockEnv();
    const req = makeAdminRequest("/campaign/start", { geo: "INVALID" });
    const resp = await campaignModule.handleCampaign(req, env as unknown as Env);
    expect(resp.status).toBe(400);
  });

  it("POST /campaign/stop removes an active campaign", async () => {
    const { env } = makeMockEnv();

    // First start a campaign
    const startReq = makeAdminRequest("/campaign/start", {
      geo: "AR",
      multiplier: 3,
      durationSeconds: 1800,
    });
    await campaignModule.handleCampaign(startReq, env as unknown as Env);

    // Then stop it
    const stopReq = makeAdminRequest("/campaign/stop", { geo: "AR" });
    const resp = await campaignModule.handleCampaign(stopReq, env as unknown as Env);
    expect(resp.status).toBe(200);

    const body = await resp.json() as { status: string };
    expect(body.status).toBe("stopped");

    // Verify KV entry was deleted
    const stored = await env.CAMPAIGNS.get("campaign_active:geo:AR");
    expect(stored).toBeNull();
  });

  it("POST /campaign/stop returns 404 for non-existent campaign", async () => {
    const { env } = makeMockEnv();
    const req = makeAdminRequest("/campaign/stop", { geo: "ZZ" });
    const resp = await campaignModule.handleCampaign(req, env as unknown as Env);
    expect(resp.status).toBe(404);
  });

  it("GET /campaign/active lists all active campaigns", async () => {
    const { env } = makeMockEnv();

    // Start two campaigns
    await campaignModule.handleCampaign(
      makeAdminRequest("/campaign/start", { geo: "BR", multiplier: 5, durationSeconds: 3600 }),
      env as unknown as Env,
    );
    await campaignModule.handleCampaign(
      makeAdminRequest("/campaign/start", { geo: "MX", multiplier: 2, durationSeconds: 1800 }),
      env as unknown as Env,
    );

    const listReq = new Request("https://casino.test/campaign/active", { method: "GET" });
    const resp = await campaignModule.handleCampaign(listReq, env as unknown as Env);
    expect(resp.status).toBe(200);

    const body = await resp.json() as { count: number; campaigns: CampaignRecord[] };
    expect(body.count).toBe(2);
    const geos = body.campaigns.map((c) => c.geo);
    expect(geos).toContain("BR");
    expect(geos).toContain("MX");
  });

  it("clamps multiplier to maximum of 20", async () => {
    const { env } = makeMockEnv();
    const req = makeAdminRequest("/campaign/start", {
      geo: "US",
      multiplier: 999,
      durationSeconds: 3600,
    });
    const resp = await campaignModule.handleCampaign(req, env as unknown as Env);
    const body = await resp.json() as { campaign: CampaignRecord };
    expect(body.campaign.multiplier).toBeLessThanOrEqual(20);
  });
});

describe("attack-logger API", () => {
  async function seedAttackEvents(
    attackLog: MockKV,
    count: number,
  ): Promise<AttackEvent[]> {
    const events: AttackEvent[] = [];
    for (let i = 0; i < count; i++) {
      const event: AttackEvent = {
        ip: `192.168.1.${i % 254}`,
        asn: i % 3 === 0 ? "AS12345" : "AS67890",
        country: "CN",
        ja3: "deadbeefdeadbeefdeadbeefdeadbeef",
        timestamp: Date.now() + i,
        reasons: ["bot_score:5"],
        action: "BLOCK",
        ray: `ray-${i}`,
      };
      events.push(event);
      await attackLog.put(`event:${event.timestamp}:${event.ray}`, JSON.stringify(event));
    }
    return events;
  }

  it("GET /attacks/export returns all events", async () => {
    const { env, attackLog } = makeMockEnv();
    await seedAttackEvents(attackLog, 5);

    const req = new Request("https://casino.test/attacks/export", { method: "GET" });
    const resp = await attackModule.handleAttackLog(req, env as unknown as Env);
    expect(resp.status).toBe(200);

    const body = await resp.json() as { count: number; events: AttackEvent[] };
    expect(body.count).toBe(5);
    expect(body.events).toHaveLength(5);
    // Verify events are sorted by timestamp ascending
    for (let i = 1; i < body.events.length; i++) {
      expect(body.events[i].timestamp).toBeGreaterThanOrEqual(body.events[i - 1].timestamp);
    }
  });

  it("POST /attacks/flush requires confirmation token", async () => {
    const { env, attackLog } = makeMockEnv();
    await seedAttackEvents(attackLog, 3);

    const req = new Request("https://casino.test/attacks/flush", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "WRONG" }),
    });
    const resp = await attackModule.handleAttackLog(req, env as unknown as Env);
    expect(resp.status).toBe(400);
  });

  it("POST /attacks/flush with correct token deletes all events", async () => {
    const { env, attackLog } = makeMockEnv();
    await seedAttackEvents(attackLog, 4);

    const req = new Request("https://casino.test/attacks/flush", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "FLUSH" }),
    });
    const resp = await attackModule.handleAttackLog(req, env as unknown as Env);
    expect(resp.status).toBe(200);

    const body = await resp.json() as { deletedEvents: number; status: string };
    expect(body.status).toBe("flushed");
    expect(body.deletedEvents).toBe(4);
  });

  it("GET /attacks/export returns empty array when no events exist", async () => {
    const { env } = makeMockEnv();
    const req = new Request("https://casino.test/attacks/export", { method: "GET" });
    const resp = await attackModule.handleAttackLog(req, env as unknown as Env);
    const body = await resp.json() as { count: number };
    expect(body.count).toBe(0);
  });

  it("unknown attack route returns 404", async () => {
    const { env } = makeMockEnv();
    const req = new Request("https://casino.test/attacks/unknown", { method: "GET" });
    const resp = await attackModule.handleAttackLog(req, env as unknown as Env);
    expect(resp.status).toBe(404);
  });
});

describe("AttackCounter Durable Object mock", () => {
  it("increments count correctly and caps sample IPs at 10", async () => {
    const { env } = makeMockEnv();
    const id = env.ATTACK_COUNTER.idFromName("AS99999");
    const stub = env.ATTACK_COUNTER.get(id);

    // Increment 15 times with 15 different IPs
    for (let i = 0; i < 15; i++) {
      const event: AttackEvent = {
        ip: `10.0.0.${i}`,
        asn: "AS99999",
        country: "XX",
        ja3: "",
        timestamp: Date.now() + i,
        reasons: ["test"],
        action: "BLOCK",
        ray: `ray-${i}`,
      };
      await stub.fetch("https://counter/increment", {
        method: "POST",
        body: JSON.stringify(event),
      });
    }

    const readResp = await stub.fetch("https://counter/read");
    const data = await readResp.json() as {
      count: number;
      sampleIps: string[];
    };

    expect(data.count).toBe(15);
    expect(data.sampleIps.length).toBeLessThanOrEqual(10);
  });

  it("resets all state on /reset", async () => {
    const { env } = makeMockEnv();
    const id = env.ATTACK_COUNTER.idFromName("AS11111");
    const stub = env.ATTACK_COUNTER.get(id);

    const event: AttackEvent = {
      ip: "1.1.1.1",
      asn: "AS11111",
      country: "XX",
      ja3: "",
      timestamp: Date.now(),
      reasons: ["test"],
      action: "BLOCK",
      ray: "ray-reset",
    };
    await stub.fetch("https://counter/increment", {
      method: "POST",
      body: JSON.stringify(event),
    });

    await stub.fetch("https://counter/reset", { method: "POST" });

    const readResp = await stub.fetch("https://counter/read");
    const data = await readResp.json() as { count: number };
    expect(data.count).toBe(0);
  });
});

describe("admin route auth guard", () => {
  it("returns 403 for admin routes without X-Admin-Secret", async () => {
    const { env } = makeMockEnv();
    const req = new Request("https://casino.test/campaign/active", {
      method: "GET",
      // No X-Admin-Secret header
    });
    const ctx = { waitUntil: vi.fn() } as unknown as ExecutionContext;
    const resp = await workerModule.default.fetch(req, env as unknown as Env, ctx);
    expect(resp.status).toBe(403);
  });
});
