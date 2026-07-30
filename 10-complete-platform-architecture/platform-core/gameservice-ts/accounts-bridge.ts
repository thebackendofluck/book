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
 * accounts-bridge.ts
 * -------------------
 * Central transaction coordinator for the edge GAL (Cloudflare Workers).
 *
 * Edge architecture notes
 * -----------------------
 * On Cloudflare Workers, there are no shared in-process mutexes — each
 * Worker invocation is isolated. Per-player serialisation is achieved via
 * **Durable Objects**: one Durable Object per player_id. The DO's single-
 * threaded execution model guarantees that only one request runs at a time
 * for a given player, even across multiple Worker instances.
 *
 * Idempotency is stored in Workers KV (global, eventually-consistent) or
 * in the Durable Object's storage (strongly-consistent, preferred for
 * financial data).
 *
 * For balance checks, target < 10ms response time by:
 * 1. Caching the balance in KV with a 500ms TTL.
 * 2. Using colocated D1 (SQLite) for the transaction log.
 * 3. Keeping supplier HTTP calls out of the critical path where possible.
 */

import type { AccountsProvider, PlayerSession, SupplierOperation, TransactionContext } from "./accounts-provider";
import { DebitOperation, CreditOperation, RefundOperation } from "./accounts-provider";
import {
  BalanceStatus,
  GameServiceError,
  InsufficientFundsError,
  InvalidSessionError,
  TransactionResult,
  TransactionStatus,
  TransactionType,
  alreadyProcessedResult,
  failureResult,
  successResult,
} from "./transaction-result";

// ---------------------------------------------------------------------------
// Durable Object wallet state (per-player)
// ---------------------------------------------------------------------------

/**
 * WalletDurableObject
 *
 * Each player has one Durable Object. All wallet mutations go through the DO
 * to ensure atomic, serialised updates. The DO stores the transaction log
 * locally (DO storage) and writes to D1 asynchronously for analytics.
 *
 * Response time target: < 5ms for balance reads, < 15ms for transactions.
 */
export class WalletDurableObject {
  private state: DurableObjectState;
  private env: Env;

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    try {
      if (request.method === "GET" && path === "/balance") {
        return this.handleGetBalance();
      }
      if (request.method === "POST" && path === "/debit") {
        const body = await request.json() as DebitRequest;
        return this.handleDebit(body);
      }
      if (request.method === "POST" && path === "/credit") {
        const body = await request.json() as CreditRequest;
        return this.handleCredit(body);
      }
      if (request.method === "POST" && path === "/refund") {
        const body = await request.json() as RefundRequest;
        return this.handleRefund(body);
      }
      return new Response("Not Found", { status: 404 });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return Response.json({ error: message }, { status: 500 });
    }
  }

  private async handleGetBalance(): Promise<Response> {
    const cashBalance = (await this.state.storage.get<string>("cashBalance")) ?? "0";
    const bonusBalance = (await this.state.storage.get<string>("bonusBalance")) ?? "0";
    const currency = (await this.state.storage.get<string>("currency")) ?? "GBP";
    return Response.json({ cashBalance, bonusBalance, currency });
  }

  private async handleDebit(req: DebitRequest): Promise<Response> {
    // Check idempotency
    const existingTx = await this.state.storage.get<TransactionResult>(`tx:${req.supplierRef}`);
    if (existingTx) {
      if (existingTx.status === TransactionStatus.SUCCESS) {
        return Response.json({ ...existingTx, status: TransactionStatus.ALREADY_PROCESSED });
      }
    }

    const cashBalance = BigInt((await this.state.storage.get<string>("cashBalance")) ?? "0");
    const bonusBalance = BigInt((await this.state.storage.get<string>("bonusBalance")) ?? "0");
    const amount = BigInt(req.amount);
    const totalBalance = cashBalance + bonusBalance;

    if (amount > totalBalance) {
      throw new InsufficientFundsError(`Insufficient funds: need ${amount}, have ${totalBalance}`);
    }

    // Deduct from cash first, then bonus
    const cashUsed = cashBalance >= amount ? amount : cashBalance;
    const bonusUsed = amount - cashUsed;
    const newCash = cashBalance - cashUsed;
    const newBonus = bonusBalance - bonusUsed;

    // Atomic write using DO's transactional storage
    await this.state.storage.transaction(async () => {
      await this.state.storage.put("cashBalance", newCash.toString());
      await this.state.storage.put("bonusBalance", newBonus.toString());
    });

    const balance: BalanceStatus = {
      cashBalance: newCash.toString(),
      bonusBalance: newBonus.toString(),
      currency: req.currency,
    };

    const result = successResult(TransactionType.DEBIT, balance, {
      txId: req.txId,
      externalId: req.supplierRef,
      cashUsage: cashUsed.toString(),
      bonusUsage: bonusUsed.toString(),
    });

    await this.state.storage.put(`tx:${req.supplierRef}`, result);
    return Response.json(result);
  }

  private async handleCredit(req: CreditRequest): Promise<Response> {
    const existingTx = await this.state.storage.get<TransactionResult>(`tx:${req.supplierRef}`);
    if (existingTx?.status === TransactionStatus.SUCCESS) {
      return Response.json({ ...existingTx, status: TransactionStatus.ALREADY_PROCESSED });
    }

    const cashBalance = BigInt((await this.state.storage.get<string>("cashBalance")) ?? "0");
    const bonusBalance = BigInt((await this.state.storage.get<string>("bonusBalance")) ?? "0");
    const amount = BigInt(req.amount);
    const newCash = cashBalance + amount;

    await this.state.storage.transaction(async () => {
      await this.state.storage.put("cashBalance", newCash.toString());
    });

    const balance: BalanceStatus = {
      cashBalance: newCash.toString(),
      bonusBalance: bonusBalance.toString(),
      currency: req.currency,
    };

    const result = successResult(TransactionType.CREDIT, balance, {
      txId: req.txId,
      externalId: req.supplierRef,
      cashUsage: amount.toString(),
    });

    await this.state.storage.put(`tx:${req.supplierRef}`, result);
    return Response.json(result);
  }

  private async handleRefund(req: RefundRequest): Promise<Response> {
    const originalKey = `tx:${req.originalSupplierRef}`;
    const original = await this.state.storage.get<TransactionResult>(originalKey);

    if (!original) {
      return Response.json(
        { error: `Original transaction not found: ${req.originalSupplierRef}` },
        { status: 404 },
      );
    }

    if (original.status === TransactionStatus.ALREADY_REFUNDED) {
      return Response.json({ ...original, status: TransactionStatus.ALREADY_REFUNDED });
    }

    const cashBalance = BigInt((await this.state.storage.get<string>("cashBalance")) ?? "0");
    const bonusBalance = BigInt((await this.state.storage.get<string>("bonusBalance")) ?? "0");
    const cashRefund = BigInt(original.cashUsage ?? "0");
    const bonusRefund = BigInt(original.bonusUsage ?? "0");

    await this.state.storage.transaction(async () => {
      await this.state.storage.put("cashBalance", (cashBalance + cashRefund).toString());
      await this.state.storage.put("bonusBalance", (bonusBalance + bonusRefund).toString());
      await this.state.storage.put(originalKey, { ...original, status: TransactionStatus.ALREADY_REFUNDED });
    });

    const balance: BalanceStatus = {
      cashBalance: (cashBalance + cashRefund).toString(),
      bonusBalance: (bonusBalance + bonusRefund).toString(),
      currency: req.currency,
    };

    const result = successResult(TransactionType.REFUND, balance, {
      txId: req.txId,
      externalId: req.originalSupplierRef,
      cashUsage: cashRefund.toString(),
      bonusUsage: bonusRefund.toString(),
    });

    await this.state.storage.put(`tx:${req.txId}`, result);
    return Response.json(result);
  }
}

// ---------------------------------------------------------------------------
// Request types for Durable Object messages
// ---------------------------------------------------------------------------

interface DebitRequest {
  txId: string;
  supplierRef: string;
  amount: string;
  currency: string;
  roundId: string;
}

interface CreditRequest {
  txId: string;
  supplierRef: string;
  amount: string;
  currency: string;
  roundId: string;
}

interface RefundRequest {
  txId: string;
  originalSupplierRef: string;
  currency: string;
}

// ---------------------------------------------------------------------------
// Environment bindings (from wrangler.toml)
// ---------------------------------------------------------------------------

export interface Env {
  WALLET: DurableObjectNamespace;
  SESSION_CACHE: KVNamespace;
  TRANSACTION_LOG: D1Database;
  EVOLUTION_API_SECRET: string;
  PRAGMATIC_SECRET_KEY: string;
  KAMBI_OPERATOR_ID: string;
  /** Comma-separated browser-origin allowlist for CORS. Unset = no browser origin is allowed. */
  ALLOWED_ORIGINS?: string;
  /** Comma-separated per-supplier HMAC secrets for authenticating inbound API requests, "supplierId:secret" pairs. */
  SUPPLIER_CALLBACK_SECRETS?: string;
}

// ---------------------------------------------------------------------------
// AccountsBridge — edge variant
// ---------------------------------------------------------------------------

export class AccountsBridge {
  constructor(
    private readonly providerFactory: (supplierId: string) => AccountsProvider,
    private readonly env: Env,
  ) {}

  /**
   * Validate a game-launch token.
   * Result cached in KV for 60 seconds to avoid repeated auth calls.
   */
  async authenticate(token: string, supplierId: string): Promise<PlayerSession> {
    // Check session cache first (KV read < 1ms from edge)
    const cacheKey = `session:${supplierId}:${hashString(token)}`;
    const cached = await this.env.SESSION_CACHE.get<PlayerSession>(cacheKey, "json");
    if (cached) return cached;

    const provider = this.providerFactory(supplierId);
    const session = await provider.authenticate(token);

    // Cache for 60 seconds
    await this.env.SESSION_CACHE.put(cacheKey, JSON.stringify(session), { expirationTtl: 60 });
    return session;
  }

  /**
   * Re-validate the caller-supplied sessionToken before any wallet
   * mutation or balance read.
   *
   * Route handlers build a PlayerSession straight from the request body
   * (see index.ts), which is untrusted input. Without this check, any
   * caller who already knows a playerId could move money for that
   * player by sending an arbitrary sessionToken — nothing downstream
   * ever verified it belongs to that player.
   *
   * sessionToken is the same launch token the supplier already re-sends
   * on every wallet callback, so re-authenticating here reuses the
   * supplier's own token verification (e.g. EvolutionProvider.authenticate,
   * which HMAC-verifies the token) rather than a parallel session store.
   */
  private async authorizeSession(
    provider: AccountsProvider,
    session: PlayerSession,
  ): Promise<void> {
    if (!session.sessionToken) {
      throw new InvalidSessionError("Missing sessionToken");
    }
    let authenticated: PlayerSession;
    try {
      authenticated = await provider.authenticate(session.sessionToken);
    } catch (err) {
      if (err instanceof GameServiceError) throw err;
      const message = err instanceof Error ? err.message : String(err);
      throw new InvalidSessionError(`Session validation failed: ${message}`);
    }
    if (authenticated.playerId !== session.playerId) {
      throw new InvalidSessionError("sessionToken does not match playerId");
    }
  }

  /**
   * Get balance via the player's Durable Object.
   * Target: < 10ms (DO is colocated with the player's data).
   */
  async getBalance(session: PlayerSession, supplierId: string): Promise<BalanceStatus> {
    await this.authorizeSession(this.providerFactory(supplierId), session);
    const doId = this.env.WALLET.idFromName(session.playerId);
    const stub = this.env.WALLET.get(doId);
    const response = await stub.fetch("https://do/balance");
    if (!response.ok) {
      throw new GameServiceError("Failed to retrieve balance");
    }
    return response.json<BalanceStatus>();
  }

  /**
   * Debit via the player's Durable Object (atomic, serialised).
   */
  async debit(
    session: PlayerSession,
    supplierId: string,
    supplierRef: string,
    roundId: string,
    amount: string,
  ): Promise<TransactionResult> {
    await this.authorizeSession(this.providerFactory(supplierId), session);
    const txId = crypto.randomUUID();
    const doId = this.env.WALLET.idFromName(session.playerId);
    const stub = this.env.WALLET.get(doId);

    const body: DebitRequest = { txId, supplierRef, amount, currency: session.currency, roundId };
    const response = await stub.fetch("https://do/debit", {
      method: "POST",
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const err = await response.json<{ error: string }>();
      throw new GameServiceError(err.error);
    }
    return response.json<TransactionResult>();
  }

  /**
   * Credit via the player's Durable Object.
   */
  async credit(
    session: PlayerSession,
    supplierId: string,
    supplierRef: string,
    roundId: string,
    amount: string,
  ): Promise<TransactionResult> {
    await this.authorizeSession(this.providerFactory(supplierId), session);
    const txId = crypto.randomUUID();
    const doId = this.env.WALLET.idFromName(session.playerId);
    const stub = this.env.WALLET.get(doId);

    const body: CreditRequest = { txId, supplierRef, amount, currency: session.currency, roundId };
    const response = await stub.fetch("https://do/credit", {
      method: "POST",
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const err = await response.json<{ error: string }>();
      throw new GameServiceError(err.error);
    }
    return response.json<TransactionResult>();
  }

  /**
   * Refund via the player's Durable Object.
   */
  async refund(
    playerId: string,
    supplierId: string,
    originalSupplierRef: string,
    currency: string,
  ): Promise<TransactionResult> {
    const txId = crypto.randomUUID();
    const doId = this.env.WALLET.idFromName(playerId);
    const stub = this.env.WALLET.get(doId);

    const body: RefundRequest = { txId, originalSupplierRef, currency };
    const response = await stub.fetch("https://do/refund", {
      method: "POST",
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const err = await response.json<{ error: string }>();
      throw new GameServiceError(err.error);
    }
    return response.json<TransactionResult>();
  }
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

/** Fast non-cryptographic string hash for cache keys. */
function hashString(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(16);
}
