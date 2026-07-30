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
 * accounts-provider.ts
 * ---------------------
 * TypeScript interface that every supplier wallet integration must implement.
 *
 * Mirrors the Python AccountsProvider Protocol. In TypeScript this is a
 * plain interface — no runtime reflection needed. Type-checking at compile
 * time ensures every supplier implements the full contract.
 *
 * Edge-specific notes
 * -------------------
 * - No Node.js APIs (Buffer, fs, etc.) — use Web Standard APIs only.
 * - fetch() is the standard HTTP client on Cloudflare Workers.
 * - crypto.subtle is available for HMAC verification.
 * - All async operations return Promises (no callbacks).
 */

import type { BalanceStatus, TransactionResult } from "./transaction-result";

// ---------------------------------------------------------------------------
// Player session
// ---------------------------------------------------------------------------

export interface PlayerSession {
  playerId: string;
  brandId: string;
  externalId: string;
  currency: string;
  country: string;
  jurisdiction: string;
  sessionToken: string;
  gameId: string;
  mobile: boolean;
  credentials?: string;
}

// ---------------------------------------------------------------------------
// Operation descriptors
// ---------------------------------------------------------------------------

export interface SupplierOperation {
  type: "debit" | "credit" | "refund" | "adjust" | "clawback";
  roundId: string;
}

export interface DebitOperation extends SupplierOperation {
  type: "debit";
  amount: string; // Minor units as string
  errorIfUsingBonus?: string;
  applyWagering?: boolean;
}

export interface CreditOperation extends SupplierOperation {
  type: "credit";
  amount: string;
  applyGeoverification?: boolean;
}

export interface RefundOperation extends SupplierOperation {
  type: "refund";
  originalTxId: string;
}

export interface AdjustOperation extends SupplierOperation {
  type: "adjust";
  newAmount: string;
  applyWagering?: boolean;
}

export interface ClawbackOperation extends SupplierOperation {
  type: "clawback";
  amount: string;
}

// ---------------------------------------------------------------------------
// Transaction context
// ---------------------------------------------------------------------------

export interface TransactionContext {
  txId: string;
  supplierRef: string;
  disallowLocked?: boolean;
  rejectIfRcElapsed?: boolean;
  offline?: boolean;
  allowRollbackWhenRoundComplete?: boolean;
  requireDebits?: boolean;
}

// ---------------------------------------------------------------------------
// AccountsProvider interface
// ---------------------------------------------------------------------------

export interface AccountsProvider {
  /**
   * Validate a game-launch token and return the player session.
   * Throws AuthenticationError / InvalidSessionError on failure.
   */
  authenticate(token: string): Promise<PlayerSession>;

  /**
   * Retrieve the player's current wallet balance.
   */
  getBalance(session: PlayerSession, gameId?: string): Promise<BalanceStatus>;

  /**
   * Deduct a stake from the player's wallet.
   */
  debit(
    session: PlayerSession,
    operation: DebitOperation,
    context: TransactionContext,
  ): Promise<TransactionResult>;

  /**
   * Add winnings to the player's wallet.
   */
  credit(
    session: PlayerSession,
    operation: CreditOperation,
    context: TransactionContext,
  ): Promise<TransactionResult>;

  /**
   * Reverse a previous debit (incomplete-round rollback).
   */
  refund(
    session: PlayerSession,
    operation: RefundOperation,
    context: TransactionContext,
  ): Promise<TransactionResult>;

  /**
   * Apply a composite transaction (multiple operations atomically).
   */
  applyTransaction(
    session: PlayerSession,
    operations: SupplierOperation[],
    context: TransactionContext,
  ): Promise<TransactionResult>;

  /**
   * Reverse a previously applied composite transaction.
   */
  reverseTransaction(
    session: PlayerSession,
    operations: SupplierOperation[],
    context: TransactionContext,
  ): Promise<TransactionResult>;
}
