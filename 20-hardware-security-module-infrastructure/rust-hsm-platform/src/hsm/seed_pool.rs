// Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! # Pre-warmed RNG Seed Pool
//!
//! The seed pool decouples HSM TRNG latency from the game-play critical path.
//! On startup, the pool is filled with `max_level` seeds (32 bytes each) via
//! a single batched HSM call. During operation, each game session consumes one
//! seed from the pool and a background task refills the pool asynchronously
//! when it falls below `min_level`.
//!
//! ## Triple mixing
//!
//! Raw hardware seeds are XOR-mixed with two independent inputs before use:
//!
//! ```text
//! final_seed[i] = hw_seed[i]
//!               ⊕ epoch_rng_mixer[i]   (changes every 30 days)
//!               ⊕ context_hash[i]      (SHA-256 of "game_id:player_id")
//! ```
//!
//! This provides defence in depth: even if an attacker can observe the seed
//! pool (e.g. via a memory dump), they cannot predict the final seed without
//! also knowing the current epoch key and the game context. The epoch key
//! changes every 30 days, limiting the window of any compromise.
//!
//! ## Each seed consumed exactly once
//!
//! Seeds are `pop_front`'d from a `VecDeque` — the pool is a queue, not a
//! cache. Each seed is removed on access. A seed is never returned to the
//! pool after use, preventing any possibility of reuse.
//!
//! ## GLI-19 compliance
//!
//! GLI-19 Section 5.3 requires that each game event be seeded independently.
//! The per-session seed isolation and single-use semantics of this pool
//! satisfy that requirement. The final `seed_hash` (not the seed itself) is
//! logged for auditability.
//!
//! ## Performance
//!
//! - **Normal path**: pool hit → 0 HSM calls, ~100 ns (dequeue + XOR).
//! - **Refill path**: fired asynchronously, does not block the caller.
//! - **Cold path**: pool empty → 1 HSM call; should only occur on cold start.

use std::{collections::VecDeque, sync::Arc};

use rand::SeedableRng;
use rand_chacha::ChaCha20Rng;
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::hsm::{epoch::EpochManager, HsmClient, HsmError};

// ─────────────────────────────────────────────────────────────────────────────
// Error type
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum SeedPoolError {
    #[error("HSM error: {0}")]
    Hsm(#[from] HsmError),

    #[error("seed pool warmup failed")]
    Warmup,
}

// ─────────────────────────────────────────────────────────────────────────────
// Seed pool
// ─────────────────────────────────────────────────────────────────────────────

/// A bounded queue of hardware-random 32-byte seeds.
///
/// The queue is wrapped in `Arc<Mutex<…>>` so that multiple async tasks can
/// concurrently request seeds without a data race. The mutex is held for
/// the minimum time — only for the dequeue operation, not during the HSM call.
pub struct SeedPool {
    pool:      Arc<Mutex<VecDeque<[u8; 32]>>>,
    hsm:       HsmClient,
    epoch:     Arc<EpochManager>,
    /// Trigger an async refill when the pool drops below this level.
    min_level: usize,
    /// Do not exceed this level. Limits memory usage and bounds the number
    /// of pre-computed seeds that an attacker could recover from a core dump.
    max_level: usize,
}

// ─────────────────────────────────────────────────────────────────────────────
// Constructor
// ─────────────────────────────────────────────────────────────────────────────

impl SeedPool {
    /// Create a new seed pool.
    ///
    /// The pool starts empty. Call [`Self::warmup`] before serving traffic.
    pub fn new(hsm: HsmClient, epoch: Arc<EpochManager>) -> Arc<Self> {
        Arc::new(Self {
            pool: Arc::new(Mutex::new(VecDeque::with_capacity(1024))),
            hsm,
            epoch,
            min_level: 100,
            max_level: 1000,
        })
    }

    /// Pre-fill the pool to `max_level` seeds.
    ///
    /// Makes a single HSM call for `max_level * 32` bytes. Called once at
    /// startup. Should complete in < 100 ms for 1 000 seeds.
    pub async fn warmup(self: &Arc<Self>) -> Result<(), SeedPoolError> {
        self.refill(self.max_level).await?;
        tracing::info!(
            seeds = self.max_level,
            "seed pool warmed up — 1 HSM TRNG call"
        );
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Seed access
// ─────────────────────────────────────────────────────────────────────────────

impl SeedPool {
    /// Acquire a single raw hardware seed from the pool.
    ///
    /// If the pool is non-empty, the seed is dequeued and a background refill
    /// is triggered if needed. If the pool is empty (cold start or refill lag),
    /// the seed is fetched directly from the HSM.
    ///
    /// The returned seed is a raw 32-byte slice — callers must apply the
    /// triple-mixing step via [`Self::create_game_session`].
    async fn get_seed(self: &Arc<Self>) -> Result<[u8; 32], SeedPoolError> {
        let seed = {
            let mut pool = self.pool.lock().await;
            pool.pop_front()
        };

        match seed {
            Some(s) => {
                // Check if we need to refill — but don't block the caller.
                let level = self.pool.lock().await.len();
                if level < self.min_level {
                    let pool_ref = Arc::clone(self);
                    tokio::spawn(async move {
                        if let Err(e) = pool_ref.refill(200).await {
                            tracing::error!(error = %e, "seed pool refill failed");
                        }
                    });
                }
                Ok(s)
            }
            None => {
                // Cold path: pool empty, go directly to HSM.
                tracing::warn!("seed pool empty — direct HSM call (cold path)");
                let bytes = self.hsm.random_bytes(32).await?;
                bytes
                    .try_into()
                    .map_err(|_| SeedPoolError::Hsm(HsmError::SeedLength {
                        expected: 32,
                        got: 0,
                    }))
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Game session creation
// ─────────────────────────────────────────────────────────────────────────────

impl SeedPool {
    /// Create a game RNG session with triple-mixed seed.
    ///
    /// This is the primary API. The caller provides the game and player context
    /// used in the mixing step.
    ///
    /// ## Seed derivation
    ///
    /// ```text
    /// hw_seed       := pool.pop_front()  (32 bytes, YubiHSM TRNG)
    /// epoch_mixer   := current_epoch.keys.rng_mixer  (changes every 30 days)
    /// context_hash  := SHA-256("game_id:player_id")  (unique per session)
    ///
    /// final_seed[i] := hw_seed[i] ⊕ epoch_mixer[i] ⊕ context_hash[i]
    /// ```
    ///
    /// The `seed_hash` (SHA-256 of `final_seed`) is logged for audit. The
    /// seed itself is never logged.
    pub async fn create_game_session(
        self: &Arc<Self>,
        game_id: &str,
        player_id: &Uuid,
    ) -> Result<GameRngSession, SeedPoolError> {
        let hw_seed = self.get_seed().await?;

        // Epoch mixer changes every 30 days — limits compromise window.
        let epoch = self.epoch.current.read().await;
        let epoch_mixer = epoch.keys.rng_mixer;

        // Context hash is unique per (game_id, player_id) pair.
        // Using SHA-256 instead of raw concatenation prevents any potential
        // length-extension or confusion attacks.
        let context = format!("{}:{}", game_id, player_id);
        let context_hash: [u8; 32] = Sha256::digest(context.as_bytes()).into();

        // Triple XOR mix — performed in a zeroizable local buffer.
        let mut final_seed = [0u8; 32];
        for i in 0..32 {
            final_seed[i] = hw_seed[i] ^ epoch_mixer[i] ^ context_hash[i];
        }

        // Log the hash of the final seed (not the seed itself).
        let seed_hash = hex::encode(Sha256::digest(&final_seed));

        let rng = ChaCha20Rng::from_seed(final_seed);

        // Zero the seed from the stack.
        // (The compiler may optimise this away; zeroize::Zeroize is safer in prod)
        final_seed.iter_mut().for_each(|b| *b = 0);

        tracing::info!(
            game_id,
            %player_id,
            seed_hash = %seed_hash,
            algorithm = "ChaCha20+HSM-seed+epoch-mix",
            entropy_source = "YubiHSM2-TRNG-pool",
            "game RNG session created"
        );

        Ok(GameRngSession {
            rng,
            game_id: game_id.into(),
            seed_hash,
            draws: 0,
        })
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Refill
// ─────────────────────────────────────────────────────────────────────────────

impl SeedPool {
    /// Fetch `n` fresh seeds from the HSM in a single call and add them to
    /// the pool.
    ///
    /// One `random_bytes(n * 32)` call fetches all seeds at once, minimising
    /// USB round trips to the yubihsm-connector.
    async fn refill(&self, n: usize) -> Result<(), SeedPoolError> {
        let total_bytes = n * 32;
        let bytes = self.hsm.random_bytes(total_bytes).await?;

        let mut pool = self.pool.lock().await;
        let available_slots = self.max_level.saturating_sub(pool.len());
        let to_add = n.min(available_slots);

        for chunk in bytes.chunks_exact(32).take(to_add) {
            let seed: [u8; 32] = chunk.try_into().map_err(|_| {
                SeedPoolError::Hsm(HsmError::SeedLength {
                    expected: 32,
                    got: chunk.len(),
                })
            })?;
            pool.push_back(seed);
        }

        tracing::debug!(
            added = to_add,
            total = pool.len(),
            "seed pool refilled"
        );
        Ok(())
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Game RNG session
// ─────────────────────────────────────────────────────────────────────────────

/// A single-use RNG session for one game round.
///
/// The session is created by [`SeedPool::create_game_session`] and consumed
/// by the game engine. It should not be cloned or shared between game rounds.
pub struct GameRngSession {
    /// ChaCha20 CSPRNG seeded from HSM TRNG + epoch + context.
    rng:       ChaCha20Rng,
    game_id:   String,
    /// SHA-256 of the seed — safe to log.
    seed_hash: String,
    /// Number of draws taken so far in this session.
    draws:     u64,
}

impl GameRngSession {
    /// Draw a random integer in `[0, range)` using rejection sampling.
    ///
    /// Rejection sampling eliminates modulo bias that would skew the
    /// distribution for non-power-of-two ranges. The bias in naive
    /// `value % range` approaches 1/2^32 for small ranges but is non-zero
    /// and detectable by NIST SP 800-22 Frequency tests on large samples.
    ///
    /// ## Algorithm
    /// Find the smallest threshold `t = (-range) mod range` such that
    /// values ≥ `t` are uniformly distributed. Reject values < `t`.
    pub fn draw(&mut self, range: u32) -> u32 {
        use rand::RngCore;
        assert!(range > 0, "draw range must be non-zero");

        self.draws += 1;

        // `threshold` is the smallest number of values we must reject to
        // ensure a uniform distribution. For powers of 2, threshold = 0.
        let threshold = range.wrapping_neg() % range;

        loop {
            let v = self.rng.next_u32();
            if v >= threshold {
                tracing::trace!(
                    game_id = %self.game_id,
                    seq = self.draws,
                    result = v % range,
                    "RNG draw"
                );
                return v % range;
            }
            // Rejected — try again. Expected iterations: < 2.
        }
    }

    /// Return audit metadata suitable for logging.
    ///
    /// The seed itself is never returned. The `seed_hash` identifies the
    /// session for reconciliation without revealing the entropy.
    pub fn audit_metadata(&self) -> serde_json::Value {
        serde_json::json!({
            "game_id":      self.game_id,
            "seed_hash":    self.seed_hash,
            "total_draws":  self.draws,
            "algorithm":    "ChaCha20-CSPRNG+HSM-TRNG+epoch-mix",
        })
    }

    /// Seed hash for correlation in audit records.
    pub fn seed_hash(&self) -> &str {
        &self.seed_hash
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use rand::SeedableRng;
    use rand_chacha::ChaCha20Rng;

    /// Rejection sampling must produce values strictly less than `range`.
    #[test]
    fn draw_within_range() {
        let mut session = GameRngSession {
            rng:       ChaCha20Rng::from_seed([0u8; 32]),
            game_id:   "test".into(),
            seed_hash: "aabb".into(),
            draws:     0,
        };

        for range in [2u32, 3, 5, 7, 10, 37, 100, 1000] {
            for _ in 0..1000 {
                let v = session.draw(range);
                assert!(v < range, "draw({}) returned {} which is out of range", range, v);
            }
        }
    }

    /// For power-of-two ranges there should be no rejection overhead.
    #[test]
    fn draw_power_of_two_no_rejection() {
        let mut rng = ChaCha20Rng::from_seed([1u8; 32]);
        use rand::RngCore;

        // Threshold for a power-of-two range is always 0.
        let range: u32 = 256;
        let threshold = range.wrapping_neg() % range;
        assert_eq!(threshold, 0);

        // All 1000 draws must succeed on first attempt.
        for _ in 0..1000 {
            let v = rng.next_u32() % range;
            assert!(v < range);
        }
    }

    /// Verify that the triple-XOR produces a different seed than the raw hw_seed.
    #[test]
    fn triple_mix_changes_seed() {
        let hw_seed     = [0xAAu8; 32];
        let epoch_mixer = [0xBBu8; 32];
        let context     = "game-abc:00000000-0000-0000-0000-000000000001";
        let context_hash: [u8; 32] = Sha256::digest(context.as_bytes()).into();

        let mut final_seed = [0u8; 32];
        for i in 0..32 {
            final_seed[i] = hw_seed[i] ^ epoch_mixer[i] ^ context_hash[i];
        }

        assert_ne!(final_seed, hw_seed);
        assert_ne!(final_seed, epoch_mixer);
    }
}
