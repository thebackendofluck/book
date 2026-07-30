// Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * index.ts
 * ---------
 * Cloudflare Workers entry point for the edge GAL.
 *
 * Uses Hono — a lightweight, edge-optimised web framework with Express-like
 * routing. Hono runs on Cloudflare Workers, Deno, Bun, and Node.js.
 *
 * Performance targets (from the edge):
 * - Balance check:   < 10ms (DO colocated with player data)
 * - Debit/credit:    < 20ms (DO write + KV idempotency check)
 * - Authentication:  < 15ms (KV cache hit < 1ms, miss calls supplier)
 *
 * The Durable Objects handle per-player serialisation and atomic wallet
 * mutations. KV handles session caching and idempotency tombstones.
 * D1 (SQLite at the edge) stores the full transaction audit log.
 */

import { Hono } from "hono";
import type { Context, Next } from "hono";
import { z } from "zod";
import { zValidator } from "@hono/zod-validator";
import { cors } from "hono/cors";
import { logger } from "hono/logger";
import { timing } from "hono/timing";

import type { Env } from "./accounts-bridge";
import { AccountsBridge, WalletDurableObject } from "./accounts-bridge";
import { EvolutionProvider } from "./suppliers/evolution";
import { PragmaticProvider } from "./suppliers/pragmatic";
import { KambiProvider } from "./suppliers/kambi";
import type { AccountsProvider } from "./accounts-provider";
import {
  AuthenticationError,
  GameServiceError,
  InsufficientFundsError,
  TransactionBlockedError,
  UserLockedError,
} from "./transaction-result";

// Re-export DO for wrangler binding
export { WalletDurableObject };

// ---------------------------------------------------------------------------
// Hono app
// ---------------------------------------------------------------------------

const app = new Hono<{ Bindings: Env }>();

// Middleware
app.use("*", logger());
app.use("*", timing());
app.use("/api/*", async (c, next) => {
  // Origin allowlist is read from the environment per-request (see Hono's
  // docs for accessing c.env inside cors()). ALLOWED_ORIGINS unset means
  // no browser origin is allowed — fail closed, not "*".
  const allowlist = (c.env.ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean);
  const corsMiddleware = cors({
    // A non-matching, fixed placeholder — never equal to a real request
    // Origin — so the browser rejects the response instead of the server
    // reflecting whatever Origin the caller happened to send.
    origin: (origin) => (allowlist.includes(origin) ? origin : "https://origin-not-allowed.invalid"),
    allowMethods: ["GET", "POST"],
    allowHeaders: ["Content-Type", "Authorization", "X-Supplier-Timestamp", "X-Supplier-Signature"],
  });
  return corsMiddleware(c, next);
});

// ---------------------------------------------------------------------------
// Supplier request authentication
// ---------------------------------------------------------------------------
//
// Every route under /api/v1 either issues a session or moves money.
// Mirrors the Python GAL's verify_supplier_signature(): a per-supplier
// HMAC-SHA256 signature over "{timestamp}.{rawBody}", sent as the
// X-Supplier-Timestamp / X-Supplier-Signature headers. A supplier with
// no configured secret is rejected — there is no unauthenticated mode.

const MAX_REQUEST_AGE_S = 300;

function parseSupplierSecrets(raw: string | undefined): Record<string, string> {
  const secrets: Record<string, string> = {};
  for (const pair of (raw ?? "").split(",")) {
    const [supplierId, secret] = pair.split(":");
    if (supplierId && secret) secrets[supplierId.trim()] = secret.trim();
  }
  return secrets;
}

async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Constant-time string comparison — avoids leaking signature bytes via timing. */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

async function verifySupplierSignature(
  c: Context<{ Bindings: Env }>,
  next: Next,
): Promise<Response | void> {
  const rawBody = await c.req.raw.clone().text();
  let parsed: Record<string, unknown>;
  try {
    parsed = rawBody ? JSON.parse(rawBody) : {};
  } catch {
    return c.json({ error: "Malformed JSON body" }, 400);
  }

  const supplierId = typeof parsed.supplierId === "string" ? parsed.supplierId : "";
  const secrets = parseSupplierSecrets(c.env.SUPPLIER_CALLBACK_SECRETS);
  const secret = secrets[supplierId];
  if (!supplierId || !secret) {
    return c.json({ error: "Unknown or unconfigured supplier" }, 401);
  }

  const timestamp = c.req.header("X-Supplier-Timestamp") ?? "";
  const providedSig = c.req.header("X-Supplier-Signature") ?? "";
  if (!timestamp || !providedSig) {
    return c.json({ error: "Missing signature headers" }, 401);
  }

  const ts = Number(timestamp);
  if (!Number.isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > MAX_REQUEST_AGE_S) {
    return c.json({ error: "Invalid or stale timestamp" }, 401);
  }

  const expectedSig = await hmacSha256Hex(secret, `${timestamp}.${rawBody}`);
  if (!timingSafeEqual(providedSig, expectedSig)) {
    return c.json({ error: "Invalid signature" }, 401);
  }

  await next();
}

// ---------------------------------------------------------------------------
// Provider factory
// ---------------------------------------------------------------------------

function makeProviderFactory(env: Env): (supplierId: string) => AccountsProvider {
  const providers: Record<string, AccountsProvider> = {
    evolution: new EvolutionProvider(env.EVOLUTION_API_SECRET, ""),
    pragmatic: new PragmaticProvider(env.PRAGMATIC_SECRET_KEY, ""),
    kambi: new KambiProvider(env.KAMBI_OPERATOR_ID, "GB"),
  };

  return (supplierId: string) => {
    const p = providers[supplierId];
    if (!p) throw new GameServiceError(`Unknown supplier: ${supplierId}`);
    return p;
  };
}

// ---------------------------------------------------------------------------
// Request schemas (Zod)
// ---------------------------------------------------------------------------

const AuthSchema = z.object({
  token: z.string(),
  supplierId: z.string(),
});

const WalletSchema = z.object({
  playerId: z.string(),
  supplierId: z.string(),
  supplierRef: z.string(),
  roundId: z.string(),
  amount: z.string().regex(/^\d+$/, "Amount must be a non-negative integer string (minor units)"),
  currency: z.string().length(3),
  gameId: z.string(),
  sessionToken: z.string(),
});

const RefundSchema = z.object({
  playerId: z.string(),
  supplierId: z.string(),
  supplierRef: z.string(),
  roundId: z.string(),
  currency: z.string().length(3),
});

// ---------------------------------------------------------------------------
// Error handler
// ---------------------------------------------------------------------------

app.onError((err, c) => {
  if (err instanceof AuthenticationError) {
    return c.json({ error: err.message }, 401);
  }
  if (err instanceof InsufficientFundsError) {
    return c.json({ error: err.message }, 402);
  }
  if (err instanceof TransactionBlockedError || err instanceof UserLockedError) {
    return c.json({ error: err.message }, 403);
  }
  if (err instanceof GameServiceError) {
    return c.json({ error: err.message }, 500);
  }
  console.error("Unhandled error:", err);
  return c.json({ error: "Internal server error" }, 500);
});

// ---------------------------------------------------------------------------
// Health endpoints
// ---------------------------------------------------------------------------

app.get("/health", (c) => c.json({ status: "ok", timestamp: Date.now() }));

app.get("/ready", (c) => c.json({ status: "ready", timestamp: Date.now() }));

// ---------------------------------------------------------------------------
// Auth endpoints
// ---------------------------------------------------------------------------

app.post("/api/v1/auth", verifySupplierSignature, zValidator("json", AuthSchema), async (c) => {
  const { token, supplierId } = c.req.valid("json");
  const bridge = new AccountsBridge(makeProviderFactory(c.env), c.env);
  const session = await bridge.authenticate(token, supplierId);
  return c.json({
    playerId: session.playerId,
    currency: session.currency,
    gameId: session.gameId,
  });
});

// ---------------------------------------------------------------------------
// Wallet endpoints
// ---------------------------------------------------------------------------

app.post("/api/v1/wallet/balance", verifySupplierSignature, zValidator("json", WalletSchema.pick({ playerId: true, sessionToken: true, currency: true, gameId: true, supplierId: true, roundId: true }).partial({ roundId: true })), async (c) => {
  const { playerId, currency, gameId, sessionToken, supplierId } = c.req.valid("json");
  const bridge = new AccountsBridge(makeProviderFactory(c.env), c.env);
  const session = {
    playerId,
    brandId: "",
    externalId: playerId,
    currency,
    country: "",
    jurisdiction: "",
    sessionToken,
    gameId: gameId ?? "",
    mobile: false,
  };
  const balance = await bridge.getBalance(session, supplierId);
  return c.json({
    playerId,
    cashBalance: balance.cashBalance,
    bonusBalance: balance.bonusBalance,
    currency: balance.currency,
  });
});

app.post("/api/v1/wallet/debit", verifySupplierSignature, zValidator("json", WalletSchema), async (c) => {
  const body = c.req.valid("json");
  const bridge = new AccountsBridge(makeProviderFactory(c.env), c.env);
  const session = {
    playerId: body.playerId,
    brandId: "",
    externalId: body.playerId,
    currency: body.currency,
    country: "",
    jurisdiction: "",
    sessionToken: body.sessionToken,
    gameId: body.gameId,
    mobile: false,
  };
  const result = await bridge.debit(
    session,
    body.supplierId,
    body.supplierRef,
    body.roundId,
    body.amount,
  );
  return c.json(result);
});

app.post("/api/v1/wallet/credit", verifySupplierSignature, zValidator("json", WalletSchema), async (c) => {
  const body = c.req.valid("json");
  const bridge = new AccountsBridge(makeProviderFactory(c.env), c.env);
  const session = {
    playerId: body.playerId,
    brandId: "",
    externalId: body.playerId,
    currency: body.currency,
    country: "",
    jurisdiction: "",
    sessionToken: body.sessionToken,
    gameId: body.gameId,
    mobile: false,
  };
  const result = await bridge.credit(
    session,
    body.supplierId,
    body.supplierRef,
    body.roundId,
    body.amount,
  );
  return c.json(result);
});

app.post("/api/v1/wallet/refund", verifySupplierSignature, zValidator("json", RefundSchema), async (c) => {
  const { playerId, supplierId, supplierRef, currency } = c.req.valid("json");
  const bridge = new AccountsBridge(makeProviderFactory(c.env), c.env);
  const result = await bridge.refund(playerId, supplierId, supplierRef, currency);
  return c.json(result);
});

// ---------------------------------------------------------------------------
// Cloudflare Workers export
// ---------------------------------------------------------------------------

export default app;
