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
 * Tests for GDPR encryption, pseudonymisation, and crypto-shredding.
 *
 * Run with Vitest in a Workers-compatible environment:
 *   npx vitest run test-encryption.ts
 *
 * Or with wrangler's built-in test runner:
 *   npx wrangler dev --test
 *
 * All tests use the Web Crypto API directly (no mocks) to ensure
 * they validate the actual cryptographic behaviour.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import { FieldCipher, type EncryptedField } from './field-cipher.js';
import { Pseudonymiser } from './pseudonymiser.js';
import { computeSearchToken } from './data-residency-worker.js';

// ─── Test key material ───────────────────────────────────────────────────────
// These are test-only keys. Never use in production.
// Production keys are stored as Workers Secrets.
const TEST_ENCRYPTION_KEY_V1 = btoa(
  String.fromCharCode(...new Uint8Array(32).fill(0x01))
);  // 32 bytes of 0x01
const TEST_ENCRYPTION_KEY_V2 = btoa(
  String.fromCharCode(...new Uint8Array(32).fill(0x02))
);  // 32 bytes of 0x02 (rotation target)
const TEST_HMAC_KEY = btoa(
  String.fromCharCode(...new Uint8Array(32).fill(0xAA))
);  // 32 bytes of 0xAA

// ─── FieldCipher tests ───────────────────────────────────────────────────────

describe('FieldCipher', () => {
  let cipher: FieldCipher;

  beforeAll(async () => {
    cipher = await FieldCipher.fromSecret(TEST_ENCRYPTION_KEY_V1, 1);
  });

  // ── Roundtrip ──────────────────────────────────────────────────────────────

  it('encrypts and decrypts a string correctly (roundtrip)', async () => {
    const plaintext = 'john@example.com';
    const ct = await cipher.encrypt(plaintext);
    const pt = await cipher.decrypt(ct);
    expect(pt).toBe(plaintext);
  });

  it('roundtrips Unicode and special characters', async () => {
    const inputs = [
      'José García-Martínez',
      '+44 7700 900 000',
      '日本語テスト',
      '1985-03-15T00:00:00Z',
      'Rua das Flores, nº 42, 1200-001 Lisboa',
      'test+tag@subdomain.example.co.uk',
    ];
    for (const input of inputs) {
      const ct = await cipher.encrypt(input);
      const pt = await cipher.decrypt(ct);
      expect(pt).toBe(input);
    }
  });

  // ── Ciphertext properties ──────────────────────────────────────────────────

  it('produces valid JSON envelope with iv, ct, and v fields', async () => {
    const ct = await cipher.encrypt('test@example.com');
    const envelope = JSON.parse(ct) as EncryptedField;
    expect(envelope).toHaveProperty('iv');
    expect(envelope).toHaveProperty('ct');
    expect(envelope).toHaveProperty('v', 1);
    // IV should be 12 bytes = 16 base64 characters (rounded up)
    const ivBytes = Uint8Array.from(atob(envelope.iv), c => c.charCodeAt(0));
    expect(ivBytes.length).toBe(12);
  });

  it('produces different ciphertext on each encryption (non-deterministic)', async () => {
    const plaintext = 'john@example.com';
    const ct1 = await cipher.encrypt(plaintext);
    const ct2 = await cipher.encrypt(plaintext);
    // Same plaintext must produce different ciphertext (different IVs)
    expect(ct1).not.toBe(ct2);
    // Both must decrypt to the same plaintext
    expect(await cipher.decrypt(ct1)).toBe(plaintext);
    expect(await cipher.decrypt(ct2)).toBe(plaintext);
  });

  it('stores different IVs on each encryption call', async () => {
    const ct1 = await cipher.encrypt('same-value');
    const ct2 = await cipher.encrypt('same-value');
    const iv1 = (JSON.parse(ct1) as EncryptedField).iv;
    const iv2 = (JSON.parse(ct2) as EncryptedField).iv;
    expect(iv1).not.toBe(iv2);
  });

  // ── Authentication ─────────────────────────────────────────────────────────

  it('rejects tampered ciphertext (GCM authentication failure)', async () => {
    const ct = await cipher.encrypt('sensitive-data');
    const envelope = JSON.parse(ct) as EncryptedField;

    // Flip a bit in the ciphertext
    const ctBytes = Uint8Array.from(atob(envelope.ct), c => c.charCodeAt(0));
    ctBytes[0] ^= 0xFF;
    envelope.ct = btoa(String.fromCharCode(...ctBytes));

    const tamperedCt = JSON.stringify(envelope);
    await expect(cipher.decrypt(tamperedCt)).rejects.toThrow(
      'Decryption failed: authentication tag mismatch or wrong key'
    );
  });

  it('rejects decryption with the wrong key', async () => {
    const ct = await cipher.encrypt('sensitive-data');
    const wrongKeyCipher = await FieldCipher.fromSecret(TEST_ENCRYPTION_KEY_V2, 2);
    await expect(wrongKeyCipher.decrypt(ct)).rejects.toThrow();
  });

  it('rejects malformed JSON', async () => {
    await expect(cipher.decrypt('not-json')).rejects.toThrow(
      'Invalid encrypted field: not valid JSON'
    );
  });

  it('rejects JSON without required fields', async () => {
    await expect(cipher.decrypt('{"foo":"bar"}')).rejects.toThrow(
      'Invalid encrypted field: missing iv or ct'
    );
  });

  // ── Key rotation ────────────────────────────────────────────────────────────

  it('re-encrypts under a new key after key rotation', async () => {
    const plaintext = 'john@example.com';
    const oldCt = await cipher.encrypt(plaintext);

    const newCipher = await FieldCipher.fromSecret(TEST_ENCRYPTION_KEY_V2, 2);
    const newCt = await newCipher.reEncrypt(oldCt, cipher);

    // New ciphertext must decrypt with new key
    expect(await newCipher.decrypt(newCt)).toBe(plaintext);

    // Old ciphertext must not decrypt with new key
    await expect(newCipher.decrypt(oldCt)).rejects.toThrow();
  });

  it('increments key version in re-encrypted envelope', async () => {
    const oldCt = await cipher.encrypt('test');
    const newCipher = await FieldCipher.fromSecret(TEST_ENCRYPTION_KEY_V2, 2);
    const newCt = await newCipher.reEncrypt(oldCt, cipher);
    const envelope = JSON.parse(newCt) as EncryptedField;
    expect(envelope.v).toBe(2);
  });

  // ── Key validation ─────────────────────────────────────────────────────────

  it('rejects a key that is not exactly 32 bytes', async () => {
    const shortKey = btoa('too-short');
    await expect(FieldCipher.fromSecret(shortKey)).rejects.toThrow(
      'ENCRYPTION_KEY must be exactly 32 bytes'
    );
  });

  // ── Performance benchmark ───────────────────────────────────────────────────

  it('encrypts 1,000 records in under 500ms', async () => {
    const testData = [
      'user@example.com',
      'John Smith',
      '+44 7700 900000',
      '1990-01-01',
      '192.168.1.1',
    ];

    const start = Date.now();

    for (let i = 0; i < 1000; i++) {
      const field = testData[i % testData.length];
      await cipher.encrypt(field);
    }

    const elapsed = Date.now() - start;
    console.log(`1,000 encrypt ops: ${elapsed}ms (${(elapsed / 1000).toFixed(2)}ms avg)`);

    // Workers V8 isolate: AES-GCM is hardware-accelerated via SubtleCrypto.
    // 500ms is a generous bound; in practice this runs in 50-150ms.
    expect(elapsed).toBeLessThan(500);
  }, 10000);

  it('decrypts 1,000 records in under 500ms', async () => {
    // Pre-encrypt test data
    const ciphertexts = await Promise.all(
      Array.from({ length: 1000 }, (_, i) => cipher.encrypt(`user${i}@example.com`))
    );

    const start = Date.now();
    for (const ct of ciphertexts) {
      await cipher.decrypt(ct);
    }

    const elapsed = Date.now() - start;
    console.log(`1,000 decrypt ops: ${elapsed}ms (${(elapsed / 1000).toFixed(2)}ms avg)`);
    expect(elapsed).toBeLessThan(500);
  }, 10000);
});

// ─── Pseudonymiser tests ─────────────────────────────────────────────────────

describe('Pseudonymiser', () => {
  const pseudonymiser = new Pseudonymiser();

  it('replaces all ERASABLE_FIELDS with PSEUDONYMISED: prefix', async () => {
    const playerData = {
      id:           42,
      email:        'john@example.com',
      full_name:    'John Doe',
      phone:        '+44 7700 900000',
      balance:      100.00,
      country:      'GB',
      self_exclusion: false,
      kyc_status:   'verified',
      aml_flags:    null,
    };

    const result = await pseudonymiser.pseudonymise(playerData, 'REQ-001');

    // PII fields must be pseudonymised
    expect(String(result.data.email)).toMatch(/^PSEUDONYMISED:/);
    expect(String(result.data.full_name)).toMatch(/^PSEUDONYMISED:/);
    expect(String(result.data.phone)).toMatch(/^PSEUDONYMISED:/);

    // Non-PII fields must be unchanged
    expect(result.data.id).toBe(42);
    expect(result.data.balance).toBe(100.00);
    expect(result.data.country).toBe('GB');
  });

  it('never pseudonymises self_exclusion or AML fields', async () => {
    const playerData = {
      email:            'john@example.com',
      self_exclusion:   true,
      self_exclusion_until: '2026-12-31',
      kyc_status:       'verified',
      aml_flags:        'HIGH_RISK',
      aml_risk_score:   85,
      sanctions_checked: true,
      pep_status:       false,
    };

    const result = await pseudonymiser.pseudonymise(playerData, 'REQ-002');

    // These must be retained EXACTLY as-is
    expect(result.data.self_exclusion).toBe(true);
    expect(result.data.self_exclusion_until).toBe('2026-12-31');
    expect(result.data.kyc_status).toBe('verified');
    expect(result.data.aml_flags).toBe('HIGH_RISK');
    expect(result.data.aml_risk_score).toBe(85);
    expect(result.data.sanctions_checked).toBe(true);
    expect(result.data.pep_status).toBe(false);
  });

  it('produces irreversible hashes (different on each invocation)', async () => {
    // Because the ephemeral salt changes each time, the same email
    // produces a different hash on each pseudonymisation call.
    // This is intentional — it prevents cross-request correlation.
    const data = { email: 'john@example.com' };

    const result1 = await pseudonymiser.pseudonymise({ ...data }, 'REQ-001');
    const result2 = await pseudonymiser.pseudonymise({ ...data }, 'REQ-002');

    expect(result1.data.email).not.toBe(result2.data.email);
  });

  it('reports pseudonymised and retained fields correctly', async () => {
    const data = {
      email:          'john@example.com',
      full_name:      'John Doe',
      self_exclusion: false,
      kyc_status:     'verified',
      aml_flags:      null,
      transaction_id: 'TXN-12345',
    };

    const result = await pseudonymiser.pseudonymise(data, 'REQ-003');

    expect(result.pseudonymisedFields).toContain('email');
    expect(result.pseudonymisedFields).toContain('full_name');
    expect(result.retainedFields).toContain('self_exclusion');
    expect(result.retainedFields).toContain('kyc_status');
    expect(result.retainedFields).toContain('transaction_id');
  });

  it('handles null and missing fields gracefully', async () => {
    const data = {
      email:     null,
      full_name: undefined,
      phone:     '',
      balance:   0,
    };

    // Must not throw
    const result = await pseudonymiser.pseudonymise(data as Record<string, unknown>, 'REQ-004');
    expect(result.pseudonymisedFields).not.toContain('email');   // null → skipped
    expect(result.pseudonymisedFields).not.toContain('full_name'); // undefined → skipped
    expect(result.pseudonymisedFields).not.toContain('phone');    // '' is falsy → skipped
  });
});

// ─── Search token tests ──────────────────────────────────────────────────────

describe('computeSearchToken', () => {
  it('produces the same token for the same input (deterministic)', async () => {
    const token1 = await computeSearchToken('john@example.com', TEST_HMAC_KEY);
    const token2 = await computeSearchToken('john@example.com', TEST_HMAC_KEY);
    expect(token1).toBe(token2);
  });

  it('produces different tokens for different inputs', async () => {
    const t1 = await computeSearchToken('john@example.com', TEST_HMAC_KEY);
    const t2 = await computeSearchToken('jane@example.com', TEST_HMAC_KEY);
    expect(t1).not.toBe(t2);
  });

  it('is case-insensitive (normalises before hashing)', async () => {
    const t1 = await computeSearchToken('John@Example.COM', TEST_HMAC_KEY);
    const t2 = await computeSearchToken('john@example.com', TEST_HMAC_KEY);
    expect(t1).toBe(t2);
  });

  it('produces different tokens with different HMAC keys', async () => {
    const differentKey = btoa(String.fromCharCode(...new Uint8Array(32).fill(0xBB)));
    const t1 = await computeSearchToken('john@example.com', TEST_HMAC_KEY);
    const t2 = await computeSearchToken('john@example.com', differentKey);
    expect(t1).not.toBe(t2);
  });
});

// ─── Crypto-shredding verification ──────────────────────────────────────────
// Note: CryptoShredder requires a live KV binding (D1Database + KVNamespace).
// These tests are integration tests that run against a local Wrangler dev instance.
// Run with: npx wrangler dev --test

describe('CryptoShredder (integration — requires KV binding)', () => {
  it.todo('createPlayerDek stores an encrypted DEK in KV');
  it.todo('getPlayerDek returns null after shredPlayer is called');
  it.todo('shredPlayer writes an audit record before deleting the DEK');
  it.todo('shredPlayer is idempotent (second call returns success)');
  it.todo('rotateDeks re-wraps all active DEKs under the new KEK');
});
