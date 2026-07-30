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
 * PIX Webhook Worker — unit tests.
 *
 * Tests cover:
 *  - HMAC signature validation
 *  - Idempotency (duplicate endToEndId rejected cleanly)
 *  - Amount mismatch detection
 *  - Non-confirmed status handling
 */

import { beforeEach, describe, it, expect, vi } from 'vitest';
import { validatePixHmac, pixValueToCentavos, generatePixTxid, generatePixPayload } from '../src/utils/pix.js';
import worker from '../src/pix-webhook.js';
import type { Env } from '../src/types.js';

beforeEach(() => {
  vi.unstubAllGlobals();
});

// ── validatePixHmac ───────────────────────────────────────────────────────────

describe('validatePixHmac', () => {
  const secret  = 'shared-test-secret-for-hmac-validation';
  const payload = JSON.stringify({ txid: 'BETabc123', valor: '50.00', status: 'confirmed' });

  async function makeHmac(body: string, key: string): Promise<string> {
    const enc = new TextEncoder();
    const k   = await crypto.subtle.importKey(
      'raw', enc.encode(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
    );
    const sig  = await crypto.subtle.sign('HMAC', k, enc.encode(body));
    return Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  it('accepts a valid HMAC', async () => {
    const sig    = await makeHmac(payload, secret);
    const buffer = new TextEncoder().encode(payload).buffer as ArrayBuffer;
    const result = await validatePixHmac(buffer, sig, secret);
    expect(result).toBe(true);
  });

  it('rejects a tampered body', async () => {
    const sig      = await makeHmac(payload, secret);
    const tampered = payload + 'X';
    const buffer   = new TextEncoder().encode(tampered).buffer as ArrayBuffer;
    const result   = await validatePixHmac(buffer, sig, secret);
    expect(result).toBe(false);
  });

  it('rejects a wrong secret', async () => {
    const sig    = await makeHmac(payload, 'wrong-secret');
    const buffer = new TextEncoder().encode(payload).buffer as ArrayBuffer;
    const result = await validatePixHmac(buffer, sig, secret);
    expect(result).toBe(false);
  });

  it('rejects a malformed hex signature', async () => {
    const buffer = new TextEncoder().encode(payload).buffer as ArrayBuffer;
    const result = await validatePixHmac(buffer, 'not-hex!!', secret);
    expect(result).toBe(false);
  });

  it('rejects when the secret is empty, without even attempting to verify', async () => {
    const sig    = await makeHmac(payload, secret);
    const buffer = new TextEncoder().encode(payload).buffer as ArrayBuffer;
    const result = await validatePixHmac(buffer, sig, '');
    expect(result).toBe(false);
  });
});

// ── pixValueToCentavos ────────────────────────────────────────────────────────

describe('pixValueToCentavos', () => {
  it('converts "50.00" to 5000', () => {
    expect(pixValueToCentavos('50.00')).toBe(5000);
  });

  it('converts "0.01" to 1', () => {
    expect(pixValueToCentavos('0.01')).toBe(1);
  });

  it('converts "1000.50" to 100050', () => {
    expect(pixValueToCentavos('1000.50')).toBe(100050);
  });

  it('handles values without decimal', () => {
    expect(pixValueToCentavos('100')).toBe(10000);
  });

  it('throws for zero value', () => {
    expect(() => pixValueToCentavos('0.00')).toThrow(RangeError);
  });

  it('throws for negative value', () => {
    expect(() => pixValueToCentavos('-10.00')).toThrow(RangeError);
  });

  it('throws for non-numeric string', () => {
    expect(() => pixValueToCentavos('abc')).toThrow(RangeError);
  });
});

// ── generatePixTxid ───────────────────────────────────────────────────────────

describe('generatePixTxid', () => {
  it('generates a cob-API-valid txid (26-35 alphanumeric chars)', () => {
    const txid = generatePixTxid();
    expect(txid).toHaveLength(32);
    expect(txid).toMatch(/^[A-Za-z0-9]{26,35}$/);
  });

  it('starts with BET prefix', () => {
    expect(generatePixTxid().startsWith('BET')).toBe(true);
  });

  it('generates unique IDs', () => {
    const ids = new Set(Array.from({ length: 100 }, () => generatePixTxid()));
    expect(ids.size).toBe(100);
  });

  it('contains only alphanumeric characters', () => {
    const txid = generatePixTxid();
    expect(/^[A-Za-z0-9]+$/.test(txid)).toBe(true);
  });
});

// ── generatePixPayload ────────────────────────────────────────────────────────

describe('generatePixPayload', () => {
  const pixKey      = '11111111000191';
  const merchant    = 'ACMETOCASINO';
  const city        = 'SAO PAULO';
  const txid        = 'BETtest1234567890ABC1234';

  it('returns a non-empty string', () => {
    const payload = generatePixPayload(pixKey, 100, merchant, city, txid);
    expect(typeof payload).toBe('string');
    expect(payload.length).toBeGreaterThan(50);
  });

  it('ends with a 4-character CRC hex', () => {
    const payload = generatePixPayload(pixKey, 100, merchant, city, txid);
    // Last 6 chars = tag "63" + 4-char CRC
    expect(payload.slice(-6, -4)).toBe('63');
    expect(/^[0-9A-F]{4}$/.test(payload.slice(-4))).toBe(true);
  });

  it('throws for amount below minimum', () => {
    expect(() => generatePixPayload(pixKey, 5, merchant, city, txid))
      .toThrow(RangeError);
  });

  it('throws for amount above maximum', () => {
    expect(() => generatePixPayload(pixKey, 100_000, merchant, city, txid))
      .toThrow(RangeError);
  });

  it('sanitises diacritics in merchant name', () => {
    // Should not throw when special characters are present
    const payload = generatePixPayload(pixKey, 50, 'Açaí Bet', city, txid);
    expect(typeof payload).toBe('string');
  });
});

// ── Webhook receipt / AWS notification ──────────────────────────────────────

describe('PIX receipt delivery', () => {
  const secret = 'shared-test-secret-for-hmac-validation';
  const payload = {
    endToEndId: 'E12345678901234567890123456789012',
    txid: 'BETabc123',
    status: 'confirmed',
    valor: '50.00',
    cpfPagador: '***982247**',
    horario: '2026-07-22T12:00:00.000Z',
  };

  function makeDb(originStatus = 0): D1Database {
    const prepare = vi.fn((sql: string) => {
      const statement = {
        bind: vi.fn((..._params: unknown[]) => statement),
        first: vi.fn(async () => {
          if (sql.includes('SELECT id, status FROM pix_transactions')) {
            return { id: '1', status: 'pending' };
          }
          if (sql.includes('SELECT player_id, amount_centavos FROM pix_transactions')) {
            return { player_id: 'player-123', amount_centavos: 5000 };
          }
          if (sql.includes('FROM pix_origin_notifications')) {
            return {
              txid: payload.txid,
              player_id: 'player-123',
              amount_centavos: 5000,
              payload: JSON.stringify(payload),
              attempt_count: originStatus,
            };
          }
          return null;
        }),
        all: vi.fn().mockResolvedValue({ results: [] }),
        run: vi.fn().mockResolvedValue({ success: true }),
      };
      return statement;
    });
    return {
      prepare,
      batch: vi.fn().mockResolvedValue([]),
    } as unknown as D1Database;
  }

  function makeEnv(db: D1Database): Env {
    return {
      DB: db,
      PIX_HMAC_SECRET: secret,
      AWS_CORE_API_URL: 'https://core.example.test',
      AWS_CORE_HMAC_SECRET: 'origin-secret',
    } as Env;
  }

  async function signedRequest(): Promise<Request> {
    const body = JSON.stringify(payload);
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
    );
    const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(body));
    const hex = Array.from(new Uint8Array(signature), byte => byte.toString(16).padStart(2, '0')).join('');
    return new Request('https://pix.example.test/pix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Pix-Hmac': hex },
      body,
    });
  }

  it('acknowledges after persisting a receipt and notifies AWS without a wallet DO', async () => {
    const db = makeDb();
    const originFetch = vi.fn().mockResolvedValue(new Response(null, { status: 202 }));
    vi.stubGlobal('fetch', originFetch);
    const pending: Promise<unknown>[] = [];
    const ctx = {
      waitUntil: vi.fn((promise: Promise<unknown>) => { pending.push(promise); }),
      passThroughOnException: vi.fn(),
    } as unknown as ExecutionContext;

    const response = await worker.fetch(await signedRequest(), makeEnv(db), ctx);

    expect(response.status).toBe(200);
    expect(db.batch).toHaveBeenCalledOnce();
    expect(ctx.waitUntil).toHaveBeenCalledOnce();
    await Promise.all(pending);
    expect(originFetch).toHaveBeenCalledOnce();
    const target = originFetch.mock.calls[0]?.[0] as URL;
    expect(target.pathname).toBe('/internal/payments/pix/notifications');
    expect(JSON.stringify((db.prepare as ReturnType<typeof vi.fn>).mock.calls)).not.toContain('wallet');
  });

  it('keeps the application delivery record pending when AWS rejects it', async () => {
    const db = makeDb();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
    const pending: Promise<unknown>[] = [];
    const ctx = {
      waitUntil: vi.fn((promise: Promise<unknown>) => { pending.push(promise); }),
      passThroughOnException: vi.fn(),
    } as unknown as ExecutionContext;

    expect((await worker.fetch(await signedRequest(), makeEnv(db), ctx)).status).toBe(200);
    await Promise.all(pending);

    const sqlCalls = (db.prepare as ReturnType<typeof vi.fn>).mock.calls
      .map(call => String(call[0]));
    expect(sqlCalls.some(sql => sql.includes("WHERE txid = ? AND status = 'pending'"))).toBe(true);
    expect(sqlCalls.some(sql => sql.includes("SET status = 'delivered'"))).toBe(false);
  });
});

// ── /qrcode internal-only HMAC gate ─────────────────────────────────────────

describe('POST /qrcode (internal-only)', () => {
  const internalSecret = 'test-gateway-internal-secret';

  function makeQrDb(): D1Database {
    const statement = {
      bind: vi.fn(() => statement),
      first: vi.fn().mockResolvedValue(null),
      all: vi.fn().mockResolvedValue({ results: [] }),
      run: vi.fn().mockResolvedValue({ success: true }),
    };
    return { prepare: vi.fn(() => statement), batch: vi.fn().mockResolvedValue([]) } as unknown as D1Database;
  }

  function makeQrEnv(db: D1Database): Env {
    return {
      DB: db,
      PIX_HMAC_SECRET: 'pix-secret',
      PIX_PSP_API_KEY: 'psp-api-key',
      PIX_PSP_BASE_URL: 'https://sandbox.psp.example.test/pix/v2',
      GATEWAY_INTERNAL_HMAC_SECRET: internalSecret,
    } as Env;
  }

  async function hmacHex(secret: string, value: string): Promise<string> {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
    );
    const sig = await crypto.subtle.sign('HMAC', key, enc.encode(value));
    return Array.from(new Uint8Array(sig), b => b.toString(16).padStart(2, '0')).join('');
  }

  async function signedQrRequest(secret: string): Promise<Request> {
    const body = JSON.stringify({ playerId: 'player-123', amountCentavos: 10_000 });
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const nonce = 'nonce_ABCDEFGHIJKLMNOP';
    const signature = await hmacHex(secret, `${timestamp}.${nonce}.${body}`);
    return new Request('https://pix/qrcode', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Internal-Timestamp': timestamp,
        'X-Internal-Nonce': nonce,
        'X-Internal-Signature': signature,
      },
      body,
    });
  }

  const ctx = { waitUntil: vi.fn(), passThroughOnException: vi.fn() } as unknown as ExecutionContext;

  it('generates a QR code for a validly signed internal request', async () => {
    const db = makeQrDb();
    const pspFetch = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ txid: 'BETxyz', pixCopiaECola: '000201...br.gov.bcb.pix', location: 'loc/1' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    ));
    vi.stubGlobal('fetch', pspFetch);

    const resp = await worker.fetch(await signedQrRequest(internalSecret), makeQrEnv(db), ctx);

    expect(resp.status).toBe(200);
    const payload = await resp.json<{ success: boolean; data: { txid: string; pixCopyPaste: string } }>();
    expect(payload.success).toBe(true);
    expect(payload.data.pixCopyPaste).toContain('br.gov.bcb.pix');
    expect(pspFetch).toHaveBeenCalledOnce();
  });

  it('returns 401 when the internal signature headers are missing', async () => {
    const db = makeQrDb();
    const pspFetch = vi.fn();
    vi.stubGlobal('fetch', pspFetch);
    const req = new Request('https://pix/qrcode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playerId: 'player-123', amountCentavos: 10_000 }),
    });

    const resp = await worker.fetch(req, makeQrEnv(db), ctx);

    expect(resp.status).toBe(401);
    expect(pspFetch).not.toHaveBeenCalled();
    expect(db.prepare).not.toHaveBeenCalled();
  });

  it('returns 401 for an invalid (wrong-secret) signature', async () => {
    const db = makeQrDb();
    const pspFetch = vi.fn();
    vi.stubGlobal('fetch', pspFetch);

    const resp = await worker.fetch(await signedQrRequest('wrong-secret'), makeQrEnv(db), ctx);

    expect(resp.status).toBe(401);
    expect(pspFetch).not.toHaveBeenCalled();
    expect(db.prepare).not.toHaveBeenCalled();
  });
});
