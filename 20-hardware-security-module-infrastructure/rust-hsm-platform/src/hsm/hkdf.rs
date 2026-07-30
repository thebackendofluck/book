// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # HKDF Key Derivation Hierarchy
//!
//! One call to the YubiHSM 2 TRNG at startup produces 64 bytes of hardware
//! entropy. HKDF-SHA256 (RFC 5869) derives six cryptographically independent
//! sub-keys from that single IKM (input key material).
//!
//! ## Design rationale
//!
//! **Why HKDF instead of multiple HSM calls?**
//! Each YubiHSM 2 ECDSA/AES call takes 10–73 ms. At startup this is
//! acceptable; on the hot path it is not. HKDF gives us unlimited independent
//! sub-keys from one hardware call with negligible CPU overhead.
//!
//! **Why separate `info` strings per sub-key?**
//! The HKDF `info` parameter ensures that `wallet_hmac` and `field_cipher`
//! are statistically independent even if an attacker learns the PRK. A
//! compromise of the wallet HMAC key cannot be used to derive the session
//! signer or the audit MAC key. This is "key separation by design".
//!
//! **Why 64 bytes of IKM instead of 32?**
//! HKDF-SHA256 PRK is at most 32 bytes of security. Providing 64 bytes of
//! hardware entropy gives a comfortable margin against any weakness in the
//! HKDF-Extract step and satisfies the "≥ security strength of the output
//! key" requirement in NIST SP 800-108 Section 4.
//!
//! ## Compliance references
//! - PCI DSS Req. 3.5.1 — key material zeroized when no longer needed.
//! - NIST SP 800-108 — KDF in counter mode (HKDF equivalent).
//! - NIST SP 800-90B — hardware entropy source (YubiHSM 2 TRNG).
//! - GDPR Art. 32 — AES-256-GCM field cipher for PII data.
//! - GLI-19 Section 5 — separate RNG mixer key for game session seeding.

use hkdf::Hkdf;
use sha2::Sha256;
use zeroize::ZeroizeOnDrop;

use crate::hsm::{HsmClient, HsmError};

// ─────────────────────────────────────────────────────────────────────────────
// Key hierarchy
// ─────────────────────────────────────────────────────────────────────────────

/// Six cryptographically independent sub-keys derived from one HSM TRNG call.
///
/// All fields implement `ZeroizeOnDrop`: when this struct is dropped (e.g. on
/// epoch rotation) the memory is overwritten with zeros before deallocation,
/// satisfying PCI DSS Req. 3.5.1.
///
/// The struct is intentionally opaque to callers — fields are `pub` within the
/// crate but the struct itself is not `Clone` or `Copy` to prevent accidental
/// duplication of key material.
#[derive(ZeroizeOnDrop)]
pub struct KeyHierarchy {
    /// HMAC-SHA256 key for authenticating wallet transaction records.
    ///
    /// Used by the wallet engine to produce per-transaction MACs. Compromise
    /// of this key allows forging transaction MACs but cannot be used to
    /// decrypt PII or forge session JWTs (key separation).
    pub wallet_hmac: [u8; 32],

    /// AES-256-GCM key for column-level PII encryption in PostgreSQL.
    ///
    /// Applied to fields like `email`, `full_name`, `address`. GDPR Art. 32
    /// requires pseudonymisation/encryption of personal data. Key rotation
    /// uses `field_cipher_v2` and a re-encryption migration job.
    pub field_cipher: [u8; 32],

    /// Seed material for deriving Ed25519 session signing keys.
    ///
    /// Not used directly for signing — instead it is passed to
    /// [`crate::hsm::session::SessionSigningKey::generate`] as additional
    /// mixing material. The actual per-session key is ephemeral.
    pub session_signer: [u8; 32],

    /// HMAC-SHA256 key for authenticating audit chain entries.
    ///
    /// Each audit entry receives an HMAC computed in software (~1 µs) rather
    /// than an HSM ECDSA signature (~73 ms). The HSM signs only a batch
    /// checkpoint hash every 1 000 entries, amortising cost by 1 000×.
    pub audit_chain: [u8; 32],

    /// Mixing key XOR-ed into every RNG seed (GLI-19 defence in depth).
    ///
    /// Triple mixing: `hw_seed ⊕ epoch_rng_mixer ⊕ SHA256(game_id‖player_id)`.
    /// Even if the seed pool is compromised, the epoch key and game context
    /// add independent entropy that the attacker cannot predict.
    pub rng_mixer: [u8; 32],

    /// AES-256-GCM key v2 for rolling PII re-encryption during key rotation.
    ///
    /// During 90-day key rotation, new records use `field_cipher_v2` while a
    /// background job re-encrypts old rows. Both keys are live simultaneously
    /// for the duration of the migration window.
    pub field_cipher_v2: [u8; 32],
}

// ─────────────────────────────────────────────────────────────────────────────
// Derivation
// ─────────────────────────────────────────────────────────────────────────────

impl KeyHierarchy {
    /// Derive the full key hierarchy from 64 bytes of YubiHSM 2 TRNG entropy.
    ///
    /// This function makes **exactly one** call to the HSM. All six sub-keys
    /// are derived in pure software via HKDF-Expand, which is a deterministic
    /// PRF that does not require additional entropy.
    ///
    /// ## Startup sequence
    /// 1. `HsmClient::random_bytes(64)` — 64 bytes TRNG (~5 ms)
    /// 2. `Hkdf::<Sha256>::new(None, &ikm)` — HKDF-Extract (PRK)
    /// 3. Six `hk.expand(info, out)` calls — HKDF-Expand (~µs total)
    /// 4. `master_entropy` dropped → memory zeroed by `zeroize`
    ///
    /// After this function returns, the 64-byte IKM is overwritten on the
    /// stack (compiler optimisation barriers respected by `zeroize`).
    pub async fn derive_from_hsm(hsm: &HsmClient) -> Result<Self, HsmError> {
        use zeroize::Zeroizing;

        // ── Step 1: Acquire hardware entropy ─────────────────────────────────
        // `Zeroizing<Vec<u8>>` ensures the raw entropy is zeroed on drop even
        // if the function returns an error midway through derivation.
        let master_entropy: Zeroizing<Vec<u8>> =
            Zeroizing::new(hsm.random_bytes(64).await?);

        // ── Step 2: HKDF-Extract ─────────────────────────────────────────────
        // No salt → HKDF uses HMAC(0x00...00, IKM) as salt, producing a PRK
        // with full entropy. Adding an application-specific salt is optional
        // when the IKM is already from a hardware TRNG (RFC 5869 §3.1).
        let hk = Hkdf::<Sha256>::new(None, &master_entropy);

        let mut hierarchy = KeyHierarchy {
            wallet_hmac:    [0u8; 32],
            field_cipher:   [0u8; 32],
            session_signer: [0u8; 32],
            audit_chain:    [0u8; 32],
            rng_mixer:      [0u8; 32],
            field_cipher_v2:[0u8; 32],
        };

        // ── Step 3: HKDF-Expand — one independent key per `info` string ──────
        //
        // The `info` strings are domain-separation labels. Versioning (`:v1`,
        // `:v2`) allows key rotation: bump the version and re-derive without
        // invalidating keys derived with the old version.
        hk.expand(b"igaming:wallet-hmac:v1",    &mut hierarchy.wallet_hmac)
            .map_err(|_| HsmError::Init("HKDF expand wallet_hmac".into()))?;

        hk.expand(b"igaming:field-cipher:v1",   &mut hierarchy.field_cipher)
            .map_err(|_| HsmError::Init("HKDF expand field_cipher".into()))?;

        hk.expand(b"igaming:session-signer:v1", &mut hierarchy.session_signer)
            .map_err(|_| HsmError::Init("HKDF expand session_signer".into()))?;

        hk.expand(b"igaming:audit-chain:v1",    &mut hierarchy.audit_chain)
            .map_err(|_| HsmError::Init("HKDF expand audit_chain".into()))?;

        hk.expand(b"igaming:rng-mixer:v1",      &mut hierarchy.rng_mixer)
            .map_err(|_| HsmError::Init("HKDF expand rng_mixer".into()))?;

        hk.expand(b"igaming:field-cipher:v2",   &mut hierarchy.field_cipher_v2)
            .map_err(|_| HsmError::Init("HKDF expand field_cipher_v2".into()))?;

        // `master_entropy` drops here → Zeroizing<Vec<u8>> zeroes the bytes.

        tracing::info!(
            entropy_bytes = 64usize,
            keys_derived  = 6usize,
            algorithm     = "HKDF-SHA256 (RFC 5869)",
            hsm_calls     = 1usize,
            "key hierarchy initialised from YubiHSM 2 TRNG"
        );

        Ok(hierarchy)
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use hkdf::Hkdf;
    use sha2::Sha256;

    /// Verify that six distinct `info` strings produce six distinct 32-byte keys.
    ///
    /// This test does NOT require hardware: it drives the HKDF logic directly.
    /// In production the IKM would come from the HSM TRNG; here we use a fixed
    /// test vector. The test confirms that key separation is working.
    #[test]
    fn keys_are_independent() {
        let ikm = [0xABu8; 64];
        let hk = Hkdf::<Sha256>::new(None, &ikm);

        let mut wallet_hmac    = [0u8; 32];
        let mut field_cipher   = [0u8; 32];
        let mut session_signer = [0u8; 32];
        let mut audit_chain    = [0u8; 32];
        let mut rng_mixer      = [0u8; 32];
        let mut field_cipher2  = [0u8; 32];

        hk.expand(b"igaming:wallet-hmac:v1",    &mut wallet_hmac).unwrap();
        hk.expand(b"igaming:field-cipher:v1",   &mut field_cipher).unwrap();
        hk.expand(b"igaming:session-signer:v1", &mut session_signer).unwrap();
        hk.expand(b"igaming:audit-chain:v1",    &mut audit_chain).unwrap();
        hk.expand(b"igaming:rng-mixer:v1",      &mut rng_mixer).unwrap();
        hk.expand(b"igaming:field-cipher:v2",   &mut field_cipher2).unwrap();

        // All six keys must be pairwise distinct.
        let keys = [
            wallet_hmac, field_cipher, session_signer,
            audit_chain, rng_mixer, field_cipher2,
        ];
        for i in 0..keys.len() {
            for j in (i + 1)..keys.len() {
                assert_ne!(
                    keys[i], keys[j],
                    "keys[{}] == keys[{}] — HKDF info separation broken",
                    i, j
                );
            }
        }
    }

    /// Verify determinism: same IKM always produces same keys.
    #[test]
    fn derivation_is_deterministic() {
        let ikm = [0xCDu8; 64];
        let hk = Hkdf::<Sha256>::new(None, &ikm);

        let mut k1 = [0u8; 32];
        let mut k2 = [0u8; 32];
        hk.expand(b"igaming:wallet-hmac:v1", &mut k1).unwrap();
        hk.expand(b"igaming:wallet-hmac:v1", &mut k2).unwrap();

        assert_eq!(k1, k2, "HKDF must be deterministic for same IKM+info");
    }

    /// Verify v1 and v2 of the same purpose produce different keys.
    /// This is essential for the rolling re-encryption key rotation scheme.
    #[test]
    fn version_bump_produces_different_key() {
        let ikm = [0xEFu8; 64];
        let hk = Hkdf::<Sha256>::new(None, &ikm);

        let mut v1 = [0u8; 32];
        let mut v2 = [0u8; 32];
        hk.expand(b"igaming:field-cipher:v1", &mut v1).unwrap();
        hk.expand(b"igaming:field-cipher:v2", &mut v2).unwrap();

        assert_ne!(v1, v2, "Version bump must produce a different key");
    }
}
