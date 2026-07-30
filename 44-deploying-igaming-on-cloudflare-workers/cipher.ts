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
 * AcmeToCasino Platform - Field-Level PII Encryption
 *
 * AES-256-GCM field encryption for sensitive player data stored in D1.
 * Uses only the Web Crypto API (crypto.subtle) — natively available in
 * Cloudflare Workers V8 isolates, zero external dependencies.
 *
 * Design:
 *   - Each encrypted field is an independent ciphertext (nonce is unique per
 *     call; GCM authentication tag is appended to the ciphertext).
 *   - The output format is base64url(nonce || ciphertext || auth_tag), where
 *     nonce is 12 bytes (96-bit) as required by AES-256-GCM.
 *   - The raw encryption key (32 bytes / 256 bits) is stored as a hex string
 *     in the ENCRYPTION_KEY wrangler secret and imported once per isolate
 *     lifetime via the module-level cache below.
 *   - Deterministic encryption (same plaintext → same ciphertext) is NOT used.
 *     Every call to encrypt() generates a fresh 12-byte random nonce, ensuring
 *     that two encryptions of the same value produce distinct ciphertexts.
 *
 * Fields encrypted in the platform:
 *   - users.date_of_birth
 *   - users.first_name / last_name (PII under GDPR)
 *   - kyc_records.reviewer_notes
 *   - compliance_events.details when containing source-of-funds data
 *
 * Usage:
 *   const cipher = await getCipher(env);
 *   const encrypted = await cipher.encrypt('1985-07-23');
 *   const plaintext = await cipher.decrypt(encrypted);
 */

import { base64UrlEncode, base64UrlDecode } from './utils.js';

// ─── Key cache ─────────────────────────────────────────────────────────────
//
// CryptoKey import is async but the result is reusable within an isolate.
// Caching avoids re-importing on every encrypt/decrypt call.

let _cachedKey: CryptoKey | null = null;
let _cachedKeyHex: string | null = null;

async function importKey(hexKey: string): Promise<CryptoKey> {
  if (_cachedKey && _cachedKeyHex === hexKey) {
    return _cachedKey;
  }

  const keyBytes = hexToBytes(hexKey);
  if (keyBytes.byteLength !== 32) {
    throw new Error(
      `ENCRYPTION_KEY must be 64 hex characters (32 bytes). Got ${keyBytes.byteLength} bytes.`
    );
  }

  const key = await crypto.subtle.importKey(
    'raw',
    keyBytes,
    { name: 'AES-GCM', length: 256 },
    false,           // non-extractable
    ['encrypt', 'decrypt']
  );

  _cachedKey = key;
  _cachedKeyHex = hexKey;
  return key;
}

// ─── Core encrypt / decrypt ────────────────────────────────────────────────

const NONCE_BYTES = 12;   // 96-bit nonce as required by AES-GCM
const TAG_BYTES = 16;     // 128-bit authentication tag (AES-GCM default)

/**
 * Encrypts plaintext using AES-256-GCM.
 * Returns base64url( nonce || ciphertext+tag ).
 *
 * The 12-byte nonce is prepended to the output so that decrypt() is
 * self-contained — it does not need an external nonce parameter.
 */
async function encryptField(plaintext: string, key: CryptoKey): Promise<string> {
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  const encoder = new TextEncoder();

  const ciphertextWithTag = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: nonce, tagLength: TAG_BYTES * 8 },
    key,
    encoder.encode(plaintext)
  );

  // Concatenate: nonce (12) || ciphertext+tag
  const combined = new Uint8Array(nonce.byteLength + ciphertextWithTag.byteLength);
  combined.set(nonce, 0);
  combined.set(new Uint8Array(ciphertextWithTag), nonce.byteLength);

  return base64UrlEncode(combined);
}

/**
 * Decrypts a value produced by encryptField().
 * Returns the original plaintext string, or throws on authentication failure.
 */
async function decryptField(encoded: string, key: CryptoKey): Promise<string> {
  const combined = base64UrlDecode(encoded);

  if (combined.byteLength < NONCE_BYTES + TAG_BYTES) {
    throw new Error('Ciphertext is too short to be valid AES-256-GCM output');
  }

  const nonce = combined.slice(0, NONCE_BYTES);
  const ciphertextWithTag = combined.slice(NONCE_BYTES);

  const plaintext = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: nonce, tagLength: TAG_BYTES * 8 },
    key,
    ciphertextWithTag
  );

  return new TextDecoder().decode(plaintext);
}

// ─── Cipher object ─────────────────────────────────────────────────────────

export interface Cipher {
  encrypt(plaintext: string): Promise<string>;
  decrypt(ciphertext: string): Promise<string>;
  encryptIfPresent(value: string | null | undefined): Promise<string | null>;
  decryptIfPresent(value: string | null | undefined): Promise<string | null>;
}

/**
 * Returns a Cipher bound to the ENCRYPTION_KEY wrangler secret.
 * Call once per request; the underlying CryptoKey is cached at the isolate level.
 *
 * @example
 *   const cipher = await getCipher(env);
 *   const encryptedDob = await cipher.encrypt(body.dateOfBirth);
 */
export async function getCipher(env: { ENCRYPTION_KEY: string }): Promise<Cipher> {
  const key = await importKey(env.ENCRYPTION_KEY);

  return {
    encrypt: (plaintext: string) => encryptField(plaintext, key),
    decrypt: (ciphertext: string) => decryptField(ciphertext, key),

    async encryptIfPresent(value: string | null | undefined): Promise<string | null> {
      if (value == null || value === '') return null;
      return encryptField(value, key);
    },

    async decryptIfPresent(value: string | null | undefined): Promise<string | null> {
      if (value == null || value === '') return null;
      return decryptField(value, key);
    },
  };
}

// ─── Key derivation helper ─────────────────────────────────────────────────

/**
 * Derives a 256-bit key from a passphrase and a fixed salt using PBKDF2.
 * Use this only for key generation at setup time — not in the request path.
 *
 * In production, ENCRYPTION_KEY is a randomly generated 32-byte value stored
 * via `wrangler secret put ENCRYPTION_KEY`. This function supports key
 * generation in keys.sh where a raw hex key is desired.
 */
export async function deriveKey(passphrase: string, salt: string): Promise<string> {
  const encoder = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    encoder.encode(passphrase),
    'PBKDF2',
    false,
    ['deriveBits']
  );

  const saltBytes = encoder.encode(salt);
  const derived = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: saltBytes, iterations: 600_000, hash: 'SHA-256' },
    keyMaterial,
    256
  );

  return bytesToHex(new Uint8Array(derived));
}

// ─── Utility: encrypt a batch of fields ────────────────────────────────────

/**
 * Encrypts a record's PII fields in parallel.
 * Input and output shapes share the same keys; unencrypted fields are
 * passed through unchanged.
 *
 * @example
 *   const encrypted = await encryptPiiFields(cipher, {
 *     firstName: body.firstName,
 *     lastName:  body.lastName,
 *     dateOfBirth: body.dateOfBirth,
 *   });
 *   // encrypted.firstName, encrypted.lastName, encrypted.dateOfBirth
 *   // are now AES-256-GCM ciphertexts
 */
export async function encryptPiiFields(
  cipher: Cipher,
  fields: Record<string, string | null | undefined>
): Promise<Record<string, string | null>> {
  const entries = await Promise.all(
    Object.entries(fields).map(async ([key, value]) => [
      key,
      await cipher.encryptIfPresent(value),
    ])
  );
  return Object.fromEntries(entries) as Record<string, string | null>;
}

/**
 * Decrypts a record's PII fields in parallel.
 * The inverse of encryptPiiFields.
 */
export async function decryptPiiFields(
  cipher: Cipher,
  fields: Record<string, string | null | undefined>
): Promise<Record<string, string | null>> {
  const entries = await Promise.all(
    Object.entries(fields).map(async ([key, value]) => [
      key,
      await cipher.decryptIfPresent(value),
    ])
  );
  return Object.fromEntries(entries) as Record<string, string | null>;
}

// ─── Hex utilities ─────────────────────────────────────────────────────────

function hexToBytes(hex: string): Uint8Array {
  if (hex.length % 2 !== 0) {
    throw new Error('Hex string must have an even number of characters');
  }
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.slice(i, i + 2), 16);
  }
  return bytes;
}

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Generates a cryptographically random 32-byte hex key.
 * Intended for use in keys.sh and local setup scripts.
 */
export function generateKeyHex(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return bytesToHex(bytes);
}
