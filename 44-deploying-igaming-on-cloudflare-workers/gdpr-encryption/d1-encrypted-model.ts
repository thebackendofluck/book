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
 * D1 model with transparent column-level encryption for PII fields.
 *
 * Wraps a D1Database binding and automatically encrypts/decrypts
 * the columns listed in the `piiColumns` constructor argument.
 *
 * GDPR Art.32(1)(a): encryption of personal data at rest.
 * GDPR Art.25(1): data protection by design — encryption is applied
 * automatically at the model layer, so callers cannot accidentally
 * write plaintext PII to D1.
 *
 * Usage:
 *
 *   // In your Worker handler:
 *   const cipher = await FieldCipher.fromSecret(env.ENCRYPTION_KEY);
 *   const players = new EncryptedModel(
 *     env.DB,
 *     cipher,
 *     'users',
 *     ['email', 'full_name', 'phone', 'address', 'date_of_birth', 'ip_address']
 *   );
 *
 *   // Write: plaintext in → ciphertext stored in D1
 *   await players.insert({
 *     email:         'john@example.com',
 *     full_name:     'John Doe',
 *     phone:         '+44 7700 900000',
 *     date_of_birth: '1985-03-15',
 *     balance:       0,
 *     country:       'GB',
 *     currency:      'GBP',
 *   });
 *
 *   // Read: ciphertext in D1 → plaintext returned
 *   const player = await players.findById(42);
 *   // { id: 42, email: 'john@example.com', full_name: 'John Doe', ... }
 *
 * What D1 actually stores for encrypted columns:
 *   email = '{"iv":"ABC...","ct":"XYZ...","v":1}'
 *
 * Even if an attacker gains read access to the D1 database (e.g. via a SQL
 * injection vulnerability), they see only ciphertext. The AES-256-GCM key
 * lives in Workers Secrets and is never present in D1 or source control.
 */

import { FieldCipher } from './field-cipher.js';

export type Row = Record<string, unknown>;

export class EncryptedModel {
  private readonly db: D1Database;
  private readonly cipher: FieldCipher;
  private readonly table: string;
  private readonly piiColumns: ReadonlySet<string>;

  /**
   * @param db         - D1Database binding from the Workers Env
   * @param cipher     - FieldCipher instance initialised with the encryption key
   * @param table      - D1 table name (e.g. 'users')
   * @param piiColumns - Array of column names to encrypt (e.g. ['email', 'full_name'])
   */
  constructor(
    db: D1Database,
    cipher: FieldCipher,
    table: string,
    piiColumns: string[]
  ) {
    this.db = db;
    this.cipher = cipher;
    this.table = table;
    this.piiColumns = new Set(piiColumns);
  }

  // ─── Private helpers ────────────────────────────────────────────────────────

  /**
   * Encrypt all PII columns in a row before writing to D1.
   */
  private async encryptRow(row: Row): Promise<Row> {
    const encrypted = { ...row };
    for (const col of this.piiColumns) {
      if (encrypted[col] !== undefined && encrypted[col] !== null) {
        encrypted[col] = await this.cipher.encrypt(String(encrypted[col]));
      }
    }
    return encrypted;
  }

  /**
   * Decrypt all PII columns in a row after reading from D1.
   * Non-PII columns are returned as-is.
   * Rows where a PII column contains plaintext (e.g. legacy data) are
   * returned as-is with a console warning.
   */
  private async decryptRow(row: Row): Promise<Row> {
    const decrypted = { ...row };
    for (const col of this.piiColumns) {
      const val = decrypted[col];
      if (typeof val === 'string' && val.startsWith('{')) {
        try {
          decrypted[col] = await this.cipher.decrypt(val);
        } catch (err) {
          // Log but do not throw — allows partial decryption of mixed datasets
          console.warn(
            `[EncryptedModel] Failed to decrypt column "${col}" in table "${this.table}": ${err}`
          );
        }
      }
      // If value doesn't look like JSON ciphertext, leave as-is
      // (handles legacy plaintext rows and NULL values)
    }
    return decrypted;
  }

  // ─── Public interface ────────────────────────────────────────────────────────

  /**
   * Insert a row with PII columns automatically encrypted.
   * Returns the last inserted row ID.
   */
  async insert(row: Row): Promise<number> {
    const encryptedRow = await this.encryptRow(row);
    const columns = Object.keys(encryptedRow);
    const placeholders = columns.map(() => '?').join(', ');
    const values = Object.values(encryptedRow);

    const result = await this.db
      .prepare(
        `INSERT INTO ${this.table} (${columns.join(', ')}) VALUES (${placeholders})`
      )
      .bind(...values)
      .run();

    return (result.meta?.last_row_id as number) ?? 0;
  }

  /**
   * Find a row by primary key (id column), decrypt PII columns.
   * Returns null if not found.
   */
  async findById(id: number): Promise<Row | null> {
    const row = await this.db
      .prepare(`SELECT * FROM ${this.table} WHERE id = ?`)
      .bind(id)
      .first<Row>();

    if (!row) return null;
    return this.decryptRow(row);
  }

  /**
   * Find a single row by a non-PII column value.
   *
   * Important: you cannot search on encrypted columns because the ciphertext
   * is non-deterministic (different IV on each encrypt call). To look up a
   * player by email, store a deterministic search token separately:
   *
   *   email_hash = HMAC-SHA256(HMAC_KEY, email)
   *   email      = AES-GCM(ENCRYPTION_KEY, email)  ← stored encrypted
   *
   * Then: SELECT * FROM users WHERE email_hash = ?
   *
   * The HMAC_KEY must be separate from ENCRYPTION_KEY and stable (never rotated
   * unless you re-hash all email_hash values). See data-residency-worker.ts
   * for the search token pattern.
   *
   * @param column - Must NOT be a PII column (use email_hash, not email)
   * @param value  - The value to match
   */
  async findByColumn(column: string, value: unknown): Promise<Row | null> {
    if (this.piiColumns.has(column)) {
      throw new Error(
        `Cannot search on encrypted PII column "${column}". ` +
        'Use a deterministic search token (HMAC) column instead. ' +
        'See the email_hash pattern in d1-encrypted-model.ts.'
      );
    }

    const row = await this.db
      .prepare(`SELECT * FROM ${this.table} WHERE ${column} = ?`)
      .bind(value)
      .first<Row>();

    if (!row) return null;
    return this.decryptRow(row);
  }

  /**
   * Update specific columns for a row. PII columns are re-encrypted.
   *
   * @param id      - Primary key of the row to update
   * @param updates - Object with column names and new values
   */
  async update(id: number, updates: Row): Promise<void> {
    const encryptedUpdates = await this.encryptRow(updates);
    const setClauses = Object.keys(encryptedUpdates)
      .map(col => `${col} = ?`)
      .join(', ');
    const values = [...Object.values(encryptedUpdates), id];

    await this.db
      .prepare(
        `UPDATE ${this.table} SET ${setClauses}, updated_at = CURRENT_TIMESTAMP WHERE id = ?`
      )
      .bind(...values)
      .run();
  }

  /**
   * List rows with pagination. All PII columns are decrypted.
   *
   * @param limit  - Number of rows to return (default: 50, max: 200)
   * @param offset - Rows to skip (for pagination)
   */
  async list(limit = 50, offset = 0): Promise<Row[]> {
    const clampedLimit = Math.min(limit, 200);
    const { results } = await this.db
      .prepare(`SELECT * FROM ${this.table} LIMIT ? OFFSET ?`)
      .bind(clampedLimit, offset)
      .all<Row>();

    return Promise.all(results.map(row => this.decryptRow(row)));
  }

  /**
   * Re-encrypt all rows in the table after a key rotation.
   *
   * Pass the old cipher (previous key) and the current model uses its
   * cipher (new key) to re-encrypt. Processes in batches to respect
   * D1's row-write limits.
   *
   * This is a long-running operation — call from a scheduled Worker cron
   * trigger, not from a request handler.
   *
   * @param oldCipher - FieldCipher instance with the previous key
   * @param batchSize - Rows to process per batch (default: 100)
   */
  async reEncryptAll(oldCipher: FieldCipher, batchSize = 100): Promise<{ processed: number; failed: number }> {
    let offset = 0;
    let processed = 0;
    let failed = 0;

    while (true) {
      const { results } = await this.db
        .prepare(`SELECT * FROM ${this.table} LIMIT ? OFFSET ?`)
        .bind(batchSize, offset)
        .all<Row>();

      if (results.length === 0) break;

      for (const row of results) {
        try {
          // Decrypt with old key
          const id = row['id'] as number;
          const decryptedRow: Row = { ...row };

          for (const col of this.piiColumns) {
            const val = decryptedRow[col];
            if (typeof val === 'string' && val.startsWith('{')) {
              decryptedRow[col] = await oldCipher.decrypt(val);
            }
          }

          // Re-encrypt with new key (via this.cipher)
          const reEncryptedUpdates: Row = {};
          for (const col of this.piiColumns) {
            if (decryptedRow[col] !== undefined && decryptedRow[col] !== null) {
              reEncryptedUpdates[col] = await this.cipher.encrypt(String(decryptedRow[col]));
            }
          }

          if (Object.keys(reEncryptedUpdates).length > 0) {
            await this.update(id, reEncryptedUpdates);
          }

          processed++;
        } catch {
          failed++;
        }
      }

      offset += batchSize;
      if (results.length < batchSize) break;
    }

    return { processed, failed };
  }
}
