// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! Error types for the crypto engine.

use thiserror::Error;

/// Errors returned by the crypto engine.
#[derive(Debug, Error)]
pub enum CryptoError {
    /// Authentication tag did not verify. The payload was tampered with,
    /// the AAD differs from encryption time, or the wrong key was used.
    #[error("authentication failed — tag mismatch")]
    AuthenticationFailed,

    /// Wire payload was shorter than the minimum header.
    #[error("payload too short to contain header, nonce, and tag")]
    PayloadTooShort,

    /// First byte of payload was not a recognised algorithm id.
    #[error("unknown algorithm id: 0x{0:02X}")]
    UnknownAlgorithm(u8),

    /// Payload's declared algorithm doesn't match the engine.
    #[error("algorithm mismatch: payload=0x{payload_alg:02X}, engine=0x{engine_alg:02X}")]
    AlgorithmMismatch {
        /// Algorithm id in the payload header
        payload_alg: u8,
        /// Algorithm id the engine was configured for
        engine_alg: u8,
    },

    /// A caller-supplied nonce had the wrong length.
    #[error("invalid nonce length: expected {expected}, got {actual}")]
    InvalidNonceLength {
        /// Expected length in bytes
        expected: usize,
        /// Actual length in bytes
        actual: usize,
    },

    /// Underlying cipher refused to encrypt.
    #[error("encryption failed")]
    EncryptionFailed,
}
