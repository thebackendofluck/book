// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # crypto-engine
//!
//! High-throughput AEAD crypto library for iGaming workloads.
//!
//! Supports:
//! - **AEGIS-128L** (fastest, ~12 GB/s on AES-NI hardware) — default
//! - **AEGIS-256** (larger key, 256-bit security margin)
//! - **AES-256-GCM** (NIST-blessed, pgcrypto interop)
//! - **ChaCha20-Poly1305** (non-AES-NI fallback)
//!
//! ## Wire format
//!
//! ```text
//! [1 byte: alg_id | N bytes: nonce | ciphertext | 16 bytes: tag]
//! ```
//!
//! Nonce length depends on algorithm:
//! - AEGIS-128L:       16 bytes
//! - AEGIS-256:        32 bytes
//! - AES-256-GCM:      12 bytes
//! - ChaCha20-Poly1305: 12 bytes
//!
//! ## Example
//!
//! ```
//! use crypto_engine::{CryptoEngine, Key};
//!
//! let key_bytes = [0u8; 16];
//! let engine = CryptoEngine::new_aegis128l(&key_bytes).unwrap();
//! let payload = engine.encrypt(b"sensitive data", b"context").unwrap();
//! let plain = engine.decrypt(&payload, b"context").unwrap();
//! assert_eq!(plain, b"sensitive data");
//! ```

// The rest of the crate forbids unsafe code. The `ffi` module is the only
// exception and explicitly opts back in with `#[allow(unsafe_code)]`.
#![deny(unsafe_code)]
#![warn(missing_docs)]

pub mod error;
pub mod key;
pub mod nonce;

#[allow(unsafe_code)]
pub mod ffi;

pub use error::CryptoError;
pub use key::{Key, Key16, Key32};
pub use nonce::NonceGenerator;

use aegis::aegis128l::Aegis128L;
use aegis::aegis256::Aegis256;
use aes_gcm::aead::Aead as _;
use aes_gcm::{Aes256Gcm, KeyInit, Nonce as GcmNonce};
use chacha20poly1305::ChaCha20Poly1305;
use rand::RngCore;

/// Algorithm identifier written as the first byte of every payload.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum AlgId {
    /// AEGIS-128L with 16-byte key, 16-byte nonce, 16-byte tag
    Aegis128L = 0x01,
    /// AEGIS-256 with 32-byte key, 32-byte nonce, 16-byte tag
    Aegis256 = 0x02,
    /// AES-256-GCM with 32-byte key, 12-byte nonce, 16-byte tag
    Aes256Gcm = 0x03,
    /// ChaCha20-Poly1305 with 32-byte key, 12-byte nonce, 16-byte tag
    ChaCha20Poly1305 = 0x04,
}

impl AlgId {
    /// Nonce length in bytes for this algorithm.
    pub const fn nonce_len(self) -> usize {
        match self {
            AlgId::Aegis128L => 16,
            AlgId::Aegis256 => 32,
            AlgId::Aes256Gcm => 12,
            AlgId::ChaCha20Poly1305 => 12,
        }
    }

    /// Authentication tag length in bytes (all algorithms use 16).
    pub const fn tag_len(self) -> usize {
        16
    }

    /// Parse from wire byte.
    pub fn from_byte(b: u8) -> Result<Self, CryptoError> {
        match b {
            0x01 => Ok(AlgId::Aegis128L),
            0x02 => Ok(AlgId::Aegis256),
            0x03 => Ok(AlgId::Aes256Gcm),
            0x04 => Ok(AlgId::ChaCha20Poly1305),
            _ => Err(CryptoError::UnknownAlgorithm(b)),
        }
    }
}

/// Detect whether AES-NI hardware acceleration is available.
///
/// On x86_64 this checks the CPU feature flag. On other architectures,
/// returns `true` if ARMv8 AES instructions are likely (Apple Silicon, etc.)
/// and `false` otherwise.
pub fn has_aes_hardware() -> bool {
    #[cfg(target_arch = "x86_64")]
    {
        is_x86_feature_detected!("aes")
    }
    #[cfg(target_arch = "aarch64")]
    {
        // ARMv8 AES extensions are ubiquitous on modern arm64 (Apple Silicon,
        // Graviton, Cortex-A7x). std::arch feature detection on stable is
        // limited, so we assume yes — incorrect detection only affects the
        // auto-select default, not correctness.
        true
    }
    #[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
    {
        false
    }
}

/// Pick the fastest algorithm for this CPU.
pub fn recommended_algorithm() -> AlgId {
    if has_aes_hardware() {
        AlgId::Aegis128L
    } else {
        AlgId::ChaCha20Poly1305
    }
}

/// Unified crypto engine. Holds a key and an algorithm selection.
pub enum CryptoEngine {
    /// AEGIS-128L variant.
    Aegis128L(Key16),
    /// AEGIS-256 variant.
    Aegis256(Key32),
    /// AES-256-GCM variant.
    Aes256Gcm(Key32),
    /// ChaCha20-Poly1305 variant.
    ChaCha20Poly1305(Key32),
}

impl CryptoEngine {
    /// Build an AEGIS-128L engine (recommended default on AES-NI hosts).
    pub fn new_aegis128l(key: &[u8; 16]) -> Result<Self, CryptoError> {
        Ok(CryptoEngine::Aegis128L(Key16::from_slice(key)))
    }

    /// Build an AEGIS-256 engine (larger key, stronger safety margin).
    pub fn new_aegis256(key: &[u8; 32]) -> Result<Self, CryptoError> {
        Ok(CryptoEngine::Aegis256(Key32::from_slice(key)))
    }

    /// Build an AES-256-GCM engine (compatibility with pgcrypto / NIST).
    pub fn new_aes256gcm(key: &[u8; 32]) -> Result<Self, CryptoError> {
        Ok(CryptoEngine::Aes256Gcm(Key32::from_slice(key)))
    }

    /// Build a ChaCha20-Poly1305 engine (software fallback).
    pub fn new_chacha20poly1305(key: &[u8; 32]) -> Result<Self, CryptoError> {
        Ok(CryptoEngine::ChaCha20Poly1305(Key32::from_slice(key)))
    }

    /// Auto-select the fastest algorithm given the host CPU features.
    /// Caller supplies a 32-byte key (the first 16 bytes are used for
    /// AEGIS-128L if that algorithm is picked).
    pub fn new_auto(key: &[u8; 32]) -> Result<Self, CryptoError> {
        match recommended_algorithm() {
            AlgId::Aegis128L => {
                let mut k16 = [0u8; 16];
                k16.copy_from_slice(&key[..16]);
                Self::new_aegis128l(&k16)
            }
            AlgId::ChaCha20Poly1305 => Self::new_chacha20poly1305(key),
            _ => Self::new_aegis128l(&{
                let mut k16 = [0u8; 16];
                k16.copy_from_slice(&key[..16]);
                k16
            }),
        }
    }

    /// Return this engine's algorithm id.
    pub fn algorithm(&self) -> AlgId {
        match self {
            CryptoEngine::Aegis128L(_) => AlgId::Aegis128L,
            CryptoEngine::Aegis256(_) => AlgId::Aegis256,
            CryptoEngine::Aes256Gcm(_) => AlgId::Aes256Gcm,
            CryptoEngine::ChaCha20Poly1305(_) => AlgId::ChaCha20Poly1305,
        }
    }

    /// Encrypt `plaintext` with `aad` using a freshly generated random nonce.
    ///
    /// Returns a self-describing wire payload:
    /// `[alg_id | nonce | ciphertext | tag]`.
    pub fn encrypt(&self, plaintext: &[u8], aad: &[u8]) -> Result<Vec<u8>, CryptoError> {
        let alg = self.algorithm();
        let nonce_len = alg.nonce_len();
        let mut nonce = vec![0u8; nonce_len];
        rand::thread_rng().fill_bytes(&mut nonce);
        self.encrypt_with_nonce(plaintext, aad, &nonce)
    }

    /// Encrypt with a caller-supplied nonce. **Never reuse a nonce with the
    /// same key** — this breaks confidentiality and integrity for every
    /// cipher supported here. Intended for deterministic tests, interop,
    /// and counter-based nonce generation (see [`NonceGenerator`]).
    pub fn encrypt_with_nonce(
        &self,
        plaintext: &[u8],
        aad: &[u8],
        nonce: &[u8],
    ) -> Result<Vec<u8>, CryptoError> {
        let alg = self.algorithm();
        if nonce.len() != alg.nonce_len() {
            return Err(CryptoError::InvalidNonceLength {
                expected: alg.nonce_len(),
                actual: nonce.len(),
            });
        }

        let (ct, tag): (Vec<u8>, [u8; 16]) = match self {
            CryptoEngine::Aegis128L(k) => {
                let mut n16 = [0u8; 16];
                n16.copy_from_slice(nonce);
                let (c, t) = Aegis128L::<16>::new(k.as_bytes(), &n16).encrypt(plaintext, aad);
                (c, t)
            }
            CryptoEngine::Aegis256(k) => {
                let mut n32 = [0u8; 32];
                n32.copy_from_slice(nonce);
                let (c, t) = Aegis256::<16>::new(k.as_bytes(), &n32).encrypt(plaintext, aad);
                (c, t)
            }
            CryptoEngine::Aes256Gcm(k) => {
                let cipher = Aes256Gcm::new(k.as_bytes().into());
                let n = GcmNonce::from_slice(nonce);
                let payload = aes_gcm::aead::Payload {
                    msg: plaintext,
                    aad,
                };
                let mut combined = cipher
                    .encrypt(n, payload)
                    .map_err(|_| CryptoError::EncryptionFailed)?;
                let tag_start = combined.len() - 16;
                let mut tag = [0u8; 16];
                tag.copy_from_slice(&combined[tag_start..]);
                combined.truncate(tag_start);
                (combined, tag)
            }
            CryptoEngine::ChaCha20Poly1305(k) => {
                use chacha20poly1305::aead::Aead as _;
                let cipher = ChaCha20Poly1305::new(k.as_bytes().into());
                let n = chacha20poly1305::Nonce::from_slice(nonce);
                let payload = chacha20poly1305::aead::Payload {
                    msg: plaintext,
                    aad,
                };
                let mut combined = cipher
                    .encrypt(n, payload)
                    .map_err(|_| CryptoError::EncryptionFailed)?;
                let tag_start = combined.len() - 16;
                let mut tag = [0u8; 16];
                tag.copy_from_slice(&combined[tag_start..]);
                combined.truncate(tag_start);
                (combined, tag)
            }
        };

        // Assemble wire payload: alg | nonce | ct | tag
        let mut out = Vec::with_capacity(1 + nonce.len() + ct.len() + 16);
        out.push(alg as u8);
        out.extend_from_slice(nonce);
        out.extend_from_slice(&ct);
        out.extend_from_slice(&tag);
        Ok(out)
    }

    /// Decrypt a wire payload produced by [`encrypt`] or
    /// [`encrypt_with_nonce`].
    pub fn decrypt(&self, payload: &[u8], aad: &[u8]) -> Result<Vec<u8>, CryptoError> {
        if payload.is_empty() {
            return Err(CryptoError::PayloadTooShort);
        }
        let alg_byte = payload[0];
        let alg = AlgId::from_byte(alg_byte)?;

        if alg != self.algorithm() {
            return Err(CryptoError::AlgorithmMismatch {
                payload_alg: alg_byte,
                engine_alg: self.algorithm() as u8,
            });
        }

        let nonce_len = alg.nonce_len();
        let tag_len = alg.tag_len();
        if payload.len() < 1 + nonce_len + tag_len {
            return Err(CryptoError::PayloadTooShort);
        }

        let nonce = &payload[1..1 + nonce_len];
        let ct_end = payload.len() - tag_len;
        let ct = &payload[1 + nonce_len..ct_end];
        let tag = &payload[ct_end..];

        let mut tag16 = [0u8; 16];
        tag16.copy_from_slice(tag);

        match self {
            CryptoEngine::Aegis128L(k) => {
                let mut n16 = [0u8; 16];
                n16.copy_from_slice(nonce);
                Aegis128L::<16>::new(k.as_bytes(), &n16)
                    .decrypt(ct, &tag16, aad)
                    .map_err(|_| CryptoError::AuthenticationFailed)
            }
            CryptoEngine::Aegis256(k) => {
                let mut n32 = [0u8; 32];
                n32.copy_from_slice(nonce);
                Aegis256::<16>::new(k.as_bytes(), &n32)
                    .decrypt(ct, &tag16, aad)
                    .map_err(|_| CryptoError::AuthenticationFailed)
            }
            CryptoEngine::Aes256Gcm(k) => {
                let cipher = Aes256Gcm::new(k.as_bytes().into());
                let n = GcmNonce::from_slice(nonce);
                let mut combined = Vec::with_capacity(ct.len() + 16);
                combined.extend_from_slice(ct);
                combined.extend_from_slice(&tag16);
                let payload_obj = aes_gcm::aead::Payload {
                    msg: &combined,
                    aad,
                };
                cipher
                    .decrypt(n, payload_obj)
                    .map_err(|_| CryptoError::AuthenticationFailed)
            }
            CryptoEngine::ChaCha20Poly1305(k) => {
                use chacha20poly1305::aead::Aead as _;
                let cipher = ChaCha20Poly1305::new(k.as_bytes().into());
                let n = chacha20poly1305::Nonce::from_slice(nonce);
                let mut combined = Vec::with_capacity(ct.len() + 16);
                combined.extend_from_slice(ct);
                combined.extend_from_slice(&tag16);
                let payload_obj = chacha20poly1305::aead::Payload {
                    msg: &combined,
                    aad,
                };
                cipher
                    .decrypt(n, payload_obj)
                    .map_err(|_| CryptoError::AuthenticationFailed)
            }
        }
    }
}

/// Generate a random 16-byte key suitable for AEGIS-128L.
pub fn random_key_16() -> [u8; 16] {
    let mut k = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut k);
    k
}

/// Generate a random 32-byte key suitable for AEGIS-256, AES-256-GCM,
/// and ChaCha20-Poly1305.
pub fn random_key_32() -> [u8; 32] {
    let mut k = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut k);
    k
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_aegis128l() {
        let k = [7u8; 16];
        let e = CryptoEngine::new_aegis128l(&k).unwrap();
        let pt = b"ladies and gentlemen of the class of 99";
        let aad = b"context-tag-v1";
        let payload = e.encrypt(pt, aad).unwrap();
        let out = e.decrypt(&payload, aad).unwrap();
        assert_eq!(out, pt);
        assert_eq!(payload[0], AlgId::Aegis128L as u8);
    }

    #[test]
    fn roundtrip_all_algorithms() {
        let pt = b"high-throughput game event payload";
        let aad = b"user=42;event=bet";

        let e = CryptoEngine::new_aegis128l(&[1u8; 16]).unwrap();
        assert_eq!(e.decrypt(&e.encrypt(pt, aad).unwrap(), aad).unwrap(), pt);

        let e = CryptoEngine::new_aegis256(&[2u8; 32]).unwrap();
        assert_eq!(e.decrypt(&e.encrypt(pt, aad).unwrap(), aad).unwrap(), pt);

        let e = CryptoEngine::new_aes256gcm(&[3u8; 32]).unwrap();
        assert_eq!(e.decrypt(&e.encrypt(pt, aad).unwrap(), aad).unwrap(), pt);

        let e = CryptoEngine::new_chacha20poly1305(&[4u8; 32]).unwrap();
        assert_eq!(e.decrypt(&e.encrypt(pt, aad).unwrap(), aad).unwrap(), pt);
    }

    #[test]
    fn aad_validation_rejects_mismatch() {
        let e = CryptoEngine::new_aegis128l(&[9u8; 16]).unwrap();
        let payload = e.encrypt(b"balance:5000", b"ctx=A").unwrap();
        let res = e.decrypt(&payload, b"ctx=B");
        assert!(matches!(res, Err(CryptoError::AuthenticationFailed)));
    }

    #[test]
    fn tampered_tag_rejected() {
        let e = CryptoEngine::new_aegis128l(&[9u8; 16]).unwrap();
        let mut payload = e.encrypt(b"secret", b"aad").unwrap();
        let last = payload.len() - 1;
        payload[last] ^= 0xFF;
        let res = e.decrypt(&payload, b"aad");
        assert!(matches!(res, Err(CryptoError::AuthenticationFailed)));
    }

    #[test]
    fn tampered_ciphertext_rejected() {
        let e = CryptoEngine::new_aegis128l(&[9u8; 16]).unwrap();
        let mut payload = e.encrypt(b"a longer secret message", b"aad").unwrap();
        // Flip a byte in ciphertext region (after 1 + 16 nonce, before last 16 tag)
        payload[20] ^= 0x01;
        let res = e.decrypt(&payload, b"aad");
        assert!(matches!(res, Err(CryptoError::AuthenticationFailed)));
    }

    #[test]
    fn wrong_algorithm_engine_rejects_payload() {
        let e1 = CryptoEngine::new_aegis128l(&[1u8; 16]).unwrap();
        let e2 = CryptoEngine::new_aes256gcm(&[1u8; 32]).unwrap();
        let payload = e1.encrypt(b"hello", b"").unwrap();
        let res = e2.decrypt(&payload, b"");
        assert!(matches!(res, Err(CryptoError::AlgorithmMismatch { .. })));
    }

    #[test]
    fn deterministic_nonce_reproducible() {
        let e = CryptoEngine::new_aegis128l(&[5u8; 16]).unwrap();
        let nonce = [0u8; 16];
        let p1 = e.encrypt_with_nonce(b"msg", b"aad", &nonce).unwrap();
        let p2 = e.encrypt_with_nonce(b"msg", b"aad", &nonce).unwrap();
        assert_eq!(p1, p2, "same nonce + same key must give same ciphertext");
    }

    #[test]
    fn key_zeroize_on_drop() {
        // Construct, drop. We can't inspect memory after drop safely, but
        // we can confirm Zeroize is implemented and drop doesn't panic.
        let k = Key16::from_slice(&[0xAAu8; 16]);
        drop(k);
        let k = Key32::from_slice(&[0xBBu8; 32]);
        drop(k);
    }

    #[test]
    fn alg_id_roundtrip() {
        for b in [0x01u8, 0x02, 0x03, 0x04] {
            let a = AlgId::from_byte(b).unwrap();
            assert_eq!(a as u8, b);
        }
        assert!(AlgId::from_byte(0xFF).is_err());
    }
}
