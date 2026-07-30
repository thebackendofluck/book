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
 * suppliers/evolution.ts
 * -----------------------
 * Evolution Gaming — Live dealer seamless-wallet integration.
 *
 * Edge-specific notes
 * -------------------
 * - All crypto via Web Crypto API (crypto.subtle) — no Node.js crypto.
 * - HMAC verification uses subtle.verify() for constant-time comparison.
 * - fetch() for any outbound HTTP (rare for Evolution — mostly inbound).
 * - No heavy dependencies — entire file tree-shakes to < 5 KB.
 *
 * Quirks (same as Python version, listed for reference):
 * 1. Single callback may contain WITHDRAW + DEPOSIT.
 * 2. Idempotency via transaction.id.
 * 3. uuid must be echoed back in every response.
 * 4. Balance in major units (GBP, EUR — not pence/cents).
 * 5. Reality-check flag via retrasmission: true (note: supplier typo).
 */

import { z } from "zod";
import type { AccountsProvider, PlayerSession, SupplierOperation, TransactionContext } from "../accounts-provider";
import { DebitOperation, CreditOperation } from "../accounts-provider";
import {
  BalanceStatus,
  TransactionResult,
  TransactionType,
  successResult,
} from "../transaction-result";
import { AuthenticationError } from "../transaction-result";

// ---------------------------------------------------------------------------
// Protocol schemas
// ---------------------------------------------------------------------------

const EvoTransactionSchema = z.object({
  id: z.string(),
  refId: z.string(),
  amount: z.number(),
});

const EvoRequestSchema = z.object({
  authToken: z.string(),
  sid: z.string().optional(),
  playerId: z.string(),
  uuid: z.string(),
  currency: z.string().optional(),
  transaction: EvoTransactionSchema.optional(),
});

export type EvoRequest = z.infer<typeof EvoRequestSchema>;

export interface EvoResponse {
  status: string;
  balance?: number;
  bonus?: number;
  retrasmission?: boolean; // Evolution's typo — do not fix
  uuid: string;
}

// Status codes
export const EVO_OK = "OK";
export const EVO_INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS";
export const EVO_INVALID_TOKEN = "INVALID_TOKEN_ID";
export const EVO_ACCOUNT_LOCKED = "ACCOUNT_LOCKED";
export const EVO_TEMPORARY_ERROR = "TEMPORARY_ERROR";
export const EVO_BET_ALREADY_EXISTS = "BET_ALREADY_EXIST";

// ---------------------------------------------------------------------------
// HMAC helpers (Web Crypto API — Cloudflare Workers compatible)
// ---------------------------------------------------------------------------

async function importHmacKey(secret: string): Promise<CryptoKey> {
  const enc = new TextEncoder();
  return crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function signToken(payload: string, secret: string): Promise<string> {
  const key = await importHmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function verifyTokenSignature(
  payload: string,
  signature: string,
  secret: string,
): Promise<boolean> {
  const key = await importHmacKey(secret);
  const sigBytes = Uint8Array.from(signature.match(/.{2}/g)!.map((b) => parseInt(b, 16)));
  return crypto.subtle.verify("HMAC", key, sigBytes, new TextEncoder().encode(payload));
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class EvolutionProvider implements AccountsProvider {
  constructor(
    private readonly apiSecret: string,
    private readonly operatorId: string,
  ) {}

  async authenticate(token: string): Promise<PlayerSession> {
    /**
     * Validate Evolution's operator-signed auth token.
     * Token format: base64(payload).hmacSig
     */
    const parts = token.split(".");
    if (parts.length < 2) {
      throw new AuthenticationError("Evolution token format invalid");
    }
    const [payloadB64, sig] = [parts.slice(0, -1).join("."), parts[parts.length - 1]] as [string, string];
    const isValid = await verifyTokenSignature(payloadB64, sig, this.apiSecret);
    if (!isValid) {
      throw new AuthenticationError("Evolution token signature invalid");
    }

    const payload = atob(payloadB64);
    const [playerId, brandId, gameId, currency, country, jurisdiction] = payload.split(":") as [string, string, string, string, string, string];

    return {
      playerId,
      brandId,
      externalId: playerId,
      currency,
      country,
      jurisdiction,
      sessionToken: token,
      gameId,
      mobile: false,
    };
  }

  async getBalance(session: PlayerSession): Promise<BalanceStatus> {
    // In production: query the wallet Durable Object
    return { cashBalance: "0", bonusBalance: "0", currency: session.currency || "GBP" };
  }

  async debit(session: PlayerSession, operation: DebitOperation, context: TransactionContext): Promise<TransactionResult> {
    return this.applyTransaction(session, [operation], context);
  }

  async credit(session: PlayerSession, operation: CreditOperation, context: TransactionContext): Promise<TransactionResult> {
    return this.applyTransaction(session, [operation], context);
  }

  async refund(session: PlayerSession, operation: any, context: TransactionContext): Promise<TransactionResult> {
    return this.reverseTransaction(session, [operation], context);
  }

  async applyTransaction(
    session: PlayerSession,
    operations: SupplierOperation[],
    context: TransactionContext,
  ): Promise<TransactionResult> {
    const balance: BalanceStatus = {
      cashBalance: "0",
      bonusBalance: "0",
      currency: session.currency || "GBP",
    };
    return successResult(TransactionType.DEBIT, balance, {
      txId: context.txId,
      externalId: context.supplierRef,
    });
  }

  async reverseTransaction(
    session: PlayerSession,
    operations: SupplierOperation[],
    context: TransactionContext,
  ): Promise<TransactionResult> {
    const balance: BalanceStatus = {
      cashBalance: "0",
      bonusBalance: "0",
      currency: session.currency || "GBP",
    };
    return successResult(TransactionType.REFUND, balance, {
      txId: context.txId,
      externalId: context.supplierRef,
    });
  }

  /** Convert minor-unit balance to major units for Evolution responses. */
  toMajorUnits(minorUnits: bigint, decimals = 2): number {
    const divisor = BigInt(10 ** decimals);
    const whole = minorUnits / divisor;
    const frac = minorUnits % divisor;
    return Number(`${whole}.${frac.toString().padStart(decimals, "0")}`);
  }
}
