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
 * Chapter 24 — IP Detection Pipeline Worker
 *
 * Implements the iGaming security flowchart as an 8-gate sequential chain.
 * Each gate is a pure(-ish) function returning { action, reason, gate }.
 * The chain uses an early-return pattern: the first BLOCK action immediately
 * terminates the chain and returns a 403 to the downstream origin.
 *
 * Performance budget:
 *   Gate 1  (IP type)        <0.05 ms  — in-memory only
 *   Gate 2  (VPN)            <0.05 ms  — in-memory only
 *   Gate 3  (Known proxy)    <0.05 ms  — in-memory only
 *   Gate 4  (Blacklist)      <1 ms     — 1 KV read, edge-cached
 *   Gate 5  (Fraud score)    <1 ms     — 3 parallel KV reads
 *   Gate 6  (Device FP)      <1 ms     — 2–3 parallel KV reads
 *   Gate 7  (Sanctions)      <1 ms     — 1–5 parallel KV reads
 *   Gate 8  (KYC)            <2 ms     — 1 D1 prepared-statement query
 *   ──────────────────────────────────
 *   Total (worst case)       <5 ms     ✓
 *
 * Response format:
 *   Allowed:  proxy to origin with X-Security-* headers added
 *   Blocked:  403 JSON  { error: "ACCESS_DENIED", code: "<ReasonCode>" }
 *   Review:   403 JSON  { error: "UNDER_REVIEW",  code: "<ReasonCode>" }
 *             (the review queue receives the full pipeline result via a
 *              non-blocking waitUntil log push)
 */

import type {
  Env,
  GateConfig,
  GateResult,
  PipelineResult,
  PlayerRequest,
} from "./types.js";
import { DEFAULT_CONFIG } from "./types.js";
import { checkIpType }           from "./gates/ip-type.js";
import { checkVpn, checkKnownProxy } from "./gates/vpn-proxy.js";
import { checkBlacklist }        from "./gates/blacklist.js";
import { checkFraudScore }       from "./gates/fraud-score.js";
import { checkDeviceFingerprint } from "./gates/device-fingerprint.js";
import { checkSanctions }        from "./gates/sanctions.js";
import { checkKycStatus }        from "./gates/kyc-status.js";

// ─── Request parsing ──────────────────────────────────────────────────────────

function buildPlayerRequest(request: Request): PlayerRequest {
  const cf = (request.cf ?? {}) as PlayerRequest["cf"];
  const url = new URL(request.url);

  // Player ID is extracted from a signed JWT / session header by upstream
  // middleware. Here we read the pre-validated header value directly.
  const playerId = request.headers.get("X-Player-Id") ?? undefined;
  const playerName = request.headers.get("X-Player-Name") ?? undefined;

  // CF attaches the real client IP in CF-Connecting-IP.
  const ip =
    request.headers.get("CF-Connecting-IP") ??
    request.headers.get("X-Forwarded-For")?.split(",")[0].trim() ??
    "0.0.0.0";

  return {
    ip,
    cf,
    playerId,
    playerName,
    userAgent: request.headers.get("User-Agent") ?? undefined,
    acceptLanguage: request.headers.get("Accept-Language") ?? undefined,
    requestedAt: new Date().toISOString(),
    ja3Raw: cf.botManagement?.ja3Hash, // re-exposed for logging convenience
  };
}

// ─── Pipeline runner ──────────────────────────────────────────────────────────

async function runPipeline(
  req: PlayerRequest,
  config: GateConfig,
  env: Env,
  ctx: ExecutionContext,
): Promise<PipelineResult> {
  const gates: GateResult[] = [];
  const pipelineStart = Date.now();

  /**
   * Runs a gate function, records timing, appends result, and returns it.
   * Timing uses Date.now() (ms resolution) converted to microseconds.
   * The CF Workers runtime does not expose performance.now() at µs resolution,
   * but Date.now() is sufficient for p95 budget tracking.
   */
  async function runGate(fn: () => Promise<GateResult> | GateResult): Promise<GateResult> {
    const start = Date.now();
    const result = await fn();
    result.durationUs = (Date.now() - start) * 1000; // ms → µs (approximate)
    gates.push(result);
    return result;
  }

  // ── Gate 1: IP Type ────────────────────────────────────────────────────────
  {
    const g = await runGate(() => checkIpType(req, config));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
  }

  // ── Gate 2: VPN Detection ──────────────────────────────────────────────────
  {
    const g = await runGate(() => checkVpn(req, config));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
  }

  // ── Gate 3: Known Proxy ────────────────────────────────────────────────────
  {
    const g = await runGate(() => checkKnownProxy(req, config));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
  }

  // ── Gate 4: IP Blacklist ───────────────────────────────────────────────────
  {
    const g = await runGate(() => checkBlacklist(req, config, env));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
  }

  // ── Gate 5: Fraud Score ────────────────────────────────────────────────────
  {
    const g = await runGate(() => checkFraudScore(req, config, env, ctx));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
    // "review" from fraud score does NOT stop the chain — we still run
    // sanctions and KYC. The final review flag is set after all gates pass.
  }

  // ── Gate 6: Device Fingerprint ─────────────────────────────────────────────
  {
    const g = await runGate(() => checkDeviceFingerprint(req, config, env, ctx));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
  }

  // ── Gate 7: Sanctions / PEP ────────────────────────────────────────────────
  {
    const g = await runGate(() => checkSanctions(req, config, env));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
  }

  // ── Gate 8: KYC Status ─────────────────────────────────────────────────────
  {
    const g = await runGate(() => checkKycStatus(req, config, env));
    if (g.action === "block") {
      return finalisePipeline(g, gates, pipelineStart);
    }
    if (g.action === "review") {
      return finalisePipeline(g, gates, pipelineStart);
    }
  }

  // All gates passed — check if any gate recommended review.
  const reviewGate = gates.find((g) => g.action === "review");
  if (reviewGate) {
    return finalisePipeline(reviewGate, gates, pipelineStart);
  }

  // Full pass.
  const passResult: GateResult = { action: "pass", reason: "PASS", gate: 8 };
  return finalisePipeline(passResult, gates, pipelineStart);
}

function finalisePipeline(
  deciding: GateResult,
  gates: GateResult[],
  startMs: number,
): PipelineResult {
  return {
    action: deciding.action,
    reason: deciding.reason,
    decisionGate: deciding.gate,
    gates,
    totalDurationUs: (Date.now() - startMs) * 1000,
  };
}

// ─── Response builders ────────────────────────────────────────────────────────

function blockedResponse(result: PipelineResult): Response {
  const body = JSON.stringify({
    error: result.action === "review" ? "UNDER_REVIEW" : "ACCESS_DENIED",
    code: result.reason,
  });

  return new Response(body, {
    status: 403,
    headers: {
      "Content-Type": "application/json",
      "X-Security-Decision": result.action,
      "X-Security-Gate": String(result.decisionGate),
      // Never expose detail or reason text to the client in production.
      // The code field is sufficient for client-side localisation.
    },
  });
}

function passedResponse(
  originalRequest: Request,
  result: PipelineResult,
): Request {
  // Clone the request and attach security metadata as headers so the origin
  // knows the request was validated by the edge pipeline.
  const modified = new Request(originalRequest, {
    headers: new Headers(originalRequest.headers),
  });

  modified.headers.set("X-Security-Decision", "pass");
  modified.headers.set("X-Security-Gate", String(result.decisionGate));
  modified.headers.set("X-Security-Pipeline-Us", String(result.totalDurationUs));
  modified.headers.set("X-Security-Gates-Passed", String(result.gates.length));

  return modified;
}

// ─── Structured logging ───────────────────────────────────────────────────────

interface SecurityEvent {
  ts: string;
  ip: string;
  country?: string;
  asn?: number;
  playerId?: string;
  action: string;
  reason: string;
  decisionGate: number;
  totalDurationUs: number;
  gates: Array<{ gate: number; action: string; reason: string; durationUs?: number }>;
}

async function logSecurityEvent(
  req: PlayerRequest,
  result: PipelineResult,
): Promise<void> {
  const event: SecurityEvent = {
    ts: req.requestedAt,
    ip: req.ip,
    country: req.cf.country,
    asn: req.cf.asn,
    playerId: req.playerId,
    action: result.action,
    reason: result.reason,
    decisionGate: result.decisionGate,
    totalDurationUs: result.totalDurationUs,
    gates: result.gates.map((g) => ({
      gate: g.gate,
      action: g.action,
      reason: g.reason,
      durationUs: g.durationUs,
    })),
  };

  // console.log is forwarded to Cloudflare Logpush / Workers Trace Events.
  console.log(JSON.stringify(event));
}

// ─── Worker entry point ───────────────────────────────────────────────────────

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<Response> {
    // Health check endpoint — bypasses the security pipeline.
    if (new URL(request.url).pathname === "/health") {
      return new Response(JSON.stringify({ status: "ok" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    const req = buildPlayerRequest(request);
    const config: GateConfig = DEFAULT_CONFIG;

    let result: PipelineResult;
    try {
      result = await runPipeline(req, config, env, ctx);
    } catch (err) {
      // Pipeline errors must never silently pass traffic — fail closed.
      console.error(
        JSON.stringify({
          ts: req.requestedAt,
          ip: req.ip,
          error: String(err),
          stack: err instanceof Error ? err.stack : undefined,
        }),
      );
      return new Response(
        JSON.stringify({ error: "PIPELINE_ERROR" }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      );
    }

    // Log the security event non-blocking.
    ctx.waitUntil(logSecurityEvent(req, result));

    if (result.action !== "pass") {
      return blockedResponse(result);
    }

    // Pass the request to the origin with security headers attached.
    const outbound = passedResponse(request, result);

    // In a real deployment this would be `fetch(outbound)`.
    // Returning the modified request object here so the worker can be used
    // as both a standalone service worker and a subrequest transformer.
    return fetch(outbound);
  },
} satisfies ExportedHandler<Env>;
