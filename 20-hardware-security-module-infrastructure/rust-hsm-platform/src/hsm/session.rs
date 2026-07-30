// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # Session Key Management
//!
//! Ephemeral Ed25519 session keys are generated with hardware entropy, then
//! *endorsed* (signed with ECDSA P-256) by the YubiHSM 2 before entering
//! service. After endorsement, all JWT signing is done in software at ~50 µs/op
//! with zero further HSM calls.
//!
//! ## Why ephemeral keys endorsed by the HSM rather than direct HSM signing?
//!
//! Direct HSM signing (ECDSA P-256) costs 73 ms. At 10 000 logins/second this
//! would require ~730 YubiHSM 2 units. Instead:
//!
//! 1. One HSM call generates 32 bytes of TRNG for the Ed25519 seed.
//! 2. One HSM call endorses the public key (proves hardware origin).
//! 3. All subsequent JWT operations use software Ed25519 (~50 µs each).
//! 4. Verifiers check the endorsement once (at key rotation) to establish
//!    trust, then verify JWTs using the Ed25519 public key.
//!
//! The endorsement binds the session key to a specific epoch, game instance,
//! and timestamp, preventing reuse across epochs.
//!
//! ## Key lifecycle
//!
//! ```text
//! generate() → [active] → sign_jwt() (many) → [expired] → drop()
//!                              ↑                               ↓
//!                        ZeroizeOnDrop              memory zeroed
//! ```
//!
//! Keys are rotated hourly by [`SessionKeyManager`]. The previous key is
//! retained until its TTL expires so that JWTs issued just before rotation
//! remain valid.
//!
//! ## Compliance references
//! - PCI DSS Req. 8.3 — strong authentication; session tokens must be
//!   generated with a FIPS-validated random source.
//! - GLI-19 Section 7 — session integrity; each game session must be bound
//!   to a unique cryptographic identifier.

use std::sync::Arc;

use chrono::{DateTime, Duration, Utc};
use ed25519_dalek::{Signer, SigningKey};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::sync::RwLock;
use zeroize::ZeroizeOnDrop;

use crate::hsm::{HsmClient, HsmError};

// ─────────────────────────────────────────────────────────────────────────────
// Error type
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum SessionKeyError {
    #[error("HSM error: {0}")]
    Hsm(#[from] HsmError),

    #[error("session key expired at {expired_at}")]
    Expired { expired_at: DateTime<Utc> },

    #[error("JWT serialisation error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("key seed length mismatch")]
    SeedLength,
}

// ─────────────────────────────────────────────────────────────────────────────
// JWT claims
// ─────────────────────────────────────────────────────────────────────────────

/// Standard + custom claims for iGaming JWTs.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Claims {
    /// Subject: player UUID.
    pub sub: String,
    /// Issuer: service identifier.
    pub iss: String,
    /// Issued-at (Unix timestamp seconds).
    pub iat: i64,
    /// Expiry (Unix timestamp seconds).
    pub exp: i64,
    /// JWT ID — unique per token, used for revocation check.
    pub jti: String,
    /// Player jurisdiction (ISO 3166-1 alpha-2, e.g. `"DE"`, `"GB"`).
    pub jurisdiction: String,
    /// Current epoch ID. Verifiers use this to select the correct endorsement.
    pub epoch_id: u32,
}

// ─────────────────────────────────────────────────────────────────────────────
// Session signing key
// ─────────────────────────────────────────────────────────────────────────────

/// An ephemeral Ed25519 signing key with HSM endorsement.
///
/// `ZeroizeOnDrop` ensures the private key bytes are overwritten when this
/// struct is dropped. The `#[zeroize(skip)]` annotations on non-sensitive
/// fields prevent unnecessary zeroing of public data.
#[derive(ZeroizeOnDrop)]
pub struct SessionSigningKey {
    /// Ed25519 private key. Zeroed on drop.
    signing_key: SigningKey,

    /// Hex-encoded ECDSA P-256 signature by the HSM over the public key.
    /// Proves that the session key was generated with HSM TRNG entropy.
    #[zeroize(skip)]
    pub endorsement: String,

    /// Base64-encoded Ed25519 public key. Safe to publish in JWKS.
    #[zeroize(skip)]
    pub pub_key_b64: String,

    /// Unique identifier for this key. Used in JWT `kid` header for JWKS lookup.
    #[zeroize(skip)]
    pub key_id: String,

    /// Absolute expiry time. `sign_jwt` rejects calls after this point.
    #[zeroize(skip)]
    pub expires_at: DateTime<Utc>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Generation
// ─────────────────────────────────────────────────────────────────────────────

impl SessionSigningKey {
    /// Generate a new session key.
    ///
    /// Makes **two** HSM calls:
    /// 1. `random_bytes(32)` — TRNG seed for Ed25519 key generation.
    /// 2. `sign_p256(...)` — ECDSA endorsement of the public key.
    ///
    /// All subsequent JWT signing operations use software Ed25519 with zero
    /// further HSM calls.
    pub async fn generate(
        hsm: &HsmClient,
        epoch_id: u32,
        ttl_hours: i64,
    ) -> Result<Self, SessionKeyError> {
        // ── Step 1: Hardware entropy for key generation ───────────────────────
        // 32 bytes from the YubiHSM 2 TRNG — guaranteed hardware randomness.
        // PCI DSS Req. 8.3 requires FIPS-validated entropy for session keys.
        let seed_bytes = hsm.random_bytes(32).await?;
        let seed: [u8; 32] = seed_bytes
            .try_into()
            .map_err(|_| SessionKeyError::SeedLength)?;

        let signing_key = SigningKey::from_bytes(&seed);
        let verifying_key = signing_key.verifying_key();
        let pub_key_bytes = verifying_key.to_bytes();
        use base64::Engine as _;
        let pub_key_b64 = base64::engine::general_purpose::STANDARD.encode(&pub_key_bytes);
        let key_id = uuid::Uuid::new_v4().to_string();
        let now = Utc::now();
        let expires_at = now + Duration::hours(ttl_hours);

        // ── Step 2: HSM endorsement ───────────────────────────────────────────
        // The endorsement payload is deterministic and includes the epoch ID
        // so that a key generated in epoch N cannot be reused in epoch N+1.
        // Format: "session-key:v1:{pub_key_b64}:{epoch_id}:{unix_ts}:{key_id}"
        let endorsement_payload = format!(
            "session-key:v1:{}:{}:{}:{}",
            pub_key_b64,
            epoch_id,
            now.timestamp(),
            key_id,
        );

        let sig = hsm
            .sign_p256("session-endorsement-key", endorsement_payload.as_bytes())
            .await?;

        tracing::info!(
            key_id = %key_id,
            epoch_id,
            expires_at = %expires_at,
            "session key generated — 2 HSM calls (seed + endorsement)"
        );

        Ok(Self {
            signing_key,
            endorsement: hex::encode(&sig),
            pub_key_b64,
            key_id,
            expires_at,
        })
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// JWT signing
// ─────────────────────────────────────────────────────────────────────────────

impl SessionSigningKey {
    /// Sign a JWT with this session key.
    ///
    /// This is pure software Ed25519 — **zero HSM calls**. Expected latency:
    /// ~50 µs including serialisation.
    ///
    /// The JWT header embeds `kid` (key ID) so that verifiers can fetch the
    /// corresponding public key from the JWKS endpoint without iterating all
    /// active keys.
    pub fn sign_jwt(&self, claims: &Claims) -> Result<String, SessionKeyError> {
        if Utc::now() > self.expires_at {
            return Err(SessionKeyError::Expired {
                expired_at: self.expires_at,
            });
        }

        use base64::engine::general_purpose::URL_SAFE_NO_PAD;
        use base64::Engine as _;

        // ── Header ─────────────────────────────────────────────────────────────
        let header_json = serde_json::json!({
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": self.key_id,
        });
        let header_b64 = URL_SAFE_NO_PAD.encode(header_json.to_string().as_bytes());

        // ── Payload ────────────────────────────────────────────────────────────
        let payload_json = serde_json::to_string(claims)?;
        let payload_b64 = URL_SAFE_NO_PAD.encode(payload_json.as_bytes());

        // ── Signature ──────────────────────────────────────────────────────────
        let message = format!("{}.{}", header_b64, payload_b64);
        let sig = self.signing_key.sign(message.as_bytes());
        let sig_b64 = URL_SAFE_NO_PAD.encode(sig.to_bytes());

        Ok(format!("{}.{}", message, sig_b64))
    }

    /// Returns `true` if the key has passed its expiry.
    pub fn is_expired(&self) -> bool {
        Utc::now() > self.expires_at
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Session key manager
// ─────────────────────────────────────────────────────────────────────────────

/// Manages the current and previous session signing keys with automatic
/// hourly rotation.
///
/// The `previous` key is retained so that JWTs issued in the last hour before
/// rotation can still be verified. After the next rotation, the previous key
/// is dropped and its memory zeroed.
pub struct SessionKeyManager {
    current:  Arc<RwLock<SessionSigningKey>>,
    previous: Arc<RwLock<Option<SessionSigningKey>>>,
    hsm:      HsmClient,
    epoch_id: Arc<std::sync::atomic::AtomicU32>,
}

impl SessionKeyManager {
    /// Initialise the manager with the first session key.
    pub async fn new(
        hsm: HsmClient,
        epoch_id: u32,
    ) -> Result<Arc<Self>, SessionKeyError> {
        let initial = SessionSigningKey::generate(&hsm, epoch_id, 1).await?;

        Ok(Arc::new(Self {
            current:  Arc::new(RwLock::new(initial)),
            previous: Arc::new(RwLock::new(None)),
            hsm,
            epoch_id: Arc::new(std::sync::atomic::AtomicU32::new(epoch_id)),
        }))
    }

    /// Spawn a background task that rotates the session key every `interval_secs`.
    ///
    /// Default: 3 600 seconds (1 hour). On rotation failure the error is
    /// logged and the current key continues in service.
    pub fn start_rotation(manager: Arc<Self>, interval_secs: u64) {
        tokio::spawn(async move {
            let mut interval =
                tokio::time::interval(std::time::Duration::from_secs(interval_secs));
            interval.tick().await; // Skip immediate tick.

            loop {
                interval.tick().await;

                let epoch_id = manager
                    .epoch_id
                    .load(std::sync::atomic::Ordering::Relaxed);

                match SessionSigningKey::generate(&manager.hsm, epoch_id, 1).await {
                    Ok(new_key) => {
                        let old = std::mem::replace(
                            &mut *manager.current.write().await,
                            new_key,
                        );
                        // Retain old key for grace period (one rotation interval).
                        *manager.previous.write().await = Some(old);
                        tracing::info!("session key rotated");
                    }
                    Err(e) => {
                        tracing::error!(error = %e, "session key rotation failed");
                    }
                }
            }
        });
    }

    /// Sign a JWT with the current session key.
    pub async fn sign_jwt(&self, claims: &Claims) -> Result<String, SessionKeyError> {
        self.current.read().await.sign_jwt(claims)
    }

    /// Update the epoch ID used for new session key endorsements.
    ///
    /// Called by the epoch manager when it rotates the epoch.
    pub fn set_epoch_id(&self, new_id: u32) {
        self.epoch_id
            .store(new_id, std::sync::atomic::Ordering::Relaxed);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claims_serialise_round_trip() {
        let claims = Claims {
            sub:          "550e8400-e29b-41d4-a716-446655440000".into(),
            iss:          "igaming-platform".into(),
            iat:          1_000_000,
            exp:          1_003_600,
            jti:          "unique-jwt-id".into(),
            jurisdiction: "DE".into(),
            epoch_id:     1,
        };

        let json = serde_json::to_string(&claims).unwrap();
        let decoded: Claims = serde_json::from_str(&json).unwrap();
        assert_eq!(decoded.sub, claims.sub);
        assert_eq!(decoded.epoch_id, claims.epoch_id);
    }
}
