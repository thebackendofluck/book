// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/// AEGIS-128L encrypt / decrypt wrappers.
///
/// Wire format for ciphertext files:
///   [nonce: 16 bytes][tag: 16 bytes][ciphertext: variable]
///
/// AEGIS-128L uses a 128-bit (16-byte) key and 128-bit (16-byte) nonce.
/// We derive the AEGIS key from the caller's 256-bit key by taking the first
/// 16 bytes — keeps the API uniform while matching the AEGIS-128L key size.
use aegis::aegis128l::Aegis128L;
use aes_gcm::aead::OsRng;
use anyhow::{bail, Result};
use rand::RngCore;

const NONCE_LEN: usize = 16;
const TAG_LEN: usize = 16;

/// Encrypt `plaintext` with the given 32-byte key.
/// Returns: [nonce (16)] || [tag (16)] || [ciphertext].
pub fn encrypt(key: &[u8], plaintext: &[u8]) -> Result<Vec<u8>> {
    if key.len() != 32 {
        bail!("encrypt: key must be 32 bytes");
    }

    let mut nonce = [0u8; NONCE_LEN];
    OsRng.fill_bytes(&mut nonce);

    // Use first 16 bytes as the AEGIS-128L key
    let aegis_key: &[u8; 16] = key[..16].try_into().unwrap();

    let cipher = Aegis128L::<TAG_LEN>::new(aegis_key, &nonce);
    let (ciphertext, tag) = cipher.encrypt(plaintext, &[]);

    let mut out = Vec::with_capacity(NONCE_LEN + TAG_LEN + ciphertext.len());
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&tag);
    out.extend_from_slice(&ciphertext);
    Ok(out)
}

/// Decrypt a blob produced by `encrypt`.
pub fn decrypt(key: &[u8], blob: &[u8]) -> Result<Vec<u8>> {
    if key.len() != 32 {
        bail!("decrypt: key must be 32 bytes");
    }
    if blob.len() < NONCE_LEN + TAG_LEN {
        bail!("decrypt: ciphertext blob too short");
    }

    let nonce: &[u8; NONCE_LEN] = blob[..NONCE_LEN].try_into().unwrap();
    let tag: &[u8; TAG_LEN] = blob[NONCE_LEN..NONCE_LEN + TAG_LEN].try_into().unwrap();
    let ciphertext = &blob[NONCE_LEN + TAG_LEN..];

    let aegis_key: &[u8; 16] = key[..16].try_into().unwrap();
    let cipher = Aegis128L::<TAG_LEN>::new(aegis_key, nonce);

    let plaintext = cipher
        .decrypt(ciphertext, tag, &[])
        .map_err(|_| anyhow::anyhow!("AEGIS decryption failed — wrong key or corrupted ciphertext"))?;

    Ok(plaintext)
}
