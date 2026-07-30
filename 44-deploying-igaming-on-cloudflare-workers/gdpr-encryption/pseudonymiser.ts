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
 * Pseudonymisation for GDPR Art.17 erasure requests on Cloudflare Workers.
 *
 * Implements GDPR Art.4(5): "pseudonymisation means the processing of personal
 * data in such a manner that the personal data can no longer be attributed to a
 * specific data subject without the use of additional information".
 *
 * GDPR Art.25(1): "data protection by design" — pseudonymisation is one of
 * the measures controllers must implement at the time of processing.
 *
 * GDPR Art.17(1): "right to be forgotten" — when a player requests erasure,
 * pseudonymisation of PII fields with an ephemeral key satisfies the erasure
 * obligation while preserving AML-required transaction records.
 *
 * LGPD (Brazil) Art.13(IV): equivalent pseudonymisation definition.
 * UK GDPR Art.4(5): identical to EU GDPR.
 *
 * Technique: HMAC-SHA-256 with an ephemeral salt that is generated for each
 * erasure request and never persisted. The salt is garbage-collected after
 * the operation — the resulting hashes are irreversible even to the operator.
 *
 * AML/CFT conflict resolution:
 * GDPR Art.17(3)(b) explicitly states that the right to erasure does not
 * apply where processing is necessary "for compliance with a legal obligation
 * which requires processing by Union or Member State law". AML regulations
 * (4AMLD, FATF Recommendations) require retention of transaction records
 * for a minimum of 5 years. Therefore:
 *
 *   PII fields     → pseudonymised (erasure satisfied)
 *   Transactions   → retained intact (AML obligation)
 *   Self-exclusion → NEVER pseudonymised (player safety)
 *   AML flags      → NEVER pseudonymised (regulatory requirement)
 *
 * UK GDPR Art.17(3)(b): identical override for UK-regulated operators.
 * LGPD Art.16(V): equivalent legal obligation retention basis.
 */

export interface PseudonymisationResult {
  data: Record<string, unknown>;
  pseudonymisedFields: string[];
  retainedFields: string[];
  requestId: string;
  timestamp: string;
}

export class Pseudonymiser {
  /**
   * Fields that CAN be pseudonymised under GDPR Art.17.
   * These are personal data fields with no AML/regulatory retention obligation.
   */
  static readonly ERASABLE_FIELDS: ReadonlyArray<string> = [
    'email',
    'full_name',
    'first_name',
    'last_name',
    'phone',
    'phone_number',
    'address',
    'address_line1',
    'address_line2',
    'city',
    'postal_code',
    'date_of_birth',
    'ip_address',
    'last_login_ip',
    'device_fingerprint',
    'bank_account_iban',   // IBAN is PII but not AML-required post-transaction
    'username',
  ];

  /**
   * Fields that MUST be retained regardless of erasure request.
   *
   * Legal basis for retention:
   *   - Transaction records: 4AMLD Art.40, FATF Rec.11 (5-year minimum)
   *   - Self-exclusion:      UKGC Social Responsibility Code 3.5.3
   *   - KYC status:          4AMLD Art.13, MLD5 Art.14
   *   - AML flags:           FATF Recommendations, POCA 2002 (UK)
   *   - player_id_hash:      Required to link retained records
   *
   * GDPR Art.17(3)(b): retention override for legal obligation compliance.
   */
  static readonly RETAINED_FIELDS: ReadonlyArray<string> = [
    'player_id',
    'player_id_hash',
    'transaction_id',
    'transaction_history',
    'amount',
    'currency',
    'transaction_type',
    'transaction_status',
    'self_exclusion',
    'self_exclusion_until',
    'kyc_status',
    'kyc_level',
    'aml_flags',
    'aml_risk_score',
    'sanctions_checked',
    'pep_status',
    'compliance_events',
    'created_at',
    'updated_at',
  ];

  /**
   * Pseudonymise a player record, replacing all ERASABLE_FIELDS with
   * irreversible HMAC-SHA-256 hashes.
   *
   * The ephemeral salt is generated fresh for this invocation and is never
   * stored anywhere — after this function returns, the hashes cannot be
   * reversed even by the operator. The garbage collector will reclaim the
   * salt from memory when the Workers isolate is next recycled.
   *
   * @param playerData - The player record to pseudonymise (from D1)
   * @param requestId  - The GDPR erasure request ID (for audit log)
   * @returns          - Pseudonymised record and metadata
   */
  async pseudonymise(
    playerData: Record<string, unknown>,
    requestId: string
  ): Promise<PseudonymisationResult> {
    // Generate ephemeral salt — 32 cryptographically random bytes.
    // This salt is NEVER stored. Once the function returns, the salt
    // is eligible for garbage collection. The resulting HMAC hashes
    // are irreversible without the salt.
    const salt = crypto.getRandomValues(new Uint8Array(32));

    const hmacKey = await crypto.subtle.importKey(
      'raw',
      salt,
      { name: 'HMAC', hash: 'SHA-256' },
      false,          // not extractable
      ['sign']
    );

    const result = { ...playerData };
    const pseudonymisedFields: string[] = [];
    const retainedFields: string[] = [];

    for (const field of Pseudonymiser.ERASABLE_FIELDS) {
      if (result[field] !== undefined && result[field] !== null) {
        const encoded = new TextEncoder().encode(String(result[field]));
        const hash = await crypto.subtle.sign('HMAC', hmacKey, encoded);
        // Prefix makes it unambiguous that this is a pseudonymised value,
        // not a real email/name/phone that happens to look like base64.
        result[field] = 'PSEUDONYMISED:' + btoa(String.fromCharCode(...new Uint8Array(hash)));
        pseudonymisedFields.push(field);
      }
    }

    // Document which fields were retained and why
    for (const field of Pseudonymiser.RETAINED_FIELDS) {
      if (result[field] !== undefined) {
        retainedFields.push(field);
      }
    }

    // Salt is now eligible for garbage collection.
    // The HMAC hashes in result[field] are irreversible.
    // Explicitly overwrite the salt array as a best-effort measure
    // (Workers does not guarantee immediate memory zeroing).
    salt.fill(0);

    return {
      data: result,
      pseudonymisedFields,
      retainedFields,
      requestId,
      timestamp: new Date().toISOString(),
    };
  }

  /**
   * Apply pseudonymisation to a player record in D1.
   *
   * This is the production-facing method: reads the player row, pseudonymises
   * PII fields, writes back to D1, and logs the erasure event for audit.
   *
   * @param db        - D1 database binding
   * @param playerId  - The player's numeric ID in D1
   * @param requestId - The GDPR erasure request reference number
   */
  async erasePlayer(
    db: D1Database,
    playerId: number,
    requestId: string
  ): Promise<PseudonymisationResult> {
    // Read current player record
    const player = await db
      .prepare('SELECT * FROM users WHERE id = ?')
      .bind(playerId)
      .first<Record<string, unknown>>();

    if (!player) {
      throw new Error(`Player ${playerId} not found`);
    }

    // Pseudonymise PII fields (self_exclusion and AML fields are never touched)
    const result = await this.pseudonymise(player, requestId);

    // Write pseudonymised values back to D1
    // Only update ERASABLE_FIELDS that were actually present
    for (const field of result.pseudonymisedFields) {
      // Map TypeScript field names to D1 column names (snake_case already)
      await db
        .prepare(`UPDATE users SET ${field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?`)
        .bind(result.data[field], playerId)
        .run();
    }

    // Mark account as erasure-complete (status change prevents future logins)
    await db
      .prepare(
        "UPDATE users SET status = 'erased', updated_at = CURRENT_TIMESTAMP WHERE id = ?"
      )
      .bind(playerId)
      .run();

    // Write audit log — retained for AML compliance even after erasure
    await db
      .prepare(
        `INSERT INTO compliance_events (user_id, event_type, details)
         VALUES (?, 'gdpr_erasure', ?)`
      )
      .bind(
        playerId,
        JSON.stringify({
          requestId,
          pseudonymisedFields: result.pseudonymisedFields,
          retainedFields:      result.retainedFields,
          timestamp:           result.timestamp,
          legalBasis:          'GDPR Art.17(1) — right to erasure',
          retentionOverride:   'GDPR Art.17(3)(b) — AML legal obligation (4AMLD Art.40, 5-year minimum)',
        })
      )
      .run();

    return result;
  }
}
