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
 * Encrypt existing plaintext PII columns in D1 in-place.
 *
 * Run this migration once when introducing field-level encryption to an
 * existing platform. After migration, all new writes should use EncryptedModel.
 *
 * Migration strategy:
 *   1. Read plaintext rows from D1 in batches
 *   2. Encrypt PII columns with the current ENCRYPTION_KEY
 *   3. Write ciphertext back to D1
 *   4. Verify by decrypting a sample
 *   5. Log migration progress to compliance_events
 *
 * Rollback:
 *   The migration is non-destructive in the sense that ciphertext is stored
 *   in the same column as plaintext was. If the migration fails mid-way,
 *   partially migrated rows can be identified because:
 *     - Plaintext: does NOT start with '{'
 *     - Ciphertext: starts with '{"iv":'
 *   A re-run will skip already-encrypted rows automatically.
 *
 * Usage (via Wrangler or a D1 HTTP API client):
 *   Deploy this as a scheduled Worker handler or invoke via REST API.
 *   Not intended for direct CLI execution — it requires the Workers Env.
 *
 * Example deployment in wrangler.toml:
 *   [triggers]
 *   crons = ["0 3 * * *"]  # Daily at 03:00 UTC
 *   # Then in your Worker:
 *   scheduled: async (event, env) => {
 *     if (event.cron === "0 3 * * *") {
 *       const migrator = new DataMigrator(env.DB, env.ENCRYPTION_KEY);
 *       await migrator.runMigration();
 *     }
 *   }
 */

import { FieldCipher } from './field-cipher.js';

// PII columns per table — must match EncryptedModel configuration
const MIGRATION_CONFIG: Record<string, string[]> = {
  users: [
    'email',
    'full_name',
    'phone',
    'address',
    'date_of_birth',
    'ip_address',
  ],
  kyc_records: [
    'document_number',
    'full_name',
    'date_of_birth',
    'address',
  ],
};

export interface MigrationStats {
  table: string;
  totalRows: number;
  alreadyEncrypted: number;
  encrypted: number;
  failed: number;
  durationMs: number;
}

export interface MigrationResult {
  completedAt: string;
  tables: MigrationStats[];
  totalEncrypted: number;
  totalFailed: number;
}

export class DataMigrator {
  private readonly db: D1Database;
  private readonly cipher: FieldCipher;
  private readonly batchSize: number;

  constructor(db: D1Database, cipher: FieldCipher, batchSize = 50) {
    this.db = db;
    this.cipher = cipher;
    this.batchSize = batchSize;
  }

  /**
   * Run the full migration across all configured tables.
   * Safe to re-run — already-encrypted rows are skipped.
   */
  async runMigration(): Promise<MigrationResult> {
    const results: MigrationStats[] = [];
    let totalEncrypted = 0;
    let totalFailed = 0;

    for (const [table, piiColumns] of Object.entries(MIGRATION_CONFIG)) {
      const stats = await this.migrateTable(table, piiColumns);
      results.push(stats);
      totalEncrypted += stats.encrypted;
      totalFailed += stats.failed;
    }

    const result: MigrationResult = {
      completedAt: new Date().toISOString(),
      tables: results,
      totalEncrypted,
      totalFailed,
    };

    // Log the migration for compliance audit
    await this.db
      .prepare(
        `INSERT INTO compliance_events (user_id, event_type, details)
         VALUES (0, 'pii_encryption_migration', ?)`
      )
      .bind(JSON.stringify({
        ...result,
        legalBasis: 'GDPR Art.32(1)(a) — encryption of personal data',
      }))
      .run();

    return result;
  }

  /**
   * Migrate a single table: encrypt plaintext PII columns in batches.
   */
  private async migrateTable(table: string, piiColumns: string[]): Promise<MigrationStats> {
    const start = Date.now();
    let totalRows = 0;
    let alreadyEncrypted = 0;
    let encrypted = 0;
    let failed = 0;
    let offset = 0;

    // Get total count for progress tracking
    const countResult = await this.db
      .prepare(`SELECT COUNT(*) as count FROM ${table}`)
      .first<{ count: number }>();
    totalRows = countResult?.count ?? 0;

    while (true) {
      const { results } = await this.db
        .prepare(`SELECT * FROM ${table} LIMIT ? OFFSET ?`)
        .bind(this.batchSize, offset)
        .all<Record<string, unknown>>();

      if (results.length === 0) break;

      for (const row of results) {
        const id = row['id'] as number;
        const updates: Record<string, string> = {};
        let needsUpdate = false;
        let alreadyDone = true;

        for (const col of piiColumns) {
          const val = row[col];
          if (val === null || val === undefined) continue;

          const strVal = String(val);

          // Skip if already encrypted (starts with JSON object)
          if (strVal.startsWith('{"iv":')) {
            continue;  // Already encrypted
          }

          alreadyDone = false;

          try {
            updates[col] = await this.cipher.encrypt(strVal);
            needsUpdate = true;
          } catch {
            console.error(`[DataMigrator] Failed to encrypt ${table}.${col} for row ${id}`);
            failed++;
          }
        }

        if (alreadyDone) {
          alreadyEncrypted++;
          continue;
        }

        if (needsUpdate) {
          try {
            const setClauses = Object.keys(updates).map(c => `${c} = ?`).join(', ');
            await this.db
              .prepare(
                `UPDATE ${table} SET ${setClauses}, updated_at = CURRENT_TIMESTAMP WHERE id = ?`
              )
              .bind(...Object.values(updates), id)
              .run();
            encrypted++;
          } catch (err) {
            console.error(`[DataMigrator] Failed to update ${table} row ${id}: ${err}`);
            failed++;
          }
        }
      }

      offset += this.batchSize;
      if (results.length < this.batchSize) break;
    }

    return {
      table,
      totalRows,
      alreadyEncrypted,
      encrypted,
      failed,
      durationMs: Date.now() - start,
    };
  }

  /**
   * Verify the migration by decrypting a sample of rows.
   * Call after runMigration() to validate the output.
   */
  async verifySample(table: string, piiColumns: string[], sampleSize = 5): Promise<{
    verified: number;
    failed: number;
  }> {
    const { results } = await this.db
      .prepare(`SELECT * FROM ${table} LIMIT ?`)
      .bind(sampleSize)
      .all<Record<string, unknown>>();

    let verified = 0;
    let failed = 0;

    for (const row of results) {
      for (const col of piiColumns) {
        const val = row[col];
        if (typeof val !== 'string' || !val.startsWith('{"iv":')) continue;

        try {
          const plaintext = await this.cipher.decrypt(val);
          if (plaintext.length > 0) {
            verified++;
          } else {
            failed++;
          }
        } catch {
          console.error(`[DataMigrator] Verification failed for ${table}.${col}`);
          failed++;
        }
      }
    }

    return { verified, failed };
  }
}
