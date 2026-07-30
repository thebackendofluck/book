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
 * Hybrid edge+HSM encryption pattern for PII fields.
 *
 * Strategy:
 *   Step 1 — Encrypt with local Web Crypto AES-256-GCM (<1 ms, no network)
 *   Step 2 — Hash the ciphertext with SHA-256 (local, instant)
 *   Step 3 — Send hash to remote HSM for ECDSA/Ed25519 signature (~50 ms)
 *   Step 4 — Store { ciphertext, signature, keyVersion } in D1
 *
 * Why this pattern:
 *   - Encryption is fast because it happens at the edge — no round-trip to
 *     ops-host. Even at 50 ms latency, signing is out of the critical path
 *     for bets (we fire-and-forget via ctx.waitUntil if desired).
 *   - Non-repudiation: the HSM signature proves the ciphertext was produced
 *     by a legitimate Worker with access to the HSM API key. Even if the D1
 *     database is compromised, the attacker cannot fabricate signed records.
 *   - PCI DSS compliance: the HSM key never leaves the YubiHSM hardware.
 *     The ciphertext hash (not the plaintext) is signed — so raw PII is
 *     never sent to the HSM host.
 *   - Audit trail: every encrypted PII field has an HSM-backed signature.
 *     Regulators can verify the chain: hash(ciphertext) → HSM signature.
 *
 * When to use hybrid vs. pure Web Crypto:
 *   Use HybridCipher:
 *     - KYC documents (CPF, passport numbers, tax IDs)
 *     - Payment card data (PAN last-4, billing address)
 *     - Any field requiring compliance audit trail (LGPD, PCI DSS)
 *   Use FieldCipher (field-cipher.ts) only:
 *     - Session tokens, nonces, game state
 *     - High-frequency operations (every spin, every bet)
 *     - Transient data with short retention periods
 *
 * Dependencies:
 *   import { FieldCipher }  from '../gdpr-encryption/field-cipher.js';
 *   import { RemoteHSM, sha256Base64, toBase64 } from './worker-hsm-client.js';
 */

import { FieldCipher, type EncryptedField } from '../gdpr-encryption/field-cipher.js';
import { RemoteHSM, sha256Base64 } from './worker-hsm-client.js';

// ─── Types ───────────────────────────────────────────────────────────────────

/**
 * A PII field encrypted with AES-256-GCM (edge) and signed with the YubiHSM.
 *
 * Stored as a JSON column in D1. Do not log or expose the ct or sig fields.
 */
export interface HybridEncryptedField extends EncryptedField {
  /** HSM signature over SHA-256(JSON.stringify(encryptedField)).
   *  Format: "vault:v1:<base64-signature>" from OpenBao Transit. */
  sig: string;
  /** Name of the HSM signing key used (for rotation tracking). */
  sig_key: string;
  /** Unix timestamp of when this was signed (for replay detection). */
  signed_at: number;
}

export interface HybridCipherOptions {
  /** OpenBao Transit key for AES encryption (default: "field-cipher") */
  encryptKeyName?: string;
  /** OpenBao Transit key for ECDSA signing (default: "jwt-signing") */
  signKeyName?: string;
  /**
   * If true, HSM signing failures do NOT block writes — the record is
   * stored without a signature and flagged for re-signing later.
   * Use false (default) for PCI DSS cardholder data.
   * Use true for lower-risk fields where availability > strict audit.
   */
  allowUnsigned?: boolean;
}

// ─── HybridCipher ────────────────────────────────────────────────────────────

export class HybridCipher {
  private readonly fieldCipher: FieldCipher;
  private readonly hsm: RemoteHSM;
  private readonly opts: Required<HybridCipherOptions>;

  private constructor(
    fieldCipher: FieldCipher,
    hsm: RemoteHSM,
    opts: Required<HybridCipherOptions>,
  ) {
    this.fieldCipher = fieldCipher;
    this.hsm = hsm;
    this.opts = opts;
  }

  /**
   * Create a HybridCipher from Worker environment bindings.
   *
   * Required Worker Secrets:
   *   ENCRYPTION_KEY  — base64-encoded 256-bit AES key (for FieldCipher)
   *   HSM_API_URL     — https://hsm-api.acmetocasino.com
   *   HSM_API_KEY     — 32-byte hex API key for the HSM proxy
   *
   * @param encryptionKey  env.ENCRYPTION_KEY
   * @param hsmApiUrl      env.HSM_API_URL
   * @param hsmApiKey      env.HSM_API_KEY
   * @param options        Optional key name overrides and policy flags
   */
  static async create(
    encryptionKey: string,
    hsmApiUrl: string,
    hsmApiKey: string,
    options: HybridCipherOptions = {},
  ): Promise<HybridCipher> {
    const fieldCipher = await FieldCipher.fromSecret(encryptionKey);
    const hsm = new RemoteHSM(hsmApiUrl, hsmApiKey);
    const opts: Required<HybridCipherOptions> = {
      encryptKeyName: options.encryptKeyName ?? 'field-cipher',
      signKeyName: options.signKeyName ?? 'jwt-signing',
      allowUnsigned: options.allowUnsigned ?? false,
    };
    return new HybridCipher(fieldCipher, hsm, opts);
  }

  // ─── Core operations ───────────────────────────────────────────────────────

  /**
   * Encrypt a PII field and sign the ciphertext with the YubiHSM.
   *
   * Timing breakdown (typical Cloudflare PoP → FR data centre RTT ~15 ms):
   *   Web Crypto encrypt:   <1 ms
   *   SHA-256 hash:         <1 ms
   *   HSM sign (network):   30–60 ms
   *   Total:                ~30–60 ms
   *
   * @param plaintext  The raw PII value (e.g., "john@example.com")
   * @returns HybridEncryptedField — serialize to JSON for D1 storage
   */
  async encrypt(plaintext: string): Promise<HybridEncryptedField> {
    // Step 1: Fast edge encryption (Web Crypto, <1 ms)
    const encrypted: EncryptedField = await this.fieldCipher.encrypt(plaintext);

    // Step 2: Compute a deterministic hash of the ciphertext envelope.
    // We sign the hash — NOT the plaintext — so PII never crosses the
    // network to the HSM host.
    const envelope = JSON.stringify({ iv: encrypted.iv, ct: encrypted.ct, v: encrypted.v });
    const envelopeHashB64 = await sha256Base64(envelope);

    // Step 3: Remote HSM signs the hash (~30–60 ms)
    let sig = '';
    let sig_key = '';
    const signed_at = Math.floor(Date.now() / 1000);

    try {
      const sigResult = await this.hsm.signHash(
        envelopeHashB64,
        this.opts.signKeyName,
        'sha2-256',
      );
      sig = sigResult.signature;
      sig_key = sigResult.key_name;
    } catch (err) {
      if (!this.opts.allowUnsigned) {
        throw err;
      }
      // Degrade gracefully — flag as unsigned for async re-signing
      sig = 'UNSIGNED';
      sig_key = this.opts.signKeyName;
      console.warn('[HybridCipher] HSM sign failed, storing unsigned record:', err);
    }

    return { ...encrypted, sig, sig_key, signed_at };
  }

  /**
   * Decrypt a HybridEncryptedField and optionally verify the HSM signature.
   *
   * @param field       The stored HybridEncryptedField from D1
   * @param verifySign  If true (default), verify the HSM signature before
   *                    returning the plaintext. Set false for bulk re-key ops.
   * @returns The original plaintext string
   */
  async decrypt(
    field: HybridEncryptedField,
    verifySign = true,
  ): Promise<string> {
    // Step 1: Optionally verify the HSM signature
    if (verifySign && field.sig !== 'UNSIGNED') {
      const envelope = JSON.stringify({ iv: field.iv, ct: field.ct, v: field.v });
      const envelopeHashB64 = await sha256Base64(envelope);
      // Throws HSMError if signature is invalid
      await this.hsm.verify(
        envelopeHashB64,
        field.sig,
        field.sig_key,
        'sha2-256',
      );
    }

    // Step 2: Decrypt with local Web Crypto
    const encryptedField: EncryptedField = { iv: field.iv, ct: field.ct, v: field.v };
    return this.fieldCipher.decrypt(encryptedField);
  }

  /**
   * Re-sign unsigned records (call from a scheduled Worker or Durable Object).
   *
   * Pattern:
   *   1. SELECT rows WHERE json_extract(field_col, '$.sig') = 'UNSIGNED'
   *   2. For each row: hybrid.resign(field)
   *   3. UPDATE row with new field value
   */
  async resign(field: HybridEncryptedField): Promise<HybridEncryptedField> {
    const envelope = JSON.stringify({ iv: field.iv, ct: field.ct, v: field.v });
    const envelopeHashB64 = await sha256Base64(envelope);
    const sigResult = await this.hsm.signHash(
      envelopeHashB64,
      this.opts.signKeyName,
      'sha2-256',
    );
    return {
      ...field,
      sig: sigResult.signature,
      sig_key: sigResult.key_name,
      signed_at: Math.floor(Date.now() / 1000),
    };
  }
}

// ─── D1 helpers ───────────────────────────────────────────────────────────────

/**
 * Serialize a HybridEncryptedField for D1 storage.
 * Store as TEXT column (JSON) — never as separate columns.
 */
export function serializeField(field: HybridEncryptedField): string {
  return JSON.stringify(field);
}

/**
 * Deserialize a D1 TEXT column back to HybridEncryptedField.
 * Validates the structure before returning.
 */
export function deserializeField(raw: string): HybridEncryptedField {
  const parsed = JSON.parse(raw) as Partial<HybridEncryptedField>;
  if (!parsed.iv || !parsed.ct || typeof parsed.v !== 'number' || !parsed.sig) {
    throw new TypeError('Invalid HybridEncryptedField structure in D1');
  }
  return parsed as HybridEncryptedField;
}

// ─── Example: Player KYC write ────────────────────────────────────────────────

/**
 * Example of storing a player's CPF (Brazilian tax ID) using hybrid encryption.
 *
 * This is how Chapter 44 would call HybridCipher from a Worker handler:
 *
 *   export default {
 *     async fetch(request: Request, env: Env): Promise<Response> {
 *       const cipher = await HybridCipher.create(
 *         env.ENCRYPTION_KEY,
 *         env.HSM_API_URL,
 *         env.HSM_API_KEY,
 *       );
 *       const { cpf } = await request.json();
 *       const encryptedCpf = await cipher.encrypt(cpf);
 *
 *       await env.DB.prepare(
 *         'UPDATE players SET cpf_encrypted = ? WHERE id = ?'
 *       ).bind(serializeField(encryptedCpf), playerId).run();
 *
 *       return new Response(JSON.stringify({ success: true }));
 *     }
 *   }
 *
 * To read it back:
 *   const raw = row.cpf_encrypted as string;
 *   const field = deserializeField(raw);
 *   const cpf = await cipher.decrypt(field);  // verifies HSM sig by default
 */
