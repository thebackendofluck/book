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
 * PIX Webhook Worker
 *
 * Receives real-time PIX payment confirmations from the licensed PSP
 * (Payment Service Provider), persists an idempotent receipt in D1 and
 * asynchronously notifies the authoritative AWS payment core.
 *
 * Security model:
 *  - HMAC-SHA256 request signature validation (X-Pix-Hmac header). NOTE:
 *    the BACEN callback contract itself secures webhooks with mTLS; the
 *    HMAC header model here is the PSP's convenience scheme, not the
 *    BACEN spec.
 *  - Idempotency: each endToEndId is recorded in D1; duplicates are accepted.
 *  - The HTTP acknowledgement is decoupled from AWS notification delivery.
 *  - A D1 application-managed retry record remains pending until AWS accepts
 *    it; no wallet balance is maintained at the edge.
 *  - /qrcode is an internal-only endpoint: it requires an HMAC signature
 *    minted by the API Gateway (GATEWAY_INTERNAL_HMAC_SECRET) so external
 *    clients cannot create real PIX charges directly.
 *
 * PSP webhook contract (HMAC scheme is PSP-specific; BACEN's own callback
 * contract uses mTLS): https://bacen.github.io/pix-api/#tag/Pix
 */

import type { Env, PixWebhookPayload } from './types.js';
import { validatePixHmac, pixValueToCentavos, generatePixTxid } from './utils/pix.js';
import { forwardToCore } from './utils/origin.js';

/** Max clock skew accepted on internal gateway→pix-webhook calls. */
const INTERNAL_MAX_SKEW_MS = 300_000; // 5 minutes

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // ── PIX QR Code generation (called by API Gateway) ─────────────────────
    if (url.pathname === '/qrcode' && request.method === 'POST') {
      return handleQRCodeGeneration(request, env);
    }

    // ── Inbound PIX notification from PSP ─────────────────────────────────
    if (url.pathname === '/pix' || url.pathname.startsWith('/api/pix')) {
      if (request.method !== 'POST') {
        return new Response('Method not allowed', { status: 405 });
      }
      return handlePixNotification(request, env, ctx);
    }

    return new Response('Not found', { status: 404 });
  },

  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(retryPendingOriginNotifications(env));
  },
};

// ── QR Code generation ────────────────────────────────────────────────────────

async function handleQRCodeGeneration(request: Request, env: Env): Promise<Response> {
  // Buffer the raw body first: the internal signature is computed over it and
  // the body can only be consumed once.
  const rawBody = await request.text();

  // ── Internal-only gate: reject anything not signed by the gateway ───────
  const authorized = await verifyInternalSignature(rawBody, request, env.GATEWAY_INTERNAL_HMAC_SECRET);
  if (!authorized) {
    return jsonError('Unauthorized internal request.', 401);
  }

  let body: { playerId: string; amountCentavos: number } | null;
  try {
    body = JSON.parse(rawBody) as { playerId: string; amountCentavos: number };
  } catch {
    body = null;
  }

  if (!body || !body.playerId || !body.amountCentavos) {
    return jsonError('playerId e amountCentavos são obrigatórios.', 400);
  }

  const amountBRL = body.amountCentavos / 100;
  // Operator risk-policy bounds (not a SIGAP-mandated range).
  if (amountBRL < 10 || amountBRL > 50_000) {
    return jsonError('Valor PIX deve estar entre R$ 10,00 e R$ 50.000,00.', 422);
  }

  const txid        = generatePixTxid();
  const expiresAt   = new Date(Date.now() + 30 * 60 * 1000).toISOString();

  // Store pending transaction in D1 for idempotency tracking
  await env.DB.prepare(
    `INSERT INTO pix_transactions (txid, player_id, amount_centavos, status, expires_at, created_at)
     VALUES (?, ?, ?, 'pending', ?, ?)`
  ).bind(txid, body.playerId, body.amountCentavos, expiresAt, new Date().toISOString()).run();

  // Request QR code from PSP
  const pspResponse = await fetch(`${env.PIX_PSP_BASE_URL}/cob/${txid}`, {
    method:  'PUT',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${env.PIX_PSP_API_KEY}` },
    body: JSON.stringify({
      calendario: { expiracao: 1800 },
      devedor:    {},
      valor:      { original: amountBRL.toFixed(2) },
      chave:      env.PIX_PSP_BASE_URL.includes('sandbox') ? '11111111000191' : '<YOUR-PIX-KEY>',
      solicitacaoPagador: `Depósito AcmeToCasino - ${body.playerId.slice(0, 8)}`,
    }),
  });

  if (!pspResponse.ok) {
    const errText = await pspResponse.text();
    console.error(`PSP QR code error: ${pspResponse.status} ${errText}`);
    return jsonError('Erro ao gerar QR Code PIX. Tente novamente.', 503);
  }

  const pspData = await pspResponse.json<{
    txid: string;
    pixCopiaECola: string;
    location: string;
  }>();

  return new Response(
    JSON.stringify({
      success: true,
      data: {
        txid,
        pixCopyPaste: pspData.pixCopiaECola,
        qrCodeBase64: null, // rendered client-side from pixCopyPaste
        expiresAt,
        amountBRL,
      },
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } }
  );
}

/**
 * Verify the internal gateway signature over `timestamp.nonce.rawBody`.
 *
 * Mirrors the odds publisher authentication style: 10-digit unix timestamp,
 * a URL-safe nonce and a 64-hex-char HMAC-SHA256 signature, bounded by a
 * short clock-skew window. Returns true only for a valid, in-window request.
 */
async function verifyInternalSignature(
  rawBody: string,
  request: Request,
  secret: string
): Promise<boolean> {
  if (!secret) return false;

  const timestamp = request.headers.get('X-Internal-Timestamp') ?? '';
  const nonce     = request.headers.get('X-Internal-Nonce') ?? '';
  const signature = request.headers.get('X-Internal-Signature') ?? '';

  if (!/^\d{10}$/.test(timestamp)
      || !/^[A-Za-z0-9_-]{16,128}$/.test(nonce)
      || !/^[a-fA-F0-9]{64}$/.test(signature)) {
    return false;
  }

  const timestampSeconds = Number(timestamp);
  const skew = Math.abs(Date.now() - timestampSeconds * 1_000);
  if (!Number.isFinite(timestampSeconds) || skew > INTERNAL_MAX_SKEW_MS) {
    return false;
  }

  try {
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']
    );
    const signatureBytes = new Uint8Array(signature.match(/.{2}/g)!.map(byte => parseInt(byte, 16)));
    return await crypto.subtle.verify(
      'HMAC', key, signatureBytes, encoder.encode(`${timestamp}.${nonce}.${rawBody}`)
    );
  } catch {
    return false;
  }
}

// ── PIX notification handler ──────────────────────────────────────────────────

async function handlePixNotification(
  request: Request,
  env: Env,
  ctx: ExecutionContext
): Promise<Response> {
  // Buffer the body for HMAC validation (body can only be consumed once)
  const bodyBuffer = await request.arrayBuffer();

  // ── Signature validation ────────────────────────────────────────────────
  const hmacHeader = request.headers.get('X-Pix-Hmac') ?? '';
  if (!hmacHeader) {
    return jsonError('Missing X-Pix-Hmac signature.', 401);
  }

  const signatureValid = await validatePixHmac(bodyBuffer, hmacHeader, env.PIX_HMAC_SECRET);
  if (!signatureValid) {
    return jsonError('Invalid HMAC signature.', 401);
  }

  // ── Parse payload ────────────────────────────────────────────────────────
  let payload: PixWebhookPayload;
  try {
    payload = JSON.parse(new TextDecoder().decode(bodyBuffer)) as PixWebhookPayload;
  } catch {
    return jsonError('Invalid JSON payload.', 400);
  }

  if (!payload.endToEndId || !payload.txid || !payload.status || !payload.valor) {
    return jsonError('Campos obrigatórios ausentes: endToEndId, txid, status, valor.', 422);
  }

  // ── Only process confirmed payments ─────────────────────────────────────
  if (payload.status !== 'confirmed') {
    // Acknowledge non-confirmed events without side effects
    return new Response(JSON.stringify({ received: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  // ── Idempotency check ────────────────────────────────────────────────────
  const existing = await env.DB.prepare(
    `SELECT id, status FROM pix_transactions WHERE txid = ? OR end_to_end_id = ?`
  ).bind(payload.txid, payload.endToEndId).first<{ id: string; status: string }>();

  if (existing?.status === 'confirmed') {
    // The receipt already exists. A retry record remains authoritative for
    // delivery state, so a duplicate callback may safely nudge it again.
    ctx.waitUntil(attemptOriginNotification(env, payload.txid));
    return new Response(JSON.stringify({ received: true, idempotent: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  if (!existing) {
    // Webhook for a transaction we didn't initiate — log and reject
    await logUnknownTxid(env, payload);
    return jsonError('TXID not found.', 404);
  }

  // ── Validate the receipt against the initiated transaction ───────────────
  const amountCentavos = pixValueToCentavos(payload.valor);
  const txRow = await env.DB.prepare(
    `SELECT player_id, amount_centavos FROM pix_transactions WHERE txid = ?`
  ).bind(payload.txid).first<{ player_id: string; amount_centavos: number }>();

  if (!txRow) {
    return jsonError('Transaction record not found.', 500);
  }

  // Validate PSP amount matches expected amount (anti-fraud)
  if (Math.abs(txRow.amount_centavos - amountCentavos) > 1) {
    await flagAmountMismatch(env, payload, txRow.amount_centavos, amountCentavos);
    return jsonError('Amount mismatch — transaction flagged for review.', 422);
  }

  // ── Persist receipt and origin notification atomically ───────────────────
  const now = new Date().toISOString();
  const notificationId = `pix:${payload.endToEndId}`;
  await env.DB.batch([
    env.DB.prepare(
      `UPDATE pix_transactions
       SET status = 'confirmed', end_to_end_id = ?, confirmed_at = ?, psp_payload = ?
       WHERE txid = ?`
    ).bind(payload.endToEndId, payload.horario ?? now, JSON.stringify(payload), payload.txid),
    env.DB.prepare(
      `INSERT OR IGNORE INTO pix_origin_notifications
       (notification_id, txid, end_to_end_id, player_id, amount_centavos, payload,
        status, attempt_count, next_attempt_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)`
    ).bind(
      notificationId,
      payload.txid,
      payload.endToEndId,
      txRow.player_id,
      amountCentavos,
      JSON.stringify(payload),
      now,
      now,
      now
    ),
  ]);

  // The PSP receives its ACK after durable receipt persistence. Delivery to
  // AWS is best-effort in this invocation and cron-retried from D1 afterward.
  ctx.waitUntil(attemptOriginNotification(env, payload.txid));

  return new Response(JSON.stringify({ received: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

// ── AWS core delivery ────────────────────────────────────────────────────────

interface PendingOriginNotification {
  txid: string;
  player_id: string;
  amount_centavos: number;
  payload: string;
  attempt_count: number;
}

async function attemptOriginNotification(env: Env, txid: string): Promise<void> {
  const row = await env.DB.prepare(
    `SELECT txid, player_id, amount_centavos, payload, attempt_count
     FROM pix_origin_notifications
     WHERE txid = ? AND status = 'pending' AND next_attempt_at <= ?`
  ).bind(txid, new Date().toISOString()).first<PendingOriginNotification>();
  if (!row) return;

  const now = new Date().toISOString();
  const attempts = row.attempt_count + 1;
  try {
    const response = await forwardToCore(env, {
      method: 'POST',
      path: '/internal/payments/pix/notifications',
      requestId: `pix-${row.txid}`,
      playerId: row.player_id,
      idempotencyKey: `pix:${row.txid}`,
      contentType: 'application/json',
      body: JSON.stringify({
        txid: row.txid,
        playerId: row.player_id,
        amountCentavos: row.amount_centavos,
        pspNotification: JSON.parse(row.payload) as PixWebhookPayload,
      }),
    });

    if (response.ok || response.status === 409) {
      await env.DB.prepare(
        `UPDATE pix_origin_notifications
         SET status = 'delivered', attempt_count = ?, delivered_at = ?,
             last_attempt_at = ?, last_error = NULL, updated_at = ?
         WHERE txid = ?`
      ).bind(attempts, now, now, now, row.txid).run();
      return;
    }

    await markOriginNotificationPending(
      env,
      row.txid,
      attempts,
      `AWS core returned HTTP ${response.status}`
    );
  } catch (error) {
    await markOriginNotificationPending(
      env,
      row.txid,
      attempts,
      error instanceof Error ? error.message : String(error)
    );
  }
}

async function retryPendingOriginNotifications(env: Env): Promise<void> {
  const { results } = await env.DB.prepare(
    `SELECT txid FROM pix_origin_notifications
     WHERE status = 'pending' AND next_attempt_at <= ?
     ORDER BY next_attempt_at ASC LIMIT 50`
  ).bind(new Date().toISOString()).all<{ txid: string }>();

  for (const row of results) {
    await attemptOriginNotification(env, row.txid);
  }
}

async function markOriginNotificationPending(
  env: Env,
  txid: string,
  attempts: number,
  error: string
): Promise<void> {
  const backoffSeconds = Math.min(60 * (2 ** Math.min(attempts - 1, 6)), 3600);
  const now = new Date();
  const nextAttemptAt = new Date(now.getTime() + backoffSeconds * 1000).toISOString();
  await env.DB.prepare(
    `UPDATE pix_origin_notifications
     SET attempt_count = ?, last_attempt_at = ?, next_attempt_at = ?,
         last_error = ?, updated_at = ?
     WHERE txid = ? AND status = 'pending'`
  ).bind(attempts, now.toISOString(), nextAttemptAt, error.slice(0, 500), now.toISOString(), txid).run();
}

// ── Fraud logging helpers ─────────────────────────────────────────────────────

async function logUnknownTxid(env: Env, payload: PixWebhookPayload): Promise<void> {
  try {
    await env.DB.prepare(
      `INSERT INTO security_events (id, event_type, payload, created_at)
       VALUES (?, 'pix_unknown_txid', ?, ?)`
    ).bind(generateUUID(), JSON.stringify(payload), new Date().toISOString()).run();
  } catch { /* intentional */ }
}

async function flagAmountMismatch(
  env: Env,
  payload: PixWebhookPayload,
  expectedCentavos: number,
  receivedCentavos: number
): Promise<void> {
  try {
    await env.DB.prepare(
      `INSERT INTO security_events (id, event_type, payload, created_at)
       VALUES (?, 'pix_amount_mismatch', ?, ?)`
    ).bind(
      generateUUID(),
      JSON.stringify({ payload, expectedCentavos, receivedCentavos }),
      new Date().toISOString()
    ).run();
  } catch { /* intentional */ }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ success: false, error: message }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function generateUUID(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b).map(x => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
