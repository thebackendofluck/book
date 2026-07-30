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
 * Field-level AES-256-GCM encryption for PII columns in Cloudflare D1.
 * Uses Web Crypto API (available natively in Workers runtime).
 *
 * GDPR Art.32(1)(a): "encryption of personal data" as an appropriate
 * technical measure to ensure security appropriate to the risk.
 * UK GDPR Art.32(1)(a): identical obligation.
 * LGPD (Brazil) Art.46(1): equivalent technical safeguards requirement.
 *
 * Pattern: encrypt BEFORE writing to D1, decrypt AFTER reading.
 * The encryption key lives in Workers Secrets (wrangler secret put).
 * D1 stores only ciphertext — even Cloudflare cannot read the PII.
 *
 * Key material: 256-bit AES key, base64-encoded, stored as a
 * Workers Secret (never in wrangler.toml, never in source control).
 *
 * Usage:
 *   const cipher = await FieldCipher.fromSecret(env.ENCRYPTION_KEY);
 *   const ct = await cipher.encrypt('john@example.com');
 *   const pt = await cipher.decrypt(ct);
 */

export interface EncryptedField {
  iv: string;   // base64 IV (12 bytes — 96-bit, GCM standard)
  ct: string;   // base64 ciphertext (plaintext length + 16-byte GCM auth tag)
  v: number;    // key version (for rotation — see KeyRotator)
}

export class FieldCipher {
  private readonly key: CryptoKey;
  private readonly keyVersion: number;

  private constructor(key: CryptoKey, version: number) {
    this.key = key;
    this.keyVersion = version;
  }

  /**
   * Construct a FieldCipher from a base64-encoded 32-byte Workers Secret.
   *
   * Set the secret with:
   *   npx wrangler secret put ENCRYPTION_KEY
   *   # Enter a base64-encoded 32-byte value, e.g.:
   *   # openssl rand -base64 32
   *
   * @param secretBase64 - Base64-encoded 256-bit (32-byte) AES key
   * @param version      - Key version for rotation tracking (default: 1)
   */
  static async fromSecret(secretBase64: string, version = 1): Promise<FieldCipher> {
    const rawKey = Uint8Array.from(atob(secretBase64), c => c.charCodeAt(0));
    if (rawKey.length !== 32) {
      throw new Error(
        `ENCRYPTION_KEY must be exactly 32 bytes (256-bit). Got ${rawKey.length} bytes. ` +
        'Generate with: openssl rand -base64 32'
      );
    }
    const key = await crypto.subtle.importKey(
      'raw',
      rawKey,
      { name: 'AES-GCM' },
      false,         // not extractable — key cannot leave this isolate
      ['encrypt', 'decrypt']
    );
    return new FieldCipher(key, version);
  }

  /**
   * Encrypt a plaintext string to a JSON envelope.
   *
   * Returns a JSON string containing { iv, ct, v }.
   * The GCM authentication tag is appended to the ciphertext by the
   * Web Crypto API and is included in ct — no separate tag field needed.
   * Any tampering with the ciphertext will cause decrypt() to throw.
   *
   * @param plaintext - The PII value to encrypt (e.g. 'john@example.com')
   * @returns         - JSON string safe to store in a D1 TEXT column
   */
  async encrypt(plaintext: string): Promise<string> {
    // 12-byte (96-bit) IV — GCM standard. Must be unique per encryption.
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encoded = new TextEncoder().encode(plaintext);

    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      this.key,
      encoded
    );

    const envelope: EncryptedField = {
      iv: btoa(String.fromCharCode(...iv)),
      ct: btoa(String.fromCharCode(...new Uint8Array(ciphertext))),
      v:  this.keyVersion,
    };

    return JSON.stringify(envelope);
  }

  /**
   * Decrypt a JSON envelope produced by encrypt().
   *
   * Throws if the ciphertext has been tampered with (GCM auth tag failure),
   * if the JSON is malformed, or if the key version does not match.
   *
   * @param encryptedJson - JSON string from a D1 TEXT column
   * @returns             - Original plaintext
   */
  async decrypt(encryptedJson: string): Promise<string> {
    let envelope: EncryptedField;
    try {
      envelope = JSON.parse(encryptedJson) as EncryptedField;
    } catch {
      throw new Error('Invalid encrypted field: not valid JSON');
    }

    if (!envelope.iv || !envelope.ct) {
      throw new Error('Invalid encrypted field: missing iv or ct');
    }

    const ivBytes = Uint8Array.from(atob(envelope.iv), c => c.charCodeAt(0));
    const ctBytes = Uint8Array.from(atob(envelope.ct), c => c.charCodeAt(0));

    let plaintext: ArrayBuffer;
    try {
      plaintext = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: ivBytes },
        this.key,
        ctBytes
      );
    } catch {
      // GCM auth tag failure — ciphertext was tampered with, or wrong key
      throw new Error('Decryption failed: authentication tag mismatch or wrong key');
    }

    return new TextDecoder().decode(plaintext);
  }

  /**
   * Return the current key version.
   * Used by EncryptedModel to detect rows that need re-encryption after rotation.
   */
  get version(): number {
    return this.keyVersion;
  }

  /**
   * Re-encrypt a ciphertext blob under the current key.
   * Used during key rotation: decrypt with oldCipher, encrypt with newCipher.
   *
   * @param encryptedJson - Existing ciphertext from D1
   * @param oldCipher     - Cipher instance with the previous key
   * @returns             - New ciphertext encrypted under this cipher's key
   */
  async reEncrypt(encryptedJson: string, oldCipher: FieldCipher): Promise<string> {
    const plaintext = await oldCipher.decrypt(encryptedJson);
    return this.encrypt(plaintext);
  }
}
