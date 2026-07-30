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
 * suppliers/kambi.ts
 * -------------------
 * Kambi Sportsbook — fund/withdraw wallet integration (edge variant).
 *
 * Kambi's fund/withdraw model is particularly well-suited to edge deployment:
 * every bet placement requires a real-time debit, and the Durable Object
 * provides atomic serialisation without database round-trips.
 *
 * Edge performance target: < 10ms for fund/withdraw responses.
 */

import type { AccountsProvider, PlayerSession, SupplierOperation, TransactionContext } from "../accounts-provider";
import { DebitOperation, CreditOperation } from "../accounts-provider";
import { BalanceStatus, TransactionResult, TransactionType, successResult } from "../transaction-result";
import { AuthenticationError } from "../transaction-result";

export interface KambiFundRequest {
  customerPlayerId: string;
  kambiTransactionId: string;
  kambiTransactionType: string;
  playerSessionToken: string;
  productType: string;
  currencyCode: string;
  amount?: number;
}

export interface KambiWithdrawRequest extends KambiFundRequest {
  // Same shape, different semantics:
  // "Withdraw" from Kambi's perspective = credit to player wallet
}

export interface KambiWalletResponse {
  walletTransactionReference: string;
  success: boolean;
  balance?: number; // Major units
}

export class KambiProvider implements AccountsProvider {
  constructor(
    private readonly operatorId: string,
    private readonly marketId: string,
  ) {}

  async authenticate(token: string): Promise<PlayerSession> {
    /**
     * Kambi authentication response includes:
     * - playerSessionToken (echoed back)
     * - customerPlayerId (operator's player ID)
     * - currencyCode, countryCode, regulationId
     *
     * Return a PlayerSession from the token's decoded payload.
     */
    throw new AuthenticationError("Not implemented: validate Kambi session token");
  }

  async getBalance(session: PlayerSession): Promise<BalanceStatus> {
    return {
      cashBalance: "0",
      bonusBalance: "0",
      currency: session.currency || "GBP",
    };
  }

  /**
   * FUND = debit from player wallet (Kambi's terminology is inverted).
   * Called when a player places a bet.
   */
  async debit(
    session: PlayerSession,
    operation: DebitOperation,
    context: TransactionContext,
  ): Promise<TransactionResult> {
    return this.applyTransaction(session, [operation], context);
  }

  /**
   * WITHDRAW = credit to player wallet (Kambi's terminology is inverted).
   * Called when a bet settles.
   */
  async credit(
    session: PlayerSession,
    operation: CreditOperation,
    context: TransactionContext,
  ): Promise<TransactionResult> {
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
}
