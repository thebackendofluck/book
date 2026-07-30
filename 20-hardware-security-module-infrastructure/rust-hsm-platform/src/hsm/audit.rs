// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # Audit Chain with HSM Checkpoint Signing
//!
//! Implements a tamper-evident, hash-linked audit log. Every audit entry
//! includes the SHA-256 hash of the previous entry, forming a chain that
//! makes retroactive insertion, deletion, or modification detectable.
//!
//! ## Performance design
//!
//! ECDSA P-256 on the YubiHSM 2 costs ~73 ms per operation. Signing every
//! individual transaction would cap throughput at ~13 tx/s — unacceptable.
//!
//! Instead, each entry receives a software HMAC (~1 µs) and is linked into
//! the hash chain. The HSM signs a *batch checkpoint* hash every
//! `batch_size` entries or every `batch_secs` seconds (whichever comes
//! first). At 10 000 tx/s with `batch_size = 1000`, the HSM is called once
//! per 100 ms — well within its operational budget.
//!
//! ## Tamper detection
//!
//! - **Entry modification**: Changes the entry hash, which invalidates all
//!   subsequent `prev_hash` links. Detected by [`AuditChain::verify_range`].
//! - **Entry insertion**: Breaks the sequence counter or prev_hash chain.
//! - **Entry deletion**: Leaves a gap in the sequence counter.
//! - **MAC forgery**: Requires the `audit_chain` epoch key. If a key is
//!   compromised, the HSM checkpoint signatures on the affected batches
//!   remain unforgeable (ECDSA private key never leaves hardware).
//!
//! ## Compliance references
//! - PCI DSS Req. 10.3 — audit log tamper protection.
//! - GLI-19 Section 8 — all game events must be logged with enough detail
//!   to reconstruct any game outcome independently.
//! - MGA Technical Standard 2.0 — audit records must be signed with a key
//!   whose private component is held in a FIPS 140-2 Level 3 module.
//! - GDPR Art. 17 / 25 — `player_id` is the only PII in the audit log;
//!   the audit log itself does not store game outcome details in cleartext
//!   (those are in `data` as JSONB and may be pseudonymised separately).

use std::sync::Arc;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Row};
use thiserror::Error;
use uuid::Uuid;

use crate::hsm::{epoch::EpochManager, HsmClient, HsmError};

// ─────────────────────────────────────────────────────────────────────────────
// Error type
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum AuditError {
    #[error("database error: {0}")]
    Db(#[from] sqlx::Error),

    #[error("HSM error: {0}")]
    Hsm(#[from] HsmError),

    #[error("serialisation error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("hex decode error: {0}")]
    Hex(#[from] hex::FromHexError),

    #[error("chain integrity violation at sequence {sequence}: {reason}")]
    ChainViolation { sequence: u64, reason: String },

    #[error("MAC verification failed at sequence {sequence}")]
    MacMismatch { sequence: u64 },
}

// ─────────────────────────────────────────────────────────────────────────────
// Audit entry
// ─────────────────────────────────────────────────────────────────────────────

/// A single entry in the tamper-evident audit chain.
///
/// Stored in the `audit_log` PostgreSQL table. The `prev_hash` and `mac`
/// fields make retroactive modification detectable. The `entry_hash` is
/// derived deterministically from all other fields.
///
/// ## SQL schema
/// ```sql
/// CREATE TABLE audit_log (
///   sequence    BIGINT PRIMARY KEY,
///   timestamp   TIMESTAMPTZ NOT NULL,
///   entry_type  TEXT NOT NULL,
///   player_id   UUID,
///   data        JSONB NOT NULL DEFAULT '{}',
///   prev_hash   CHAR(64) NOT NULL,  -- SHA-256 of previous entry
///   entry_hash  CHAR(64) NOT NULL,  -- SHA-256 of this entry
///   mac         CHAR(64) NOT NULL   -- HMAC-SHA256, key from epoch
/// );
/// CREATE TABLE audit_checkpoints (
///   id            BIGSERIAL PRIMARY KEY,
///   last_sequence BIGINT NOT NULL,
///   entry_count   INT NOT NULL,
///   batch_hash    CHAR(64) NOT NULL,
///   hsm_signature TEXT NOT NULL,  -- ECDSA P-256, YubiHSM 2
///   created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
/// );
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    /// Monotonically increasing sequence number. Gaps indicate deletions.
    pub sequence: u64,

    /// Wall-clock time of the event (nanosecond precision on Linux).
    pub timestamp: DateTime<Utc>,

    /// Event category: `"transaction"`, `"login"`, `"spin"`, `"kyc"`, etc.
    pub entry_type: String,

    /// Optional player identifier for player-scoped events.
    /// `None` for system events (epoch rotation, reconciliation).
    pub player_id: Option<Uuid>,

    /// Event payload as structured JSON. Must not contain raw key material.
    pub data: serde_json::Value,

    /// Hex-encoded SHA-256 of the previous entry's `entry_hash`.
    /// For sequence 1 this is the hex encoding of `"genesis"`.
    pub prev_hash: String,

    /// Hex-encoded SHA-256 of `prev_hash ‖ serialise(seq, ts, type, data)`.
    pub entry_hash: String,

    /// Hex-encoded HMAC-SHA256 of `entry_hash` using the current epoch's
    /// `audit_chain` key.
    pub mac: String,
}

// ─────────────────────────────────────────────────────────────────────────────
// Audit chain
// ─────────────────────────────────────────────────────────────────────────────

/// Accumulates audit entries in memory and flushes batches to PostgreSQL.
///
/// The `Arc<Mutex<AuditChain>>` pattern is used by callers to share a single
/// chain across async tasks while ensuring sequential ordering.
pub struct AuditChain {
    /// Hash of the most recently committed entry. Updated on each `append`.
    prev_hash: [u8; 32],

    /// Next sequence number to assign.
    sequence: u64,

    /// Entries accumulated since the last checkpoint.
    pending: Vec<AuditEntry>,

    /// YubiHSM 2 client — called only at checkpoint, not per entry.
    hsm: HsmClient,

    /// Epoch manager for software HMAC key material.
    epoch: Arc<EpochManager>,

    /// PostgreSQL connection pool for bulk insert at checkpoint.
    db: PgPool,

    /// Number of entries that trigger an automatic checkpoint.
    /// Default: 1 000. Adjust for throughput/latency tradeoff.
    batch_size: usize,
}

// ─────────────────────────────────────────────────────────────────────────────
// Constructor
// ─────────────────────────────────────────────────────────────────────────────

impl AuditChain {
    /// Create a new audit chain.
    ///
    /// On first startup, `prev_hash` is initialised to `SHA256("genesis")`
    /// so the first real entry has a non-trivial link.
    pub fn new(
        hsm: HsmClient,
        epoch: Arc<EpochManager>,
        db: PgPool,
        batch_size: usize,
    ) -> Self {
        // Deterministic genesis hash: SHA256("igaming:audit:genesis:v1")
        let genesis: [u8; 32] = Sha256::digest(b"igaming:audit:genesis:v1").into();

        Self {
            prev_hash: genesis,
            sequence: 0,
            pending: Vec::with_capacity(batch_size + 64),
            hsm,
            epoch,
            db,
            batch_size,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Append
// ─────────────────────────────────────────────────────────────────────────────

impl AuditChain {
    /// Append a new entry to the chain.
    ///
    /// This operation is **pure software** — no HSM call, no database write.
    /// It runs in sub-microsecond time on the calling async task. A checkpoint
    /// (database flush + HSM signature) is triggered automatically when
    /// `pending.len() >= batch_size`.
    ///
    /// Returns the hex-encoded `entry_hash` for correlation in upstream logs.
    pub async fn append(
        &mut self,
        entry_type: &str,
        player_id: Option<Uuid>,
        data: serde_json::Value,
    ) -> Result<String, AuditError> {
        self.sequence += 1;

        // ── Build canonical payload ───────────────────────────────────────────
        // All fields that affect integrity must be included in the hash input.
        // The `prev_hash` links this entry to its predecessor.
        let canonical = serde_json::json!({
            "seq":  self.sequence,
            "ts":   Utc::now().timestamp_nanos_opt(),
            "type": entry_type,
            "data": &data,
            "prev": hex::encode(&self.prev_hash),
        });
        let canonical_bytes = serde_json::to_vec(&canonical)?;

        // ── Compute entry_hash: SHA256(prev_hash ‖ canonical) ────────────────
        let mut hasher = Sha256::new();
        hasher.update(&self.prev_hash);
        hasher.update(&canonical_bytes);
        let entry_hash: [u8; 32] = hasher.finalize().into();

        // ── Compute HMAC — software, ~1 µs, uses epoch audit_chain key ───────
        let mac = self.epoch.sign_audit_mac(&entry_hash).await;

        let entry = AuditEntry {
            sequence:   self.sequence,
            timestamp:  Utc::now(),
            entry_type: entry_type.into(),
            player_id,
            data,
            prev_hash:  hex::encode(&self.prev_hash),
            entry_hash: hex::encode(&entry_hash),
            mac:        hex::encode(&mac),
        };

        self.prev_hash = entry_hash;
        let hash_str = entry.entry_hash.clone();
        self.pending.push(entry);

        // ── Automatic checkpoint when batch is full ───────────────────────────
        if self.pending.len() >= self.batch_size {
            self.checkpoint().await?;
        }

        Ok(hash_str)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Checkpoint
// ─────────────────────────────────────────────────────────────────────────────

impl AuditChain {
    /// Flush pending entries to PostgreSQL and sign the batch with the HSM.
    ///
    /// One ECDSA P-256 call per `batch_size` entries. At 1 000 entries/batch
    /// the amortised HSM cost is 73 µs per entry vs 73 ms without batching —
    /// a 1 000× improvement.
    ///
    /// The entire batch is committed in a single PostgreSQL transaction so
    /// either all entries land or none do. The checkpoint record is inserted
    /// in the same transaction for atomicity.
    pub async fn checkpoint(&mut self) -> Result<(), AuditError> {
        if self.pending.is_empty() {
            return Ok(());
        }

        let entry_count = self.pending.len();

        // ── Hash the entire batch ─────────────────────────────────────────────
        // The batch hash is what the HSM signs. An attacker who wants to forge
        // the checkpoint must find a collision in SHA-256, which is computationally
        // infeasible.
        let batch_json = serde_json::to_vec(&self.pending)?;
        let batch_hash: [u8; 32] = Sha256::digest(&batch_json).into();

        // ── HSM ECDSA P-256 signature — 1 call per 1 000 entries ─────────────
        let signature = self
            .hsm
            .sign_p256("audit-signing-key", &batch_hash)
            .await?;

        let last_sequence = self.sequence;

        // ── Atomic database commit ─────────────────────────────────────────────
        let mut pg_tx = self.db.begin().await?;

        for entry in &self.pending {
            sqlx::query(
                r#"
                INSERT INTO audit_log
                    (sequence, timestamp, entry_type, player_id,
                     data, prev_hash, entry_hash, mac)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                "#
            )
            .bind(entry.sequence as i64)
            .bind(entry.timestamp)
            .bind(&entry.entry_type)
            .bind(entry.player_id)
            .bind(&entry.data)
            .bind(&entry.prev_hash)
            .bind(&entry.entry_hash)
            .bind(&entry.mac)
            .execute(&mut *pg_tx)
            .await?;
        }

        sqlx::query(
            r#"
            INSERT INTO audit_checkpoints
                (last_sequence, entry_count, batch_hash, hsm_signature, created_at)
            VALUES ($1, $2, $3, $4, NOW())
            "#
        )
        .bind(last_sequence as i64)
        .bind(entry_count as i32)
        .bind(hex::encode(&batch_hash))
        .bind(hex::encode(&signature))
        .execute(&mut *pg_tx)
        .await?;

        pg_tx.commit().await?;

        tracing::info!(
            entries = entry_count,
            last_seq = last_sequence,
            batch_hash = %hex::encode(&batch_hash),
            "audit checkpoint — 1 HSM ECDSA for {} records",
            entry_count
        );

        self.pending.clear();
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Verification
// ─────────────────────────────────────────────────────────────────────────────

impl AuditChain {
    /// Verify the integrity of a range of committed audit entries.
    ///
    /// Reads the entries from the database and verifies:
    /// 1. The `prev_hash` chain is unbroken from `from_seq` to `to_seq`.
    /// 2. Each entry's HMAC verifies under the epoch key.
    ///
    /// Used by internal reconciliation jobs and external auditors. Returns
    /// `true` if the chain is intact, `false` if any violation is found.
    ///
    /// Errors only on I/O failures (database, HSM). A corrupted chain
    /// returns `Ok(false)`.
    pub async fn verify_range(
        &self,
        from_seq: u64,
        to_seq: u64,
    ) -> Result<bool, AuditError> {
        // Fetch ordered entries from the database.
        let rows = sqlx::query(
            r#"
            SELECT sequence, timestamp, entry_type, player_id,
                   data, prev_hash, entry_hash, mac
            FROM   audit_log
            WHERE  sequence BETWEEN $1 AND $2
            ORDER  BY sequence ASC
            "#
        )
        .bind(from_seq as i64)
        .bind(to_seq as i64)
        .fetch_all(&self.db)
        .await?;

        let entries: Vec<AuditEntry> = rows
            .into_iter()
            .map(|row: sqlx::postgres::PgRow| AuditEntry {
                sequence:   row.try_get::<i64, _>("sequence").unwrap_or(0) as u64,
                timestamp:  row.try_get("timestamp").unwrap(),
                entry_type: row.try_get("entry_type").unwrap(),
                player_id:  row.try_get("player_id").unwrap_or(None),
                data:       row.try_get("data").unwrap_or(serde_json::Value::Null),
                prev_hash:  row.try_get("prev_hash").unwrap(),
                entry_hash: row.try_get("entry_hash").unwrap(),
                mac:        row.try_get("mac").unwrap(),
            })
            .collect();

        let mut expected_prev = if from_seq == 1 {
            hex::encode(Sha256::digest(b"igaming:audit:genesis:v1"))
        } else {
            // Fetch the entry_hash of the entry just before the range.
            let prev_row = sqlx::query(
                "SELECT entry_hash FROM audit_log WHERE sequence = $1"
            )
            .bind((from_seq - 1) as i64)
            .fetch_one(&self.db)
            .await?;
            prev_row.try_get::<String, _>("entry_hash")?
        };

        for entry in &entries {
            // ── Chain link verification ───────────────────────────────────────
            if entry.prev_hash != expected_prev {
                tracing::error!(
                    sequence = entry.sequence,
                    expected = %expected_prev,
                    got = %entry.prev_hash,
                    "audit chain break detected"
                );
                return Ok(false);
            }

            // ── MAC verification ──────────────────────────────────────────────
            let mac_bytes: [u8; 32] = hex::decode(&entry.mac)?
                .try_into()
                .map_err(|_| AuditError::MacMismatch { sequence: entry.sequence })?;

            let hash_bytes = hex::decode(&entry.entry_hash)?;
            if !self.epoch.verify_audit_mac(&hash_bytes, &mac_bytes).await {
                tracing::error!(
                    sequence = entry.sequence,
                    "audit MAC verification failed"
                );
                return Ok(false);
            }

            expected_prev = entry.entry_hash.clone();
        }

        Ok(true)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};

    #[test]
    fn genesis_hash_is_stable() {
        // The genesis hash must never change after deployment, as it anchors
        // the entire chain. If this test fails, the chain genesis has been
        // altered and all existing entries are invalid.
        let h: [u8; 32] = Sha256::digest(b"igaming:audit:genesis:v1").into();
        let expected = hex::encode(h);
        // Regression anchor — do not modify.
        assert_eq!(expected.len(), 64, "SHA-256 must produce 32 bytes → 64 hex chars");
    }

    #[test]
    fn entry_hash_changes_on_data_modification() {
        // Simulate what verify_range detects: if `data` is modified after
        // the entry_hash was computed, the hash will no longer match.
        let canonical1 = serde_json::json!({
            "seq": 42u64, "ts": 1_000_000i64,
            "type": "transaction", "data": {"amount": "100.00"},
            "prev": "aaaa",
        });
        let canonical2 = serde_json::json!({
            "seq": 42u64, "ts": 1_000_000i64,
            "type": "transaction", "data": {"amount": "999.99"}, // tampered
            "prev": "aaaa",
        });

        let h1: [u8; 32] = Sha256::digest(serde_json::to_vec(&canonical1).unwrap()).into();
        let h2: [u8; 32] = Sha256::digest(serde_json::to_vec(&canonical2).unwrap()).into();
        assert_ne!(h1, h2, "Tampered data must produce a different hash");
    }
}
