// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! Key material wrappers that zeroize on drop.

use zeroize::{Zeroize, ZeroizeOnDrop};

/// Marker trait for secret keys that clear themselves on drop.
pub trait Key: ZeroizeOnDrop {
    /// Return the raw key bytes.
    fn as_slice(&self) -> &[u8];
}

/// A 16-byte symmetric key (AEGIS-128L).
#[derive(Clone, Zeroize, ZeroizeOnDrop)]
pub struct Key16([u8; 16]);

impl Key16 {
    /// Build from a 16-byte slice. Copies the bytes.
    pub fn from_slice(b: &[u8; 16]) -> Self {
        Self(*b)
    }
    /// Borrow the raw bytes.
    pub fn as_bytes(&self) -> &[u8; 16] {
        &self.0
    }
}

impl Key for Key16 {
    fn as_slice(&self) -> &[u8] {
        &self.0
    }
}

/// A 32-byte symmetric key (AEGIS-256, AES-256-GCM, ChaCha20-Poly1305).
#[derive(Clone, Zeroize, ZeroizeOnDrop)]
pub struct Key32([u8; 32]);

impl Key32 {
    /// Build from a 32-byte slice. Copies the bytes.
    pub fn from_slice(b: &[u8; 32]) -> Self {
        Self(*b)
    }
    /// Borrow the raw bytes.
    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

impl Key for Key32 {
    fn as_slice(&self) -> &[u8] {
        &self.0
    }
}

impl std::fmt::Debug for Key16 {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Key16(***redacted***)")
    }
}

impl std::fmt::Debug for Key32 {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Key32(***redacted***)")
    }
}
