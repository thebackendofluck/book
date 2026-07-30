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
 * payments-worker/src/index.ts
 * ----------------------------
 * Hono-based Cloudflare Worker for iGaming payment processing.
 *
 * Endpoints:
 *   POST /api/v1/payments/deposit/intent      — Create deposit intent
 *   POST /api/v1/payments/withdrawal/request  — Submit withdrawal request
 *   POST /api/v1/payments/webhook             — PSP webhook inbound
 *   GET  /api/v1/payments/status/:id          — Payment status check
 *   GET  /health                              — Liveness probe
 *
 * Bindings (wrangler.toml):
 *   D1:  TRANSACTIONS   — Audit log of all payment events
 *   KV:  PSP_CONFIG     — PSP routing configuration (JSON)
 */

import { Hono } from "hono";
import { z } from "zod";
import { zValidator } from "@hono/zod-validator";
import { cors } from "hono/cors";
import { logger } from "hono/logger";

import {
  createPayment,
  transition,
  isTerminal,
  type Payment,
} from "./state-machine";
import { routePayment, loadPspConfig, type PaymentMethod } from "./psp-router";

// ---------------------------------------------------------------------------
// Environment bindings
// ---------------------------------------------------------------------------

export interface Env {
  TRANSACTIONS: D1Database;
  PSP_CONFIG: KVNamespace;
  ENVIRONMENT?: string;
  WEBHOOK_SECRET?: string;
}

// ---------------------------------------------------------------------------
// Zod schemas
// ---------------------------------------------------------------------------

const DepositIntentSchema = z.object({
  playerId: z.string().min(1),
  amount: z.number().int().positive(),           // minor units
  currency: z.string().length(3),
  method: z.enum(["card", "bank_transfer", "ewallet", "crypto"]),
  metadata: z.record(z.string()).optional(),
});

const WithdrawalRequestSchema = z.object({
  playerId: z.string().min(1),
  amount: z.number().int().positive(),
  currency: z.string().length(3),
  method: z.enum(["card", "bank_transfer", "ewallet", "crypto"]),
  accountRef: z.string().min(1),
  metadata: z.record(z.string()).optional(),
});

const WebhookSchema = z.object({
  paymentId: z.string(),
  status: z.enum(["COMPLETED", "FAILED", "CANCELLED"]),
  pspRef: z.string().optional(),
  failureReason: z.string().optional(),
});

// ---------------------------------------------------------------------------
// DB helpers
// ---------------------------------------------------------------------------

async function savePayment(db: D1Database, payment: Payment): Promise<void> {
  await db
    .prepare(
      `INSERT OR REPLACE INTO payments
         (id, player_id, type, status, amount, currency, psp_ref, psp_name,
          failure_reason, created_at, updated_at, metadata)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      payment.id,
      payment.playerId,
      payment.type,
      payment.status,
      payment.amount,
      payment.currency,
      payment.pspRef ?? null,
      payment.pspName ?? null,
      payment.failureReason ?? null,
      payment.createdAt,
      payment.updatedAt,
      JSON.stringify(payment.metadata ?? {}),
    )
    .run();
}

async function getPayment(
  db: D1Database,
  id: string,
): Promise<Payment | null> {
  const row = await db
    .prepare("SELECT * FROM payments WHERE id = ?")
    .bind(id)
    .first<Record<string, unknown>>();

  if (!row) return null;

  return {
    id: row.id as string,
    playerId: row.player_id as string,
    type: row.type as Payment["type"],
    status: row.status as Payment["status"],
    amount: row.amount as number,
    currency: row.currency as string,
    pspRef: (row.psp_ref as string | null) ?? undefined,
    pspName: (row.psp_name as string | null) ?? undefined,
    failureReason: (row.failure_reason as string | null) ?? undefined,
    createdAt: row.created_at as number,
    updatedAt: row.updated_at as number,
    metadata: JSON.parse((row.metadata as string) ?? "{}"),
  };
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

const app = new Hono<{ Bindings: Env }>();

app.use("*", logger());
app.use("/api/*", cors({ origin: "*", allowMethods: ["GET", "POST"] }));

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

app.get("/", (c) =>
  c.json({
    service: "payments",
    status: "operational",
    version: "1.0.0",
    platform: "AcmetoCasino",
    domain: "cloud-acmetocasino.com",
    endpoints: [
      "/health",
      "/api/v1/payments/deposit/intent",
      "/api/v1/payments/withdrawal/request",
      "/api/v1/payments/webhook",
      "/api/v1/payments/status/:id",
    ],
    documentation: "https://thebackendofluck.com",
  }),
);

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

app.get("/health", (c) =>
  c.json({
    status: "ok",
    service: "payments-worker",
    timestamp: new Date().toISOString(),
  }),
);

// ---------------------------------------------------------------------------
// Deposit intent
// ---------------------------------------------------------------------------

app.post(
  "/api/v1/payments/deposit/intent",
  zValidator("json", DepositIntentSchema),
  async (c) => {
    const body = c.req.valid("json");
    const pspConfig = await loadPspConfig(c.env.PSP_CONFIG);

    const payment = createPayment({
      id: `dep_${crypto.randomUUID()}`,
      playerId: body.playerId,
      type: "DEPOSIT",
      amount: body.amount,
      currency: body.currency,
      metadata: body.metadata,
    });

    // Transition to PROCESSING
    const processing = transition(payment, "PROCESSING");
    if (!processing.ok) {
      return c.json({ error: processing.error }, 422);
    }

    // Route to PSP
    const pspResult = await routePayment(
      body.method as PaymentMethod,
      { amount: body.amount, currency: body.currency, playerId: body.playerId },
      pspConfig,
    );

    // Final state based on PSP response
    const finalStatus = pspResult.ok ? "COMPLETED" : "FAILED";
    const completed = transition(processing.payment, finalStatus, {
      pspRef: pspResult.pspRef,
      failureReason: pspResult.errorMessage,
    });
    if (!completed.ok) return c.json({ error: completed.error }, 422);

    const final: Payment = {
      ...completed.payment,
      pspName: pspResult.pspName,
    };

    await savePayment(c.env.TRANSACTIONS, final);

    const httpStatus = pspResult.ok ? 201 : 402;
    return c.json(
      {
        id: final.id,
        status: final.status,
        pspRef: final.pspRef,
        pspName: final.pspName,
        amount: final.amount,
        currency: final.currency,
      },
      httpStatus,
    );
  },
);

// ---------------------------------------------------------------------------
// Withdrawal request
// ---------------------------------------------------------------------------

app.post(
  "/api/v1/payments/withdrawal/request",
  zValidator("json", WithdrawalRequestSchema),
  async (c) => {
    const body = c.req.valid("json");

    const payment = createPayment({
      id: `wdl_${crypto.randomUUID()}`,
      playerId: body.playerId,
      type: "WITHDRAWAL",
      amount: body.amount,
      currency: body.currency,
      metadata: { ...body.metadata, accountRef: body.accountRef },
    });

    // Withdrawals start PENDING and move to PROCESSING (async review in prod)
    const processing = transition(payment, "PROCESSING");
    if (!processing.ok) return c.json({ error: processing.error }, 422);

    await savePayment(c.env.TRANSACTIONS, processing.payment);

    return c.json(
      {
        id: processing.payment.id,
        status: processing.payment.status,
        amount: processing.payment.amount,
        currency: processing.payment.currency,
        message: "Withdrawal queued for processing",
      },
      202,
    );
  },
);

// ---------------------------------------------------------------------------
// PSP webhook
// ---------------------------------------------------------------------------

app.post(
  "/api/v1/payments/webhook",
  zValidator("json", WebhookSchema),
  async (c) => {
    const body = c.req.valid("json");

    const existing = await getPayment(c.env.TRANSACTIONS, body.paymentId);
    if (!existing) {
      return c.json({ error: "Payment not found" }, 404);
    }

    if (isTerminal(existing)) {
      // Idempotent: already in terminal state
      return c.json({ id: existing.id, status: existing.status });
    }

    const result = transition(existing, body.status, {
      pspRef: body.pspRef,
      failureReason: body.failureReason,
    });

    if (!result.ok) return c.json({ error: result.error }, 422);

    await savePayment(c.env.TRANSACTIONS, result.payment);

    return c.json({ id: result.payment.id, status: result.payment.status });
  },
);

// ---------------------------------------------------------------------------
// Status check
// ---------------------------------------------------------------------------

app.get("/api/v1/payments/status/:id", async (c) => {
  const id = c.req.param("id");
  const payment = await getPayment(c.env.TRANSACTIONS, id);

  if (!payment) return c.json({ error: "Payment not found" }, 404);

  return c.json({
    id: payment.id,
    playerId: payment.playerId,
    type: payment.type,
    status: payment.status,
    amount: payment.amount,
    currency: payment.currency,
    pspRef: payment.pspRef,
    pspName: payment.pspName,
    failureReason: payment.failureReason,
    createdAt: new Date(payment.createdAt).toISOString(),
    updatedAt: new Date(payment.updatedAt).toISOString(),
  });
});

// ---------------------------------------------------------------------------
// Error handler
// ---------------------------------------------------------------------------

app.onError((err, c) => {
  console.error("[payments-worker] unhandled error:", err);
  return c.json({ error: "Internal server error" }, 500);
});

export default app;
