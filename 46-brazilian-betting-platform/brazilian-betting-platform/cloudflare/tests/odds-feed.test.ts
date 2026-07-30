// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import oddsFeed from '../src/odds-feed.js';

const SECRET = 'aws-odds-publisher-test-secret';
const NOW = new Date('2026-07-22T12:00:00.000Z');

class MemoryKV {
  readonly values = new Map<string, string>();

  async get(key: string): Promise<string | null> {
    return this.values.get(key) ?? null;
  }

  async put(key: string, value: string): Promise<void> {
    this.values.set(key, value);
  }

  async list(options?: { prefix?: string; limit?: number }): Promise<{ keys: Array<{ name: string }> }> {
    const keys = [...this.values.keys()]
      .filter(key => !options?.prefix || key.startsWith(options.prefix))
      .slice(0, options?.limit)
      .map(name => ({ name }));
    return { keys };
  }
}

function snapshot(version = 7, generatedAt = NOW.toISOString()) {
  return {
    version,
    generatedAt,
    markets: [{
      id: 'br-123',
      eventId: 'event-123',
      sport: 'brasileirao-serie-a',
      homeTeam: 'Palmeiras',
      awayTeam: 'Santos',
      startTime: '2026-07-22T19:00:00.000Z',
      odds: { home: 1.8, draw: 3.2, away: 4.1 },
      suspended: false,
      updatedAt: generatedAt,
    }],
  };
}

async function sign(body: string, timestamp: string, nonce: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', encoder.encode(SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const bytes = await crypto.subtle.sign(
    'HMAC', key, encoder.encode(`${timestamp}.${nonce}.${body}`)
  );
  return [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, '0')).join('');
}

async function publisherRequest(
  payload: ReturnType<typeof snapshot>,
  nonce = 'unique-request-id-0001',
  signatureOverride?: string
): Promise<Request> {
  const body = JSON.stringify(payload);
  const timestamp = String(Math.floor(NOW.getTime() / 1_000));
  return new Request('https://edge.example/api/odds/refresh', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Odds-Timestamp': timestamp,
      'X-Odds-Nonce': nonce,
      'X-Odds-Signature': signatureOverride ?? await sign(body, timestamp, nonce),
    },
    body,
  });
}

async function suspendRequest(
  marketId: string,
  nonce = 'suspend-request-id-0001',
  options: { signed?: boolean; reason?: string } = {}
): Promise<Request> {
  const { signed = true, reason } = options;
  const body = JSON.stringify(reason ? { marketId, reason } : { marketId });
  const timestamp = String(Math.floor(NOW.getTime() / 1_000));
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (signed) {
    headers['X-Odds-Timestamp'] = timestamp;
    headers['X-Odds-Nonce'] = nonce;
    headers['X-Odds-Signature'] = await sign(body, timestamp, nonce);
  }
  return new Request('https://edge.example/api/odds/suspend', {
    method: 'POST',
    headers,
    body,
  });
}

function environment(kv: MemoryKV) {
  return {
    ODDS_CACHE: kv,
    ODDS_PUBLISHER_HMAC_SECRET: SECRET,
  } as Parameters<typeof oddsFeed.fetch>[1];
}

const context = {} as ExecutionContext;

describe('AWS odds snapshot publication', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => vi.useRealTimers());

  it('accepts an authenticated versioned snapshot through /api/odds', async () => {
    const kv = new MemoryKV();
    const response = await oddsFeed.fetch(await publisherRequest(snapshot()), environment(kv), context);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ updated: 1, version: 7 });
    expect(JSON.parse(kv.values.get('market:br-123')!)).toMatchObject({
      snapshotVersion: 7,
      sourceTimestamp: NOW.toISOString(),
    });
  });

  it('rejects a replayed nonce', async () => {
    const kv = new MemoryKV();
    const requestOne = await publisherRequest(snapshot(), 'unique-request-id-0002');
    const requestTwo = await publisherRequest(snapshot(8), 'unique-request-id-0002');

    expect((await oddsFeed.fetch(requestOne, environment(kv), context)).status).toBe(200);
    expect((await oddsFeed.fetch(requestTwo, environment(kv), context)).status).toBe(409);
  });

  it('rejects an invalid HMAC signature', async () => {
    const kv = new MemoryKV();
    const request = await publisherRequest(snapshot(), 'unique-request-id-0003', '00'.repeat(32));

    expect((await oddsFeed.fetch(request, environment(kv), context)).status).toBe(401);
    expect(kv.values.has('market:br-123')).toBe(false);
  });

  it('rejects source timestamps outside the freshness window', async () => {
    const kv = new MemoryKV();
    const old = new Date(NOW.getTime() - 61_000).toISOString();
    const request = await publisherRequest(snapshot(7, old), 'unique-request-id-0004');

    expect((await oddsFeed.fetch(request, environment(kv), context)).status).toBe(422);
  });

  it('rejects an older snapshot version before writing any market', async () => {
    const kv = new MemoryKV();
    kv.values.set('market:br-123', JSON.stringify({
      ...snapshot(9).markets[0],
      snapshotVersion: 9,
      sourceTimestamp: NOW.toISOString(),
    }));
    const request = await publisherRequest(snapshot(8), 'unique-request-id-0005');

    expect((await oddsFeed.fetch(request, environment(kv), context)).status).toBe(409);
    expect(JSON.parse(kv.values.get('market:br-123')!).snapshotVersion).toBe(9);
  });

  it('serves fresh snapshots through the gateway-prefixed route', async () => {
    const kv = new MemoryKV();
    kv.values.set('market:br-123', JSON.stringify({
      ...snapshot().markets[0],
      snapshotVersion: 7,
      sourceTimestamp: NOW.toISOString(),
    }));

    const response = await oddsFeed.fetch(
      new Request('https://edge.example/api/odds/br-123'), environment(kv), context
    );
    expect(response.status).toBe(200);
  });

  it('suspends a market for an authenticated integrity alert', async () => {
    const kv = new MemoryKV();
    kv.values.set('market:br-123', JSON.stringify({
      ...snapshot().markets[0],
      snapshotVersion: 7,
      sourceTimestamp: NOW.toISOString(),
    }));

    const response = await oddsFeed.fetch(
      await suspendRequest('br-123'), environment(kv), context
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ suspended: true, marketId: 'br-123' });
    expect(JSON.parse(kv.values.get('market:br-123')!).suspended).toBe(true);
  });

  it('rejects an unauthenticated market suspension', async () => {
    const kv = new MemoryKV();
    kv.values.set('market:br-123', JSON.stringify({
      ...snapshot().markets[0],
      snapshotVersion: 7,
      sourceTimestamp: NOW.toISOString(),
    }));

    const response = await oddsFeed.fetch(
      await suspendRequest('br-123', 'suspend-request-id-0002', { signed: false }),
      environment(kv),
      context
    );

    expect(response.status).toBe(401);
    expect(JSON.parse(kv.values.get('market:br-123')!).suspended).toBe(false);
  });

  it('does not expose a vendor-polling scheduled handler', () => {
    expect('scheduled' in oddsFeed).toBe(false);
  });
});
