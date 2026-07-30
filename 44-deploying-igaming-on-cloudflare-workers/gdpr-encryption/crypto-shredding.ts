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
 * Crypto-shredding: delete the encryption key to make data permanently
 * unrecoverable without touching individual rows.
 *
 * GDPR Art.17(1): "right to be forgotten" — the data subject has the right
 * to have personal data erased without undue delay.
 *
 * This is the most elegant implementation of the erasure right for large
 * datasets: instead of row-by-row deletion (which leaves forensic traces
 * and is slow), we delete only the per-player DEK. All their encrypted
 * data becomes cryptographically unrecoverable — indistinguishable from
 * random noise — in a single KV delete operation.
 *
 * Architecture:
 *
 *   KEK (Key Encryption Key)
 *     — stored in Workers Secrets (ENCRYPTION_KEY)
 *     — never changes without a full key rotation exercise
 *     — encrypts all DEKs
 *
 *   DEK (Data Encryption Key)
 *     — one per player, generated at account creation
 *     — stored in KV as: DEK_STORE["dek:{playerId}"] = AES-GCM(KEK, rawDek)
 *     — used to encrypt all PII columns for that player in D1
 *
 *   PII in D1
 *     — encrypted with the player's DEK
 *     — when DEK is deleted: ciphertext remains in D1 but is unrecoverable
 *
 * To "delete" a player (GDPR Art.17 compliance):
 *   1. Delete KV["dek:{playerId}"]              ← one KV write
 *   2. Optionally: null out D1 PII columns       ← tidy but not required
 *   3. Retain transaction rows intact            ← AML obligation
 *   4. Write audit log                           ← compliance evidence
 *
 * The KV delete is the cryptographic shred. After step 1, no key material
 * exists anywhere in the system that can decrypt the player's PII.
 * Even Cloudflare cannot recover it.
 *
 * Regulatory basis for retention of ciphertext:
 *   The ciphertext in D1 satisfies AML retention requirements (4AMLD Art.40)
 *   because the financial columns (amount, currency, transaction_type) are
 *   stored in plaintext. The PII columns (name, email, address) are only
 *   needed to identify the data subject — once the DEK is deleted, they
 *   cannot be re-identified, which is equivalent to erasure under
 *   GDPR Recital 26 (pseudonymised data no longer identifies a natural person).
 */

export interface DekEnvelope {
  encryptedDek: string;   // base64(AES-GCM(KEK, rawDek))
  iv: string;             // base64 IV used for DEK encryption
  createdAt: string;      // ISO-8601 timestamp
  version: number;        // KEK version (for rotation)
}

export interface ShredResult {
  playerId: number;
  shredded: boolean;
  method: 'kek_deletion';
  timestamp: string;
  requestId: string;
  auditNote: string;
}

export class CryptoShredder {
  private readonly kek: CryptoKey;
  private readonly kekVersion: number;
  private readonly dekStore: KVNamespace;

  private constructor(kek: CryptoKey, kekVersion: number, dekStore: KVNamespace) {
    this.kek = kek;
    this.kekVersion = kekVersion;
    this.dekStore = dekStore;
  }

  /**
   * Construct a CryptoShredder from the KEK Workers Secret and a KV binding.
   *
   * Requires two Workers Secrets:
   *   ENCRYPTION_KEY — the 32-byte KEK, base64-encoded
   *
   * And one KV namespace binding in wrangler.toml:
   *   [[kv_namespaces]]
   *   binding = "DEK_STORE"
   *   id = "<your-kv-namespace-id>"
   *
   * @param kekBase64  - base64-encoded 32-byte KEK from Workers Secret
   * @param dekStore   - KV namespace binding for DEK storage
   * @param kekVersion - Current KEK version (default: 1)
   */
  static async create(
    kekBase64: string,
    dekStore: KVNamespace,
    kekVersion = 1
  ): Promise<CryptoShredder> {
    const rawKek = Uint8Array.from(atob(kekBase64), c => c.charCodeAt(0));
    const kek = await crypto.subtle.importKey(
      'raw',
      rawKek,
      { name: 'AES-GCM' },
      false,
      ['encrypt', 'decrypt', 'wrapKey', 'unwrapKey']
    );
    return new CryptoShredder(kek, kekVersion, dekStore);
  }

  /**
   * Generate a new DEK for a player and store it encrypted under the KEK.
   * Called once at account creation.
   *
   * @param playerId - The player's numeric ID
   * @returns        - The raw DEK as a CryptoKey (for immediate use)
   */
  async createPlayerDek(playerId: number): Promise<CryptoKey> {
    // Generate a fresh 256-bit DEK
    const rawDek = crypto.getRandomValues(new Uint8Array(32));

    // Encrypt the DEK under the KEK for KV storage
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encryptedDekBuffer = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      this.kek,
      rawDek
    );

    const envelope: DekEnvelope = {
      encryptedDek: btoa(String.fromCharCode(...new Uint8Array(encryptedDekBuffer))),
      iv:           btoa(String.fromCharCode(...iv)),
      createdAt:    new Date().toISOString(),
      version:      this.kekVersion,
    };

    // Store encrypted DEK in KV — key: "dek:{playerId}"
    await this.dekStore.put(
      `dek:${playerId}`,
      JSON.stringify(envelope),
      { expirationTtl: undefined }  // No TTL — persists until explicitly deleted
    );

    // Import the raw DEK as a CryptoKey for immediate use by the caller
    const dek = await crypto.subtle.importKey(
      'raw',
      rawDek,
      { name: 'AES-GCM' },
      false,
      ['encrypt', 'decrypt']
    );

    // Overwrite raw DEK bytes before returning
    rawDek.fill(0);

    return dek;
  }

  /**
   * Retrieve and decrypt a player's DEK from KV.
   * Returns null if the DEK has been shredded (player has been erased).
   *
   * @param playerId - The player's numeric ID
   * @returns        - The DEK as a CryptoKey, or null if shredded
   */
  async getPlayerDek(playerId: number): Promise<CryptoKey | null> {
    const stored = await this.dekStore.get(`dek:${playerId}`);
    if (!stored) {
      return null;  // DEK has been shredded — player data is unrecoverable
    }

    const envelope = JSON.parse(stored) as DekEnvelope;
    const ivBytes             = Uint8Array.from(atob(envelope.iv), c => c.charCodeAt(0));
    const encryptedDekBytes   = Uint8Array.from(atob(envelope.encryptedDek), c => c.charCodeAt(0));

    const rawDek = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: ivBytes },
      this.kek,
      encryptedDekBytes
    );

    return crypto.subtle.importKey(
      'raw',
      rawDek,
      { name: 'AES-GCM' },
      false,
      ['encrypt', 'decrypt']
    );
  }

  /**
   * Shred a player's encryption key, making all their PII unrecoverable.
   *
   * This is the GDPR Art.17 erasure operation. After this call:
   * - The DEK is deleted from KV
   * - All PII columns encrypted with this DEK are permanently unrecoverable
   * - Transaction rows (plaintext financial data) remain intact for AML
   * - An audit record is written before the shred (for compliance evidence)
   *
   * The shred is irreversible. There is no recovery path.
   *
   * @param db        - D1 database (for audit log)
   * @param playerId  - The player's numeric ID
   * @param requestId - The GDPR erasure request reference number
   */
  async shredPlayer(
    db: D1Database,
    playerId: number,
    requestId: string
  ): Promise<ShredResult> {
    const timestamp = new Date().toISOString();

    // Verify DEK exists before shredding (idempotency check)
    const existingDek = await this.dekStore.get(`dek:${playerId}`);
    if (!existingDek) {
      // Already shredded — return success (idempotent)
      return {
        playerId,
        shredded: true,
        method: 'kek_deletion',
        timestamp,
        requestId,
        auditNote: 'DEK already absent — player was previously erased',
      };
    }

    // Write audit record BEFORE shredding (evidence that we acted on the request)
    await db
      .prepare(
        `INSERT INTO compliance_events (user_id, event_type, details)
         VALUES (?, 'gdpr_crypto_shred', ?)`
      )
      .bind(
        playerId,
        JSON.stringify({
          requestId,
          method:          'crypto_shredding',
          kekVersion:      this.kekVersion,
          timestamp,
          legalBasis:      'GDPR Art.17(1) — right to erasure',
          retentionNote:   'GDPR Art.17(3)(b) — transaction records retained for AML compliance (4AMLD Art.40, 5-year minimum)',
          postcondition:   'All PII columns encrypted with player DEK are permanently unrecoverable',
        })
      )
      .run();

    // Mark the player account as erased BEFORE deleting the DEK
    // (so any concurrent requests fail-fast rather than attempting decryption)
    await db
      .prepare(
        "UPDATE users SET status = 'erased', updated_at = CURRENT_TIMESTAMP WHERE id = ?"
      )
      .bind(playerId)
      .run();

    // THE SHRED: delete the DEK from KV.
    // After this line, all PII encrypted with this DEK is permanently unrecoverable.
    await this.dekStore.delete(`dek:${playerId}`);

    return {
      playerId,
      shredded: true,
      method:   'kek_deletion',
      timestamp,
      requestId,
      auditNote:
        `DEK deleted. PII columns encrypted with player DEK are unrecoverable. ` +
        `Transaction records retained per GDPR Art.17(3)(b) / 4AMLD Art.40.`,
    };
  }

  /**
   * Re-encrypt all active DEKs under a new KEK.
   * Used during scheduled key rotation (see KeyRotationSchedule in wrangler.toml).
   *
   * This is a background operation — run via a scheduled Workers cron trigger.
   * It does NOT change the encrypted PII in D1 (DEKs encrypt DEKs, not rows).
   *
   * @param newKek       - New KEK to re-encrypt under
   * @param newKekVersion - New KEK version number
   * @param playerIds    - List of active player IDs to rotate
   */
  async rotateDeks(
    newKek: CryptoKey,
    newKekVersion: number,
    playerIds: number[]
  ): Promise<{ rotated: number; failed: number }> {
    let rotated = 0;
    let failed = 0;

    for (const playerId of playerIds) {
      try {
        const stored = await this.dekStore.get(`dek:${playerId}`);
        if (!stored) continue;  // Already shredded — skip

        const envelope = JSON.parse(stored) as DekEnvelope;

        // Decrypt DEK with old KEK
        const ivBytes           = Uint8Array.from(atob(envelope.iv), c => c.charCodeAt(0));
        const encryptedDekBytes = Uint8Array.from(atob(envelope.encryptedDek), c => c.charCodeAt(0));
        const rawDek = await crypto.subtle.decrypt(
          { name: 'AES-GCM', iv: ivBytes },
          this.kek,   // old KEK
          encryptedDekBytes
        );

        // Re-encrypt DEK with new KEK
        const newIv                  = crypto.getRandomValues(new Uint8Array(12));
        const reEncryptedDekBuffer   = await crypto.subtle.encrypt(
          { name: 'AES-GCM', iv: newIv },
          newKek,
          rawDek
        );

        const newEnvelope: DekEnvelope = {
          encryptedDek: btoa(String.fromCharCode(...new Uint8Array(reEncryptedDekBuffer))),
          iv:           btoa(String.fromCharCode(...newIv)),
          createdAt:    envelope.createdAt,    // preserve original creation date
          version:      newKekVersion,
        };

        await this.dekStore.put(`dek:${playerId}`, JSON.stringify(newEnvelope));
        rotated++;
      } catch {
        failed++;
      }
    }

    return { rotated, failed };
  }
}
