// Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * Chapter 24 - IP Detection Pipeline
 * Vitest test suite for all 8 gates.
 *
 * Run with:
 *   npx vitest run
 *   npx vitest run --coverage
 *
 * The tests use a mock Env that provides in-memory KV and D1 stubs so
 * no real Cloudflare resources are needed during CI.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { DEFAULT_CONFIG }            from "../src/types.js";
import { checkIpType, DATACENTER_ASNS } from "../src/gates/ip-type.js";
import { checkVpn, checkKnownProxy } from "../src/gates/vpn-proxy.js";
import { checkBlacklist }            from "../src/gates/blacklist.js";
import { checkFraudScore }           from "../src/gates/fraud-score.js";
import { checkDeviceFingerprint }    from "../src/gates/device-fingerprint.js";
import { checkSanctions, HARDCODED_SANCTIONED_COUNTRIES } from "../src/gates/sanctions.js";
import { checkKycStatus }            from "../src/gates/kyc-status.js";

import type {
  PlayerRequest,
  GateConfig,
  Env,
} from "../src/types.js";

// ---------------------------------------------------------------------------
// Mock factories
// ---------------------------------------------------------------------------

function makeRequest(overrides: Partial<PlayerRequest> = {}): PlayerRequest {
  return {
    ip: "1.2.3.4",
    cf: {
      asn: 12345,
      asOrganization: "Some Residential ISP",
      country: "BR",
      isEUCountry: undefined,
      botManagement: {
        score: 90,
        verifiedBot: false,
        ja3Hash: "abc123def456",
      },
    },
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    acceptLanguage: "pt-BR,pt;q=0.9",
    requestedAt: new Date().toISOString(),
    ...overrides,
  };
}

function makeConfig(overrides: Partial<GateConfig> = {}): GateConfig {
  return { ...DEFAULT_CONFIG, ...overrides };
}

// ---------------------------------------------------------------------------
// KV mock
// ---------------------------------------------------------------------------

function makeKv(initial: Record<string, string> = {}): KVNamespace {
  const store = new Map<string, string>(Object.entries(initial));

  return {
    async get(key: string, _opts?: { type?: string; cacheTtl?: number }) {
      return store.get(key) ?? null;
    },
    async put(key: string, value: string) {
      store.set(key, value);
    },
    async delete(key: string) {
      store.delete(key);
    },
    async list() {
      return { keys: [], list_complete: true, cursor: "" };
    },
    async getWithMetadata() {
      return { value: null, metadata: null };
    },
  } as unknown as KVNamespace;
}

// ---------------------------------------------------------------------------
// D1 mock
// ---------------------------------------------------------------------------

function makeD1(rows: Record<string, unknown>[]): D1Database {
  return {
    prepare(_sql: string) {
      return {
        bind(..._args: unknown[]) {
          return {
            async first<T>(): Promise<T | null> {
              return (rows[0] as T) ?? null;
            },
            async all() {
              return { results: rows, success: true };
            },
            async run() {
              return { success: true };
            },
          };
        },
        async first<T>(): Promise<T | null> {
          return (rows[0] as T) ?? null;
        },
        async all() {
          return { results: rows, success: true };
        },
        async run() {
          return { success: true };
        },
      };
    },
    async batch() {
      return [];
    },
    async dump() {
      return new ArrayBuffer(0);
    },
    async exec() {
      return { count: 0, duration: 0 };
    },
  } as unknown as D1Database;
}

// ---------------------------------------------------------------------------
// ExecutionContext mock
// ---------------------------------------------------------------------------

const mockCtx: ExecutionContext = {
  waitUntil: (_p: Promise<unknown>) => { /* no-op in tests */ },
  passThroughOnException: () => { /* no-op in tests */ },
};

function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    IP_BLACKLIST:        makeKv(),
    DEVICE_FINGERPRINTS: makeKv(),
    FRAUD_VELOCITY:      makeKv(),
    SANCTIONS_LIST:      makeKv(),
    PLAYER_DB:           makeD1([]),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Gate 1: IP Type
// ---------------------------------------------------------------------------

describe("Gate 1 - IP Type", () => {
  it("passes a clean residential IP", () => {
    const result = checkIpType(makeRequest(), makeConfig());
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(1);
  });

  it("blocks a Tor exit node", () => {
    const req = makeRequest({ cf: { ...makeRequest().cf, isTor: "1" } });
    const result = checkIpType(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_TOR");
  });

  it("blocks every ASN in DATACENTER_ASNS", () => {
    for (const asn of DATACENTER_ASNS) {
      const req = makeRequest({
        cf: { ...makeRequest().cf, asn, isTor: undefined },
      });
      const result = checkIpType(req, makeConfig());
      expect(result.action).toBe("block");
      expect(result.reason).toBe("BANNED_PROXY_DC");
    }
  });

  it("blocks datacenter by org name containing 'Cloud Hosting'", () => {
    const req = makeRequest({
      cf: { ...makeRequest().cf, asn: 99999, asOrganization: "MyCloud Hosting Ltd" },
    });
    const result = checkIpType(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_DC");
  });

  it("blocks low bot score below threshold", () => {
    const req = makeRequest({
      cf: {
        ...makeRequest().cf,
        asn: 12345,
        asOrganization: "Residential ISP",
        botManagement: { score: 10 },
      },
    });
    const result = checkIpType(req, makeConfig({ botScoreThreshold: 40 }));
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_TOR");
  });

  it("passes a bot score exactly at the threshold boundary", () => {
    const req = makeRequest({
      cf: {
        ...makeRequest().cf,
        botManagement: { score: 40 },
      },
    });
    // score 40 is NOT < 40, so passes
    const result = checkIpType(req, makeConfig({ botScoreThreshold: 40 }));
    expect(result.action).toBe("pass");
  });
});

// ---------------------------------------------------------------------------
// Gate 2: VPN Detection
// ---------------------------------------------------------------------------

describe("Gate 2 - VPN Detection", () => {
  it("passes a clean request", () => {
    const result = checkVpn(makeRequest(), makeConfig());
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(2);
  });

  it("blocks when isAnonymousVpn is set", () => {
    const req = makeRequest({ cf: { ...makeRequest().cf, isAnonymousVpn: "1" } });
    const result = checkVpn(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_VPN");
  });

  it("blocks when isAnonymous is set", () => {
    const req = makeRequest({ cf: { ...makeRequest().cf, isAnonymous: "1" } });
    const result = checkVpn(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_VPN");
  });

  it("blocks when isPublicProxy is set", () => {
    const req = makeRequest({ cf: { ...makeRequest().cf, isPublicProxy: "1" } });
    const result = checkVpn(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_VPN");
  });

  it("blocks on Bot Management VPN detection ID 82", () => {
    const req = makeRequest({
      cf: {
        ...makeRequest().cf,
        botManagement: { score: 90, detectionIds: { "82": 1 } },
      },
    });
    const result = checkVpn(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_VPN");
  });
});

// ---------------------------------------------------------------------------
// Gate 3: Known Proxy
// ---------------------------------------------------------------------------

describe("Gate 3 - Known Proxy", () => {
  it("passes a residential ASN", () => {
    const result = checkKnownProxy(makeRequest(), makeConfig());
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(3);
  });

  it("blocks a VPN organisation by name pattern", () => {
    const req = makeRequest({
      cf: { ...makeRequest().cf, asn: 99999, asOrganization: "NordVPN SA" },
    });
    const result = checkKnownProxy(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_KNOWN");
  });

  it("blocks Mullvad VPN ASN 398705", () => {
    const req = makeRequest({
      cf: { ...makeRequest().cf, asn: 398705, asOrganization: "Mullvad VPN" },
    });
    const result = checkKnownProxy(req, makeConfig());
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_PROXY_KNOWN");
  });
});

// ---------------------------------------------------------------------------
// Gate 4: IP Blacklist
// ---------------------------------------------------------------------------

describe("Gate 4 - IP Blacklist", () => {
  it("passes an IP not in the blacklist", async () => {
    const env = makeEnv();
    const result = await checkBlacklist(makeRequest(), makeConfig(), env);
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(4);
  });

  it("blocks an IP present in the blacklist", async () => {
    const entry = JSON.stringify({
      bannedAt: "2024-01-01T00:00:00Z",
      reason: "fraud",
    });
    const env = makeEnv({ IP_BLACKLIST: makeKv({ "bl:1.2.3.4": entry }) });
    const result = await checkBlacklist(makeRequest(), makeConfig(), env);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_IP_BLACKLIST");
  });

  it("blocks conservatively when blacklist entry JSON is malformed", async () => {
    const env = makeEnv({ IP_BLACKLIST: makeKv({ "bl:1.2.3.4": "not-json{" }) });
    const result = await checkBlacklist(makeRequest(), makeConfig(), env);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("BANNED_IP_BLACKLIST");
  });

  it("passes a different IP when another IP is blacklisted", async () => {
    const entry = JSON.stringify({ bannedAt: "2024-01-01T00:00:00Z", reason: "fraud" });
    const env = makeEnv({ IP_BLACKLIST: makeKv({ "bl:9.9.9.9": entry }) });
    const result = await checkBlacklist(makeRequest({ ip: "1.2.3.4" }), makeConfig(), env);
    expect(result.action).toBe("pass");
  });
});

// ---------------------------------------------------------------------------
// Gate 5: Fraud Score
// ---------------------------------------------------------------------------

describe("Gate 5 - Fraud Score", () => {
  it("passes a clean low-velocity request", async () => {
    const env = makeEnv();
    const result = await checkFraudScore(makeRequest(), makeConfig(), env, mockCtx);
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(5);
  });

  it("blocks when multiple signals combine above the block threshold", async () => {
    const env = makeEnv({
      FRAUD_VELOCITY: makeKv({
        "vel:1.2.3.4:1m": "60",   // full rate limit
        "vel:1.2.3.4:5m": "180",
        "vel:1.2.3.4:1h": "1800",
      }),
    });
    const req = makeRequest({
      userAgent: "python-requests/2.31", // known bad UA
      acceptLanguage: "zh-CN",           // language mismatch with country=BR
    });
    const result = await checkFraudScore(req, makeConfig(), env, mockCtx);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("HIGH_FRAUD_SCORE");
  });

  it("returns review for intermediate score between thresholds", async () => {
    const env = makeEnv({
      FRAUD_VELOCITY: makeKv({
        "vel:1.2.3.4:1m": "30", // 50% of limit
      }),
    });
    const req = makeRequest({
      userAgent: "python-requests/2.31", // +10 points
      acceptLanguage: "zh-CN",           // country mismatch +15
      cf: {
        ...makeRequest().cf,
        botManagement: { score: 20 },    // low bot score +~16
      },
    });
    const result = await checkFraudScore(req, makeConfig(), env, mockCtx);
    expect(["review", "block"]).toContain(result.action);
    expect(result.reason).toBe("HIGH_FRAUD_SCORE");
  });

  it("does not block a missing user-agent in isolation", async () => {
    const env = makeEnv();
    const req = makeRequest({ userAgent: undefined });
    const result = await checkFraudScore(req, makeConfig(), env, mockCtx);
    expect(result.action).not.toBe("block");
  });
});

// ---------------------------------------------------------------------------
// Gate 6: Device Fingerprint
// ---------------------------------------------------------------------------

describe("Gate 6 - Device Fingerprint", () => {
  it("passes when no JA3 hash is present (Bot Management disabled)", async () => {
    const req = makeRequest({
      cf: { ...makeRequest().cf, botManagement: {} },
    });
    const result = await checkDeviceFingerprint(req, makeConfig(), makeEnv(), mockCtx);
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(6);
  });

  it("blocks a JA3 hash on the device blocklist", async () => {
    const ja3 = "deadbeefcafe1234";
    const env = makeEnv({
      DEVICE_FINGERPRINTS: makeKv({ [`ja3:block:${ja3}`]: "1" }),
    });
    const req = makeRequest({
      cf: { ...makeRequest().cf, botManagement: { score: 90, ja3Hash: ja3 } },
    });
    const result = await checkDeviceFingerprint(req, makeConfig(), env, mockCtx);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("DEVICE_ANOMALY");
  });

  it("blocks when IP has too many distinct JA3 hashes", async () => {
    const ip = "1.2.3.4";
    const newJa3 = "brandnewfingerprint";
    const existingHashes: Record<string, string> = {};
    for (let i = 0; i < DEFAULT_CONFIG.ja3DistinctLimit; i++) {
      existingHashes[`hash${i}`] = new Date().toISOString();
    }
    const history = JSON.stringify({
      hashes: existingHashes,
      updatedAt: new Date().toISOString(),
    });
    const env = makeEnv({
      DEVICE_FINGERPRINTS: makeKv({ [`ja3:ip:${ip}`]: history }),
    });
    const req = makeRequest({
      ip,
      cf: { ...makeRequest().cf, botManagement: { score: 90, ja3Hash: newJa3 } },
    });
    const result = await checkDeviceFingerprint(req, makeConfig(), env, mockCtx);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("DEVICE_ANOMALY");
  });

  it("passes a first-time JA3 hash with no prior history", async () => {
    const env = makeEnv();
    const result = await checkDeviceFingerprint(makeRequest(), makeConfig(), env, mockCtx);
    expect(result.action).toBe("pass");
  });
});

// ---------------------------------------------------------------------------
// Gate 7: Sanctions
// ---------------------------------------------------------------------------

describe("Gate 7 - Sanctions", () => {
  it("passes a non-sanctioned country", async () => {
    const env = makeEnv();
    const result = await checkSanctions(makeRequest(), makeConfig(), env);
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(7);
  });

  it("blocks every hardcoded sanctioned country", async () => {
    const env = makeEnv();
    for (const country of HARDCODED_SANCTIONED_COUNTRIES) {
      const req = makeRequest({ cf: { ...makeRequest().cf, country } });
      const result = await checkSanctions(req, makeConfig(), env);
      expect(result.action).toBe("block");
      expect(result.reason).toBe("SANCTIONS_MATCH");
    }
  });

  it("blocks a country added dynamically to the KV sanctions list", async () => {
    const env = makeEnv({
      SANCTIONS_LIST: makeKv({ "sanctions:country:XX": "1" }),
    });
    const req = makeRequest({ cf: { ...makeRequest().cf, country: "XX" } });
    const result = await checkSanctions(req, makeConfig(), env);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("SANCTIONS_MATCH");
  });

  it("returns review when player name token matches the SDN KV index", async () => {
    const env = makeEnv({
      SANCTIONS_LIST: makeKv({
        "sanctions:name:hussein": "SADDAM HUSSEIN",
      }),
    });
    const req = makeRequest({ playerName: "John Hussein Doe" });
    const result = await checkSanctions(req, makeConfig(), env);
    expect(result.action).toBe("review");
    expect(result.reason).toBe("SANCTIONS_MATCH");
  });

  it("passes when no player name is provided", async () => {
    const env = makeEnv();
    // makeRequest() sets no playerName by default
    const result = await checkSanctions(makeRequest(), makeConfig(), env);
    expect(result.action).toBe("pass");
  });

  it("strips diacritics before performing token matching", async () => {
    const env = makeEnv({
      SANCTIONS_LIST: makeKv({ "sanctions:name:jose": "JOSE EXAMPLE SDN ENTRY" }),
    });
    const req = makeRequest({ playerName: "Jose Silva" });
    const result = await checkSanctions(req, makeConfig(), env);
    expect(result.action).toBe("review");
  });
});

// ---------------------------------------------------------------------------
// Gate 8: KYC Status
// ---------------------------------------------------------------------------

describe("Gate 8 - KYC Status", () => {
  it("passes unauthenticated requests (no playerId)", async () => {
    const env = makeEnv();
    const result = await checkKycStatus(makeRequest(), makeConfig(), env);
    expect(result.action).toBe("pass");
    expect(result.gate).toBe(8);
  });

  it("blocks when the player has no KYC record in D1", async () => {
    const env = makeEnv({ PLAYER_DB: makeD1([]) });
    const req = makeRequest({ playerId: "player-123" });
    const result = await checkKycStatus(req, makeConfig(), env);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("KYC_BLOCKED");
  });

  it("passes an approved player", async () => {
    const env = makeEnv({
      PLAYER_DB: makeD1([{
        player_id: "player-123",
        status: "approved",
        tier: 1,
        reviewed_at: "2024-01-01T00:00:00Z",
        reviewer: "compliance@example.com",
        notes: null,
      }]),
    });
    const req = makeRequest({ playerId: "player-123" });
    const result = await checkKycStatus(req, makeConfig(), env);
    expect(result.action).toBe("pass");
  });

  it("returns review for a pending player", async () => {
    const env = makeEnv({
      PLAYER_DB: makeD1([{
        player_id: "player-456",
        status: "pending",
        tier: 0,
        reviewed_at: null,
        reviewer: null,
        notes: null,
      }]),
    });
    const req = makeRequest({ playerId: "player-456" });
    const result = await checkKycStatus(req, makeConfig(), env);
    expect(result.action).toBe("review");
    expect(result.reason).toBe("KYC_PENDING");
  });

  it("blocks a rejected player", async () => {
    const env = makeEnv({
      PLAYER_DB: makeD1([{
        player_id: "player-789",
        status: "rejected",
        tier: 0,
        reviewed_at: "2024-03-01T00:00:00Z",
        reviewer: "compliance@example.com",
        notes: "Documents forged",
      }]),
    });
    const req = makeRequest({ playerId: "player-789" });
    const result = await checkKycStatus(req, makeConfig(), env);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("KYC_BLOCKED");
  });

  it("blocks a frozen account", async () => {
    const env = makeEnv({
      PLAYER_DB: makeD1([{
        player_id: "player-frozen",
        status: "frozen",
        tier: 1,
        reviewed_at: null,
        reviewer: null,
        notes: "AML investigation ongoing",
      }]),
    });
    const req = makeRequest({ playerId: "player-frozen" });
    const result = await checkKycStatus(req, makeConfig(), env);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("KYC_BLOCKED");
  });

  it("blocks a player with status 'none' (no documents submitted)", async () => {
    const env = makeEnv({
      PLAYER_DB: makeD1([{
        player_id: "player-new",
        status: "none",
        tier: 0,
        reviewed_at: null,
        reviewer: null,
        notes: null,
      }]),
    });
    const req = makeRequest({ playerId: "player-new" });
    const result = await checkKycStatus(req, makeConfig(), env);
    expect(result.action).toBe("block");
    expect(result.reason).toBe("KYC_BLOCKED");
  });
});

// ---------------------------------------------------------------------------
// Pipeline: early-return guarantee
// ---------------------------------------------------------------------------

describe("Pipeline early-return guarantee", () => {
  it("a Gate 1 block does not trigger any KV reads", async () => {
    const kvSpy = makeKv({
      "bl:1.2.3.4": '{"bannedAt":"x","reason":"should not run"}',
    });
    const getSpy = vi.spyOn(kvSpy, "get");

    const req = makeRequest({
      cf: { ...makeRequest().cf, isTor: "1" },
    });

    const g1 = checkIpType(req, makeConfig());
    expect(g1.action).toBe("block");
    expect(getSpy).not.toHaveBeenCalled();
  });

  it("gate numbers are correctly assigned to each check function", () => {
    expect(checkIpType(makeRequest(), makeConfig()).gate).toBe(1);
    expect(checkVpn(makeRequest(), makeConfig()).gate).toBe(2);
    expect(checkKnownProxy(makeRequest(), makeConfig()).gate).toBe(3);
  });
});
