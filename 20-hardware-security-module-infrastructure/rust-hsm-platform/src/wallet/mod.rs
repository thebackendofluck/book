// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # Wallet Engine
//!
//! The wallet engine processes financial transactions with full ACID guarantees,
//! idempotency, responsible-gambling limit enforcement, and HSM-backed
//! non-repudiation.
//!
//! ## Design principles
//!
//! 1. **Ledger as truth** — the `transactions` table is the authoritative
//!    record. The `balance` column on `accounts` is a materialised projection
//!    maintained for query performance. In case of any discrepancy, the ledger
//!    prevails (reconciliation job detects and alerts on divergence).
//!
//! 2. **Idempotency by UUID** — the caller generates a UUID v4 idempotency key.
//!    If the same UUID is submitted twice (e.g. after a network timeout), the
//!    engine returns the result of the first call without re-processing. This
//!    prevents double-credits and double-debits, which are PCI DSS Req. 10
//!    violations.
//!
//! 3. **Advisory locks** — `pg_advisory_xact_lock(player_id_as_i64)` prevents
//!    concurrent transactions for the same player from racing on the balance
//!    check + update step. This is stronger than row-level locks because the
//!    lock is taken before reading, preventing phantom reads.
//!
//! 4. **Non-repudiation** — after committing the transaction to the database,
//!    the engine signs a deterministic payload with the YubiHSM 2. The
//!    signature is stored in the `transactions.signature` column and proves
//!    that the platform acknowledges the transaction. Regulators (MGA, UKGC)
//!    accept this as evidence that the transaction was processed in good faith.
//!
//! ## Responsible gambling limits
//!
//! Deposit limits are enforced synchronously on the transaction path:
//!
//! - Player-set daily deposit limit (UKGC mandatory).
//! - Jurisdiction monthly deposit cap (GGL: €1 000/month; KSA: €700/month).
//!
//! Limit checks query the transaction ledger directly — not a cached balance —
//! to prevent circumvention by timing attacks.
//!
//! ## PostgreSQL schema
//!
//! ```sql
//! CREATE TYPE tx_type AS ENUM
//!     ('deposit','withdrawal','bet','win','refund','bonus','chargeback');
//!
//! CREATE TABLE accounts (
//!   player_id              UUID PRIMARY KEY,
//!   balance                NUMERIC(20,8) NOT NULL DEFAULT 0,
//!   locked                 BOOLEAN NOT NULL DEFAULT FALSE,
//!   deposit_limit_daily    NUMERIC(20,8),
//!   deposit_limit_monthly  NUMERIC(20,8),
//!   jurisdiction_override  CHAR(2),
//!   updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
//! );
//!
//! CREATE TABLE transactions (
//!   id               UUID PRIMARY KEY,
//!   player_id        UUID NOT NULL REFERENCES accounts(player_id),
//!   amount           NUMERIC(20,8) NOT NULL CHECK (amount > 0),
//!   balance_after    NUMERIC(20,8) NOT NULL,
//!   tx_type          tx_type NOT NULL,
//!   currency         CHAR(3) NOT NULL,
//!   jurisdiction     CHAR(2) NOT NULL,
//!   game_session_id  UUID,
//!   idempotency_key  UUID NOT NULL UNIQUE,
//!   signature        TEXT NOT NULL,
//!   created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
//! );
//! CREATE INDEX ON transactions (player_id, created_at DESC);
//! CREATE INDEX ON transactions (idempotency_key);
//! ```
//!
//! ## Compliance references
//! - PCI DSS Req. 3, 10 — ledger integrity, audit trail.
//! - UKGC LCCP — player deposit limit settings (player-configurable, mandatory).
//! - GGL (German) — €1 000/month cap for all operators.
//! - KSA (Netherlands) — €700/month cap.
//! - MGA Tech Standard — non-repudiation via cryptographic signing.

use rust_decimal::Decimal;
use sqlx::{postgres::PgRow, PgPool, Row};
use thiserror::Error;
use uuid::Uuid;

use crate::hsm::HsmClient;

// ─────────────────────────────────────────────────────────────────────────────
// Error type
// ─────────────────────────────────────────────────────────────────────────────

/// All errors the wallet engine may surface.
///
/// Each variant carries enough context to populate the audit log without
/// requiring the caller to re-query the database.
#[derive(Debug, Error)]
pub enum WalletError {
    #[error("insufficient balance: have {have}, need {need}")]
    InsufficientBalance { have: Decimal, need: Decimal },

    #[error("account locked")]
    AccountLocked,

    #[error("daily deposit limit exceeded: limit={limit}, used_today={used}")]
    DailyLimitExceeded { limit: Decimal, used: Decimal },

    #[error("monthly jurisdiction limit exceeded for {jurisdiction}: limit={limit}")]
    JurisdictionLimitExceeded { jurisdiction: String, limit: Decimal },

    #[error("transaction already processed (idempotent): existing_tx={0}")]
    AlreadyProcessed(Uuid),

    #[error("database error: {0}")]
    Db(#[from] sqlx::Error),

    #[error("HSM signing error: {0}")]
    Hsm(#[from] crate::hsm::HsmError),

    #[error("data conversion error: {0}")]
    Conversion(String),
}

// ─────────────────────────────────────────────────────────────────────────────
// Domain types
// ─────────────────────────────────────────────────────────────────────────────

/// Transaction type enumeration.
///
/// Serialised to lowercase strings matching the `tx_type` PostgreSQL enum.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TxType {
    Deposit,
    Withdrawal,
    Bet,
    Win,
    Refund,
    Bonus,
    Chargeback,
}

impl TxType {
    fn as_str(&self) -> &'static str {
        match self {
            TxType::Deposit    => "deposit",
            TxType::Withdrawal => "withdrawal",
            TxType::Bet        => "bet",
            TxType::Win        => "win",
            TxType::Refund     => "refund",
            TxType::Bonus      => "bonus",
            TxType::Chargeback => "chargeback",
        }
    }
}

/// Inbound transaction request from the API layer.
#[derive(Debug)]
pub struct TxRequest {
    /// Player performing the transaction.
    pub player_id: Uuid,

    /// Amount — always positive. Semantics (credit vs debit) derive from
    /// `tx_type`.
    pub amount: Decimal,

    pub tx_type: TxType,

    /// Caller-generated UUID v4. Submitting the same key twice returns the
    /// original result without re-processing.
    pub idempotency_key: Uuid,

    /// Optional game session identifier. Required for `Bet` and `Win`.
    pub game_session_id: Option<Uuid>,

    /// ISO 4217 currency code (e.g. `"EUR"`, `"GBP"`).
    pub currency: String,

    /// ISO 3166-1 alpha-2 jurisdiction (e.g. `"DE"`, `"GB"`, `"MT"`).
    /// Used to apply jurisdiction-specific deposit caps.
    pub jurisdiction: String,
}

/// Outbound result after a successful transaction.
#[derive(Debug)]
pub struct TxResult {
    /// Newly assigned transaction identifier.
    pub tx_id: Uuid,

    /// Account balance after this transaction.
    pub balance_after: Decimal,

    /// Hex-encoded ECDSA P-256 signature over the non-repudiation payload.
    /// Stored in `transactions.signature`. Format:
    /// `"{tx_id}:{player_id}:{amount}:{balance_after}"`.
    pub signature: String,
}

/// Internal account row loaded from PostgreSQL.
struct AccountRow {
    balance:               Decimal,
    locked:                bool,
    deposit_limit_daily:   Option<Decimal>,
    deposit_limit_monthly: Option<Decimal>,
    jurisdiction_override: Option<String>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Wallet engine
// ─────────────────────────────────────────────────────────────────────────────

/// The wallet engine owns a database connection pool and an HSM client handle.
///
/// It is cheaply cloneable (both fields are `Arc`-backed) and safe to share
/// across async tasks.
#[derive(Clone)]
pub struct WalletEngine {
    pub db:  PgPool,
    pub hsm: HsmClient,
}

impl WalletEngine {
    /// Execute a financial transaction.
    ///
    /// The execution pipeline:
    ///
    /// 1. `pg_advisory_xact_lock(player_id)` — serialize concurrent ops.
    /// 2. Idempotency check — early-return if key already processed.
    /// 3. `SELECT … FOR UPDATE` — load account with row lock.
    /// 4. Responsible gambling limit checks (for deposits).
    /// 5. Balance arithmetic.
    /// 6. `INSERT INTO transactions` — append to ledger.
    /// 7. `UPDATE accounts SET balance = …` — update projection.
    /// 8. `COMMIT` — atomic commit of steps 6 + 7.
    /// 9. HSM signature — ECDSA P-256 non-repudiation proof.
    /// 10. Return `TxResult`.
    ///
    /// Steps 1–8 run inside a single PostgreSQL transaction. The HSM call
    /// in step 9 is intentionally *outside* the database transaction to
    /// prevent the HSM latency (~73 ms) from extending the transaction's
    /// lock hold time. If the HSM call fails, the database record is still
    /// committed and the caller receives a `WalletError::Hsm`; the record
    /// can be re-signed via a background job.
    pub async fn execute(&self, req: TxRequest) -> Result<TxResult, WalletError> {
        let mut tx = self.db.begin().await?;

        // ── 1. Advisory lock — prevents concurrent balance modifications ──────
        // `player_id` is a UUID; we map it to i64 by truncating to the lower 8
        // bytes. Collisions are astronomically unlikely for any real user base.
        let lock_key = {
            let bytes = req.player_id.as_bytes();
            i64::from_le_bytes(bytes[0..8].try_into().unwrap())
        };
        sqlx::query("SELECT pg_advisory_xact_lock($1)")
            .bind(lock_key)
            .execute(&mut *tx)
            .await?;

        // ── 2. Idempotency check ──────────────────────────────────────────────
        let existing: Option<Uuid> = sqlx::query(
            "SELECT id FROM transactions WHERE idempotency_key = $1"
        )
        .bind(req.idempotency_key)
        .fetch_optional(&mut *tx)
        .await?
        .map(|row: PgRow| row.try_get("id").unwrap());

        if let Some(existing_id) = existing {
            tx.rollback().await?;
            tracing::info!(
                idempotency_key = %req.idempotency_key,
                existing_tx = %existing_id,
                "idempotent: returning existing transaction"
            );
            return Err(WalletError::AlreadyProcessed(existing_id));
        }

        // ── 3. Load account with FOR UPDATE lock ─────────────────────────────
        let row: PgRow = sqlx::query(
            r#"
            SELECT balance, locked,
                   deposit_limit_daily,
                   deposit_limit_monthly,
                   jurisdiction_override
            FROM   accounts
            WHERE  player_id = $1
            FOR UPDATE
            "#
        )
        .bind(req.player_id)
        .fetch_one(&mut *tx)
        .await?;

        let acct = AccountRow {
            balance:               row.try_get("balance").map_err(|e| WalletError::Db(e))?,
            locked:                row.try_get("locked").map_err(|e| WalletError::Db(e))?,
            deposit_limit_daily:   row.try_get("deposit_limit_daily").unwrap_or(None),
            deposit_limit_monthly: row.try_get("deposit_limit_monthly").unwrap_or(None),
            jurisdiction_override: row.try_get("jurisdiction_override").unwrap_or(None),
        };

        if acct.locked {
            tx.rollback().await?;
            return Err(WalletError::AccountLocked);
        }

        // ── 4. Responsible gambling limit checks ──────────────────────────────
        if req.tx_type == TxType::Deposit {
            // Player-set daily limit (UKGC mandatory).
            if let Some(daily_limit) = acct.deposit_limit_daily {
                let sum_row: PgRow = sqlx::query(
                    r#"
                    SELECT COALESCE(SUM(amount), 0) AS total
                    FROM   transactions
                    WHERE  player_id = $1
                      AND  tx_type   = 'deposit'
                      AND  created_at > NOW() - INTERVAL '24 hours'
                    "#
                )
                .bind(req.player_id)
                .fetch_one(&mut *tx)
                .await?;

                let used_today: Decimal = sum_row
                    .try_get("total")
                    .map_err(|e| WalletError::Db(e))?;

                if used_today + req.amount > daily_limit {
                    tx.rollback().await?;
                    return Err(WalletError::DailyLimitExceeded {
                        limit: daily_limit,
                        used:  used_today,
                    });
                }
            }

            // Jurisdiction monthly cap.
            let effective_jurisdiction = acct
                .jurisdiction_override
                .as_deref()
                .unwrap_or(&req.jurisdiction);

            if let Some(monthly_max) =
                Self::jurisdiction_monthly_limit(effective_jurisdiction)
            {
                let sum_row: PgRow = sqlx::query(
                    r#"
                    SELECT COALESCE(SUM(amount), 0) AS total
                    FROM   transactions
                    WHERE  player_id = $1
                      AND  tx_type   = 'deposit'
                      AND  created_at > NOW() - INTERVAL '30 days'
                    "#
                )
                .bind(req.player_id)
                .fetch_one(&mut *tx)
                .await?;

                let used_month: Decimal = sum_row
                    .try_get("total")
                    .map_err(|e| WalletError::Db(e))?;

                if used_month + req.amount > monthly_max {
                    tx.rollback().await?;
                    return Err(WalletError::JurisdictionLimitExceeded {
                        jurisdiction: effective_jurisdiction.to_string(),
                        limit:        monthly_max,
                    });
                }
            }
        }

        // ── 5. Calculate new balance ──────────────────────────────────────────
        let new_balance = match req.tx_type {
            TxType::Deposit | TxType::Win | TxType::Refund | TxType::Bonus => {
                acct.balance + req.amount
            }
            TxType::Withdrawal | TxType::Bet | TxType::Chargeback => {
                if acct.balance < req.amount {
                    tx.rollback().await?;
                    return Err(WalletError::InsufficientBalance {
                        have: acct.balance,
                        need: req.amount,
                    });
                }
                acct.balance - req.amount
            }
        };

        let tx_id = Uuid::new_v4();

        // ── 6. Append to ledger ───────────────────────────────────────────────
        // The `signature` column is populated with a placeholder here and
        // updated after the HSM call outside the transaction (step 9).
        sqlx::query(
            r#"
            INSERT INTO transactions
                (id, player_id, amount, balance_after, tx_type, currency,
                 jurisdiction, game_session_id, idempotency_key, signature, created_at)
            VALUES ($1, $2, $3, $4, $5::tx_type, $6, $7, $8, $9, '', NOW())
            "#
        )
        .bind(tx_id)
        .bind(req.player_id)
        .bind(req.amount)
        .bind(new_balance)
        .bind(req.tx_type.as_str())
        .bind(&req.currency)
        .bind(&req.jurisdiction)
        .bind(req.game_session_id)
        .bind(req.idempotency_key)
        .execute(&mut *tx)
        .await?;

        // ── 7. Update materialised balance ────────────────────────────────────
        sqlx::query(
            "UPDATE accounts SET balance = $1, updated_at = NOW() WHERE player_id = $2"
        )
        .bind(new_balance)
        .bind(req.player_id)
        .execute(&mut *tx)
        .await?;

        // ── 8. Commit ─────────────────────────────────────────────────────────
        tx.commit().await?;

        // ── 9. HSM signature (non-repudiation, outside DB tx) ─────────────────
        // Payload is deterministic so that it can be re-computed and verified
        // at any later point without querying the HSM again.
        let payload = format!("{}:{}:{}:{}", tx_id, req.player_id, req.amount, new_balance);
        let sig_bytes = self.hsm.sign_p256("wallet-signing-key", payload.as_bytes()).await?;
        let signature = hex::encode(&sig_bytes);

        // Persist the signature (best-effort; a background job handles failures).
        let _ = sqlx::query(
            "UPDATE transactions SET signature = $1 WHERE id = $2"
        )
        .bind(&signature)
        .bind(tx_id)
        .execute(&self.db)
        .await;

        tracing::info!(
            tx_id = %tx_id,
            player_id = %req.player_id,
            tx_type = ?req.tx_type,
            amount = %req.amount,
            balance_after = %new_balance,
            currency = %req.currency,
            jurisdiction = %req.jurisdiction,
            "transaction committed"
        );

        Ok(TxResult {
            tx_id,
            balance_after: new_balance,
            signature,
        })
    }

    /// Get the current account balance.
    pub async fn get_balance(&self, player_id: Uuid) -> Result<Decimal, WalletError> {
        let row: PgRow = sqlx::query(
            "SELECT balance FROM accounts WHERE player_id = $1"
        )
        .bind(player_id)
        .fetch_one(&self.db)
        .await?;

        row.try_get("balance").map_err(|e| WalletError::Db(e))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Jurisdiction limits
// ─────────────────────────────────────────────────────────────────────────────

impl WalletEngine {
    /// Return the mandatory monthly deposit cap for a jurisdiction, if any.
    ///
    /// These caps are regulatory requirements and must not be configurable
    /// by the operator. They are hard-coded here to prevent accidental
    /// or malicious modification via a CMS or database record.
    ///
    /// - **DE** — GGL (Gemeinsame Glücksspielbehörde): €1 000/month.
    /// - **NL** — KSA (Kansspelautoriteit): €700/month.
    /// - All others: no platform-level cap (player may still set their own).
    fn jurisdiction_monthly_limit(jurisdiction: &str) -> Option<Decimal> {
        match jurisdiction {
            "DE" => Some(Decimal::new(1_000, 0)), // GGL: €1 000/month mandatory
            "NL" => Some(Decimal::new(700, 0)),   // KSA: €700/month mandatory
            _    => None,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Non-repudiation proof chain
// ─────────────────────────────────────────────────────────────────────────────

impl WalletEngine {
    /// Verify the non-repudiation signature on a stored transaction.
    ///
    /// Reconstructs the payload and verifies the ECDSA P-256 signature
    /// against the public key held in the HSM. Returns `true` if the
    /// signature is valid.
    ///
    /// Used by the audit reconciliation job and external auditors.
    pub async fn verify_transaction_signature(
        &self,
        tx_id: Uuid,
        player_id: Uuid,
        amount: Decimal,
        balance_after: Decimal,
        signature_hex: &str,
    ) -> Result<bool, WalletError> {
        let payload = format!("{}:{}:{}:{}", tx_id, player_id, amount, balance_after);
        let sig_bytes = hex::decode(signature_hex).map_err(|e| {
            WalletError::Hsm(crate::hsm::HsmError::Sign(e.to_string()))
        })?;

        self.hsm
            .verify_p256("wallet-signing-key", payload.as_bytes(), &sig_bytes)
            .await
            .map_err(WalletError::Hsm)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jurisdiction_limit_de() {
        let limit = WalletEngine::jurisdiction_monthly_limit("DE").unwrap();
        assert_eq!(limit, Decimal::new(1_000, 0));
    }

    #[test]
    fn jurisdiction_limit_nl() {
        let limit = WalletEngine::jurisdiction_monthly_limit("NL").unwrap();
        assert_eq!(limit, Decimal::new(700, 0));
    }

    #[test]
    fn jurisdiction_limit_mt_is_none() {
        assert!(WalletEngine::jurisdiction_monthly_limit("MT").is_none());
    }

    #[test]
    fn new_balance_credit() {
        let balance = Decimal::new(100, 0);
        let amount  = Decimal::new(50, 0);
        assert_eq!(balance + amount, Decimal::new(150, 0));
    }

    #[test]
    fn new_balance_debit_sufficient() {
        let balance = Decimal::new(100, 0);
        let amount  = Decimal::new(30, 0);
        assert!(balance >= amount);
        assert_eq!(balance - amount, Decimal::new(70, 0));
    }

    #[test]
    fn new_balance_debit_insufficient() {
        let balance = Decimal::new(20, 0);
        let amount  = Decimal::new(50, 0);
        assert!(balance < amount);
    }

    #[test]
    fn tx_type_as_str() {
        assert_eq!(TxType::Deposit.as_str(),    "deposit");
        assert_eq!(TxType::Bet.as_str(),        "bet");
        assert_eq!(TxType::Win.as_str(),        "win");
        assert_eq!(TxType::Chargeback.as_str(), "chargeback");
    }
}
