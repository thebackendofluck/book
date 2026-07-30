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
 * SIGAP prepared-batch delivery Worker.
 *
 * The AWS compliance pipeline builds the applicable official XML document
 * family, validates it against its XSD, signs it with the operator e-CNPJ,
 * compresses it with GZIP and Base64-encodes it before this Worker sees it.
 * Cloudflare Queue is the retry transport. D1 is only the delivery ledger.
 */

import type { Env } from './types.js';

const MAX_RECORDS_PER_BATCH = 7_500;
const MAX_COMPRESSED_BYTES = 3 * 1024 * 1024;
const MAX_RETRY_DELAY_SECONDS = 900;

/** Replay/skew window for the compliance origin HMAC, in milliseconds. */
const MAX_COMPLIANCE_AUTH_SKEW_MS = 60_000;

const DOCUMENT_FAMILIES = new Set([
  'bettor',
  'wallet',
  'operator_daily',
  'operator_monthly',
  'sports_bets',
  'online_games',
] as const);

type SigapDocumentFamily =
  | 'bettor'
  | 'wallet'
  | 'operator_daily'
  | 'operator_monthly'
  | 'sports_bets'
  | 'online_games';

export interface PreparedSigapBatchEnvelope {
  batchId: string;
  operatorId: string;
  documentFamily: SigapDocumentFamily;
  referenceDate: string;
  schemaVersion: string;
  receptionPath: string;
  recordCount: number;
  compressedSizeBytes: number;
  payloadBase64: string;
  signedXml: true;
  generatedAt: string;
}

type SigapReporterEnv = Pick<Env, 'DB' | 'SIGAP_API_URL'> & {
  SIGAP_BATCH_QUEUE: Queue<PreparedSigapBatchEnvelope>;
  SIGAP_MTLS: Fetcher;
  SIGAP_BEARER_TOKEN: string;
  // HMAC secret shared with the AWS compliance pipeline. Every /batches request
  // must be signed with this key; without it a forged POST could ride into the
  // Queue and reach SIGAP with production bearer/mTLS credentials.
  SIGAP_COMPLIANCE_HMAC_SECRET: string;
};

interface DeliveryResult {
  delivered: boolean;
  status: number;
  responseBody: string;
  movementId: string | null;
}

export default {
  async fetch(request: Request, env: SigapReporterEnv): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === '/batches' && request.method === 'POST') {
      return enqueuePreparedBatch(request, env);
    }

    if (url.pathname === '/events' && request.method === 'POST') {
      return jsonResponse({
        error: 'Per-event SIGAP submission is not supported; submit a prepared signed batch to /batches.',
      }, 410);
    }

    if (url.pathname === '/status' && request.method === 'GET') {
      return handleStatusCheck(env);
    }

    return new Response('Not found', { status: 404 });
  },

  async queue(
    batch: MessageBatch<PreparedSigapBatchEnvelope>,
    env: SigapReporterEnv,
    _ctx: ExecutionContext
  ): Promise<void> {
    for (const message of batch.messages) {
      const errors = validatePreparedBatch(message.body);
      if (errors.length > 0) {
        console.error(`Invalid prepared SIGAP batch ${message.id}: ${errors.join('; ')}`);
        message.ack();
        continue;
      }

      const envelope = message.body;
      try {
        await writeBatchToDeliveryLedger(env, envelope);

        if (await isBatchDelivered(env, envelope.batchId)) {
          message.ack();
          continue;
        }

        const result = await sendBatchToSIGAP(env, envelope);
        await recordDeliveryAttempt(env, envelope.batchId, result);

        // SIGAP acceptance and duplicate acknowledgement are both terminal.
        if (result.delivered) {
          await markBatchDelivered(env, envelope.batchId);
          message.ack();
          continue;
        }

        message.retry({ delaySeconds: retryDelaySeconds(message.attempts) });
      } catch (error) {
        console.error(`SIGAP delivery failed for batch ${envelope.batchId}:`, error);
        await recordDeliveryFailure(env, envelope.batchId);
        message.retry({ delaySeconds: retryDelaySeconds(message.attempts) });
      }
    }
  },
};

async function enqueuePreparedBatch(
  request: Request,
  env: SigapReporterEnv
): Promise<Response> {
  // Read the raw body once so the same bytes are both authenticated and parsed.
  const rawBody = await request.text();

  const authenticationError = await authenticateComplianceOrigin(request, rawBody, env);
  if (authenticationError) return authenticationError;

  let envelope: unknown;
  try {
    envelope = JSON.parse(rawBody);
  } catch {
    return jsonResponse({ error: 'Invalid JSON.' }, 400);
  }

  const errors = validatePreparedBatch(envelope);
  if (errors.length > 0) {
    return jsonResponse({ error: 'Invalid prepared SIGAP batch.', errors }, 422);
  }

  const prepared = envelope as PreparedSigapBatchEnvelope;

  // Queue is the durable transport. The consumer also idempotently creates the
  // ledger row, so a transient D1 failure here cannot strand a batch in D1.
  await env.SIGAP_BATCH_QUEUE.send(prepared, {
    contentType: 'json',
  });
  await writeBatchToDeliveryLedger(env, prepared);

  return jsonResponse({
    accepted: true,
    batchId: prepared.batchId,
    documentFamily: prepared.documentFamily,
  }, 202);
}

/**
 * Authenticate the AWS compliance pipeline as the origin of a prepared batch.
 *
 * Mirrors the odds-feed publisher guard: an HMAC-SHA256 over
 * `timestamp.nonce.rawBody`, verified in constant time via crypto.subtle.verify
 * against SIGAP_COMPLIANCE_HMAC_SECRET, with a bounded timestamp skew window.
 * Returns a Response on failure and null on success.
 */
async function authenticateComplianceOrigin(
  request: Request,
  rawBody: string,
  env: SigapReporterEnv
): Promise<Response | null> {
  const timestamp = request.headers.get('X-SIGAP-Timestamp') ?? '';
  const nonce = request.headers.get('X-SIGAP-Nonce') ?? '';
  const signature = request.headers.get('X-SIGAP-Signature') ?? '';
  const timestampSeconds = Number(timestamp);

  if (!/^\d{10}$/.test(timestamp) || !/^[A-Za-z0-9_-]{16,128}$/.test(nonce)
      || !/^[a-fA-F0-9]{64}$/.test(signature)) {
    return jsonResponse({ error: 'missing or malformed compliance authentication' }, 401);
  }

  const skew = Math.abs(Date.now() - timestampSeconds * 1_000);
  if (!Number.isFinite(timestampSeconds) || skew > MAX_COMPLIANCE_AUTH_SKEW_MS) {
    return jsonResponse({ error: 'compliance timestamp outside replay window' }, 401);
  }

  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(env.SIGAP_COMPLIANCE_HMAC_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['verify']
  );
  const signatureBytes = new Uint8Array(signature.match(/.{2}/g)!.map(byte => parseInt(byte, 16)));
  const valid = await crypto.subtle.verify(
    'HMAC',
    key,
    signatureBytes,
    encoder.encode(`${timestamp}.${nonce}.${rawBody}`)
  );
  if (!valid) return jsonResponse({ error: 'invalid compliance signature' }, 401);

  return null;
}

function validatePreparedBatch(value: unknown): string[] {
  if (!value || typeof value !== 'object') return ['batch envelope must be an object'];

  const envelope = value as Partial<PreparedSigapBatchEnvelope>;
  const errors: string[] = [];

  if (!envelope.batchId || envelope.batchId.length > 128) errors.push('batchId is required');
  if (!envelope.operatorId) errors.push('operatorId is required');
  if (!DOCUMENT_FAMILIES.has(envelope.documentFamily as SigapDocumentFamily)) {
    errors.push('documentFamily must be one of the six SIGAP document families');
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(envelope.referenceDate ?? '')) {
    errors.push('referenceDate must use YYYY-MM-DD');
  }
  if (!envelope.schemaVersion) errors.push('schemaVersion is required');
  if (!isSafeReceptionPath(envelope.receptionPath)) {
    errors.push('receptionPath must be a relative category-specific /lote endpoint');
  }
  if (!Number.isSafeInteger(envelope.recordCount)
      || (envelope.recordCount ?? 0) < 1
      || (envelope.recordCount ?? 0) > MAX_RECORDS_PER_BATCH) {
    errors.push(`recordCount must be between 1 and ${MAX_RECORDS_PER_BATCH}`);
  }
  if (!Number.isSafeInteger(envelope.compressedSizeBytes)
      || (envelope.compressedSizeBytes ?? 0) < 1
      || (envelope.compressedSizeBytes ?? 0) > MAX_COMPRESSED_BYTES) {
    errors.push(`compressedSizeBytes must be between 1 and ${MAX_COMPRESSED_BYTES}`);
  }
  if (!isBase64(envelope.payloadBase64)) errors.push('payloadBase64 must contain the GZIP batch');
  if (envelope.signedXml !== true) errors.push('signedXml must confirm e-CNPJ XML signing');
  if (!envelope.generatedAt || !Number.isFinite(Date.parse(envelope.generatedAt))) {
    errors.push('generatedAt must be an ISO-8601 timestamp');
  }

  return errors;
}

function isSafeReceptionPath(path: string | undefined): boolean {
  return typeof path === 'string'
    && /^\/[A-Za-z0-9/_-]+\/lote$/.test(path)
    && !path.includes('..')
    && !path.includes('//');
}

function isBase64(value: string | undefined): boolean {
  if (!value || value.length % 4 !== 0) return false;
  return /^[A-Za-z0-9+/]+={0,2}$/.test(value);
}

async function handleStatusCheck(env: SigapReporterEnv): Promise<Response> {
  const pending = await env.DB.prepare(
    `SELECT COUNT(*) as count
     FROM sigap_delivery_ledger
     WHERE delivery_status = 'pending'`
  ).first<{ count: number }>();

  const lastDelivered = await env.DB.prepare(
    `SELECT delivered_at
     FROM sigap_delivery_ledger
     WHERE delivery_status = 'delivered'
     ORDER BY delivered_at DESC
     LIMIT 1`
  ).first<{ delivered_at: string | null }>();

  return jsonResponse({
    pendingBatches: pending?.count ?? 0,
    lastDeliveredAt: lastDelivered?.delivered_at ?? null,
    retryTransport: 'cloudflare-queue',
    auditStore: 'd1-delivery-ledger',
  }, 200);
}

async function writeBatchToDeliveryLedger(
  env: SigapReporterEnv,
  envelope: PreparedSigapBatchEnvelope
): Promise<void> {
  const now = new Date().toISOString();
  const payloadSha256 = await sha256Base64Payload(envelope.payloadBase64);

  await env.DB.prepare(
    `INSERT OR IGNORE INTO sigap_delivery_ledger
     (batch_id, operator_id, document_family, reference_date, schema_version,
      reception_path, record_count, compressed_size_bytes, payload_sha256,
      delivery_status, attempt_count, reconciliation_status, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 'not_started', ?, ?)`
  ).bind(
    envelope.batchId,
    envelope.operatorId,
    envelope.documentFamily,
    envelope.referenceDate,
    envelope.schemaVersion,
    envelope.receptionPath,
    envelope.recordCount,
    envelope.compressedSizeBytes,
    payloadSha256,
    now,
    now
  ).run();
}

async function isBatchDelivered(env: SigapReporterEnv, batchId: string): Promise<boolean> {
  const row = await env.DB.prepare(
    `SELECT delivery_status FROM sigap_delivery_ledger WHERE batch_id = ?`
  ).bind(batchId).first<{ delivery_status: string }>();
  return row?.delivery_status === 'delivered';
}

async function markBatchDelivered(env: SigapReporterEnv, batchId: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE sigap_delivery_ledger
     SET delivery_status = 'delivered', delivered_at = ?, updated_at = ?
     WHERE batch_id = ?`
  ).bind(now, now, batchId).run();
}

async function recordDeliveryAttempt(
  env: SigapReporterEnv,
  batchId: string,
  result: DeliveryResult
): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE sigap_delivery_ledger
     SET attempt_count = attempt_count + 1,
         last_http_status = ?,
         last_response_body = ?,
         sigap_movement_id = COALESCE(?, sigap_movement_id),
         last_attempt_at = ?,
         updated_at = ?
     WHERE batch_id = ?`
  ).bind(
    result.status,
    result.responseBody,
    result.movementId,
    now,
    now,
    batchId
  ).run();

  if (!result.delivered) {
    console.error(
      `SIGAP kept batch ${batchId} pending: HTTP ${result.status} ${result.responseBody}`
    );
  }
}

async function recordDeliveryFailure(env: SigapReporterEnv, batchId: string): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE sigap_delivery_ledger
     SET attempt_count = attempt_count + 1,
         last_response_body = 'Worker delivery exception; see Worker logs.',
         last_attempt_at = ?,
         updated_at = ?
     WHERE batch_id = ?`
  ).bind(now, now, batchId).run();
}

/**
 * Submit one immutable, signed batch envelope to its official reception path.
 * Only 2xx and 409 are delivered. Every other response remains pending so the
 * Queue retry/dead-letter policy, not D1 polling, owns recovery.
 */
async function sendBatchToSIGAP(
  env: SigapReporterEnv,
  envelope: PreparedSigapBatchEnvelope
): Promise<DeliveryResult> {
  const request = new Request(`${env.SIGAP_API_URL}${envelope.receptionPath}`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${env.SIGAP_BEARER_TOKEN}`,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-Idempotency-Key': envelope.batchId,
      'X-SIGAP-Schema-Version': envelope.schemaVersion,
    },
    // At the regulatory boundary the payload is a lote, not a stream of
    // internal platform events. The XML is already signed and GZIP-compressed.
    body: JSON.stringify({ lote: envelope.payloadBase64 }),
  });

  const response = await env.SIGAP_MTLS.fetch(request);
  const responseBody = (await response.text()).slice(0, 4_096);
  return {
    delivered: response.ok || response.status === 409,
    status: response.status,
    responseBody,
    movementId: extractMovementId(responseBody),
  };
}

function extractMovementId(responseBody: string): string | null {
  try {
    const body = JSON.parse(responseBody) as Record<string, unknown>;
    const value = body.movementId ?? body.movimentacaoId ?? body.idMovimentacao;
    return typeof value === 'string' && value.length <= 256 ? value : null;
  } catch {
    return null;
  }
}

async function sha256Base64Payload(value: string): Promise<string> {
  const compressedPayload = Uint8Array.from(atob(value), character => character.charCodeAt(0));
  const digest = await crypto.subtle.digest('SHA-256', compressedPayload);
  return [...new Uint8Array(digest)]
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

function retryDelaySeconds(attempts: number): number {
  return Math.min(2 ** Math.max(attempts - 1, 0) * 30, MAX_RETRY_DELAY_SECONDS);
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
