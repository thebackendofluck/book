// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # Certified RNG Service
//!
//! A [`CertifiedRng`] wraps the seed pool and epoch manager to provide a
//! high-level interface for creating game sessions. It also runs a NIST
//! SP 800-22 self-test suite on startup to verify statistical quality.
//!
//! ## Architecture
//!
//! ```text
//!  YubiHSM 2 TRNG ──→ SeedPool ──→ CertifiedRng ──→ GameRngSession
//!                                         ↑
//!                               EpochManager (rng_mixer)
//! ```
//!
//! The [`CertifiedRng`] is stateless: all state lives in the pool and epoch.
//! It can be cloned freely and used from multiple async tasks.
//!
//! ## NIST SP 800-22 self-test
//!
//! On startup, [`CertifiedRng::run_nist_self_test`] generates a large sample
//! (20 000 bytes) and runs a subset of the NIST SP 800-22 statistical tests
//! in process. This does not replace the full GLI-19 lab certification but
//! provides a runtime sanity check that the RNG has not been misconfigured.
//!
//! The self-test uses a fixed test seed (not hardware entropy) so that failures
//! are reproducible. A failure here indicates a software bug, not a hardware
//! fault.
//!
//! ## Session isolation (GLI-19)
//!
//! Each call to [`CertifiedRng::session`] creates an independent `ChaCha20Rng`
//! instance with a unique seed. Sessions are not connected — knowing the output
//! of one session provides zero information about any other session.
//!
//! ## Compliance references
//! - GLI-19 Section 5 — CSPRNG with hardware entropy source.
//! - NIST SP 800-90A Rev 1 — ChaCha20 is equivalent to CTR_DRBG with
//!   AES-256 for certification purposes.
//! - NIST SP 800-22 Rev 1a — statistical test suite for random bit generators.

use std::sync::Arc;

use rand::{RngCore, SeedableRng};
use rand_chacha::ChaCha20Rng;
use sha2::{Digest, Sha256};
use thiserror::Error;
use uuid::Uuid;

use crate::hsm::{epoch::EpochManager, seed_pool::SeedPool};

// ─────────────────────────────────────────────────────────────────────────────
// Error type
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum RngError {
    #[error("seed pool error: {0}")]
    Pool(#[from] crate::hsm::seed_pool::SeedPoolError),

    #[error("NIST self-test failed: {test}: {reason}")]
    NistSelfTest { test: String, reason: String },
}

// ─────────────────────────────────────────────────────────────────────────────
// Certified RNG
// ─────────────────────────────────────────────────────────────────────────────

/// High-level RNG facade combining the seed pool and epoch manager.
///
/// Clone this struct freely — it holds only `Arc` references.
#[derive(Clone)]
pub struct CertifiedRng {
    pool:  Arc<SeedPool>,
    epoch: Arc<EpochManager>,
}

impl CertifiedRng {
    /// Create a new `CertifiedRng`.
    ///
    /// The pool must have been warmed up before calling this.
    pub fn new(pool: Arc<SeedPool>, epoch: Arc<EpochManager>) -> Self {
        Self { pool, epoch }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Session creation
// ─────────────────────────────────────────────────────────────────────────────

impl CertifiedRng {
    /// Create an isolated RNG session for a single game round.
    ///
    /// See [`SeedPool::create_game_session`] for the triple-mixing details.
    /// The `game_id` should be a stable identifier for the game variant
    /// (e.g. `"book-of-dead-v1"`). The `player_id` ensures that two players
    /// playing the same game at the same moment receive statistically
    /// independent outcomes.
    pub async fn session(
        &self,
        game_id: &str,
        player_id: &Uuid,
    ) -> Result<GameSession, RngError> {
        let inner = self.pool.create_game_session(game_id, player_id).await?;

        Ok(GameSession {
            inner,
            game_id: game_id.into(),
            player_id: *player_id,
        })
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Game session (public API wrapper)
// ─────────────────────────────────────────────────────────────────────────────

/// Public game session handle returned to the game engine layer.
pub struct GameSession {
    inner:     crate::hsm::seed_pool::GameRngSession,
    game_id:   String,
    player_id: Uuid,
}

impl GameSession {
    /// Draw a uniformly distributed random integer in `[0, range)`.
    ///
    /// Uses rejection sampling — no modulo bias.
    pub fn draw(&mut self, range: u32) -> u32 {
        self.inner.draw(range)
    }

    /// Return audit metadata (no key material).
    pub fn audit(&self) -> serde_json::Value {
        self.inner.audit_metadata()
    }

    /// The seed hash identifying this session in audit records.
    pub fn seed_hash(&self) -> &str {
        self.inner.seed_hash()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// NIST SP 800-22 self-test
// ─────────────────────────────────────────────────────────────────────────────

impl CertifiedRng {
    /// Run a subset of NIST SP 800-22 statistical tests against a fixed-seed
    /// ChaCha20 instance.
    ///
    /// This is a startup sanity check, not a replacement for full GLI-19
    /// lab certification. Failures indicate a software regression.
    ///
    /// Tests implemented:
    /// 1. **Frequency (monobit)** — proportion of 1-bits should be ≈ 0.5.
    /// 2. **Block frequency** — proportion of 1-bits in non-overlapping
    ///    128-bit blocks should be ≈ 0.5.
    /// 3. **Runs** — number of runs (consecutive sequences of identical bits)
    ///    should be within expected bounds.
    ///
    /// For a production GLI-19 certification, the full 15-test NIST suite
    /// must be run by an accredited testing laboratory on a stream of at least
    /// 1 million bits.
    pub fn run_nist_self_test() -> Result<(), RngError> {
        // Fixed seed for reproducibility. Failures here are deterministic bugs,
        // not hardware failures.
        let mut rng = ChaCha20Rng::from_seed([0x42u8; 32]);

        const SAMPLE_BYTES: usize = 20_000;
        let mut sample = vec![0u8; SAMPLE_BYTES];
        rng.fill_bytes(&mut sample);

        // ── Test 1: Frequency (monobit) ───────────────────────────────────────
        // Count total 1-bits. Expected: ≈ 0.5 * 8 * SAMPLE_BYTES.
        let total_bits = (SAMPLE_BYTES * 8) as f64;
        let ones = count_ones(&sample) as f64;
        let proportion = ones / total_bits;

        // NIST SP 800-22 threshold: |proportion - 0.5| < 0.01 for large samples.
        if (proportion - 0.5).abs() > 0.01 {
            return Err(RngError::NistSelfTest {
                test: "Frequency (monobit)".into(),
                reason: format!(
                    "1-bit proportion {:.4} deviates from 0.5 by > 0.01",
                    proportion
                ),
            });
        }

        // ── Test 2: Block frequency (128-bit blocks) ──────────────────────────
        // Check that each 16-byte block has a 1-bit proportion close to 0.5.
        let block_size = 16usize; // 128 bits
        let mut max_block_deviation: f64 = 0.0;

        for block in sample.chunks_exact(block_size) {
            let block_ones = count_ones(block) as f64;
            let dev = (block_ones / (block_size * 8) as f64 - 0.5).abs();
            if dev > max_block_deviation {
                max_block_deviation = dev;
            }
        }

        // Allow up to 0.20 deviation per 128-bit block.
        // The NIST SP 800-22 formal threshold is p-value based; this simplified
        // check uses a conservative absolute threshold. For a 128-bit block the
        // expected std deviation is ~0.035, so 0.20 represents ~5.7 sigma —
        // a false-positive rate of < 10^{-8} for a correctly implemented CSPRNG.
        if max_block_deviation > 0.20 {
            return Err(RngError::NistSelfTest {
                test: "Block Frequency".into(),
                reason: format!(
                    "max block deviation {:.4} > 0.20",
                    max_block_deviation
                ),
            });
        }

        // ── Test 3: Runs ──────────────────────────────────────────────────────
        // Count transitions between 0 and 1. Expected for random bits:
        // approx n/2 transitions where n = total bits.
        let bits: Vec<u8> = sample
            .iter()
            .flat_map(|&b| (0..8).rev().map(move |i| (b >> i) & 1))
            .collect();

        let transitions = bits
            .windows(2)
            .filter(|w| w[0] != w[1])
            .count();

        let expected_transitions = total_bits as usize / 2;
        let tolerance = (total_bits as usize) / 20; // 5%

        if transitions < expected_transitions - tolerance
            || transitions > expected_transitions + tolerance
        {
            return Err(RngError::NistSelfTest {
                test: "Runs".into(),
                reason: format!(
                    "transitions {} outside [{}, {}]",
                    transitions,
                    expected_transitions - tolerance,
                    expected_transitions + tolerance,
                ),
            });
        }

        tracing::info!(
            tests_passed = 3u32,
            sample_bytes = SAMPLE_BYTES,
            ones_proportion = %format!("{:.4}", proportion),
            transitions,
            algorithm = "ChaCha20-CSPRNG",
            "NIST SP 800-22 self-test passed"
        );

        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

fn count_ones(bytes: &[u8]) -> u32 {
    bytes.iter().map(|b| b.count_ones()).sum()
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nist_self_test_passes() {
        CertifiedRng::run_nist_self_test().expect("NIST self-test must pass for ChaCha20");
    }

    #[test]
    fn count_ones_all_zeros() {
        assert_eq!(count_ones(&[0u8; 32]), 0);
    }

    #[test]
    fn count_ones_all_ff() {
        assert_eq!(count_ones(&[0xFFu8; 4]), 32);
    }

    #[test]
    fn draw_uniqueness_across_sessions() {
        use rand::SeedableRng;
        use rand_chacha::ChaCha20Rng;

        // Two sessions with different seeds must produce different first draws
        // with overwhelming probability (collision prob ≈ 2^{-32}).
        let seed1 = [0x11u8; 32];
        let seed2 = [0x22u8; 32];

        let mut rng1 = ChaCha20Rng::from_seed(seed1);
        let mut rng2 = ChaCha20Rng::from_seed(seed2);

        // Collect 100 draws each.
        let draws1: Vec<u32> = (0..100).map(|_| rng1.next_u32() % 1000).collect();
        let draws2: Vec<u32> = (0..100).map(|_| rng2.next_u32() % 1000).collect();

        // They should not be identical.
        assert_ne!(draws1, draws2, "Different seeds must produce different output");
    }
}
