// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! Deterministic counter-based nonce generators.
//!
//! Nonce reuse with the same key breaks AEAD confidentiality and integrity.
//! Random nonces are fine at moderate volume (birthday bound ~2^48 messages
//! for a 96-bit nonce), but high-throughput services should prefer a counter
//! to guarantee uniqueness.
//!
//! # Example
//!
//! ```
//! use crypto_engine::NonceGenerator;
//! let mut gen = NonceGenerator::new(16, 42);  // prefix = producer id
//! let n1 = gen.next();
//! let n2 = gen.next();
//! assert_ne!(n1, n2);
//! assert_eq!(n1.len(), 16);
//! ```

use std::sync::atomic::{AtomicU64, Ordering};

/// Counter-based nonce generator.
///
/// Layout: `[8-byte BE prefix | 8-byte BE counter]` padded/truncated
/// to the required nonce length.
///
/// The `prefix` should be unique per producer (pod, worker, kafka partition).
/// The counter must never wrap — at 2^64 invocations this is a 584-year
/// budget at 1 billion nonces/sec, so in practice always safe.
pub struct NonceGenerator {
    nonce_len: usize,
    prefix: u64,
    counter: AtomicU64,
}

impl NonceGenerator {
    /// Build a new generator for a target nonce length (12, 16, or 32).
    pub fn new(nonce_len: usize, prefix: u64) -> Self {
        Self {
            nonce_len,
            prefix,
            counter: AtomicU64::new(0),
        }
    }

    /// Start the counter at a specific value. Useful for resuming a
    /// producer after restart from a persisted sequence number.
    pub fn with_start(nonce_len: usize, prefix: u64, start: u64) -> Self {
        Self {
            nonce_len,
            prefix,
            counter: AtomicU64::new(start),
        }
    }

    /// Produce the next unique nonce.
    pub fn next(&self) -> Vec<u8> {
        let c = self.counter.fetch_add(1, Ordering::Relaxed);
        let mut nonce = vec![0u8; self.nonce_len];
        let prefix_bytes = self.prefix.to_be_bytes();
        let counter_bytes = c.to_be_bytes();
        // Place prefix in first 8 bytes (truncated if shorter) and counter
        // in the next 8 bytes.
        if self.nonce_len >= 16 {
            nonce[..8].copy_from_slice(&prefix_bytes);
            nonce[8..16].copy_from_slice(&counter_bytes);
        } else if self.nonce_len >= 12 {
            // 12-byte nonce: 4-byte prefix lower || 8-byte counter
            nonce[..4].copy_from_slice(&prefix_bytes[4..]);
            nonce[4..12].copy_from_slice(&counter_bytes);
        } else {
            // shouldn't happen with supported algorithms
            nonce.copy_from_slice(&counter_bytes[..self.nonce_len.min(8)]);
        }
        nonce
    }

    /// Current counter value (for persistence / recovery).
    pub fn current(&self) -> u64 {
        self.counter.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counter_increments() {
        let g = NonceGenerator::new(16, 42);
        let a = g.next();
        let b = g.next();
        assert_ne!(a, b);
        assert_eq!(a.len(), 16);
    }

    #[test]
    fn recovered_start_skips_used() {
        let g = NonceGenerator::with_start(16, 1, 1000);
        let n = g.next();
        assert_eq!(&n[8..16], &1000u64.to_be_bytes());
    }

    #[test]
    fn supports_12_byte_nonce() {
        let g = NonceGenerator::new(12, 0xFFFFFFFF);
        let n = g.next();
        assert_eq!(n.len(), 12);
    }

    #[test]
    fn supports_32_byte_nonce() {
        let g = NonceGenerator::new(32, 7);
        let n = g.next();
        assert_eq!(n.len(), 32);
        // bytes 16..32 stay zero (reserved for future extension)
        assert_eq!(&n[16..], &[0u8; 16]);
    }
}
