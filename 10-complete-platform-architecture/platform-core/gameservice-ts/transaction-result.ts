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
 * transaction-result.ts
 * ----------------------
 * Canonical transaction response types for the edge GAL.
 *
 * Uses Zod for runtime validation (Pydantic equivalent for TypeScript).
 * All amounts are strings to avoid IEEE-754 float precision loss when
 * serialising to/from JSON — parse to BigInt or a decimal library before
 * arithmetic.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------------

export enum TransactionType {
  DEBIT = "DEBIT",
  CREDIT = "CREDIT",
  REFUND = "REFUND",
  BONUS = "BONUS",
  ADJUST = "ADJUST",
  CLAWBACK = "CLAWBACK",
}

export enum TransactionStatus {
  SUCCESS = "SUCCESS",
  FAILED = "FAILED",
  ALREADY_PROCESSED = "ALREADY_PROCESSED",
  ALREADY_REFUNDED = "ALREADY_REFUNDED",
  INVALID_OPERATION = "INVALID_OPERATION",
}

// ---------------------------------------------------------------------------
// Domain errors
// ---------------------------------------------------------------------------

export class GameServiceError extends Error {
  constructor(
    message: string,
    public readonly supplierCode?: string,
  ) {
    super(message);
    this.name = "GameServiceError";
  }
}

export class AuthenticationError extends GameServiceError {
  constructor(message: string) {
    super(message);
    this.name = "AuthenticationError";
  }
}

export class InvalidSessionError extends GameServiceError {
  constructor(message: string) {
    super(message);
    this.name = "InvalidSessionError";
  }
}

export class UserLockedError extends GameServiceError {
  constructor(message: string) {
    super(message);
    this.name = "UserLockedError";
  }
}

export class InsufficientFundsError extends GameServiceError {
  constructor(message: string) {
    super(message);
    this.name = "InsufficientFundsError";
  }
}

export class TransactionBlockedError extends GameServiceError {
  constructor(message: string) {
    super(message);
    this.name = "TransactionBlockedError";
  }
}

export class NoMatchingDebitError extends GameServiceError {
  constructor(message: string) {
    super(message);
    this.name = "NoMatchingDebitError";
  }
}

// ---------------------------------------------------------------------------
// Zod schemas
// ---------------------------------------------------------------------------

export const BalanceStatusSchema = z.object({
  /** Real-money balance in minor units (pence/cents) as string */
  cashBalance: z.string(),
  /** Bonus balance in minor units */
  bonusBalance: z.string(),
  currency: z.string().length(3).toUpperCase(),
});

export type BalanceStatus = z.infer<typeof BalanceStatusSchema>;

export const TransactionResultSchema = z.object({
  txId: z.string().uuid(),
  externalId: z.string().optional(),
  status: z.nativeEnum(TransactionStatus),
  txType: z.nativeEnum(TransactionType),
  balance: BalanceStatusSchema.optional(),
  cashUsage: z.string().default("0"),
  bonusUsage: z.string().default("0"),
  rcTimeElapsed: z.boolean().default(false),
  errorMessage: z.string().optional(),
  timestamp: z.string().datetime(),
});

export type TransactionResult = z.infer<typeof TransactionResultSchema>;

// ---------------------------------------------------------------------------
// Factory helpers
// ---------------------------------------------------------------------------

function nowIso(): string {
  return new Date().toISOString();
}

function newTxId(): string {
  return crypto.randomUUID();
}

export function successResult(
  txType: TransactionType,
  balance: BalanceStatus,
  options: {
    txId?: string;
    externalId?: string;
    cashUsage?: string;
    bonusUsage?: string;
    rcTimeElapsed?: boolean;
  } = {},
): TransactionResult {
  return {
    txId: options.txId ?? newTxId(),
    externalId: options.externalId,
    status: TransactionStatus.SUCCESS,
    txType,
    balance,
    cashUsage: options.cashUsage ?? "0",
    bonusUsage: options.bonusUsage ?? "0",
    rcTimeElapsed: options.rcTimeElapsed ?? false,
    timestamp: nowIso(),
  };
}

export function failureResult(
  txType: TransactionType,
  errorMessage: string,
  options: { txId?: string; balance?: BalanceStatus } = {},
): TransactionResult {
  return {
    txId: options.txId ?? newTxId(),
    status: TransactionStatus.FAILED,
    txType,
    balance: options.balance,
    cashUsage: "0",
    bonusUsage: "0",
    rcTimeElapsed: false,
    errorMessage,
    timestamp: nowIso(),
  };
}

export function alreadyProcessedResult(
  txType: TransactionType,
  txId: string,
  balance: BalanceStatus,
  refunded = false,
): TransactionResult {
  return {
    txId,
    status: refunded
      ? TransactionStatus.ALREADY_REFUNDED
      : TransactionStatus.ALREADY_PROCESSED,
    txType,
    balance,
    cashUsage: "0",
    bonusUsage: "0",
    rcTimeElapsed: false,
    timestamp: nowIso(),
  };
}
