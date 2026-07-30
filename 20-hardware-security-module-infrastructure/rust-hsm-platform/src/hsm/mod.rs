// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # HSM Module — YubiHSM 2 PKCS#11 Wrapper
//!
//! This module provides a thread-safe, async-compatible wrapper around the
//! YubiHSM 2 hardware security module via the PKCS#11 interface (crate `cryptoki`).
//!
//! ## Compliance rationale
//!
//! - **PCI DSS Req. 3.6 / 3.7**: All cryptographic key operations (generate,
//!   wrap, unwrap, sign, verify, encrypt) must use a FIPS-validated key
//!   management system. The YubiHSM 2 is certified FIPS 140-2 Level 3, which
//!   satisfies tamper-evidence and tamper-response requirements.
//!
//! - **FIPS 140-2 Level 3**: Key material never leaves the hardware in
//!   plaintext. The wrap / unwrap API allows key backup only under an AES-256
//!   wrapping key that is itself non-extractable from the HSM.
//!
//! - **GLI-19 Section 5.2**: The TRNG source (`random_bytes`) must be a
//!   hardware entropy source, not a software PRNG. The YubiHSM 2's internal
//!   TRNG satisfies this requirement.
//!
//! ## Architecture
//!
//! The [`HsmClient`] wraps an `Arc<Mutex<HsmInner>>` so that multiple async
//! tasks can share a single connector session without races. A session is
//! opened fresh per operation to avoid PKCS#11 session state leakage between
//! unrelated callers (defence in depth).
//!
//! For high-throughput workloads the seed pool ([`crate::hsm::seed_pool`])
//! amortises HSM latency by pre-fetching entropy in batch during idle periods.

use std::sync::Arc;

use cryptoki::{
    context::{CInitializeArgs, Pkcs11},
    mechanism::Mechanism,
    object::Attribute,
    session::UserType,
    types::AuthPin,
};
use thiserror::Error;
use tokio::sync::Mutex;

pub mod audit;
pub mod epoch;
pub mod hkdf;
pub mod seed_pool;
pub mod session;

// ─────────────────────────────────────────────────────────────────────────────
// Error type
// ─────────────────────────────────────────────────────────────────────────────

/// Errors surfaced by HSM operations.
///
/// Each variant carries enough context for the audit trail without exposing
/// key material or internal PKCS#11 error codes to callers.
#[derive(Debug, Error)]
pub enum HsmError {
    #[error("PKCS#11 initialisation failed: {0}")]
    Init(String),

    #[error("no token found at slot index {0}")]
    SlotNotFound(u64),

    #[error("HSM session error: {0}")]
    Session(String),

    #[error("key not found: label={label}")]
    KeyNotFound { label: String },

    #[error("random byte generation failed: {0}")]
    Random(String),

    #[error("sign operation failed: {0}")]
    Sign(String),

    #[error("encrypt operation failed: {0}")]
    Encrypt(String),

    #[error("verify operation failed: {0}")]
    Verify(String),

    #[error("seed length mismatch: expected {expected}, got {got}")]
    SeedLength { expected: usize, got: usize },
}

// ─────────────────────────────────────────────────────────────────────────────
// Core types
// ─────────────────────────────────────────────────────────────────────────────

/// Internal state shared across all clones of [`HsmClient`].
///
/// The `Mutex` serialises PKCS#11 calls because the `cryptoki` session object
/// is not `Send + Sync`. Each operation opens and closes its own session so
/// that a crash inside one task cannot corrupt another task's session state.
struct HsmInner {
    pkcs11: Pkcs11,
    slot:   cryptoki::slot::Slot,
    pin:    AuthPin,
}

/// Thread-safe, cloneable handle to the YubiHSM 2.
///
/// Cloning is cheap — all clones share the same underlying `Arc<Mutex<…>>`.
/// Use this handle everywhere in the service tree; a single physical HSM
/// is accessed through one logical client.
#[derive(Clone)]
pub struct HsmClient {
    inner: Arc<Mutex<HsmInner>>,
}

// ─────────────────────────────────────────────────────────────────────────────
// Constructor
// ─────────────────────────────────────────────────────────────────────────────

impl HsmClient {
    /// Connect to the YubiHSM 2 via PKCS#11.
    ///
    /// # Parameters
    /// - `lib`      — Path to the PKCS#11 shared library
    ///                (`/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so`).
    /// - `slot_idx` — Zero-based slot index reported by `pkcs11-tool --list-slots`.
    /// - `pin`      — Authentication PIN (YubiHSM password), format `0001<password>`.
    ///
    /// # Compliance
    /// `CInitializeArgs::OsThreads` delegates mutex management to the operating
    /// system, satisfying the PKCS#11 multi-thread safety requirement for
    /// concurrent callers.
    pub fn new(lib: &str, slot_idx: u64, pin: &str) -> Result<Self, HsmError> {
        let pkcs11 = Pkcs11::new(lib).map_err(|e| HsmError::Init(e.to_string()))?;

        pkcs11
            .initialize(CInitializeArgs::OsThreads)
            .map_err(|e| HsmError::Init(e.to_string()))?;

        let slots = pkcs11
            .get_slots_with_token()
            .map_err(|e| HsmError::Init(e.to_string()))?;

        let slot = slots
            .get(slot_idx as usize)
            .copied()
            .ok_or(HsmError::SlotNotFound(slot_idx))?;

        tracing::info!(
            lib,
            slot_idx,
            "YubiHSM 2 PKCS#11 session established"
        );

        Ok(Self {
            inner: Arc::new(Mutex::new(HsmInner {
                pkcs11,
                slot,
                pin: AuthPin::new(pin.into()),
            })),
        })
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Key operations
// ─────────────────────────────────────────────────────────────────────────────

impl HsmClient {
    /// Generate `n` bytes of hardware true random from the YubiHSM 2 TRNG.
    ///
    /// This is the **only** entropy source used to seed the RNG (GLI-19) and
    /// derive the key hierarchy (HKDF). Software PRNGs are never used as a
    /// primary entropy source.
    ///
    /// Typical latency: ~1 ms for 32 bytes; ~5 ms for 1 KB.
    pub async fn random_bytes(&self, n: usize) -> Result<Vec<u8>, HsmError> {
        let inner = self.inner.lock().await;

        // Read-only session is sufficient for C_GenerateRandom.
        let session = inner
            .pkcs11
            .open_ro_session(inner.slot)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        session
            .login(UserType::User, Some(&inner.pin))
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let mut buf = vec![0u8; n];
        session
            .generate_random_slice(&mut buf)
            .map_err(|e| HsmError::Random(e.to_string()))?;
        Ok(buf)
    }

    /// Sign `data` with ECDSA P-256 using the named key inside the HSM.
    ///
    /// The private key never leaves the hardware boundary. The DER-encoded
    /// signature is returned to the caller.
    ///
    /// ## Compliance
    /// Non-repudiation for financial transactions (PCI DSS Req. 10,
    /// MGA Tech Standard). Typical latency: 73 ms (YubiHSM 2 specification).
    /// For bulk use-cases, see [`crate::hsm::audit`] which amortises one
    /// ECDSA call across 1 000 records.
    ///
    /// # Parameters
    /// - `key_label` — PKCS#11 `CKA_LABEL` of the private key object.
    /// - `data`      — Payload to sign. Will be hashed internally by the HSM
    ///                 (mechanism: `CKM_ECDSA_SHA256`).
    pub async fn sign_p256(&self, key_label: &str, data: &[u8]) -> Result<Vec<u8>, HsmError> {
        let inner = self.inner.lock().await;

        let session = inner
            .pkcs11
            .open_rw_session(inner.slot)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        session
            .login(UserType::User, Some(&inner.pin))
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let template = vec![
            Attribute::Label(key_label.as_bytes().to_vec()),
            Attribute::Class(cryptoki::object::ObjectClass::PRIVATE_KEY),
        ];

        let objects = session
            .find_objects(&template)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let key = objects.first().ok_or_else(|| HsmError::KeyNotFound {
            label: key_label.to_string(),
        })?;

        session
            .sign(&Mechanism::EcdsaSha256, *key, data)
            .map_err(|e| HsmError::Sign(e.to_string()))
    }

    /// Verify an ECDSA P-256 signature against the named public key in the HSM.
    ///
    /// Returns `true` if the signature is valid. Using the HSM for verification
    /// ensures the public key is the authoritative one stored in hardware,
    /// preventing a confused-deputy attack where a caller substitutes a
    /// different public key.
    pub async fn verify_p256(
        &self,
        key_label: &str,
        data: &[u8],
        signature: &[u8],
    ) -> Result<bool, HsmError> {
        let inner = self.inner.lock().await;

        let session = inner
            .pkcs11
            .open_ro_session(inner.slot)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        session
            .login(UserType::User, Some(&inner.pin))
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let template = vec![
            Attribute::Label(key_label.as_bytes().to_vec()),
            Attribute::Class(cryptoki::object::ObjectClass::PUBLIC_KEY),
        ];

        let objects = session
            .find_objects(&template)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let key = objects.first().ok_or_else(|| HsmError::KeyNotFound {
            label: key_label.to_string(),
        })?;

        match session.verify(&Mechanism::EcdsaSha256, *key, data, signature) {
            Ok(()) => Ok(true),
            Err(cryptoki::error::Error::Pkcs11(
                cryptoki::error::RvError::SignatureInvalid,
                _,
            )) => Ok(false),
            Err(e) => Err(HsmError::Verify(e.to_string())),
        }
    }

    /// AES-256-GCM encrypt `plaintext` using the named symmetric key in the HSM.
    ///
    /// The IV (12 bytes) is generated by the HSM's TRNG. The returned buffer
    /// is `IV || ciphertext || authentication-tag` (tag length 128 bits).
    ///
    /// ## Compliance
    /// Column-level PII encryption for GDPR Article 32 and PCI DSS Req. 3.4.
    /// IV randomness is hardware-guaranteed; no counter or nonce reuse is
    /// possible without HSM tampering, which FIPS 140-2 Level 3 detects and
    /// responds to with key erasure.
    pub async fn aes_gcm_encrypt(
        &self,
        key_label: &str,
        plaintext: &[u8],
    ) -> Result<Vec<u8>, HsmError> {
        let inner = self.inner.lock().await;

        let session = inner
            .pkcs11
            .open_rw_session(inner.slot)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        session
            .login(UserType::User, Some(&inner.pin))
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let template = vec![
            Attribute::Label(key_label.as_bytes().to_vec()),
            Attribute::Class(cryptoki::object::ObjectClass::SECRET_KEY),
        ];

        let objects = session
            .find_objects(&template)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let key = objects.first().ok_or_else(|| HsmError::KeyNotFound {
            label: key_label.to_string(),
        })?;

        // Hardware-generated IV: no risk of nonce reuse.
        let mut iv = vec![0u8; 12];
        session
            .generate_random_slice(&mut iv)
            .map_err(|e| HsmError::Random(e.to_string()))?;

        let aad: &[u8] = &[];
        let params = cryptoki::mechanism::aead::GcmParams::new(&mut iv, aad, 128.into())
            .map_err(|e| HsmError::Encrypt(e.to_string()))?;
        let ciphertext = session
            .encrypt(&Mechanism::AesGcm(params), *key, plaintext)
            .map_err(|e| HsmError::Encrypt(e.to_string()))?;

        // Prepend IV so decryption can recover it without a separate channel.
        let mut out = iv;
        out.extend_from_slice(&ciphertext);
        Ok(out)
    }

    /// Wrap (export encrypted) a key object for backup purposes.
    ///
    /// The wrapping key (`wrap_key_label`) must be an AES-256 key with the
    /// `CKA_WRAP` attribute set. The wrapped blob can be stored offline and
    /// re-imported with [`Self::unwrap_key`] on a replacement HSM.
    ///
    /// ## Compliance
    /// PCI DSS Req. 3.7.1 mandates a documented key-custodian procedure for
    /// key backup. The wrap/unwrap API satisfies the technical control; the
    /// procedural control (dual-custodian ceremony) is documented separately.
    pub async fn wrap_key(
        &self,
        wrap_key_label: &str,
        target_key_label: &str,
    ) -> Result<Vec<u8>, HsmError> {
        let inner = self.inner.lock().await;

        let session = inner
            .pkcs11
            .open_rw_session(inner.slot)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        session
            .login(UserType::User, Some(&inner.pin))
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let wrap_tmpl = vec![
            Attribute::Label(wrap_key_label.as_bytes().to_vec()),
            Attribute::Class(cryptoki::object::ObjectClass::SECRET_KEY),
        ];
        let target_tmpl = vec![
            Attribute::Label(target_key_label.as_bytes().to_vec()),
        ];

        let wrap_objects = session
            .find_objects(&wrap_tmpl)
            .map_err(|e| HsmError::Session(e.to_string()))?;
        let target_objects = session
            .find_objects(&target_tmpl)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let wrapping_key = wrap_objects.first().ok_or_else(|| HsmError::KeyNotFound {
            label: wrap_key_label.to_string(),
        })?;
        let target_key = target_objects
            .first()
            .ok_or_else(|| HsmError::KeyNotFound {
                label: target_key_label.to_string(),
            })?;

        session
            .wrap_key(&Mechanism::AesKeyWrap, *wrapping_key, *target_key)
            .map_err(|e| HsmError::Encrypt(e.to_string()))
    }

    /// Unwrap (import) a previously wrapped key blob into the HSM.
    pub async fn unwrap_key(
        &self,
        wrap_key_label: &str,
        wrapped_blob: &[u8],
        new_label: &str,
    ) -> Result<(), HsmError> {
        use cryptoki::object::KeyType;

        let inner = self.inner.lock().await;

        let session = inner
            .pkcs11
            .open_rw_session(inner.slot)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        session
            .login(UserType::User, Some(&inner.pin))
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let wrap_tmpl = vec![
            Attribute::Label(wrap_key_label.as_bytes().to_vec()),
            Attribute::Class(cryptoki::object::ObjectClass::SECRET_KEY),
        ];
        let wrap_objects = session
            .find_objects(&wrap_tmpl)
            .map_err(|e| HsmError::Session(e.to_string()))?;

        let wrapping_key = wrap_objects.first().ok_or_else(|| HsmError::KeyNotFound {
            label: wrap_key_label.to_string(),
        })?;

        let new_key_template = vec![
            Attribute::Label(new_label.as_bytes().to_vec()),
            Attribute::Class(cryptoki::object::ObjectClass::SECRET_KEY),
            Attribute::KeyType(KeyType::AES),
            Attribute::Token(true),
            Attribute::Private(true),
            Attribute::Sensitive(true),
            Attribute::Extractable(false),
        ];

        session
            .unwrap_key(
                &Mechanism::AesKeyWrap,
                *wrapping_key,
                wrapped_blob,
                &new_key_template,
            )
            .map_err(|e| HsmError::Encrypt(e.to_string()))?;

        tracing::info!(new_label, "key unwrapped into HSM");
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Unit tests (run against a mock/stub without real hardware)
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    /// Verifies that `HsmError` variants produce human-readable messages.
    /// Smoke test — does not require hardware.
    #[test]
    fn error_display() {
        let e = super::HsmError::KeyNotFound {
            label: "wallet-signing-key".into(),
        };
        assert!(e.to_string().contains("wallet-signing-key"));
    }

    #[test]
    fn slot_not_found_message() {
        let e = super::HsmError::SlotNotFound(99);
        assert!(e.to_string().contains("99"));
    }
}
