// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { describe, expect, it, vi } from 'vitest';
import sigapReporter, { type PreparedSigapBatchEnvelope } from '../src/sigap-reporter.js';

const COMPLIANCE_SECRET = 'sigap-compliance-test-secret';

interface LedgerRow {
  deliveryStatus: 'pending' | 'delivered';
  attemptCount: number;
  deliveredAt?: string;
  lastHttpStatus?: number;
  movementId?: string;
}

class LedgerDB {
  readonly rows = new Map<string, LedgerRow>();
  readonly preparedSql: string[] = [];

  prepare(sql: string) {
    this.preparedSql.push(sql);
    let values: unknown[] = [];
    const statement = {
      bind: (...args: unknown[]) => {
        values = args;
        return statement;
      },
      run: async () => {
        if (sql.includes('INSERT OR IGNORE')) {
          const id = values[0] as string;
          if (!this.rows.has(id)) {
            this.rows.set(id, { deliveryStatus: 'pending', attemptCount: 0 });
          }
        } else if (sql.includes("delivery_status = 'delivered'")) {
          const id = values[2] as string;
          const row = this.rows.get(id)!;
          row.deliveryStatus = 'delivered';
          row.deliveredAt = values[0] as string;
        } else if (sql.includes('last_http_status = ?')) {
          const id = values[5] as string;
          const row = this.rows.get(id)!;
          row.attemptCount++;
          row.lastHttpStatus = values[0] as number;
          if (values[2]) row.movementId = values[2] as string;
        } else if (sql.includes('attempt_count = attempt_count + 1')) {
          const id = values[2] as string;
          this.rows.get(id)!.attemptCount++;
        }
        return { success: true };
      },
      first: async () => {
        if (sql.includes('SELECT delivery_status FROM')) {
          const row = this.rows.get(values[0] as string);
          return row ? { delivery_status: row.deliveryStatus } : null;
        }
        if (sql.includes('COUNT(*)')) {
          return {
            count: [...this.rows.values()].filter(row => row.deliveryStatus === 'pending').length,
          };
        }
        if (sql.includes('SELECT delivered_at')) {
          const deliveredAt = [...this.rows.values()]
            .map(row => row.deliveredAt)
            .filter(Boolean)
            .sort()
            .at(-1);
          return deliveredAt ? { delivered_at: deliveredAt } : null;
        }
        return null;
      },
    };
    return statement;
  }
}

function envelope(): PreparedSigapBatchEnvelope {
  return {
    batchId: 'sports-bets-2026-07-20-001',
    operatorId: 'DEMO-001',
    documentFamily: 'sports_bets',
    referenceDate: '2026-07-20',
    schemaVersion: '1.0',
    receptionPath: '/apostas-esportivas/lote',
    recordCount: 125,
    compressedSizeBytes: 12,
    payloadBase64: 'H4sIAAAAAAAA',
    signedXml: true,
    generatedAt: '2026-07-22T02:30:00.000Z',
  };
}

async function signCompliance(body: string, timestamp: string, nonce: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(COMPLIANCE_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const bytes = await crypto.subtle.sign(
    'HMAC', key, encoder.encode(`${timestamp}.${nonce}.${body}`)
  );
  return [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, '0')).join('');
}

async function batchRequest(
  body: string,
  options: { signed?: boolean; nonce?: string; signatureOverride?: string } = {}
): Promise<Request> {
  const { signed = true, nonce = 'compliance-request-id-000001', signatureOverride } = options;
  const timestamp = String(Math.floor(Date.now() / 1_000));
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (signed) {
    headers['X-SIGAP-Timestamp'] = timestamp;
    headers['X-SIGAP-Nonce'] = nonce;
    headers['X-SIGAP-Signature'] = signatureOverride ?? await signCompliance(body, timestamp, nonce);
  }
  return new Request('https://worker.example/batches', {
    method: 'POST',
    headers,
    body,
  });
}

function environment(status = 202) {
  const db = new LedgerDB();
  const send = vi.fn(async () => undefined);
  const mtlsFetch = vi.fn(async () => new Response('{"movementId":"mov-1"}', { status }));
  return {
    db,
    send,
    mtlsFetch,
    env: {
      DB: db,
      SIGAP_API_URL: 'https://homolog.sigap.example/v1',
      SIGAP_BATCH_QUEUE: { send },
      SIGAP_MTLS: { fetch: mtlsFetch },
      SIGAP_BEARER_TOKEN: 'temporary-jwt',
      SIGAP_COMPLIANCE_HMAC_SECRET: COMPLIANCE_SECRET,
    } as unknown as Parameters<typeof sigapReporter.fetch>[1],
  };
}

function queueBatch(body: PreparedSigapBatchEnvelope, attempts = 1) {
  const ack = vi.fn();
  const retry = vi.fn();
  return {
    ack,
    retry,
    batch: {
      queue: 'sigap-batch-delivery',
      messages: [{
        id: 'queue-message-1',
        timestamp: new Date(),
        body,
        attempts,
        ack,
        retry,
      }],
    } as unknown as MessageBatch<PreparedSigapBatchEnvelope>,
  };
}

describe('SIGAP prepared batch delivery', () => {
  it('accepts a prepared signed batch and publishes it to Queue', async () => {
    const { env, send, db } = environment();
    const response = await sigapReporter.fetch(
      await batchRequest(JSON.stringify(envelope())), env
    );

    expect(response.status).toBe(202);
    expect(send).toHaveBeenCalledWith(envelope(), { contentType: 'json' });
    expect(db.rows.get(envelope().batchId)?.deliveryStatus).toBe('pending');
    const insert = db.preparedSql.find(sql => sql.includes('INSERT OR IGNORE'));
    expect(insert).toContain('sigap_delivery_ledger');
    expect(insert).toContain('batch_id');
    expect(insert).toContain('document_family');
    expect(insert).toContain('payload_sha256');
    expect(insert).toContain('reconciliation_status');
  });

  it('rejects an unsigned batch before it can reach the Queue', async () => {
    const { env, send } = environment();
    const response = await sigapReporter.fetch(
      await batchRequest(JSON.stringify(envelope()), { signed: false }), env
    );

    expect(response.status).toBe(401);
    expect(send).not.toHaveBeenCalled();
  });

  it('rejects a batch carrying an invalid compliance signature', async () => {
    const { env, send } = environment();
    const response = await sigapReporter.fetch(
      await batchRequest(JSON.stringify(envelope()), { signatureOverride: '00'.repeat(32) }), env
    );

    expect(response.status).toBe(401);
    expect(send).not.toHaveBeenCalled();
  });

  it('refuses the obsolete event-by-event endpoint', async () => {
    const { env } = environment();
    const response = await sigapReporter.fetch(new Request('https://worker.example/events', {
      method: 'POST', body: '{}',
    }), env);
    expect(response.status).toBe(410);
  });

  it.each([202, 409])('marks HTTP %s as delivered through the mTLS binding', async status => {
    const { env, db, mtlsFetch } = environment(status);
    const message = queueBatch(envelope());

    await sigapReporter.queue(message.batch, env, {} as ExecutionContext);

    expect(message.ack).toHaveBeenCalledOnce();
    expect(message.retry).not.toHaveBeenCalled();
    expect(db.rows.get(envelope().batchId)).toMatchObject({
      deliveryStatus: 'delivered',
      attemptCount: 1,
      lastHttpStatus: status,
      movementId: 'mov-1',
    });
    const request = mtlsFetch.mock.calls[0][0] as Request;
    expect(request.url).toBe('https://homolog.sigap.example/v1/apostas-esportivas/lote');
    expect(request.headers.get('Authorization')).toBe('Bearer temporary-jwt');
    expect(await request.json()).toEqual({ lote: envelope().payloadBase64 });
  });

  it.each([400, 429, 500])('keeps HTTP %s pending for Queue retry', async status => {
    const { env, db } = environment(status);
    const message = queueBatch(envelope(), 2);

    await sigapReporter.queue(message.batch, env, {} as ExecutionContext);

    expect(message.ack).not.toHaveBeenCalled();
    expect(message.retry).toHaveBeenCalledWith({ delaySeconds: 60 });
    expect(db.rows.get(envelope().batchId)).toMatchObject({
      deliveryStatus: 'pending',
      attemptCount: 1,
      lastHttpStatus: status,
    });
  });

  it('rejects a batch that was not signed or exceeds official limits', async () => {
    const { env, send } = environment();
    const invalid = { ...envelope(), signedXml: false, recordCount: 7_501 };
    const response = await sigapReporter.fetch(
      await batchRequest(JSON.stringify(invalid)), env
    );

    expect(response.status).toBe(422);
    expect(send).not.toHaveBeenCalled();
  });
});
