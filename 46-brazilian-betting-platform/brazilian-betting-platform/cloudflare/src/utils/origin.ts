// Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import type { Env } from '../types.js';

export interface CoreRequestOptions {
  method: string;
  path: string;
  requestId: string;
  playerId?: string;
  idempotencyKey?: string;
  body?: BodyInit | null;
  contentType?: string;
}

/**
 * Send an authenticated request to the authoritative AWS core.
 *
 * The signature covers the timestamp, HTTP method, path, player identity,
 * idempotency key and body digest. The core must enforce the same canonical
 * form and a short replay window.
 */
export async function forwardToCore(env: Env, options: CoreRequestOptions): Promise<Response> {
  const baseUrl = env.AWS_CORE_API_URL?.trim();
  const secret = env.AWS_CORE_HMAC_SECRET?.trim();
  if (!baseUrl || !secret) {
    return jsonError('Authoritative core is not configured.', 503);
  }

  const method = options.method.toUpperCase();
  const target = new URL(options.path, ensureTrailingSlash(baseUrl));
  const bodyBytes = await toBytes(options.body);
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const playerId = options.playerId ?? '';
  const idempotencyKey = options.idempotencyKey ?? options.requestId;
  const bodyDigest = await sha256Hex(bodyBytes);
  const canonical = [
    timestamp,
    method,
    `${target.pathname}${target.search}`,
    playerId,
    idempotencyKey,
    bodyDigest,
  ].join('\n');
  const signature = await hmacHex(secret, canonical);

  const headers = new Headers({
    Accept: 'application/json',
    'X-Edge-Request-Id': options.requestId,
    'X-Origin-Timestamp': timestamp,
    'X-Origin-Signature': `sha256=${signature}`,
    'Idempotency-Key': idempotencyKey,
  });
  if (playerId) headers.set('X-Player-Id', playerId);
  if (options.contentType) headers.set('Content-Type', options.contentType);

  try {
    return await fetch(target, {
      method,
      headers,
      body: method === 'GET' || method === 'HEAD' ? undefined : bodyBytes,
    });
  } catch (error) {
    console.error('Authoritative core request failed:', error instanceof Error ? error.message : String(error));
    return jsonError('Authoritative core is unavailable.', 502);
  }
}

function ensureTrailingSlash(value: string): string {
  return value.endsWith('/') ? value : `${value}/`;
}

async function toBytes(body: BodyInit | null | undefined): Promise<Uint8Array> {
  if (body === undefined || body === null) return new Uint8Array();
  if (typeof body === 'string') return new TextEncoder().encode(body);
  if (body instanceof ArrayBuffer) return new Uint8Array(body);
  if (ArrayBuffer.isView(body)) {
    return new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
  }
  return new Uint8Array(await new Response(body).arrayBuffer());
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return hex(new Uint8Array(digest));
}

async function hmacHex(secret: string, value: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(value));
  return hex(new Uint8Array(signature));
}

function hex(bytes: Uint8Array): string {
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
}

function jsonError(message: string, status: number): Response {
  return new Response(JSON.stringify({ success: false, error: message }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
