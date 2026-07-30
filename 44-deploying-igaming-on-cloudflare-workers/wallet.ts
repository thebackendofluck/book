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
 * AcmeToCasino Platform - Wallet Operations
 * Deposit, withdrawal, balance, transaction history
 */

import {
  Env,
  successResponse,
  errorResponse,
  internalErrorResponse,
  parseJSON,
} from './utils.js';
import { authenticateRequest, UserRow } from './auth.js';

// ─── Types ─────────────────────────────────────────────────────────────────

interface TransactionRow {
  id: number;
  user_id: number;
  type: 'deposit' | 'withdrawal' | 'bonus' | 'wager' | 'win';
  amount: number;
  currency: string;
  status: 'pending' | 'completed' | 'failed' | 'cancelled';
  payment_method: string | null;
  reference_id: string | null;
  created_at: string;
  processed_at: string | null;
}

interface DepositBody {
  amount: number;
  currency: string;
  method: string;
  returnUrl?: string;
}

interface WithdrawalBody {
  amount: number;
  currency: string;
  method: string;
  accountDetails?: Record<string, string>;
}

// ─── Route handler ─────────────────────────────────────────────────────────

export async function handleWallet(request: Request, env: Env): Promise<Response> {
  const user = await authenticateRequest(request, env);
  if (!user) return errorResponse('Unauthorized', 401);

  const url = new URL(request.url);
  const { method } = request;

  if (method === 'GET' && url.pathname === '/api/wallet/balance') {
    return handleBalance(user, env);
  }
  if (method === 'POST' && url.pathname === '/api/wallet/deposit') {
    return handleDeposit(request, env, user);
  }
  if (method === 'POST' && url.pathname === '/api/wallet/withdraw') {
    return handleWithdrawal(request, env, user);
  }
  if (method === 'GET' && url.pathname === '/api/wallet/transactions') {
    return handleTransactionHistory(request, env, user);
  }

  return errorResponse('Route not found', 404);
}

// ─── Balance ───────────────────────────────────────────────────────────────

async function handleBalance(user: UserRow, env: Env): Promise<Response> {
  try {
    // Fetch fresh balance directly from DB (not cache, for accuracy)
    const row = await env.DB.prepare(
      'SELECT balance, currency FROM users WHERE id = ?'
    )
      .bind(user.id)
      .first<{ balance: number; currency: string }>();

    if (!row) return errorResponse('User not found', 404);

    return successResponse({ balance: row.balance, currency: row.currency });
  } catch (err) {
    console.error('Balance error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Deposit ───────────────────────────────────────────────────────────────

async function handleDeposit(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<DepositBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  const errors = validateAmount(body.amount, body.currency);
  if (errors.length > 0) return errorResponse(errors.join(', '), 422);

  const DEPOSIT_MIN = 10;
  const DEPOSIT_MAX = 50000;
  if (body.amount < DEPOSIT_MIN) {
    return errorResponse(`Minimum deposit is ${DEPOSIT_MIN} ${body.currency}`);
  }
  if (body.amount > DEPOSIT_MAX) {
    return errorResponse(`Maximum deposit is ${DEPOSIT_MAX} ${body.currency}`);
  }

  if (!body.method) return errorResponse('Payment method is required');

  try {
    // Create pending transaction record
    const txResult = await env.DB.prepare(
      `INSERT INTO transactions
         (user_id, type, amount, currency, status, payment_method, reference_id)
       VALUES (?, 'deposit', ?, ?, 'pending', ?, ?)`
    )
      .bind(
        user.id,
        body.amount,
        body.currency,
        body.method,
        `acme-dep-${Date.now()}-${user.id}`
      )
      .run();

    const transactionId = txResult.meta.last_row_id as number;

    // Call payment processor (stubbed — replace with real provider SDK)
    const processorResult = await callPaymentProcessor(body.method, {
      amount: body.amount,
      currency: body.currency,
      userId: user.id,
      transactionId,
      returnUrl: body.returnUrl ?? 'https://acmetocasino.com/wallet',
    });

    // For instant methods, credit balance immediately
    if (processorResult.status === 'completed') {
      await creditBalance(user.id, body.amount, transactionId, env);
    }

    return successResponse({
      transactionId,
      status: processorResult.status,
      redirectUrl: processorResult.redirectUrl ?? null,
    });
  } catch (err) {
    console.error('Deposit error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Withdrawal ────────────────────────────────────────────────────────────

async function handleWithdrawal(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<WithdrawalBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  const errors = validateAmount(body.amount, body.currency);
  if (errors.length > 0) return errorResponse(errors.join(', '), 422);

  const WITHDRAWAL_MIN = 20;
  const WITHDRAWAL_MAX = 10000;
  if (body.amount < WITHDRAWAL_MIN) {
    return errorResponse(`Minimum withdrawal is ${WITHDRAWAL_MIN} ${body.currency}`);
  }
  if (body.amount > WITHDRAWAL_MAX) {
    return errorResponse(`Maximum withdrawal is ${WITHDRAWAL_MAX} ${body.currency}`);
  }

  // Fetch live balance
  const balanceRow = await env.DB.prepare(
    'SELECT balance FROM users WHERE id = ?'
  )
    .bind(user.id)
    .first<{ balance: number }>();

  if (!balanceRow || balanceRow.balance < body.amount) {
    return errorResponse('Insufficient balance');
  }

  try {
    // Reserve funds (deduct immediately, reverse on failure)
    await env.DB.prepare(
      'UPDATE users SET balance = balance - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND balance >= ?'
    )
      .bind(body.amount, user.id, body.amount)
      .run();

    const txResult = await env.DB.prepare(
      `INSERT INTO transactions
         (user_id, type, amount, currency, status, payment_method, reference_id)
       VALUES (?, 'withdrawal', ?, ?, 'pending', ?, ?)`
    )
      .bind(
        user.id,
        body.amount,
        body.currency,
        body.method,
        `acme-wdl-${Date.now()}-${user.id}`
      )
      .run();

    const transactionId = txResult.meta.last_row_id as number;

    // Invalidate user cache
    await env.CACHE.delete(`user:${user.id}`);

    return successResponse({
      transactionId,
      status: 'pending',
      message: 'Withdrawal submitted. Processing typically takes 1-3 business days.',
    });
  } catch (err) {
    console.error('Withdrawal error:', err);
    // Attempt to reverse the balance deduction
    try {
      await env.DB.prepare(
        'UPDATE users SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?'
      )
        .bind(body.amount, user.id)
        .run();
    } catch (reverseErr) {
      console.error('CRITICAL: Failed to reverse balance deduction:', reverseErr);
    }
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Transaction history ───────────────────────────────────────────────────

async function handleTransactionHistory(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  try {
    const url = new URL(request.url);
    const page = Math.max(1, parseInt(url.searchParams.get('page') ?? '1', 10));
    const limit = Math.min(100, Math.max(1, parseInt(url.searchParams.get('limit') ?? '20', 10)));
    const type = url.searchParams.get('type');
    const offset = (page - 1) * limit;

    let query = 'SELECT * FROM transactions WHERE user_id = ?';
    const params: (string | number)[] = [user.id];

    if (type) {
      query += ' AND type = ?';
      params.push(type);
    }

    query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params.push(limit, offset);

    const result = await env.DB.prepare(query)
      .bind(...params)
      .all<TransactionRow>();

    // Get total count
    let countQuery = 'SELECT COUNT(*) as total FROM transactions WHERE user_id = ?';
    const countParams: (string | number)[] = [user.id];
    if (type) {
      countQuery += ' AND type = ?';
      countParams.push(type);
    }

    const countResult = await env.DB.prepare(countQuery)
      .bind(...countParams)
      .first<{ total: number }>();

    return successResponse({
      transactions: result.results,
      pagination: {
        page,
        limit,
        total: countResult?.total ?? 0,
        pages: Math.ceil((countResult?.total ?? 0) / limit),
      },
    });
  } catch (err) {
    console.error('Transaction history error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── Internal helpers ──────────────────────────────────────────────────────

async function creditBalance(
  userId: number,
  amount: number,
  transactionId: number,
  env: Env
): Promise<void> {
  await env.DB.prepare(
    'UPDATE users SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?'
  )
    .bind(amount, userId)
    .run();

  await env.DB.prepare(
    "UPDATE transactions SET status = 'completed', processed_at = CURRENT_TIMESTAMP WHERE id = ?"
  )
    .bind(transactionId)
    .run();

  // Invalidate user cache
  await env.CACHE.delete(`user:${userId}`);
}

async function callPaymentProcessor(
  method: string,
  params: {
    amount: number;
    currency: string;
    userId: number;
    transactionId: number;
    returnUrl: string;
  }
): Promise<{ status: 'pending' | 'completed' | 'failed'; redirectUrl?: string }> {
  // Production: integrate with actual payment processors
  // (Stripe, Skrill, Neteller, Trustly, etc.) based on `method`
  const instantMethods = ['skrill', 'neteller', 'paypal'];

  if (instantMethods.includes(method.toLowerCase())) {
    return {
      status: 'completed',
    };
  }

  // Card and bank transfer need redirect
  return {
    status: 'pending',
    redirectUrl: `https://payment.acmetocasino.com/checkout?ref=${params.transactionId}&return=${encodeURIComponent(params.returnUrl)}`,
  };
}

function validateAmount(amount: unknown, currency: unknown): string[] {
  const errors: string[] = [];
  if (typeof amount !== 'number' || isNaN(amount) || amount <= 0) {
    errors.push('amount must be a positive number');
  }
  if (typeof currency !== 'string' || !/^[A-Z]{3}$/.test(currency)) {
    errors.push('currency must be a 3-letter ISO code');
  }
  return errors;
}
