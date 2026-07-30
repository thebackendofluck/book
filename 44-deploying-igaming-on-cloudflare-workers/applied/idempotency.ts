// Companion code for "The Backend of Luck" - Chapter 44, Deploying iGaming Platforms on Cloudflare Workers.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * D1-backed idempotency helper for Cloudflare Workers.
 *
 * INSERT-as-lock pattern using SQLite's ON CONFLICT DO NOTHING. If the insert
 * wins, the caller executes the handler and then UPDATEs the row to terminal.
 * If the insert loses, the caller fetches the row and either replays the
 * cached response or rejects (409) on body-hash mismatch or in-progress timeout.
 *
 * Non-terminal responses (202 Accepted) are NOT cached; the in-progress row
 * is deleted so a retry can re-execute.
 */

export interface IdempotencyRecord {
  key: string;
  user_id: string | null;
  path: string;
  body_hash: string;
  state: "in_progress" | "terminal";
  response_status: number | null;
  response_body: string | null;
  response_headers: string | null;
  created_at: number;
  expires_at: number;
}

export interface IdempotencyBinding {
  DB: D1Database;
}

const LONG_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const DEFAULT_TTL_MS = 24 * 60 * 60 * 1000;
const POLL_INTERVAL_MS = 100;
const POLL_TIMEOUT_MS = 30_000;

const LONG_TTL_PREFIXES = ["/api/payments/", "/api/wallet/"];

const REQUIRED_PREFIXES = [
  "/api/payments/deposit",
  "/api/payments/withdraw",
  "/api/wallet/",
];

export function pathRequiresKey(path: string): boolean {
  return REQUIRED_PREFIXES.some((p) => path.startsWith(p));
}

function ttlForPath(path: string): number {
  return LONG_TTL_PREFIXES.some((p) => path.startsWith(p))
    ? LONG_TTL_MS
    : DEFAULT_TTL_MS;
}

export function isTerminalStatus(status: number): boolean {
  if (status === 102 || status === 202) return false;
  return status >= 200 && status < 600;
}

export async function canonicalBodyHash(body: string): Promise<string> {
  let canonical = body;
  try {
    const parsed = JSON.parse(body);
    canonical = canonicalStringify(parsed);
  } catch {
    /* fall through, hash raw */
  }
  const buf = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonical),
  );
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function canonicalStringify(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) {
    return "[" + v.map(canonicalStringify).join(",") + "]";
  }
  const obj = v as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return (
    "{" +
    keys
      .map((k) => JSON.stringify(k) + ":" + canonicalStringify(obj[k]))
      .join(",") +
    "}"
  );
}

export async function tryAcquire(
  db: D1Database,
  rec: Omit<IdempotencyRecord, "response_status" | "response_body" | "response_headers">,
): Promise<boolean> {
  const result = await db
    .prepare(
      `INSERT INTO idempotency_records
         (key, user_id, path, body_hash, state, created_at, expires_at)
       VALUES (?, ?, ?, ?, 'in_progress', ?, ?)
       ON CONFLICT (key) DO NOTHING
       RETURNING key`,
    )
    .bind(
      rec.key,
      rec.user_id,
      rec.path,
      rec.body_hash,
      rec.created_at,
      rec.expires_at,
    )
    .first<{ key: string }>();
  return result !== null;
}

export async function fetchRecord(
  db: D1Database,
  key: string,
): Promise<IdempotencyRecord | null> {
  const now = Date.now();
  const row = await db
    .prepare(
      `SELECT * FROM idempotency_records WHERE key = ? AND expires_at > ?`,
    )
    .bind(key, now)
    .first<IdempotencyRecord>();
  return row ?? null;
}

export async function markTerminal(
  db: D1Database,
  key: string,
  status: number,
  body: string,
  headers: Record<string, string>,
): Promise<void> {
  await db
    .prepare(
      `UPDATE idempotency_records
       SET state = 'terminal',
           response_status = ?,
           response_body = ?,
           response_headers = ?
       WHERE key = ?`,
    )
    .bind(status, body, JSON.stringify(headers), key)
    .run();
}

export async function releaseInflight(
  db: D1Database,
  key: string,
): Promise<void> {
  await db
    .prepare(
      `DELETE FROM idempotency_records WHERE key = ? AND state = 'in_progress'`,
    )
    .bind(key)
    .run();
}

/**
 * Wraps a handler with idempotency semantics.
 *
 *   return withIdempotency(request, env.DB, userId, () => realHandler(request));
 */
export async function withIdempotency(
  request: Request,
  db: D1Database,
  userId: string | null,
  handler: (body: string) => Promise<Response>,
): Promise<Response> {
  if (!["POST", "PUT", "PATCH", "DELETE"].includes(request.method)) {
    return handler(await request.text());
  }

  const url = new URL(request.url);
  const key = request.headers.get("idempotency-key");
  const body = await request.text();

  if (!key) {
    if (pathRequiresKey(url.pathname)) {
      return json(
        { error: "idempotency_key_required" },
        400,
      );
    }
    return handler(body);
  }

  if (key.length > 255) {
    return json({ error: "idempotency_key_too_long" }, 400);
  }

  const bodyHash = await canonicalBodyHash(body);
  const now = Date.now();
  const expiresAt = now + ttlForPath(url.pathname);

  const acquired = await tryAcquire(db, {
    key,
    user_id: userId,
    path: url.pathname,
    body_hash: bodyHash,
    state: "in_progress",
    created_at: now,
    expires_at: expiresAt,
  });

  if (!acquired) {
    return handleNonOwner(db, key, bodyHash);
  }

  let response: Response;
  try {
    response = await handler(body);
  } catch (err) {
    await releaseInflight(db, key);
    throw err;
  }

  const respBody = await response.clone().text();
  const headers: Record<string, string> = {};
  response.headers.forEach((v, k) => {
    headers[k] = v;
  });

  if (isTerminalStatus(response.status) && respBody.length <= 1_048_576) {
    await markTerminal(db, key, response.status, respBody, headers);
  } else {
    await releaseInflight(db, key);
  }

  return response;
}

async function handleNonOwner(
  db: D1Database,
  key: string,
  bodyHash: string,
): Promise<Response> {
  const existing = await fetchRecord(db, key);
  if (!existing) {
    return json({ error: "idempotency_race_retry" }, 409);
  }
  if (existing.body_hash !== bodyHash) {
    return json(
      { error: "idempotency_key_reused_with_different_body" },
      409,
    );
  }
  if (existing.state === "terminal") {
    return replay(existing);
  }

  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let interval = POLL_INTERVAL_MS;
  while (Date.now() < deadline) {
    await sleep(interval);
    const cur = await fetchRecord(db, key);
    if (!cur) {
      return json({ error: "idempotency_owner_released_retry" }, 409);
    }
    if (cur.state === "terminal") return replay(cur);
    interval = Math.min(interval * 1.5, 1000);
  }
  return json({ error: "idempotency_original_request_timeout" }, 409);
}

function replay(rec: IdempotencyRecord): Response {
  const headers = rec.response_headers
    ? (JSON.parse(rec.response_headers) as Record<string, string>)
    : {};
  headers["x-idempotent-replay"] = "true";
  return new Response(rec.response_body ?? "", {
    status: rec.response_status ?? 200,
    headers,
  });
}

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
