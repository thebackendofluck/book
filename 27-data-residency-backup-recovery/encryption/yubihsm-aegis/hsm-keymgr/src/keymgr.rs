// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/// Core key management commands — dispatch between HSM and dev backends.
use anyhow::{bail, Result};
use std::fs;
use std::path::Path;

/// Wrapped-key blob format (all little-endian):
///   [0]        version  : u8   (currently 0x01)
///   [1]        alg      : u8   (0x01 = AES-256-GCM-wrapped, 0x02 = YubiHSM-wrapped)
///   [2..6]     wrapped_key_len : u32
///   [6..6+wkl] wrapped_key
///   rest       nonce + tag (appended by the wrapping cipher)
pub const BLOB_VERSION: u8 = 0x01;
pub const ALG_DEV: u8 = 0x01;
pub const ALG_HSM: u8 = 0x02;

pub fn cmd_generate(label: &str, no_hsm: bool, output: &Path) -> Result<()> {
    if no_hsm {
        let blob = crate::dev::generate_and_wrap(label)?;
        write_blob(output, ALG_DEV, &blob)?;
        eprintln!("[dev] Wrapped key written to {}", output.display());
    } else {
        #[cfg(feature = "hsm")]
        {
            let blob = crate::hsm::generate_and_wrap(label)?;
            write_blob(output, ALG_HSM, &blob)?;
            eprintln!("[hsm] Wrapped key written to {}", output.display());
        }
        #[cfg(not(feature = "hsm"))]
        bail!("Binary was compiled without HSM support. Use --no-hsm or enable the 'hsm' feature.");
    }
    Ok(())
}

pub fn cmd_unwrap(wrapped: &Path, output: &Path, no_hsm: bool) -> Result<()> {
    let (alg, payload) = read_blob(wrapped)?;

    if no_hsm || alg == ALG_DEV {
        let key = crate::dev::unwrap_key(&payload)?;
        fs::write(output, key.as_ref())?;
        eprintln!("[dev] Key unwrapped to {}", output.display());
    } else {
        #[cfg(feature = "hsm")]
        {
            let key = crate::hsm::unwrap_key(&payload)?;
            fs::write(output, key.as_ref())?;
            eprintln!("[hsm] Key unwrapped to {}", output.display());
        }
        #[cfg(not(feature = "hsm"))]
        bail!("Binary was compiled without HSM support. Use --no-hsm or enable the 'hsm' feature.");
    }
    Ok(())
}

pub fn cmd_encrypt(key_path: &Path, input: &Path, output: &Path) -> Result<()> {
    let key_bytes = fs::read(key_path)?;
    if key_bytes.len() != 32 {
        bail!("Key must be exactly 32 bytes, got {}", key_bytes.len());
    }
    let plaintext = fs::read(input)?;
    let ciphertext = crate::aegis_ops::encrypt(&key_bytes, &plaintext)?;
    fs::write(output, &ciphertext)?;
    eprintln!("Encrypted {} -> {}", input.display(), output.display());
    Ok(())
}

pub fn cmd_decrypt(key_path: &Path, input: &Path, output: &Path) -> Result<()> {
    let key_bytes = fs::read(key_path)?;
    if key_bytes.len() != 32 {
        bail!("Key must be exactly 32 bytes, got {}", key_bytes.len());
    }
    let ciphertext = fs::read(input)?;
    let plaintext = crate::aegis_ops::decrypt(&key_bytes, &ciphertext)?;
    fs::write(output, &plaintext)?;
    eprintln!("Decrypted {} -> {}", input.display(), output.display());
    Ok(())
}

// ── Blob serialisation ────────────────────────────────────────────────────────

/// Serialize blob: [version][alg][u32-le payload len][payload]
pub fn write_blob(path: &Path, alg: u8, payload: &[u8]) -> Result<()> {
    let len = payload.len() as u32;
    let mut out = Vec::with_capacity(6 + payload.len());
    out.push(BLOB_VERSION);
    out.push(alg);
    out.extend_from_slice(&len.to_le_bytes());
    out.extend_from_slice(payload);
    fs::write(path, out)?;
    Ok(())
}

/// Deserialize blob, return (alg, payload).
pub fn read_blob(path: &Path) -> Result<(u8, Vec<u8>)> {
    let data = fs::read(path)?;
    if data.len() < 6 {
        bail!("Blob too short");
    }
    let version = data[0];
    if version != BLOB_VERSION {
        bail!("Unknown blob version: {version}");
    }
    let alg = data[1];
    let len = u32::from_le_bytes([data[2], data[3], data[4], data[5]]) as usize;
    if data.len() < 6 + len {
        bail!("Blob truncated: expected {} bytes of payload", len);
    }
    let payload = data[6..6 + len].to_vec();
    Ok((alg, payload))
}
