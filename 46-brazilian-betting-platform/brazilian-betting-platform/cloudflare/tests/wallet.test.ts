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
 * WalletBalance Durable Object — unit tests.
 *
 * Tests cover the internal HMAC auth gate, atomic balance operations,
 * idempotency, closed-loop PIX key enforcement, and business rules
 * (minimum/maximum amounts, insufficient balance).
 *
 * The Durable Object is tested by constructing instances directly with a
 * mock DurableObjectState, without deploying to the Workers runtime. The
 * transaction mock shares a single backing store so that values written in
 * one storage transaction (e.g. `registered_pix_key`) are visible to a
 * later one — matching real Durable Object storage semantics.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { WalletBalance } from '../src/wallet.js';
import type { WalletState } from '../src/types.js';

const INTERNAL_SECRET = 'test-internal-hmac-secret';

// ── Mock DurableObjectState ───────────────────────────────────────────────────

interface MockStorage {
  get:         ReturnType<typeof vi.fn>;
  put:         ReturnType<typeof vi.fn>;
  delete:      ReturnType<typeof vi.fn>;
  list:        ReturnType<typeof vi.fn>;
  setAlarm:    ReturnType<typeof vi.fn>;
  deleteAlarm: ReturnType<typeof vi.fn>;
  transaction: ReturnType<typeof vi.fn>;
  _store:      Map<string, unknown>;
}

function makeMockStorage(store: Map<string, unknown>): MockStorage {
  const api: MockStorage = {
    get:    vi.fn(async (key: string) => store.get(key) ?? undefined),
    put:    vi.fn(async (key: string, value: unknown) => { store.set(key, value); }),
    delete: vi.fn(async (key: string) => store.delete(key)),
    list:   vi.fn(async () => new Map()),
    setAlarm:    vi.fn(),
    deleteAlarm: vi.fn(),
    // Transactions operate on the same backing store (real DO semantics).
    transaction: vi.fn(async (fn: (txn: MockStorage) => Promise<Response>) => fn(api)),
    _store: store,
  };
  return api;
}

function makeMockState(wallet?: WalletState): DurableObjectState {
  const store = new Map<string, unknown>();
  if (wallet) store.set('wallet', wallet);
  const storage = makeMockStorage(store);
  return {
    id:      { toString: () => 'player-uuid-123', name: 'player-uuid-123', equals: vi.fn() } as unknown as DurableObjectId,
    storage: storage as unknown as DurableObjectStorage,
    blockConcurrencyWhile: vi.fn((fn: () => Promise<unknown>) => fn()),
    acceptWebSocket:       vi.fn(),
    getWebSockets:         vi.fn(() => []),
  } as unknown as DurableObjectState;
}

function storeOf(state: DurableObjectState): Map<string, unknown> {
  return (state.storage as unknown as MockStorage)._store;
}

function makeMockWalletEnv() {
  return {
    DB: {
      prepare: vi.fn().mockReturnValue({
        bind: vi.fn().mockReturnValue({
          run:   vi.fn().mockResolvedValue({ success: true }),
          first: vi.fn().mockResolvedValue(null),
          all:   vi.fn().mockResolvedValue({ results: [] }),
        }),
      }),
    } as unknown as D1Database,
    PIX_PSP_BASE_URL: 'https://sandbox.psp.example.com',
    PIX_HMAC_SECRET:  'test-secret',
    PIX_PSP_API_KEY:  'psp-api-key',
    WALLET_INTERNAL_HMAC_SECRET: INTERNAL_SECRET,
  };
}

// ── Signing helpers ─────────────────────────────────────────────────────────

async function hmacHex(secret: string, value: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(value));
  return Array.from(new Uint8Array(sig), b => b.toString(16).padStart(2, '0')).join('');
}

async function makeWalletRequest(
  path: string,
  body?: unknown,
  opts: { sign?: boolean; nonce?: string } = {}
): Promise<Request> {
  const sign     = opts.sign ?? true;
  const bodyText = body !== undefined ? JSON.stringify(body) : '';
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  if (sign) {
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const nonce     = opts.nonce ?? `nonce-${crypto.randomUUID()}`;
    headers['X-Wallet-Timestamp'] = timestamp;
    headers['X-Wallet-Nonce']     = nonce;
    headers['X-Wallet-Signature'] = await hmacHex(INTERNAL_SECRET, `${timestamp}.${nonce}.${bodyText}`);
  }

  return new Request(`https://wallet${path}`, {
    method:  body !== undefined ? 'POST' : 'GET',
    headers,
    body:    body !== undefined ? bodyText : undefined,
  });
}

// Stub the outbound PIX PSP call so successful withdrawals don't hit the network.
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── Internal auth gate ────────────────────────────────────────────────────────

describe('Internal HMAC auth gate', () => {
  it('rejects a request with no signature (401)', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/balance', undefined, { sign: false }));
    expect(resp.status).toBe(401);
  });

  it('rejects a POST with no signature (401)', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest(
      '/deposit', { amountCentavos: 5000, reference: 'pix:x' }, { sign: false }
    ));
    expect(resp.status).toBe(401);
  });

  it('rejects a tampered body (signature no longer matches, 401)', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const signed = await makeWalletRequest('/deposit', { amountCentavos: 5000, reference: 'pix:a' });
    // Replace the body while keeping the original signature headers.
    const tampered = new Request(signed.url, {
      method:  'POST',
      headers: signed.headers,
      body:    JSON.stringify({ amountCentavos: 999999, reference: 'pix:a' }),
    });
    const resp = await wallet.fetch(tampered);
    expect(resp.status).toBe(401);
  });

  it('fails closed with 503 when the secret is unconfigured', async () => {
    const env = { ...makeMockWalletEnv(), WALLET_INTERNAL_HMAC_SECRET: '' };
    const wallet = new WalletBalance(makeMockState(), env as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/balance'));
    expect(resp.status).toBe(503);
  });
});

// ── Balance ────────────────────────────────────────────────────────────────────

describe('GET /balance', () => {
  it('returns zero balance for new wallet', async () => {
    const wallet  = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp    = await wallet.fetch(await makeWalletRequest('/balance'));
    const body    = await resp.json<{ data: { balanceBRL: number } }>();
    expect(body.data.balanceBRL).toBe(0);
  });

  it('returns existing balance', async () => {
    const state  = makeMockState({
      playerId: 'player-uuid-123',
      balanceCentavos: 10000,
      reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/balance'));
    const body   = await resp.json<{ data: { balanceBRL: number } }>();
    expect(body.data.balanceBRL).toBe(100);
  });
});

// ── Deposit ────────────────────────────────────────────────────────────────────

describe('POST /deposit', () => {
  it('credits the wallet', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 5000,
      reference: 'pix:E123456789',
    }));
    expect(resp.status).toBe(201);
    const body = await resp.json<{ data: { balanceCentavos: number } }>();
    expect(body.data.balanceCentavos).toBe(5000);
  });

  it('registers the pix key of the first deposit that carries one', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 5000,
      reference: 'pix:E1',
      pixKey: 'player@bank.com',
    }));
    expect(resp.status).toBe(201);
    expect(storeOf(state).get('registered_pix_keys')).toEqual(['player@bank.com']);
  });

  it('accumulates every distinct deposit pix key (same-CPF accounts)', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 5000, reference: 'pix:E1', pixKey: 'first@bank.com',
    }));
    await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 5000, reference: 'pix:E2', pixKey: 'second@bank.com',
    }));
    // Duplicate key must not be stored twice.
    await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 5000, reference: 'pix:E3', pixKey: 'first@bank.com',
    }));
    expect(storeOf(state).get('registered_pix_keys')).toEqual(['first@bank.com', 'second@bank.com']);
  });

  it('rejects negative amount', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: -100,
      reference: 'pix:neg',
    }));
    expect(resp.status).toBe(400);
  });

  it('rejects zero amount', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 0,
      reference: 'pix:zero',
    }));
    expect(resp.status).toBe(400);
  });

  it('rejects missing reference', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 5000,
    }));
    expect(resp.status).toBe(400);
  });
});

// ── Debit (bet placement) ──────────────────────────────────────────────────────

describe('POST /debit', () => {
  it('debits available balance', async () => {
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 10000, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/debit', {
      amountCentavos: 500,
      reference: 'bet:uuid-1',
    }));
    expect(resp.status).toBe(200);
    const body = await resp.json<{ data: { balanceCentavos: number } }>();
    expect(body.data.balanceCentavos).toBe(9500);
  });

  it('rejects debit when insufficient balance', async () => {
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 100, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/debit', {
      amountCentavos: 5000,
      reference: 'bet:uuid-2',
    }));
    expect(resp.status).toBe(422);
    const body = await resp.json<{ error: string }>();
    expect(body.error).toContain('Saldo insuficiente');
  });
});

// ── Withdraw ──────────────────────────────────────────────────────────────────

describe('POST /withdraw', () => {
  it('rejects withdrawal below minimum (R$ 10)', async () => {
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 100000, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/withdraw', {
      amountCentavos: 500, // R$ 5.00 — below R$ 10 minimum
      reference: 'wd:1',
    }));
    expect(resp.status).toBe(422);
    const body = await resp.json<{ error: string }>();
    expect(body.error).toContain('mínimo');
  });

  it('rejects withdrawal above maximum (R$ 50,000)', async () => {
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 999_999_99, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/withdraw', {
      amountCentavos: 5_100_000, // R$ 51,000 — above limit
      reference: 'wd:2',
    }));
    expect(resp.status).toBe(422);
    const body = await resp.json<{ error: string }>();
    expect(body.error).toContain('máximo');
  });

  it('rejects withdrawal to an unregistered pix key (422)', async () => {
    // No deposit has registered a key yet.
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 100000, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/withdraw', {
      amountCentavos: 5000, reference: 'wd:nokey', pixKey: 'anything@bank.com',
    }));
    expect(resp.status).toBe(422);
    const body = await resp.json<{ error: string }>();
    expect(body.error).toContain('chave de depósito');
  });

  it('rejects withdrawal to a DIFFERENT pix key than the deposit key (422)', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 100000, reference: 'dep:1', pixKey: 'player@bank.com',
    }));
    const resp = await wallet.fetch(await makeWalletRequest('/withdraw', {
      amountCentavos: 5000, reference: 'wd:diff', pixKey: 'attacker@bank.com',
    }));
    expect(resp.status).toBe(422);
    const body = await resp.json<{ error: string }>();
    expect(body.error).toContain('chave de depósito');
  });

  it('allows withdrawal to the registered deposit pix key', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 100000, reference: 'dep:2', pixKey: 'player@bank.com',
    }));
    const resp = await wallet.fetch(await makeWalletRequest('/withdraw', {
      amountCentavos: 5000, reference: 'wd:same', pixKey: 'player@bank.com',
    }));
    expect(resp.status).toBe(200);
    const body = await resp.json<{ data: { status: string } }>();
    expect(body.data.status).toBe('pending');
  });

  it('rejects withdrawal when insufficient balance', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    // Register the key with a small deposit, then try to overdraw.
    await wallet.fetch(await makeWalletRequest('/deposit', {
      amountCentavos: 1000, reference: 'dep:3', pixKey: 'player@bank.com',
    }));
    const resp = await wallet.fetch(await makeWalletRequest('/withdraw', {
      amountCentavos: 5000, reference: 'wd:3', pixKey: 'player@bank.com',
    }));
    expect(resp.status).toBe(422);
    const body = await resp.json<{ error: string }>();
    expect(body.error).toContain('Saldo insuficiente');
  });
});

// ── Money-safety fixes: idempotency, settlement, replay ─────────────────────────

describe('idempotency and settlement', () => {
  it('does not double-debit on a retried bet with the same reference', async () => {
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 10000, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const first  = await wallet.fetch(await makeWalletRequest('/debit', { amountCentavos: 500, reference: 'bet:dup' }));
    const retry  = await wallet.fetch(await makeWalletRequest('/debit', { amountCentavos: 500, reference: 'bet:dup' }));
    expect(first.status).toBe(200);
    expect(retry.status).toBe(200);
    const b = await retry.json<{ data: { balanceCentavos: number; idempotent?: boolean } }>();
    expect(b.data.idempotent).toBe(true);
    expect(b.data.balanceCentavos).toBe(9500); // charged once, not twice
  });

  it('does not double-reserve or double-call the PSP on a retried withdraw', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    await wallet.fetch(await makeWalletRequest('/deposit', { amountCentavos: 100000, reference: 'dep:w', pixKey: 'p@bank.com' }));
    const psp = fetch as unknown as { mock: { calls: unknown[] } };
    const before = psp.mock.calls.length;
    await wallet.fetch(await makeWalletRequest('/withdraw', { amountCentavos: 5000, reference: 'wd:dup', pixKey: 'p@bank.com' }));
    await wallet.fetch(await makeWalletRequest('/withdraw', { amountCentavos: 5000, reference: 'wd:dup', pixKey: 'p@bank.com' }));
    const wallet2 = storeOf(state).get('wallet') as { reservedCentavos: number };
    expect(wallet2.reservedCentavos).toBe(5000); // reserved once
    expect(psp.mock.calls.length - before).toBe(1); // PSP called once
  });

  it('settle reduces balance and clears the reserve exactly once', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    await wallet.fetch(await makeWalletRequest('/deposit', { amountCentavos: 100000, reference: 'dep:s', pixKey: 'p@bank.com' }));
    await wallet.fetch(await makeWalletRequest('/withdraw', { amountCentavos: 5000, reference: 'wd:s', pixKey: 'p@bank.com' }));
    await wallet.fetch(await makeWalletRequest('/withdraw/settle', { reference: 'wd:s' }));
    await wallet.fetch(await makeWalletRequest('/withdraw/settle', { reference: 'wd:s' })); // idempotent
    const w = storeOf(state).get('wallet') as { balanceCentavos: number; reservedCentavos: number };
    expect(w.balanceCentavos).toBe(95000);
    expect(w.reservedCentavos).toBe(0);
  });

  it('cancel releases the reserve and leaves balance intact', async () => {
    const state  = makeMockState();
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    await wallet.fetch(await makeWalletRequest('/deposit', { amountCentavos: 100000, reference: 'dep:c', pixKey: 'p@bank.com' }));
    await wallet.fetch(await makeWalletRequest('/withdraw', { amountCentavos: 5000, reference: 'wd:c', pixKey: 'p@bank.com' }));
    await wallet.fetch(await makeWalletRequest('/withdraw/cancel', { reference: 'wd:c' }));
    const w = storeOf(state).get('wallet') as { balanceCentavos: number; reservedCentavos: number };
    expect(w.balanceCentavos).toBe(100000);
    expect(w.reservedCentavos).toBe(0);
  });

  it('rejects a replayed nonce on a mutating request', async () => {
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 10000, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const first  = await wallet.fetch(await makeWalletRequest('/debit', { amountCentavos: 100, reference: 'bet:n1' }, { nonce: 'fixed-nonce' }));
    const replay = await wallet.fetch(await makeWalletRequest('/debit', { amountCentavos: 100, reference: 'bet:n2' }, { nonce: 'fixed-nonce' }));
    expect(first.status).toBe(200);
    expect(replay.status).toBe(401);
  });
});

// ── Win ───────────────────────────────────────────────────────────────────────

describe('POST /win', () => {
  it('credits winnings to the balance', async () => {
    const state  = makeMockState({
      playerId: 'p1', balanceCentavos: 10000, reservedCentavos: 0,
      updatedAt: new Date().toISOString(),
    });
    const wallet = new WalletBalance(state, makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/win', {
      amountCentavos: 3500,
      reference: 'win:bet-uuid-1',
    }));
    expect(resp.status).toBe(200);
    const body = await resp.json<{ data: { balanceCentavos: number } }>();
    expect(body.data.balanceCentavos).toBe(13500);
  });
});

// ── Unknown route ─────────────────────────────────────────────────────────────

describe('Unknown route', () => {
  it('returns 404', async () => {
    const wallet = new WalletBalance(makeMockState(), makeMockWalletEnv() as never);
    const resp   = await wallet.fetch(await makeWalletRequest('/unknown'));
    expect(resp.status).toBe(404);
  });
});
