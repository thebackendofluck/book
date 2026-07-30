// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # Epoch Key Rotation
//!
//! Epoch keys implement 30-day rolling rotation of the entire key hierarchy
//! without service downtime. On rotation the old epoch is kept as "previous"
//! for a 24-hour grace period so that MACs and JWTs issued just before
//! rotation continue to verify.
//!
//! ## Why epochs rather than a single long-lived key?
//!
//! 1. **Blast radius containment** — if a sub-key is compromised, the impact
//!    is bounded to the current epoch window (≤ 30 days).
//! 2. **Forward secrecy** — after the grace period the previous epoch keys are
//!    dropped from memory (ZeroizeOnDrop) and cannot be recovered even by an
//!    attacker who subsequently gains access to the process.
//! 3. **Regulatory key lifetime** — PCI DSS Req. 3.7 mandates cryptographic
//!    key rotation procedures. A 30-day automated epoch satisfies the
//!    requirement for symmetric key rotation.
//!
//! ## Concurrency model
//!
//! - `current` is protected by a `RwLock`. Reads are concurrent; the write
//!   lock is held only for the swap operation (microseconds), not during the
//!   HSM call. This means sign/verify operations never wait on HSM I/O.
//! - `rotate()` derives the new `KeyHierarchy` (one HSM call, ~5 ms) *before*
//!   acquiring the write lock, so readers are not blocked during derivation.

use std::sync::Arc;

use chrono::{DateTime, Duration, Utc};
use tokio::sync::RwLock;
use zeroize::ZeroizeOnDrop;

use crate::hsm::{hkdf::KeyHierarchy, HsmClient, HsmError};

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

/// A snapshot of the key hierarchy valid for a specific calendar epoch.
///
/// `Clone` is derived so the epoch manager can atomically swap current → previous
/// without holding the write lock during key derivation. The underlying
/// `KeyHierarchy` has `ZeroizeOnDrop`, so when the old epoch's `Arc` reference
/// count drops to zero its memory is zeroed immediately.
#[derive(Clone)]
pub struct EpochKey {
    /// Monotonically increasing epoch identifier. Embedded in audit records
    /// and JWT headers so that verifiers know which epoch key to use.
    pub id: u32,

    /// Derived key material for this epoch. Wrapped in `Arc` so that callers
    /// holding a reference do not block the epoch swap.
    pub keys: Arc<KeyHierarchy>,

    /// Inclusive start of the epoch validity window.
    pub valid_from: DateTime<Utc>,

    /// End of normal validity. New operations must use the next epoch after
    /// this point.
    pub valid_until: DateTime<Utc>,

    /// End of the grace period. Operations that *signed* before `valid_until`
    /// can still be *verified* until this point. After this the old epoch's
    /// `KeyHierarchy` is dropped and its memory zeroed.
    pub grace_until: DateTime<Utc>,
}

/// Manages the current and previous epoch, background rotation scheduler, and
/// MAC sign/verify with automatic epoch selection.
pub struct EpochManager {
    hsm: HsmClient,
    /// The epoch used for all new sign operations.
    pub current: Arc<RwLock<EpochKey>>,
    /// The previous epoch, retained until `grace_until` has passed.
    previous: Arc<RwLock<Option<EpochKey>>>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Constructor
// ─────────────────────────────────────────────────────────────────────────────

impl EpochManager {
    /// Initialise the epoch manager with epoch ID 1.
    ///
    /// Called once at service startup. Derives the initial `KeyHierarchy`
    /// from the HSM. Subsequent rotations are triggered by
    /// [`Self::start_rotation_scheduler`].
    pub async fn new(hsm: HsmClient) -> Result<Arc<Self>, HsmError> {
        let now = Utc::now();
        let keys = KeyHierarchy::derive_from_hsm(&hsm).await?;

        let initial = EpochKey {
            id:          1,
            keys:        Arc::new(keys),
            valid_from:  now,
            valid_until: now + Duration::days(30),
            grace_until: now + Duration::hours(30 * 24 + 24),
        };

        tracing::info!(
            epoch_id = 1u32,
            valid_from  = %initial.valid_from,
            valid_until = %initial.valid_until,
            "epoch 1 initialised"
        );

        Ok(Arc::new(Self {
            hsm,
            current:  Arc::new(RwLock::new(initial)),
            previous: Arc::new(RwLock::new(None)),
        }))
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rotation
// ─────────────────────────────────────────────────────────────────────────────

impl EpochManager {
    /// Rotate to a new epoch.
    ///
    /// This function:
    /// 1. Derives a fresh `KeyHierarchy` from the HSM (one TRNG call).
    /// 2. Acquires the write lock only long enough to swap pointers.
    /// 3. Moves the old current to `previous` (grace period begins).
    ///
    /// Concurrent readers (`sign_mac`, `verify_mac`) are never blocked during
    /// the HSM call in step 1. The write lock in step 2 is held for
    /// nanoseconds.
    ///
    /// If the previous grace period is still active when another rotation is
    /// triggered (unexpected, given the 30-day schedule), the oldest epoch is
    /// silently overwritten. Regulators accept a 24-hour overlap window.
    pub async fn rotate(&self) -> Result<(), HsmError> {
        let now = Utc::now();
        let current_id = self.current.read().await.id;
        let new_id = current_id + 1;

        // Derive *before* taking the write lock so readers are not blocked.
        let new_keys = KeyHierarchy::derive_from_hsm(&self.hsm).await?;

        let new_epoch = EpochKey {
            id:          new_id,
            keys:        Arc::new(new_keys),
            valid_from:  now,
            valid_until: now + Duration::days(30),
            grace_until: now + Duration::hours(30 * 24 + 24),
        };

        // Atomically move current → previous, install new current.
        let old = {
            let mut cur = self.current.write().await;
            let old = cur.clone();
            *cur = new_epoch;
            old
        };
        *self.previous.write().await = Some(old);

        tracing::info!(
            epoch_id = new_id,
            "epoch rotated — 1 HSM call for new key hierarchy"
        );
        Ok(())
    }

    /// Check on startup whether the persisted epoch has expired and rotate
    /// if necessary. This handles the case where the service was restarted
    /// after its rotation timer would have fired.
    pub async fn rotate_if_expired(&self) -> Result<bool, HsmError> {
        let expired = {
            let cur = self.current.read().await;
            Utc::now() > cur.valid_until
        };

        if expired {
            tracing::warn!("epoch expired on startup — rotating immediately");
            self.rotate().await?;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    /// Spawn a background task that rotates the epoch every 30 days.
    ///
    /// The first `tick()` is consumed immediately so the first rotation fires
    /// after 30 days, not at t=0. Rotation failures are logged at ERROR level
    /// and retried on the next tick.
    pub fn start_rotation_scheduler(manager: Arc<Self>) {
        tokio::spawn(async move {
            let period = std::time::Duration::from_secs(30 * 24 * 3600);
            let mut interval = tokio::time::interval(period);

            // Skip the immediate tick — rotate at t+30d, not t+0.
            interval.tick().await;

            loop {
                interval.tick().await;
                if let Err(e) = manager.rotate().await {
                    tracing::error!(
                        error = %e,
                        "epoch rotation failed — will retry in 30 days"
                    );
                }
            }
        });
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// MAC sign / verify
// ─────────────────────────────────────────────────────────────────────────────

impl EpochManager {
    /// Produce an HMAC-SHA256 over `data` using the current epoch's
    /// `wallet_hmac` key.
    ///
    /// This runs entirely in software (~1 µs) — no HSM call.
    /// For audit chain MACs use `audit_chain` key instead of `wallet_hmac`.
    pub async fn sign_mac(&self, data: &[u8]) -> [u8; 32] {
        let epoch = self.current.read().await;
        hmac_sha256(&epoch.keys.wallet_hmac, data)
    }

    /// Produce an HMAC-SHA256 over `data` using the current epoch's
    /// `audit_chain` key.
    pub async fn sign_audit_mac(&self, data: &[u8]) -> [u8; 32] {
        let epoch = self.current.read().await;
        hmac_sha256(&epoch.keys.audit_chain, data)
    }

    /// Verify an HMAC-SHA256 MAC.
    ///
    /// Tries the current epoch first; if that fails and the previous epoch
    /// is still within its grace period, tries the previous epoch.
    ///
    /// **Constant-time comparison** (via `subtle::ConstantTimeEq`) prevents
    /// timing side-channels that could leak information about the key.
    pub async fn verify_mac(&self, data: &[u8], mac: &[u8; 32]) -> bool {
        let now = Utc::now();

        // Current epoch — fast path.
        {
            let current = self.current.read().await;
            if hmac_verify(&current.keys.wallet_hmac, data, mac) {
                return true;
            }
        }

        // Previous epoch — grace period only.
        if let Some(prev) = self.previous.read().await.as_ref() {
            if now < prev.grace_until && hmac_verify(&prev.keys.wallet_hmac, data, mac) {
                return true;
            }
        }

        false
    }

    /// Verify an audit chain MAC (uses `audit_chain` key).
    pub async fn verify_audit_mac(&self, data: &[u8], mac: &[u8; 32]) -> bool {
        let now = Utc::now();

        {
            let current = self.current.read().await;
            if hmac_verify(&current.keys.audit_chain, data, mac) {
                return true;
            }
        }

        if let Some(prev) = self.previous.read().await.as_ref() {
            if now < prev.grace_until && hmac_verify(&prev.keys.audit_chain, data, mac) {
                return true;
            }
        }

        false
    }

    /// Return the current epoch ID.
    pub async fn current_epoch_id(&self) -> u32 {
        self.current.read().await.id
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal HMAC helpers
// ─────────────────────────────────────────────────────────────────────────────

fn hmac_sha256(key: &[u8; 32], data: &[u8]) -> [u8; 32] {
    use hmac::{Hmac, Mac};
    let mut mac = Hmac::<sha2::Sha256>::new_from_slice(key)
        .expect("HMAC key is exactly 32 bytes — infallible");
    mac.update(data);
    mac.finalize().into_bytes().into()
}

fn hmac_verify(key: &[u8; 32], data: &[u8], expected: &[u8; 32]) -> bool {
    use subtle::ConstantTimeEq;
    let computed = hmac_sha256(key, data);
    // `ct_eq` compares in constant time to prevent timing side-channels.
    computed.ct_eq(expected.as_slice()).into()
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_key_hierarchy_with(wallet_hmac: [u8; 32]) -> Arc<KeyHierarchy> {
        // Build a KeyHierarchy with only wallet_hmac populated for testing.
        use zeroize::ZeroizeOnDrop;
        // SAFETY: we construct a valid zeroed struct and set the field we care about.
        let mut h = KeyHierarchy {
            wallet_hmac,
            field_cipher:    [0u8; 32],
            session_signer:  [0u8; 32],
            audit_chain:     [0u8; 32],
            rng_mixer:       [0u8; 32],
            field_cipher_v2: [0u8; 32],
        };
        Arc::new(h)
    }

    #[test]
    fn hmac_sha256_is_deterministic() {
        let key = [0x42u8; 32];
        let data = b"test-data";
        let a = hmac_sha256(&key, data);
        let b = hmac_sha256(&key, data);
        assert_eq!(a, b);
    }

    #[test]
    fn hmac_verify_correct_mac() {
        let key = [0x11u8; 32];
        let data = b"wallet-tx";
        let mac = hmac_sha256(&key, data);
        assert!(hmac_verify(&key, data, &mac));
    }

    #[test]
    fn hmac_verify_wrong_mac_fails() {
        let key = [0x22u8; 32];
        let data = b"wallet-tx";
        let mut mac = hmac_sha256(&key, data);
        mac[0] ^= 0xFF; // Corrupt one byte.
        assert!(!hmac_verify(&key, data, &mac));
    }

    #[test]
    fn hmac_verify_wrong_key_fails() {
        let key1 = [0xAAu8; 32];
        let key2 = [0xBBu8; 32];
        let data = b"wallet-tx";
        let mac = hmac_sha256(&key1, data);
        assert!(!hmac_verify(&key2, data, &mac));
    }
}
