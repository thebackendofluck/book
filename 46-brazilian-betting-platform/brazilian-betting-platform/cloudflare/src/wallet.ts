// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * WalletBalance Durable Object
 *
 * Provides atomic, strongly consistent wallet operations for a single player.
 * One Durable Object instance per player ID.
 *
 * All monetary amounts are stored and transmitted as integer centavos (BRL)
 * to avoid floating-point precision errors.
 *
 * Operations:
 *  - deposit:  credit wallet (from confirmed PIX)
 *  - withdraw: debit wallet (PIX out to player bank account)
 *  - debit:    reserve funds for a placed bet
 *  - credit:   return funds on bet refund
 *  - win:      credit winnings on settled bet
 *
 * Closed payment loop enforcement:
 *  - Regulation (Portaria SPA/MF 615/2024) requires withdrawals to return
 *    to an account held in the bettor's own name (same CPF), not literally
 *    the single first-deposit key. This DO enforces the stricter operator
 *    policy of paying back to any PIX key the player has deposited from;
 *    same-CPF verification across keys is done upstream at KYC time.
 *  - Bonus funds cannot be withdrawn directly (must be wagered first).
 *
 * Access control:
 *  - Every request to the DO fetch dispatcher must carry a valid internal
 *    HMAC-SHA256 signature (see requireInternalAuth). The DO is a financial
 *    primitive and must never be reachable unauthenticated.
 *
 * Transaction history is written to D1 for audit and SIGAP reporting.
 * The Durable Object holds the canonical balance in durable storage;
 * D1 is the write-ahead log.
 */

import type { WalletOperation, WalletState, WalletTransaction } from './types.js';

export default {};

// ── Constants ────────────────────────────────────────────────────────────────

// Operator risk-policy limits (NOT statutory: Lei 14.790/SIGAP prescribe no
// universal fixed thresholds). Tune per licence and AML posture.
const MAX_BALANCE_CENTAVOS   = 500_000_00; // R$ 500,000.00 wallet cap (operator policy)
const MIN_WITHDRAW_CENTAVOS  =     10_00;  // R$ 10.00 (operator policy)
const MAX_WITHDRAW_CENTAVOS  = 50_000_00;  // R$ 50,000.00 per transaction (operator policy)

// Maximum allowed clock skew for a signed internal request (replay window).
const AUTH_MAX_SKEW_SECONDS  = 300;        // 5 minutes

// ── Durable Object class ──────────────────────────────────────────────────────

export class WalletBalance {
  private state: DurableObjectState;
  private env:   WalletEnv;

  constructor(state: DurableObjectState, env: WalletEnv) {
    this.state = state;
    this.env   = env;
  }

  async fetch(request: Request): Promise<Response> {
    // Buffer the body once: it is consumed for HMAC validation and then
    // handed to the downstream handlers via a re-created Request.
    const rawBody = await request.arrayBuffer();

    // ── Internal authentication gate ─────────────────────────────────────────
    const auth = await requireInternalAuth(
      request,
      rawBody,
      this.env.WALLET_INTERNAL_HMAC_SECRET
    );
    if (!auth.ok) {
      return jsonError(auth.error, auth.status);
    }

    // Replay protection: reject a nonce already seen within the skew window.
    // Combined with per-reference idempotency, a captured signed mutation
    // cannot be re-applied. Old nonces are swept opportunistically below.
    const nonce = request.headers.get('X-Wallet-Nonce') ?? '';
    if (request.method.toUpperCase() !== 'GET') {
      const nonceKey = `nonce:${nonce}`;
      if (await this.state.storage.get(nonceKey)) {
        return jsonError('Requisição repetida (nonce já utilizado).', 401);
      }
      await this.state.storage.put(nonceKey, Math.floor(Date.now() / 1000));
      await this.sweepExpiredNonces();
    }

    const url    = new URL(request.url);
    const method = request.method.toUpperCase();

    // Re-create the request with the buffered body so downstream handlers can
    // consume it (the original body stream was drained for HMAC validation).
    const authedRequest =
      method === 'GET' || method === 'HEAD'
        ? request
        : new Request(request.url, {
            method,
            headers: request.headers,
            body:    rawBody,
          });

    switch (url.pathname) {
      case '/balance':
        return this.handleBalance();
      case '/deposit':
        return this.handleDeposit(authedRequest);
      case '/debit':
        return this.handleDebit(authedRequest);
      case '/credit':
        return this.handleCredit(authedRequest);
      case '/withdraw':
        return this.handleWithdraw(authedRequest);
      case '/withdraw/settle':
        return this.handleWithdrawSettle(authedRequest);
      case '/withdraw/cancel':
        return this.handleWithdrawCancel(authedRequest);
      case '/win':
        return this.handleWin(authedRequest);
      case '/history':
        return this.handleHistory(authedRequest);
      default:
        return new Response('Not found', { status: 404 });
    }
  }

  // ── Balance ────────────────────────────────────────────────────────────────

  private async handleBalance(): Promise<Response> {
    const wallet = await this.getOrCreateWallet();
    return jsonSuccess({
      balanceBRL:    wallet.balanceCentavos / 100,
      balanceCentavos: wallet.balanceCentavos,
      reservedBRL:   wallet.reservedCentavos / 100,
      availableBRL:  (wallet.balanceCentavos - wallet.reservedCentavos) / 100,
    });
  }

  // ── Deposit (PIX confirmed) ────────────────────────────────────────────────

  private async handleDeposit(request: Request): Promise<Response> {
    const body = await parseJSON<{
      amountCentavos: number;
      reference: string;
      pixKey?: string;
      operation?: WalletOperation;
    }>(request);

    if (!body || body.amountCentavos <= 0) {
      return jsonError('amountCentavos inválido.', 400);
    }

    if (!body.reference) {
      return jsonError('reference é obrigatório.', 400);
    }

    return this.state.storage.transaction(async (txn) => {
      const wallet = await this.getOrCreateWalletInTxn(txn);

      // Check for duplicate reference (idempotency)
      const dupKey = `txn_ref:${body.reference}`;
      const isDup  = await txn.get<boolean>(dupKey);
      if (isDup) {
        return jsonSuccess({ idempotent: true, balanceCentavos: wallet.balanceCentavos });
      }

      const newBalance = wallet.balanceCentavos + body.amountCentavos;
      if (newBalance > MAX_BALANCE_CENTAVOS) {
        return jsonError(
          `Saldo máximo permitido excedido (R$ ${MAX_BALANCE_CENTAVOS / 100}.`,
          422
        );
      }

      wallet.balanceCentavos = newBalance;
      wallet.updatedAt       = new Date().toISOString();

      await txn.put('wallet', wallet);
      await txn.put(dupKey, true);

      // Closed-loop enforcement: remember every PIX key the player has
      // deposited from. Withdrawals may target any of them (all belong to the
      // same CPF, verified upstream at KYC); see handleWithdraw.
      if (body.pixKey) {
        const keys = (await txn.get<string[]>('registered_pix_keys')) ?? [];
        if (!keys.includes(body.pixKey)) {
          keys.push(body.pixKey);
          await txn.put('registered_pix_keys', keys);
        }
      }

      const tx = this.buildTransaction(
        wallet.playerId,
        'deposit',
        body.amountCentavos,
        newBalance,
        body.reference
      );
      await this.persistTransaction(tx);

      return jsonSuccess({
        transactionId:   tx.id,
        balanceCentavos: wallet.balanceCentavos,
        balanceBRL:      wallet.balanceCentavos / 100,
      }, 201);
    });
  }

  // ── Debit (bet placement) ─────────────────────────────────────────────────

  private async handleDebit(request: Request): Promise<Response> {
    const body = await parseJSON<{ amountCentavos: number; reference: string }>(request);

    if (!body || body.amountCentavos <= 0) {
      return jsonError('amountCentavos inválido.', 400);
    }

    if (!body.reference) {
      return jsonError('reference é obrigatório.', 400);
    }

    return this.state.storage.transaction(async (txn) => {
      const wallet    = await this.getOrCreateWalletInTxn(txn);

      // Idempotency: a retried/replayed bet must not debit twice.
      const dupKey = `txn_ref:${body.reference}`;
      if (await txn.get<boolean>(dupKey)) {
        return jsonSuccess({ idempotent: true, balanceCentavos: wallet.balanceCentavos });
      }

      const available = wallet.balanceCentavos - wallet.reservedCentavos;

      if (available < body.amountCentavos) {
        return jsonError('Saldo insuficiente.', 422);
      }

      wallet.balanceCentavos  -= body.amountCentavos;
      wallet.updatedAt         = new Date().toISOString();

      await txn.put('wallet', wallet);
      await txn.put(dupKey, true);

      const tx = this.buildTransaction(
        wallet.playerId, 'bet', body.amountCentavos, wallet.balanceCentavos, body.reference
      );
      await this.persistTransaction(tx);

      return jsonSuccess({ transactionId: tx.id, balanceCentavos: wallet.balanceCentavos });
    });
  }

  // ── Credit (bet refund) ───────────────────────────────────────────────────

  private async handleCredit(request: Request): Promise<Response> {
    const body = await parseJSON<{ amountCentavos: number; reference: string }>(request);

    if (!body || body.amountCentavos <= 0) {
      return jsonError('amountCentavos inválido.', 400);
    }

    return this.state.storage.transaction(async (txn) => {
      const wallet = await this.getOrCreateWalletInTxn(txn);

      // Check for duplicate reference (idempotency)
      const dupKey = `txn_ref:${body.reference}`;
      const isDup  = await txn.get<boolean>(dupKey);
      if (isDup) {
        return jsonSuccess({ idempotent: true, balanceCentavos: wallet.balanceCentavos });
      }

      if (wallet.balanceCentavos + body.amountCentavos > MAX_BALANCE_CENTAVOS) {
        return jsonError(`Saldo máximo permitido excedido (R$ ${MAX_BALANCE_CENTAVOS / 100}).`, 422);
      }

      wallet.balanceCentavos += body.amountCentavos;
      wallet.updatedAt        = new Date().toISOString();

      await txn.put('wallet', wallet);
      await txn.put(dupKey, true);

      const tx = this.buildTransaction(
        wallet.playerId, 'refund', body.amountCentavos, wallet.balanceCentavos, body.reference
      );
      await this.persistTransaction(tx);

      return jsonSuccess({ transactionId: tx.id, balanceCentavos: wallet.balanceCentavos });
    });
  }

  // ── Win (bet settled — player wins) ───────────────────────────────────────

  private async handleWin(request: Request): Promise<Response> {
    const body = await parseJSON<{ amountCentavos: number; reference: string }>(request);

    if (!body || body.amountCentavos <= 0) {
      return jsonError('amountCentavos inválido.', 400);
    }

    return this.state.storage.transaction(async (txn) => {
      const wallet = await this.getOrCreateWalletInTxn(txn);

      // Idempotency
      const dupKey = `txn_ref:${body.reference}`;
      const isDup  = await txn.get<boolean>(dupKey);
      if (isDup) {
        return jsonSuccess({ idempotent: true, balanceCentavos: wallet.balanceCentavos });
      }

      if (wallet.balanceCentavos + body.amountCentavos > MAX_BALANCE_CENTAVOS) {
        return jsonError(`Saldo máximo permitido excedido (R$ ${MAX_BALANCE_CENTAVOS / 100}).`, 422);
      }

      wallet.balanceCentavos += body.amountCentavos;
      wallet.updatedAt        = new Date().toISOString();

      await txn.put('wallet', wallet);
      await txn.put(dupKey, true);

      const tx = this.buildTransaction(
        wallet.playerId, 'win', body.amountCentavos, wallet.balanceCentavos, body.reference
      );
      await this.persistTransaction(tx);

      return jsonSuccess({
        transactionId:   tx.id,
        balanceCentavos: wallet.balanceCentavos,
        balanceBRL:      wallet.balanceCentavos / 100,
      });
    });
  }

  // ── Withdraw (PIX out) ────────────────────────────────────────────────────

  private async handleWithdraw(request: Request): Promise<Response> {
    const body = await parseJSON<{
      amountCentavos: number;
      reference: string;
      pixKey?: string;
    }>(request);

    if (!body || body.amountCentavos <= 0) {
      return jsonError('amountCentavos inválido.', 400);
    }

    if (body.amountCentavos < MIN_WITHDRAW_CENTAVOS) {
      return jsonError(`Saque mínimo é R$ ${MIN_WITHDRAW_CENTAVOS / 100}.`, 422);
    }

    if (body.amountCentavos > MAX_WITHDRAW_CENTAVOS) {
      return jsonError(`Saque máximo por transação é R$ ${MAX_WITHDRAW_CENTAVOS / 100}.`, 422);
    }

    if (!body.reference) {
      return jsonError('reference é obrigatório.', 400);
    }

    return this.state.storage.transaction(async (txn) => {
      const wallet    = await this.getOrCreateWalletInTxn(txn);

      // Idempotency: a retried withdraw must not reserve funds or call the PSP
      // twice for the same reference.
      const dupKey = `txn_ref:${body.reference}`;
      if (await txn.get<boolean>(dupKey)) {
        return jsonSuccess({ idempotent: true, status: 'pending', balanceCentavos: wallet.balanceCentavos });
      }

      // Payout destination must be one of the player's own registered PIX keys
      // (same CPF, verified upstream). This is the operator's closed-loop
      // policy; the statutory rule is same-owner, not same-key.
      const registeredKeys = (await txn.get<string[]>('registered_pix_keys')) ?? [];
      if (!body.pixKey || !registeredKeys.includes(body.pixKey)) {
        return jsonError('PIX key não corresponde a nenhuma chave de depósito cadastrada.', 422);
      }

      const available = wallet.balanceCentavos - wallet.reservedCentavos;

      if (available < body.amountCentavos) {
        return jsonError('Saldo insuficiente para saque.', 422);
      }

      // Reserve the funds while the PSP processes the payout.
      wallet.reservedCentavos += body.amountCentavos;
      wallet.updatedAt         = new Date().toISOString();

      await txn.put('wallet', wallet);
      await txn.put(dupKey, true);

      // Store pending withdrawal for the PSP settlement callback (see
      // handleWithdrawSettle / handleWithdrawCancel).
      const withdrawalKey = `pending_withdrawal:${body.reference}`;
      await txn.put(withdrawalKey, {
        amountCentavos: body.amountCentavos,
        pixKey:         body.pixKey,
        status:         'pending',
        createdAt:      new Date().toISOString(),
      });

      const tx = this.buildTransaction(
        wallet.playerId, 'withdraw', body.amountCentavos, wallet.balanceCentavos, body.reference
      );
      await this.persistTransaction(tx);

      // Initiate the PSP payout (fire and forget — settled via webhook, which
      // then calls /withdraw/settle or /withdraw/cancel on this DO).
      await this.initiatePixWithdrawal(body.amountCentavos, body.pixKey, body.reference);

      return jsonSuccess({
        transactionId:   tx.id,
        status:          'pending',
        balanceCentavos: wallet.balanceCentavos,
        reservedBRL:     wallet.reservedCentavos / 100,
      });
    });
  }

  // ── Withdraw settlement (PSP confirmed the payout left the account) ────────

  private async handleWithdrawSettle(request: Request): Promise<Response> {
    const body = await parseJSON<{ reference: string }>(request);
    if (!body || !body.reference) {
      return jsonError('reference é obrigatório.', 400);
    }

    return this.state.storage.transaction(async (txn) => {
      const withdrawalKey = `pending_withdrawal:${body.reference}`;
      const pending = await txn.get<{ amountCentavos: number; status: string }>(withdrawalKey);
      if (!pending || pending.status !== 'pending') {
        // Idempotent: already settled/cancelled or unknown.
        const w = await this.getOrCreateWalletInTxn(txn);
        return jsonSuccess({ idempotent: true, balanceCentavos: w.balanceCentavos });
      }

      const wallet = await this.getOrCreateWalletInTxn(txn);
      // Money actually left: drop it from both the reserve and the balance.
      wallet.reservedCentavos -= pending.amountCentavos;
      wallet.balanceCentavos  -= pending.amountCentavos;
      wallet.updatedAt         = new Date().toISOString();
      await txn.put('wallet', wallet);
      await txn.put(withdrawalKey, { ...pending, status: 'settled', settledAt: new Date().toISOString() });

      return jsonSuccess({ status: 'settled', balanceCentavos: wallet.balanceCentavos });
    });
  }

  // ── Withdraw cancel (PSP rejected/failed the payout) ──────────────────────

  private async handleWithdrawCancel(request: Request): Promise<Response> {
    const body = await parseJSON<{ reference: string }>(request);
    if (!body || !body.reference) {
      return jsonError('reference é obrigatório.', 400);
    }

    return this.state.storage.transaction(async (txn) => {
      const withdrawalKey = `pending_withdrawal:${body.reference}`;
      const pending = await txn.get<{ amountCentavos: number; status: string }>(withdrawalKey);
      if (!pending || pending.status !== 'pending') {
        const w = await this.getOrCreateWalletInTxn(txn);
        return jsonSuccess({ idempotent: true, balanceCentavos: w.balanceCentavos });
      }

      const wallet = await this.getOrCreateWalletInTxn(txn);
      // Payout never happened: release the reserve, balance is untouched.
      wallet.reservedCentavos -= pending.amountCentavos;
      wallet.updatedAt         = new Date().toISOString();
      await txn.put('wallet', wallet);
      await txn.put(withdrawalKey, { ...pending, status: 'cancelled', cancelledAt: new Date().toISOString() });

      return jsonSuccess({ status: 'cancelled', balanceCentavos: wallet.balanceCentavos });
    });
  }

  // ── Transaction history ───────────────────────────────────────────────────

  private async handleHistory(request: Request): Promise<Response> {
    const url    = new URL(request.url);
    const limit  = Math.min(parseInt(url.searchParams.get('limit') ?? '20', 10), 100);
    const offset = parseInt(url.searchParams.get('offset') ?? '0', 10);

    const wallet = await this.getOrCreateWallet();

    const { results } = await this.env.DB.prepare(
      `SELECT id, operation, amount_centavos, balance_after_centavos, reference, created_at
       FROM wallet_transactions
       WHERE player_id = ?
       ORDER BY created_at DESC
       LIMIT ? OFFSET ?`
    ).bind(wallet.playerId, limit, offset).all();

    return jsonSuccess({ transactions: results, limit, offset });
  }

  // ── Internal helpers ───────────────────────────────────────────────────────

  private async sweepExpiredNonces(): Promise<void> {
    const now  = Math.floor(Date.now() / 1000);
    const seen = await this.state.storage.list<number>({ prefix: 'nonce:' });
    for (const [key, ts] of seen) {
      if (now - ts > AUTH_MAX_SKEW_SECONDS) {
        await this.state.storage.delete(key);
      }
    }
  }

  private async getOrCreateWallet(): Promise<WalletState> {
    return (await this.state.storage.get<WalletState>('wallet')) ?? {
      playerId:          this.state.id.toString(),
      balanceCentavos:   0,
      reservedCentavos:  0,
      updatedAt:         new Date().toISOString(),
    };
  }

  private async getOrCreateWalletInTxn(
    txn: DurableObjectTransaction
  ): Promise<WalletState> {
    return (await txn.get<WalletState>('wallet')) ?? {
      playerId:          this.state.id.toString(),
      balanceCentavos:   0,
      reservedCentavos:  0,
      updatedAt:         new Date().toISOString(),
    };
  }

  private buildTransaction(
    playerId: string,
    operation: WalletOperation,
    amountCentavos: number,
    balanceAfterCentavos: number,
    reference: string
  ): WalletTransaction {
    return {
      id:                    generateUUID(),
      playerId,
      operation,
      amountCentavos,
      balanceAfterCentavos,
      reference,
      createdAt:             new Date().toISOString(),
    };
  }

  private async persistTransaction(tx: WalletTransaction): Promise<void> {
    try {
      await this.env.DB.prepare(
        `INSERT INTO wallet_transactions
         (id, player_id, operation, amount_centavos, balance_after_centavos, reference, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`
      ).bind(
        tx.id, tx.playerId, tx.operation,
        tx.amountCentavos, tx.balanceAfterCentavos,
        tx.reference, tx.createdAt
      ).run();
    } catch (err) {
      // Log but do not let D1 write failure block the wallet operation
      console.error('Failed to persist transaction to D1:', err);
    }
  }

  private async initiatePixWithdrawal(
    amountCentavos: number,
    pixKey: string,
    reference: string
  ): Promise<void> {
    try {
      // A withdrawal is a Pix SEND (payment out), not a charge. /cob and /cobv
      // are cobranças (money the operator RECEIVES); paying a player uses the
      // PSP's payout / Pix-envio endpoint keyed by the destination PIX key.
      await fetch(`${this.env.PIX_PSP_BASE_URL}/pix/v2/pagamentos`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.env.PIX_PSP_API_KEY}` },
        body: JSON.stringify({
          chaveDestino: pixKey,
          valor:        (amountCentavos / 100).toFixed(2),
          // idempotency key so PSP retries don't pay twice
          idEnvio:      reference,
          infoAdicionais: [{ nome: 'plataforma', valor: 'AcmeToCasino' }],
        }),
      });
    } catch (err) {
      console.error('PIX withdrawal initiation failed:', err);
    }
  }
}

// ── Environment interface for this Worker ─────────────────────────────────────

interface WalletEnv {
  DB:               D1Database;
  PIX_PSP_BASE_URL: string;
  PIX_HMAC_SECRET:  string;  // inbound only: validate PSP webhook signatures
  PIX_PSP_API_KEY:  string;  // outbound only: bearer credential for PSP API calls
  // Shared secret for authenticating internal callers of the DO fetch
  // dispatcher (HMAC-SHA256 over `timestamp.nonce.rawBody`). Set via
  // `wrangler secret put WALLET_INTERNAL_HMAC_SECRET`.
  WALLET_INTERNAL_HMAC_SECRET: string;
}

// ── Internal authentication ───────────────────────────────────────────────────

interface AuthResult {
  ok:     boolean;
  status: number;
  error:  string;
}

/**
 * Require a valid internal HMAC-SHA256 signature on a DO request.
 *
 * Canonical signed string is `timestamp.nonce.rawBody`. The signature is
 * verified in constant time via crypto.subtle.verify. The timestamp bounds a
 * short replay window; the nonce is recorded in DO storage and any repeat
 * within that window is rejected (see the dispatcher's replay check).
 *
 * Fails closed: if the secret is not configured the DO refuses all traffic.
 */
async function requireInternalAuth(
  request: Request,
  rawBody: ArrayBuffer,
  secret: string | undefined
): Promise<AuthResult> {
  const trimmed = secret?.trim();
  if (!trimmed) {
    // An unauthenticated financial worker must never accept traffic.
    return { ok: false, status: 503, error: 'Autenticação interna da carteira não configurada.' };
  }

  const timestamp = request.headers.get('X-Wallet-Timestamp') ?? '';
  const nonce     = request.headers.get('X-Wallet-Nonce')     ?? '';
  const signature = request.headers.get('X-Wallet-Signature') ?? '';

  if (!timestamp || !nonce || !signature) {
    return { ok: false, status: 401, error: 'Assinatura interna ausente.' };
  }

  // Reject stale requests to bound the replay window.
  const ts  = Number(timestamp);
  const now = Math.floor(Date.now() / 1000);
  if (!Number.isFinite(ts) || Math.abs(now - ts) > AUTH_MAX_SKEW_SECONDS) {
    return { ok: false, status: 401, error: 'Assinatura interna expirada.' };
  }

  try {
    const encoder   = new TextEncoder();
    const bodyText  = new TextDecoder().decode(rawBody);
    const canonical = `${timestamp}.${nonce}.${bodyText}`;

    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(trimmed),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify']
    );

    const valid = await crypto.subtle.verify(
      'HMAC',
      key,
      hexToBytes(signature),
      encoder.encode(canonical)
    );

    if (!valid) {
      return { ok: false, status: 401, error: 'Assinatura interna inválida.' };
    }
    return { ok: true, status: 200, error: '' };
  } catch {
    return { ok: false, status: 401, error: 'Assinatura interna inválida.' };
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function jsonSuccess(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ success: true, data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ success: false, error: message }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function parseJSON<T>(req: Request): Promise<T | null> {
  try { return (await req.json()) as T; } catch { return null; }
}

function hexToBytes(hexStr: string): Uint8Array {
  const clean = hexStr.trim().toLowerCase();
  if (clean.length === 0 || clean.length % 2 !== 0) return new Uint8Array();
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i++) {
    const byte = parseInt(clean.substr(i * 2, 2), 16);
    if (Number.isNaN(byte)) return new Uint8Array();
    out[i] = byte;
  }
  return out;
}

function generateUUID(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b).map(x => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
