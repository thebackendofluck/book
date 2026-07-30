// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/// Dev-mode backend: wraps/unwraps data keys using AES-256-GCM with a master
/// key supplied via the `DEV_MASTER_KEY` environment variable (32-byte hex).
///
/// No physical HSM required. Exercises the exact same code paths as HSM mode.
use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Key, Nonce,
};
use anyhow::{bail, Context, Result};
use rand::RngCore;
use zeroize::{Zeroize, ZeroizeOnDrop};

const NONCE_LEN: usize = 12;
const KEY_LEN: usize = 32;

/// A 32-byte key that zeroes itself on drop.
#[derive(ZeroizeOnDrop)]
pub struct DataKey(pub [u8; KEY_LEN]);

impl AsRef<[u8]> for DataKey {
    fn as_ref(&self) -> &[u8] {
        &self.0
    }
}

/// Read master key from env var `DEV_MASTER_KEY` (hex, 64 chars = 32 bytes).
fn load_master_key() -> Result<[u8; KEY_LEN]> {
    let hex_val = std::env::var("DEV_MASTER_KEY")
        .context("DEV_MASTER_KEY env var not set (required for --no-hsm mode)")?;
    let bytes = hex::decode(hex_val.trim())
        .context("DEV_MASTER_KEY must be a 64-character hex string (32 bytes)")?;
    if bytes.len() != KEY_LEN {
        bail!(
            "DEV_MASTER_KEY must be exactly 32 bytes (64 hex chars), got {}",
            bytes.len()
        );
    }
    let mut key = [0u8; KEY_LEN];
    key.copy_from_slice(&bytes);
    Ok(key)
}

/// Generate a fresh random 32-byte data key and wrap it under the master key.
/// Returns the encrypted blob: [nonce (12)] || [ciphertext (32 + 16 GCM tag)].
pub fn generate_and_wrap(_label: &str) -> Result<Vec<u8>> {
    // Generate a fresh data key
    let mut data_key = [0u8; KEY_LEN];
    OsRng.fill_bytes(&mut data_key);

    let result = wrap_raw_key(&data_key);

    // Zeroize before returning regardless of outcome
    data_key.zeroize();
    result
}

/// Wrap an existing raw 32-byte key under the master key.
pub fn wrap_raw_key(data_key: &[u8]) -> Result<Vec<u8>> {
    let mut master_key_bytes = load_master_key()?;

    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(&master_key_bytes));

    let mut nonce_bytes = [0u8; NONCE_LEN];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, data_key)
        .map_err(|e| anyhow::anyhow!("AES-GCM encryption failed: {e}"))?;

    master_key_bytes.zeroize();

    // blob = nonce || ciphertext(+tag)
    let mut blob = Vec::with_capacity(NONCE_LEN + ciphertext.len());
    blob.extend_from_slice(&nonce_bytes);
    blob.extend_from_slice(&ciphertext);
    Ok(blob)
}

/// Unwrap a blob produced by `wrap_raw_key` / `generate_and_wrap`.
pub fn unwrap_key(blob: &[u8]) -> Result<DataKey> {
    if blob.len() < NONCE_LEN + KEY_LEN + 16 {
        bail!("Dev blob too short (expected at least {} bytes)", NONCE_LEN + KEY_LEN + 16);
    }

    let mut master_key_bytes = load_master_key()?;
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(&master_key_bytes));

    let nonce = Nonce::from_slice(&blob[..NONCE_LEN]);
    let plaintext = cipher
        .decrypt(nonce, &blob[NONCE_LEN..])
        .map_err(|_| anyhow::anyhow!("AES-GCM decryption failed — wrong master key or corrupted blob"))?;

    master_key_bytes.zeroize();

    if plaintext.len() != KEY_LEN {
        bail!("Unexpected plaintext length after unwrap: {}", plaintext.len());
    }
    let mut key = DataKey([0u8; KEY_LEN]);
    key.0.copy_from_slice(&plaintext);
    Ok(key)
}
