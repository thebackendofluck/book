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
 * attack-logger.ts
 *
 * Two responsibilities:
 *
 * 1. AttackCounter — a Durable Object that provides accurate, race-condition-free
 *    increment/read operations for per-ASN attack counters.  The main classifier
 *    calls it via waitUntil() so it never adds latency to the hot path.
 *
 * 2. handleAttackLog — an admin HTTP router that exposes:
 *      GET  /attacks/export       — full JSON export of all blocked events
 *      GET  /attacks/asn-summary  — grouped counts by ASN
 *      POST /attacks/flush        — delete all records after filing an abuse report
 *
 * KV schema (namespace: ATTACK_LOG):
 *   event:<timestamp>:<ray>  →  JSON AttackEvent
 *
 * Durable Object storage schema (per-instance, keyed by ASN):
 *   count          →  number
 *   firstSeen      →  timestamp ms
 *   lastSeen       →  timestamp ms
 *   sampleIps      →  JSON string[]  (up to 10 unique IPs)
 */

import type { Env, AttackEvent, AsnSummaryEntry } from "./types.js";

// ─── Durable Object ───────────────────────────────────────────────────────────

export class AttackCounter implements DurableObject {
  private readonly state: DurableObjectState;

  constructor(state: DurableObjectState) {
    this.state = state;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/increment" && request.method === "POST") {
      return this.handleIncrement(request);
    }
    if (url.pathname === "/read" && request.method === "GET") {
      return this.handleRead();
    }
    if (url.pathname === "/reset" && request.method === "POST") {
      return this.handleReset();
    }

    return new Response("Not found", { status: 404 });
  }

  private async handleIncrement(request: Request): Promise<Response> {
    let event: AttackEvent;
    try {
      event = (await request.json()) as AttackEvent;
    } catch {
      return new Response("Bad request", { status: 400 });
    }

    // All reads and writes are serialised by the DO runtime — no locks needed.
    const count = ((await this.state.storage.get<number>("count")) ?? 0) + 1;
    const firstSeen =
      (await this.state.storage.get<number>("firstSeen")) ?? event.timestamp;
    const rawSamples =
      (await this.state.storage.get<string>("sampleIps")) ?? "[]";
    const sampleIps: string[] = JSON.parse(rawSamples);

    if (!sampleIps.includes(event.ip) && sampleIps.length < 10) {
      sampleIps.push(event.ip);
    }

    await this.state.storage.put({
      count,
      firstSeen,
      lastSeen: event.timestamp,
      sampleIps: JSON.stringify(sampleIps),
    });

    return new Response(JSON.stringify({ count }), {
      headers: { "Content-Type": "application/json" },
    });
  }

  private async handleRead(): Promise<Response> {
    const count = (await this.state.storage.get<number>("count")) ?? 0;
    const firstSeen = (await this.state.storage.get<number>("firstSeen")) ?? 0;
    const lastSeen = (await this.state.storage.get<number>("lastSeen")) ?? 0;
    const rawSamples =
      (await this.state.storage.get<string>("sampleIps")) ?? "[]";
    const sampleIps: string[] = JSON.parse(rawSamples);

    return new Response(
      JSON.stringify({ count, firstSeen, lastSeen, sampleIps }),
      { headers: { "Content-Type": "application/json" } },
    );
  }

  private async handleReset(): Promise<Response> {
    await this.state.storage.deleteAll();
    return new Response(JSON.stringify({ status: "reset" }), {
      headers: { "Content-Type": "application/json" },
    });
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

/**
 * Page through all KV keys with the given prefix.
 * Workers KV list() returns at most 1000 keys; we loop until cursor is exhausted.
 */
async function listAllKeys(
  kv: KVNamespace,
  prefix: string,
): Promise<string[]> {
  const keys: string[] = [];
  let cursor: string | undefined;

  do {
    const result = await kv.list({ prefix, cursor, limit: 1000 });
    for (const k of result.keys) keys.push(k.name);
    cursor = result.list_complete ? undefined : result.cursor;
  } while (cursor !== undefined);

  return keys;
}

// ─── Route handlers ───────────────────────────────────────────────────────────

/**
 * GET /attacks/export
 * Returns all stored attack events as a JSON array, sorted ascending by
 * timestamp.  Suitable for feeding directly into an ISP abuse reporting tool.
 */
async function exportAttacks(env: Env): Promise<Response> {
  const keys = await listAllKeys(env.ATTACK_LOG, "event:");

  // Fetch all events concurrently in batches of 100 to avoid hammering KV.
  const events: AttackEvent[] = [];
  const BATCH = 100;

  for (let i = 0; i < keys.length; i += BATCH) {
    const batch = keys.slice(i, i + BATCH);
    const values = await Promise.all(batch.map((k) => env.ATTACK_LOG.get(k)));
    for (const raw of values) {
      if (raw === null) continue;
      try {
        events.push(JSON.parse(raw) as AttackEvent);
      } catch {
        // Skip malformed entries
      }
    }
  }

  // Sort by timestamp ascending
  events.sort((a, b) => a.timestamp - b.timestamp);

  return jsonResponse({
    exportedAt: new Date().toISOString(),
    count: events.length,
    events,
  });
}

/**
 * GET /attacks/asn-summary
 * Returns a deduplicated summary grouped by ASN, reading live counts from
 * Durable Objects.  The list of distinct ASNs is derived from the ATTACK_LOG
 * KV events.
 */
async function asnSummary(env: Env): Promise<Response> {
  const keys = await listAllKeys(env.ATTACK_LOG, "event:");
  const asnSet = new Set<string>();

  // Collect unique ASNs from event keys (events store ASN in the payload).
  // We sample a subset to build the ASN set without fetching every value.
  const sampleSize = Math.min(keys.length, 500);
  const sampleKeys = keys.slice(0, sampleSize);
  const sampleValues = await Promise.all(
    sampleKeys.map((k) => env.ATTACK_LOG.get(k)),
  );

  for (const raw of sampleValues) {
    if (!raw) continue;
    try {
      const ev = JSON.parse(raw) as AttackEvent;
      asnSet.add(ev.asn);
    } catch {
      // Skip
    }
  }

  // Fetch DO state for each unique ASN concurrently
  const summary: AsnSummaryEntry[] = [];
  const asnList = Array.from(asnSet);

  const doResults = await Promise.all(
    asnList.map(async (asn) => {
      try {
        const id = env.ATTACK_COUNTER.idFromName(asn);
        const stub = env.ATTACK_COUNTER.get(id);
        const resp = await stub.fetch("https://counter/read");
        const data = (await resp.json()) as {
          count: number;
          firstSeen: number;
          lastSeen: number;
          sampleIps: string[];
        };
        return { asn, ...data };
      } catch {
        return null;
      }
    }),
  );

  for (const result of doResults) {
    if (result === null || result.count === 0) continue;
    summary.push({
      asn: result.asn,
      count: result.count,
      firstSeen: result.firstSeen,
      lastSeen: result.lastSeen,
      sampleIps: result.sampleIps,
    });
  }

  // Sort by count descending
  summary.sort((a, b) => b.count - a.count);

  return jsonResponse({
    generatedAt: new Date().toISOString(),
    asnCount: summary.length,
    summary,
  });
}

/**
 * POST /attacks/flush
 * Deletes all attack event records from KV and resets all DO counters.
 * Call this after an abuse report has been filed.  Requires a
 * confirmation token in the request body: { "confirm": "FLUSH" }
 */
async function flushAttacks(request: Request, env: Env): Promise<Response> {
  let body: { confirm?: string } = {};
  try {
    body = (await request.json()) as { confirm?: string };
  } catch {
    return jsonResponse({ error: "Invalid JSON body" }, 400);
  }

  if (body.confirm !== "FLUSH") {
    return jsonResponse(
      { error: 'Send { "confirm": "FLUSH" } to confirm deletion' },
      400,
    );
  }

  // Collect all event keys then delete in parallel batches
  const keys = await listAllKeys(env.ATTACK_LOG, "event:");
  const BATCH = 100;
  let deletedEvents = 0;

  for (let i = 0; i < keys.length; i += BATCH) {
    const batch = keys.slice(i, i + BATCH);
    await Promise.all(batch.map((k) => env.ATTACK_LOG.delete(k)));
    deletedEvents += batch.length;
  }

  // Collect all unique ASNs and reset their DOs
  const asnKeys = await listAllKeys(env.ATTACK_LOG, "asn:");
  const asnSet = new Set<string>();
  // Derive ASNs from keys: asn:<asnId>
  for (const k of asnKeys) {
    const parts = k.split(":");
    if (parts.length >= 2) asnSet.add(parts[1]);
  }

  // Also reset DOs for any ASN we know about via event payloads in the last page
  const eventKeys = keys.slice(0, Math.min(keys.length, 1000));
  const eventValues = await Promise.all(
    eventKeys.map((k) => env.ATTACK_LOG.get(k)),
  );
  for (const raw of eventValues) {
    if (!raw) continue;
    try {
      const ev = JSON.parse(raw) as AttackEvent;
      asnSet.add(ev.asn);
    } catch { /* skip */ }
  }

  let resetCounters = 0;
  await Promise.all(
    Array.from(asnSet).map(async (asn) => {
      try {
        const id = env.ATTACK_COUNTER.idFromName(asn);
        const stub = env.ATTACK_COUNTER.get(id);
        await stub.fetch("https://counter/reset", { method: "POST" });
        resetCounters++;
      } catch { /* best effort */ }
    }),
  );

  return jsonResponse({
    status: "flushed",
    deletedEvents,
    resetCounters,
    flushedAt: new Date().toISOString(),
  });
}

// ─── Router ───────────────────────────────────────────────────────────────────

export async function handleAttackLog(
  request: Request,
  env: Env,
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/attacks/export" && method === "GET") {
    return exportAttacks(env);
  }
  if (path === "/attacks/asn-summary" && method === "GET") {
    return asnSummary(env);
  }
  if (path === "/attacks/flush" && method === "POST") {
    return flushAttacks(request, env);
  }

  return jsonResponse(
    { error: "Unknown attack route", path, method },
    404,
  );
}
