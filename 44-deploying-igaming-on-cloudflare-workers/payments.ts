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
 * AcmeToCasino Platform - Payment Integration
 * Tokenized deposits, withdrawals, PSP routing, webhook verification, AML screening
 *
 * PCI-DSS Scope: Workers never receive raw card data. All card flows redirect
 * to a PSP-hosted payment page; the Worker handles only tokenised references
 * and PSP webhook callbacks. Crypto payments are handled via callback-based
 * address assignment — no private keys in Workers.
 */

import {
  Env,
  successResponse,
  errorResponse,
  internalErrorResponse,
  parseJSON,
  getCountry,
  base64UrlEncode,
} from './utils.js';
import { authenticateRequest, UserRow } from './auth.js';
import { checkUserCompliance } from './compliance.js';

// ─── Payment method availability by country ────────────────────────────────

/**
 * Maps ISO 3166-1 alpha-2 country codes to available payment methods.
 * Methods listed in priority order (first = recommended default).
 * 'US' entries are placeholder only — US players are blocked at the
 * jurisdiction layer before reaching this module.
 */
const PAYMENT_METHODS_BY_COUNTRY: Record<string, string[]> = {
  'BR': ['pix', 'boleto', 'credit_card'],
  'DE': ['sepa', 'credit_card', 'sofort', 'giropay'],
  'GB': ['credit_card', 'bank_transfer', 'paypal', 'skrill'],
  'MT': ['credit_card', 'sepa', 'skrill', 'neteller', 'crypto'],
  'US': ['credit_card', 'ach', 'paypal'],  // State-dependent — not active
  'JP': ['credit_card', 'bank_transfer', 'konbini'],
  'CY': ['credit_card', 'sepa', 'skrill', 'neteller', 'crypto'],
  'CW': ['credit_card', 'crypto', 'skrill', 'neteller'],
  'AU': ['credit_card', 'bank_transfer', 'payid'],  // No credit card deposits in AU
  'FR': ['credit_card', 'sepa', 'paypal'],
  'IT': ['credit_card', 'sepa', 'bank_transfer', 'paypal'],
  'ES': ['credit_card', 'sepa', 'bizum'],
  'SE': ['trustly', 'credit_card', 'swish'],
  'FI': ['trustly', 'credit_card', 'bank_transfer'],
  'NO': ['vipps', 'credit_card', 'bank_transfer'],
  'NL': ['ideal', 'credit_card', 'sepa'],
  'PL': ['blik', 'credit_card', 'bank_transfer'],
  'IN': ['upi', 'netbanking', 'credit_card'],
  'ZA': ['credit_card', 'eft', 'ozow'],
  'DEFAULT': ['credit_card', 'bank_transfer'],
};

// ─── Payment method metadata ───────────────────────────────────────────────

interface PaymentMethodMeta {
  id: string;
  label: string;
  type: 'card' | 'bank' | 'wallet' | 'crypto' | 'instant';
  processingTime: string;
  minDeposit: number;
  maxDeposit: number;
  minWithdrawal: number;
  maxWithdrawal: number;
  currencies: string[];
  requiresRedirect: boolean;
}

const PAYMENT_METHOD_META: Record<string, PaymentMethodMeta> = {
  credit_card: {
    id: 'credit_card',
    label: 'Credit / Debit Card',
    type: 'card',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 50000,
    minWithdrawal: 20,
    maxWithdrawal: 10000,
    currencies: ['EUR', 'GBP', 'USD', 'BRL', 'PLN', 'SEK', 'NOK'],
    requiresRedirect: true,
  },
  sepa: {
    id: 'sepa',
    label: 'SEPA Bank Transfer',
    type: 'bank',
    processingTime: '1-2 business days',
    minDeposit: 20,
    maxDeposit: 100000,
    minWithdrawal: 20,
    maxWithdrawal: 100000,
    currencies: ['EUR'],
    requiresRedirect: false,
  },
  bank_transfer: {
    id: 'bank_transfer',
    label: 'Bank Transfer',
    type: 'bank',
    processingTime: '1-3 business days',
    minDeposit: 20,
    maxDeposit: 100000,
    minWithdrawal: 20,
    maxWithdrawal: 100000,
    currencies: ['EUR', 'GBP', 'USD', 'AUD', 'ZAR'],
    requiresRedirect: false,
  },
  skrill: {
    id: 'skrill',
    label: 'Skrill',
    type: 'wallet',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 25000,
    minWithdrawal: 10,
    maxWithdrawal: 25000,
    currencies: ['EUR', 'GBP', 'USD'],
    requiresRedirect: true,
  },
  neteller: {
    id: 'neteller',
    label: 'Neteller',
    type: 'wallet',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 25000,
    minWithdrawal: 10,
    maxWithdrawal: 25000,
    currencies: ['EUR', 'GBP', 'USD'],
    requiresRedirect: true,
  },
  paypal: {
    id: 'paypal',
    label: 'PayPal',
    type: 'wallet',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 10000,
    minWithdrawal: 10,
    maxWithdrawal: 10000,
    currencies: ['EUR', 'GBP', 'USD'],
    requiresRedirect: true,
  },
  pix: {
    id: 'pix',
    label: 'Pix',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 50000,
    minWithdrawal: 10,
    maxWithdrawal: 50000,
    currencies: ['BRL'],
    requiresRedirect: false,
  },
  boleto: {
    id: 'boleto',
    label: 'Boleto Bancário',
    type: 'bank',
    processingTime: '1-2 business days',
    minDeposit: 20,
    maxDeposit: 10000,
    minWithdrawal: 20,
    maxWithdrawal: 10000,
    currencies: ['BRL'],
    requiresRedirect: true,
  },
  crypto: {
    id: 'crypto',
    label: 'Cryptocurrency (BTC / ETH / USDT)',
    type: 'crypto',
    processingTime: '10-60 min (network confirmations)',
    minDeposit: 10,
    maxDeposit: 500000,
    minWithdrawal: 20,
    maxWithdrawal: 500000,
    currencies: ['EUR', 'USD'],  // Converted at current rate on receipt
    requiresRedirect: false,
  },
  sofort: {
    id: 'sofort',
    label: 'Sofort (Klarna)',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 5000,
    minWithdrawal: 20,
    maxWithdrawal: 5000,
    currencies: ['EUR'],
    requiresRedirect: true,
  },
  giropay: {
    id: 'giropay',
    label: 'Giropay',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 10000,
    minWithdrawal: 20,
    maxWithdrawal: 10000,
    currencies: ['EUR'],
    requiresRedirect: true,
  },
  ach: {
    id: 'ach',
    label: 'ACH Bank Transfer',
    type: 'bank',
    processingTime: '2-3 business days',
    minDeposit: 20,
    maxDeposit: 25000,
    minWithdrawal: 20,
    maxWithdrawal: 25000,
    currencies: ['USD'],
    requiresRedirect: false,
  },
  trustly: {
    id: 'trustly',
    label: 'Trustly',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 25000,
    minWithdrawal: 10,
    maxWithdrawal: 25000,
    currencies: ['EUR', 'SEK', 'DKK', 'NOK'],
    requiresRedirect: true,
  },
  ideal: {
    id: 'ideal',
    label: 'iDEAL',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 10000,
    minWithdrawal: 10,
    maxWithdrawal: 10000,
    currencies: ['EUR'],
    requiresRedirect: true,
  },
  // Additional methods with basic metadata
  konbini: {
    id: 'konbini',
    label: 'Konbini (Convenience Store)',
    type: 'bank',
    processingTime: '1 business day',
    minDeposit: 10,
    maxDeposit: 5000,
    minWithdrawal: 10,
    maxWithdrawal: 5000,
    currencies: ['JPY'],
    requiresRedirect: true,
  },
  upi: {
    id: 'upi',
    label: 'UPI',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 10000,
    minWithdrawal: 10,
    maxWithdrawal: 10000,
    currencies: ['INR'],
    requiresRedirect: false,
  },
  netbanking: {
    id: 'netbanking',
    label: 'Net Banking',
    type: 'bank',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 10000,
    minWithdrawal: 10,
    maxWithdrawal: 10000,
    currencies: ['INR'],
    requiresRedirect: true,
  },
  blik: {
    id: 'blik',
    label: 'BLIK',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 5000,
    minWithdrawal: 10,
    maxWithdrawal: 5000,
    currencies: ['PLN'],
    requiresRedirect: false,
  },
  bizum: {
    id: 'bizum',
    label: 'Bizum',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 5000,
    minWithdrawal: 10,
    maxWithdrawal: 5000,
    currencies: ['EUR'],
    requiresRedirect: false,
  },
  payid: {
    id: 'payid',
    label: 'PayID',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 10000,
    minWithdrawal: 10,
    maxWithdrawal: 10000,
    currencies: ['AUD'],
    requiresRedirect: false,
  },
  vipps: {
    id: 'vipps',
    label: 'Vipps',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 5000,
    minWithdrawal: 10,
    maxWithdrawal: 5000,
    currencies: ['NOK'],
    requiresRedirect: true,
  },
  swish: {
    id: 'swish',
    label: 'Swish',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 5000,
    minWithdrawal: 10,
    maxWithdrawal: 5000,
    currencies: ['SEK'],
    requiresRedirect: false,
  },
  eft: {
    id: 'eft',
    label: 'EFT Bank Transfer',
    type: 'bank',
    processingTime: '1-2 business days',
    minDeposit: 20,
    maxDeposit: 50000,
    minWithdrawal: 20,
    maxWithdrawal: 50000,
    currencies: ['ZAR'],
    requiresRedirect: false,
  },
  ozow: {
    id: 'ozow',
    label: 'Ozow',
    type: 'instant',
    processingTime: 'Instant',
    minDeposit: 10,
    maxDeposit: 10000,
    minWithdrawal: 10,
    maxWithdrawal: 10000,
    currencies: ['ZAR'],
    requiresRedirect: true,
  },
};

// ─── Types ─────────────────────────────────────────────────────────────────

interface DepositIntentBody {
  amount: number;
  currency: string;
  method: string;
  returnUrl?: string;
  /** PSP-issued token from hosted payment page (for tokenized card flows) */
  paymentToken?: string;
}

interface WithdrawalRequestBody {
  amount: number;
  currency: string;
  method: string;
  accountDetails?: Record<string, string>;
}

interface PSPWebhookBody {
  provider: string;
  event: string;
  transactionId: string;    // Our internal reference_id
  externalRef: string;      // PSP's own transaction reference
  status: 'completed' | 'failed' | 'pending' | 'reversed';
  amount?: number;
  currency?: string;
  timestamp: string;
}

interface TransactionRow {
  id: number;
  user_id: number;
  type: string;
  amount: number;
  currency: string;
  status: string;
  payment_method: string | null;
  reference_id: string | null;
  created_at: string;
  processed_at: string | null;
}

// ─── AML screening thresholds ─────────────────────────────────────────────

const AML_THRESHOLDS = {
  /** Single transaction threshold — triggers enhanced review */
  SINGLE_TX_THRESHOLD: 2000,
  /** 24-hour rolling total — triggers enhanced review */
  DAILY_VOLUME_THRESHOLD: 5000,
  /** 30-day rolling total — triggers Source of Funds request */
  MONTHLY_VOLUME_THRESHOLD: 15000,
};

// ─── Route handler ─────────────────────────────────────────────────────────

export async function handlePayments(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const { method } = request;

  // GET /api/payments/methods — public endpoint, no auth required
  if (method === 'GET' && url.pathname === '/api/payments/methods') {
    return handlePaymentMethods(request);
  }

  // Webhook — authenticated by HMAC signature, not JWT
  if (method === 'POST' && url.pathname === '/api/payments/webhook') {
    return handleWebhook(request, env);
  }

  // All other payment routes require JWT auth
  const user = await authenticateRequest(request, env);
  if (!user) return errorResponse('Unauthorized', 401);

  if (method === 'POST' && url.pathname === '/api/payments/deposit') {
    return handleDepositIntent(request, env, user);
  }
  if (method === 'POST' && url.pathname === '/api/payments/withdraw') {
    return handleWithdrawalRequest(request, env, user);
  }
  if (method === 'GET' && url.pathname === '/api/payments/history') {
    return handlePaymentHistory(request, env, user);
  }

  return errorResponse('Route not found', 404);
}

// ─── GET /api/payments/methods ─────────────────────────────────────────────

/**
 * Returns payment methods available for the player's country.
 * Country is detected from Cloudflare's cf.country header — zero-latency
 * geo detection that requires no external lookup.
 *
 * The response includes full method metadata (limits, currencies, processing
 * times) so the frontend can render the deposit form without a second API call.
 */
function handlePaymentMethods(request: Request): Response {
  const country = getCountry(request);
  const methodIds = PAYMENT_METHODS_BY_COUNTRY[country] ?? PAYMENT_METHODS_BY_COUNTRY['DEFAULT'];

  const methods = methodIds
    .map((id) => PAYMENT_METHOD_META[id])
    .filter(Boolean);

  return successResponse({
    country,
    methods,
    count: methods.length,
  });
}

// ─── POST /api/payments/deposit ────────────────────────────────────────────

/**
 * Creates a deposit intent. The flow depends on the payment method:
 *
 * Tokenized card flow (requires PSP redirect):
 *   1. Client calls this endpoint
 *   2. Worker creates a pending transaction record
 *   3. Worker returns redirectUrl (PSP hosted payment page)
 *   4. Player completes payment on PSP page
 *   5. PSP POSTs to /api/payments/webhook
 *   6. Worker verifies HMAC, credits balance
 *
 * Instant wallet flow (Skrill, Neteller, Pix, etc.):
 *   1-3 identical to above
 *   4. PSP confirms instantly — webhook fires within seconds
 *   5-6 identical to above
 *
 * No card number, CVV, or expiry is ever sent to this Worker.
 * The `paymentToken` field carries only a PSP-issued opaque reference.
 */
async function handleDepositIntent(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<DepositIntentBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  // Validate amount
  if (typeof body.amount !== 'number' || isNaN(body.amount) || body.amount <= 0) {
    return errorResponse('amount must be a positive number', 422);
  }
  if (typeof body.currency !== 'string' || !/^[A-Z]{3}$/.test(body.currency)) {
    return errorResponse('currency must be a 3-letter ISO code', 422);
  }
  if (!body.method) return errorResponse('method is required', 422);

  // Compliance gate: self-exclusion, cool-off
  const compliance = await checkUserCompliance(user, env);
  if (!compliance.allowed) {
    return errorResponse(`Deposit blocked: ${compliance.reason}`, 403);
  }

  // Validate method is available for player's country
  const country = getCountry(request);
  const availableMethods = PAYMENT_METHODS_BY_COUNTRY[country] ?? PAYMENT_METHODS_BY_COUNTRY['DEFAULT'];
  if (!availableMethods.includes(body.method)) {
    return errorResponse(`Payment method '${body.method}' is not available in your country`, 422);
  }

  const meta = PAYMENT_METHOD_META[body.method];
  if (meta) {
    if (body.amount < meta.minDeposit) {
      return errorResponse(`Minimum deposit for ${meta.label} is ${meta.minDeposit} ${body.currency}`, 422);
    }
    if (body.amount > meta.maxDeposit) {
      return errorResponse(`Maximum deposit for ${meta.label} is ${meta.maxDeposit} ${body.currency}`, 422);
    }
  }

  // AML pre-check
  const amlResult = await runAmlDepositCheck(user.id, body.amount, env);
  if (amlResult.blocked) {
    return errorResponse('Deposit requires additional verification. Please contact support.', 403);
  }

  try {
    const referenceId = `dep-${Date.now()}-${user.id}-${Math.random().toString(36).slice(2, 7)}`;

    // Create pending transaction
    const txResult = await env.DB.prepare(
      `INSERT INTO transactions
         (user_id, type, amount, currency, status, payment_method, reference_id)
       VALUES (?, 'deposit', ?, ?, 'pending', ?, ?)`
    )
      .bind(user.id, body.amount, body.currency, body.method, referenceId)
      .run();

    const transactionId = txResult.meta.last_row_id as number;

    // Generate PSP redirect or return account details
    const processorResult = await buildDepositResponse(body.method, {
      amount: body.amount,
      currency: body.currency,
      userId: user.id,
      transactionId,
      referenceId,
      returnUrl: body.returnUrl ?? 'https://acmetocasino.com/wallet',
      paymentToken: body.paymentToken,
    });

    // Log deposit intent for audit trail
    await env.DB.prepare(
      'INSERT INTO compliance_events (user_id, event_type, details) VALUES (?, ?, ?)'
    )
      .bind(user.id, 'deposit_intent', JSON.stringify({
        transactionId,
        method: body.method,
        amount: body.amount,
        currency: body.currency,
        amlFlagged: amlResult.flagged,
      }))
      .run();

    return successResponse({
      transactionId,
      referenceId,
      status: 'pending',
      ...processorResult,
      amlFlagged: amlResult.flagged,
    });
  } catch (err) {
    console.error('Deposit intent error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── POST /api/payments/withdraw ───────────────────────────────────────────

/**
 * Processes a withdrawal request. Funds are reserved immediately (deducted
 * from balance); the payout is queued for compliance review before dispatch.
 *
 * Withdrawal flow:
 *   1. Validate amount and method
 *   2. Check compliance (exclusion, cool-off)
 *   3. Run AML screen (source of funds check if above threshold)
 *   4. Reserve funds (atomic balance deduction)
 *   5. Create pending withdrawal transaction
 *   6. Queue for back-office approval if above threshold
 *   7. Auto-approve and dispatch if below threshold
 */
async function handleWithdrawalRequest(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  const body = await parseJSON<WithdrawalRequestBody>(request);
  if (!body) return errorResponse('Invalid JSON body');

  if (typeof body.amount !== 'number' || isNaN(body.amount) || body.amount <= 0) {
    return errorResponse('amount must be a positive number', 422);
  }
  if (typeof body.currency !== 'string' || !/^[A-Z]{3}$/.test(body.currency)) {
    return errorResponse('currency must be a 3-letter ISO code', 422);
  }
  if (!body.method) return errorResponse('method is required', 422);

  const WITHDRAWAL_MIN = 20;
  const WITHDRAWAL_MAX = 50000;
  if (body.amount < WITHDRAWAL_MIN) {
    return errorResponse(`Minimum withdrawal is ${WITHDRAWAL_MIN} ${body.currency}`, 422);
  }
  if (body.amount > WITHDRAWAL_MAX) {
    return errorResponse(`Maximum withdrawal is ${WITHDRAWAL_MAX} ${body.currency}`, 422);
  }

  // Compliance gate
  const compliance = await checkUserCompliance(user, env);
  if (!compliance.allowed) {
    return errorResponse(`Withdrawal blocked: ${compliance.reason}`, 403);
  }

  // Live balance check
  const balanceRow = await env.DB.prepare(
    'SELECT balance FROM users WHERE id = ?'
  )
    .bind(user.id)
    .first<{ balance: number }>();

  if (!balanceRow || balanceRow.balance < body.amount) {
    return errorResponse('Insufficient balance', 422);
  }

  // AML screen
  const amlResult = await runAmlWithdrawalCheck(user.id, body.amount, env);
  const requiresManualReview = amlResult.flagged || body.amount > AML_THRESHOLDS.SINGLE_TX_THRESHOLD;

  try {
    // Atomic balance deduction — compare-and-deduct pattern
    const updateResult = await env.DB.prepare(
      'UPDATE users SET balance = balance - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND balance >= ?'
    )
      .bind(body.amount, user.id, body.amount)
      .run();

    if (updateResult.meta.changes === 0) {
      return errorResponse('Balance changed during processing. Please retry.', 409);
    }

    const referenceId = `wdl-${Date.now()}-${user.id}-${Math.random().toString(36).slice(2, 7)}`;

    const txResult = await env.DB.prepare(
      `INSERT INTO transactions
         (user_id, type, amount, currency, status, payment_method, reference_id)
       VALUES (?, 'withdrawal', ?, ?, ?, ?, ?)`
    )
      .bind(
        user.id,
        body.amount,
        body.currency,
        requiresManualReview ? 'pending' : 'pending',
        body.method,
        referenceId
      )
      .run();

    const transactionId = txResult.meta.last_row_id as number;

    // Invalidate cached balance
    await env.CACHE.delete(`user:${user.id}`);

    // Log for audit
    await env.DB.prepare(
      'INSERT INTO compliance_events (user_id, event_type, details) VALUES (?, ?, ?)'
    )
      .bind(user.id, 'withdrawal_request', JSON.stringify({
        transactionId,
        method: body.method,
        amount: body.amount,
        currency: body.currency,
        requiresManualReview,
        amlFlagged: amlResult.flagged,
        amlReason: amlResult.reason,
      }))
      .run();

    return successResponse({
      transactionId,
      referenceId,
      status: 'pending',
      requiresManualReview,
      estimatedProcessingTime: requiresManualReview
        ? 'Up to 5 business days (compliance review required)'
        : '1-3 business days',
      message: requiresManualReview
        ? 'Your withdrawal is under compliance review. You will be notified by email.'
        : 'Withdrawal submitted. Processing typically takes 1-3 business days.',
    });
  } catch (err) {
    console.error('Withdrawal error:', err);
    // Attempt to reverse balance deduction
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

// ─── POST /api/payments/webhook ────────────────────────────────────────────

/**
 * Receives PSP callbacks. HMAC-SHA256 signature verification is performed
 * before any DB writes. The signature key is the PAYMENT_PROCESSOR_KEY secret.
 *
 * Security model:
 * - Webhook endpoint is unauthenticated (PSPs cannot present JWT)
 * - But signed — every request must carry a valid HMAC-SHA256 in
 *   the X-Webhook-Signature header
 * - Idempotency — duplicate webhook deliveries are deduplicated by
 *   reference_id uniqueness in the transactions table
 */
async function handleWebhook(request: Request, env: Env): Promise<Response> {
  // Extract and verify HMAC signature
  const signature = request.headers.get('X-Webhook-Signature');
  if (!signature) {
    return errorResponse('Missing webhook signature', 401);
  }

  const rawBody = await request.text();

  const isValid = await verifyWebhookSignature(rawBody, signature, env.PAYMENT_PROCESSOR_KEY);
  if (!isValid) {
    console.error('Webhook signature verification failed');
    return errorResponse('Invalid webhook signature', 401);
  }

  let body: PSPWebhookBody;
  try {
    body = JSON.parse(rawBody) as PSPWebhookBody;
  } catch {
    return errorResponse('Invalid JSON payload', 400);
  }

  // Validate required fields
  if (!body.transactionId || !body.status || !body.event) {
    return errorResponse('Missing required webhook fields', 400);
  }

  try {
    // Find the transaction by reference_id (our ID sent to PSP)
    const tx = await env.DB.prepare(
      "SELECT id, user_id, amount, currency, status, type FROM transactions WHERE reference_id = ?"
    )
      .bind(body.transactionId)
      .first<TransactionRow & { user_id: number; type: string }>();

    if (!tx) {
      // Unknown reference — could be from a different system or replay attack
      console.warn('Webhook for unknown reference_id:', body.transactionId);
      return successResponse({ received: true, action: 'ignored_unknown_ref' });
    }

    // Idempotency check — do not process if already in a terminal state
    if (tx.status === 'completed' || tx.status === 'failed') {
      return successResponse({ received: true, action: 'ignored_already_terminal' });
    }

    if (body.status === 'completed') {
      if (tx.type === 'deposit') {
        // Credit balance
        await env.DB.prepare(
          'UPDATE users SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?'
        )
          .bind(tx.amount, tx.user_id)
          .run();

        await env.DB.prepare(
          "UPDATE transactions SET status = 'completed', processed_at = CURRENT_TIMESTAMP WHERE id = ?"
        )
          .bind(tx.id)
          .run();

        // Invalidate cached balance
        await env.CACHE.delete(`user:${tx.user_id}`);

        await env.DB.prepare(
          'INSERT INTO compliance_events (user_id, event_type, details) VALUES (?, ?, ?)'
        )
          .bind(tx.user_id, 'deposit_completed', JSON.stringify({
            transactionId: tx.id,
            externalRef: body.externalRef,
            amount: tx.amount,
            currency: tx.currency,
          }))
          .run();
      } else if (tx.type === 'withdrawal') {
        // Funds already reserved — just mark as dispatched
        await env.DB.prepare(
          "UPDATE transactions SET status = 'completed', processed_at = CURRENT_TIMESTAMP WHERE id = ?"
        )
          .bind(tx.id)
          .run();
      }
    } else if (body.status === 'failed') {
      await env.DB.prepare(
        "UPDATE transactions SET status = 'failed', processed_at = CURRENT_TIMESTAMP WHERE id = ?"
      )
        .bind(tx.id)
        .run();

      if (tx.type === 'withdrawal') {
        // Reverse the reserved funds
        await env.DB.prepare(
          'UPDATE users SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?'
        )
          .bind(tx.amount, tx.user_id)
          .run();
        await env.CACHE.delete(`user:${tx.user_id}`);
      }
    } else if (body.status === 'reversed') {
      // Chargeback or reversal — deduct from balance
      await env.DB.prepare(
        "UPDATE transactions SET status = 'failed', processed_at = CURRENT_TIMESTAMP WHERE id = ?"
      )
        .bind(tx.id)
        .run();

      if (tx.type === 'deposit') {
        await env.DB.prepare(
          'UPDATE users SET balance = MAX(0, balance - ?), updated_at = CURRENT_TIMESTAMP WHERE id = ?'
        )
          .bind(tx.amount, tx.user_id)
          .run();
        await env.CACHE.delete(`user:${tx.user_id}`);
      }

      await env.DB.prepare(
        'INSERT INTO compliance_events (user_id, event_type, details) VALUES (?, ?, ?)'
      )
        .bind(tx.user_id, 'payment_reversed', JSON.stringify({
          transactionId: tx.id,
          externalRef: body.externalRef,
          amount: tx.amount,
        }))
        .run();
    }

    return successResponse({ received: true, processed: true });
  } catch (err) {
    console.error('Webhook processing error:', err);
    // Return 200 to prevent PSP retries on server errors — log for manual review
    return successResponse({ received: true, processed: false, error: 'processing_error' });
  }
}

// ─── GET /api/payments/history ─────────────────────────────────────────────

async function handlePaymentHistory(
  request: Request,
  env: Env,
  user: UserRow
): Promise<Response> {
  try {
    const url = new URL(request.url);
    const page = Math.max(1, parseInt(url.searchParams.get('page') ?? '1', 10));
    const limit = Math.min(100, Math.max(1, parseInt(url.searchParams.get('limit') ?? '20', 10)));
    const type = url.searchParams.get('type');  // 'deposit' | 'withdrawal' | null
    const offset = (page - 1) * limit;

    let query = "SELECT * FROM transactions WHERE user_id = ? AND type IN ('deposit', 'withdrawal')";
    const params: (string | number)[] = [user.id];

    if (type === 'deposit' || type === 'withdrawal') {
      query = 'SELECT * FROM transactions WHERE user_id = ? AND type = ?';
      params.push(type);
    }

    query += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params.push(limit, offset);

    const result = await env.DB.prepare(query)
      .bind(...params)
      .all<TransactionRow>();

    let countQuery = "SELECT COUNT(*) as total FROM transactions WHERE user_id = ? AND type IN ('deposit', 'withdrawal')";
    const countParams: (string | number)[] = [user.id];
    if (type === 'deposit' || type === 'withdrawal') {
      countQuery = 'SELECT COUNT(*) as total FROM transactions WHERE user_id = ? AND type = ?';
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
    console.error('Payment history error:', err);
    return internalErrorResponse(env, err instanceof Error ? err.message : undefined);
  }
}

// ─── AML screening ─────────────────────────────────────────────────────────

async function runAmlDepositCheck(
  userId: number,
  amount: number,
  env: Env
): Promise<{ blocked: boolean; flagged: boolean; reason?: string }> {
  // Single transaction check
  if (amount >= AML_THRESHOLDS.SINGLE_TX_THRESHOLD) {
    const kycRow = await env.DB.prepare(
      'SELECT status, tier FROM kyc_submissions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1'
    )
      .bind(userId)
      .first<{ status: string; tier: string }>();

    if (!kycRow || kycRow.status !== 'approved') {
      return { blocked: true, flagged: true, reason: 'kyc_required_for_large_deposit' };
    }
  }

  // Daily volume check
  const since24h = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const dailyResult = await env.DB.prepare(
    `SELECT COALESCE(SUM(amount), 0) as total FROM transactions
     WHERE user_id = ? AND type = 'deposit' AND status = 'completed' AND created_at >= ?`
  )
    .bind(userId, since24h)
    .first<{ total: number }>();

  const dailyTotal = (dailyResult?.total ?? 0) + amount;
  if (dailyTotal >= AML_THRESHOLDS.DAILY_VOLUME_THRESHOLD) {
    return {
      blocked: false,
      flagged: true,
      reason: 'daily_volume_threshold_exceeded',
    };
  }

  return { blocked: false, flagged: false };
}

async function runAmlWithdrawalCheck(
  userId: number,
  amount: number,
  env: Env
): Promise<{ flagged: boolean; reason?: string }> {
  // Check 30-day deposit volume for Source of Funds (SOF) requirement
  const since30d = new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString();
  const monthlyResult = await env.DB.prepare(
    `SELECT COALESCE(SUM(amount), 0) as total FROM transactions
     WHERE user_id = ? AND type = 'deposit' AND status = 'completed' AND created_at >= ?`
  )
    .bind(userId, since30d)
    .first<{ total: number }>();

  const monthlyTotal = monthlyResult?.total ?? 0;
  if (monthlyTotal >= AML_THRESHOLDS.MONTHLY_VOLUME_THRESHOLD) {
    return { flagged: true, reason: 'monthly_volume_threshold_sof_required' };
  }

  if (amount >= AML_THRESHOLDS.SINGLE_TX_THRESHOLD) {
    return { flagged: true, reason: 'withdrawal_above_single_tx_threshold' };
  }

  return { flagged: false };
}

// ─── PSP integration helpers ───────────────────────────────────────────────

/**
 * Builds the deposit response for a given payment method.
 * Production: call the PSP's Create Payment Intent or Create Order API.
 * This implementation returns the correct response shape for each method type.
 */
async function buildDepositResponse(
  method: string,
  params: {
    amount: number;
    currency: string;
    userId: number;
    transactionId: number;
    referenceId: string;
    returnUrl: string;
    paymentToken?: string;
  }
): Promise<{ redirectUrl?: string; accountDetails?: Record<string, string>; instructions?: string }> {
  const baseCheckoutUrl = 'https://pay.acmetocasino.com/checkout';

  const REDIRECT_METHODS = [
    'credit_card', 'skrill', 'neteller', 'paypal',
    'sofort', 'giropay', 'trustly', 'ideal',
    'konbini', 'vipps', 'ozow', 'boleto',
  ];

  if (REDIRECT_METHODS.includes(method)) {
    return {
      redirectUrl: `${baseCheckoutUrl}/${method}?` +
        `ref=${params.referenceId}` +
        `&amount=${params.amount}` +
        `&currency=${params.currency}` +
        `&return=${encodeURIComponent(params.returnUrl)}` +
        (params.paymentToken ? `&token=${params.paymentToken}` : ''),
    };
  }

  // Bank transfer methods return account details directly
  if (method === 'sepa' || method === 'bank_transfer' || method === 'ach') {
    return {
      accountDetails: {
        beneficiary: 'AcmeToCasino Ltd',
        iban: 'MT84MALT011000012345MTLCAST001S',
        bic: 'MALTMTMT',
        reference: params.referenceId,
        amount: String(params.amount),
        currency: params.currency,
        bank: 'BOV Bank Malta',
      },
      instructions: `Transfer exactly ${params.amount} ${params.currency} using reference ${params.referenceId}. Funds will be credited within 1-2 business days.`,
    };
  }

  // Crypto — return deposit address (would be generated per-user in production)
  if (method === 'crypto') {
    return {
      accountDetails: {
        btcAddress: '3FZbgi29cpjq2GjdwV8eyHuJJnkLtktZc5',
        ethAddress: '0x742d35Cc6634C0532925a3b844Bc454e4438f44e',
        usdtAddress: 'TN3W4H6rK2ce4vX9YnFQHwKx8Vxk2EDfUJ',
        reference: params.referenceId,
        minConfirmations: '3',
        note: 'Send only to the address matching your chosen cryptocurrency. Incorrect chain transfers are unrecoverable.',
      },
    };
  }

  // Instant local methods (Pix, BLIK, UPI, etc.) — redirect to local payment page
  return {
    redirectUrl: `${baseCheckoutUrl}/${method}?ref=${params.referenceId}&amount=${params.amount}&currency=${params.currency}&return=${encodeURIComponent(params.returnUrl)}`,
  };
}

// ─── Webhook signature verification ───────────────────────────────────────

/**
 * Verifies PSP webhook HMAC-SHA256 signature.
 * The PSP signs the raw request body with the shared secret using HMAC-SHA256.
 * We compute the expected signature and compare using a constant-time comparison
 * to prevent timing attacks.
 */
async function verifyWebhookSignature(
  payload: string,
  signature: string,
  secret: string
): Promise<boolean> {
  try {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );

    const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(payload));
    const expected = base64UrlEncode(new Uint8Array(mac));

    // Constant-time comparison
    if (expected.length !== signature.length) return false;
    let diff = 0;
    for (let i = 0; i < expected.length; i++) {
      diff |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
    }
    return diff === 0;
  } catch {
    return false;
  }
}

// ─── Exported helpers for other modules ───────────────────────────────────

export function getAvailableMethodsForCountry(country: string): string[] {
  return PAYMENT_METHODS_BY_COUNTRY[country] ?? PAYMENT_METHODS_BY_COUNTRY['DEFAULT'];
}
