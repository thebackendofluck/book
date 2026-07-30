// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! pg_aegis — PostgreSQL extension exposing AEGIS-128L and ChaCha20-Poly1305
//! AEAD column encryption, built with pgrx.
//!
//! Wire format (bytea column):
//!   [1 byte alg tag | 4 bytes key_version (BE) | 16 bytes nonce | ciphertext || 16 bytes tag]
//!
//! Algorithm tags:
//!   0x01 = AEGIS-128L (16-byte key, 16-byte nonce, 16-byte tag)
//!   0x02 = ChaCha20-Poly1305 (32-byte key, 12-byte nonce, 16-byte tag)
//!
//! Keys live in table `pg_aegis_keys`. The "raw" key bytes stored there are
//! wrapped by a master key which is sourced from:
//!   1. GUC `pg_aegis.master_key_b64` (env-var / postgresql.conf for dev), or
//!   2. YubiHSM wrap call (production — not implemented inline; hook point
//!      documented in README).
//!
//! All keys are zeroized on drop. Decrypt operations can be audited via
//! the `pg_aegis_audit` table (opt-in).

use pgrx::prelude::*;
use pgrx::Spi;
use pgrx::datum::DatumWithOid;
use zeroize::{Zeroize, ZeroizeOnDrop};
use rand::RngCore;

::pgrx::pg_module_magic!();

const ALG_AEGIS_128L: u8 = 0x01;
const ALG_CHACHA20_POLY1305: u8 = 0x02;

const AEGIS_KEY_LEN: usize = 16;
const AEGIS_NONCE_LEN: usize = 16;
const AEGIS_TAG_LEN: usize = 16;

const CHACHA_KEY_LEN: usize = 32;
const CHACHA_NONCE_LEN: usize = 12;
const CHACHA_TAG_LEN: usize = 16;

const WIRE_HEADER_LEN: usize = 1 + 4; // alg + key_version

#[derive(Debug, thiserror::Error)]
enum AegisError {
    #[error("master key not configured: set GUC pg_aegis.master_key_b64 to a 32-byte base64 value")]
    MissingMasterKey,
    #[error("master key must decode to 32 bytes (got {0})")]
    BadMasterKey(usize),
    #[error("key '{0}' not found in pg_aegis_keys")]
    KeyNotFound(String),
    #[error("ciphertext too short")]
    ShortCiphertext,
    #[error("unknown algorithm tag: 0x{0:02x}")]
    UnknownAlg(u8),
    #[error("AEAD decryption failed (tampered ciphertext, wrong key, or wrong AAD)")]
    DecryptFailed,
    #[error("AEAD encryption failed")]
    EncryptFailed,
    #[error("base64 decode error: {0}")]
    Base64(String),
}

#[derive(Zeroize, ZeroizeOnDrop)]
struct SecretKey(Vec<u8>);

impl SecretKey {
    fn as_slice(&self) -> &[u8] { &self.0 }
}

// ---------------------------------------------------------------------------
// Master key handling
// ---------------------------------------------------------------------------

fn get_master_key() -> Result<SecretKey, AegisError> {
    // Read GUC `pg_aegis.master_key_b64`. In production this would be
    // swapped for a YubiHSM wrap/unwrap call.
    let guc_val: Option<String> = Spi::get_one(
        "SELECT current_setting('pg_aegis.master_key_b64', true)"
    ).ok().flatten();

    let b64 = guc_val.ok_or(AegisError::MissingMasterKey)?;
    if b64.is_empty() {
        return Err(AegisError::MissingMasterKey);
    }
    use base64::{engine::general_purpose::STANDARD, Engine};
    let raw = STANDARD.decode(b64.trim()).map_err(|e| AegisError::Base64(e.to_string()))?;
    if raw.len() != 32 {
        return Err(AegisError::BadMasterKey(raw.len()));
    }
    Ok(SecretKey(raw))
}

// Wrap a data key under the master key using ChaCha20-Poly1305.
// Stored layout: [12-byte nonce | ciphertext | 16-byte tag]
fn wrap_key(master: &SecretKey, plaintext_key: &[u8], aad: &[u8]) -> Result<Vec<u8>, AegisError> {
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce, KeyInit};
    use chacha20poly1305::aead::Aead;

    let key = Key::from_slice(master.as_slice());
    let cipher = ChaCha20Poly1305::new(key);
    let mut nonce_bytes = [0u8; 12];
    rand::thread_rng().fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ct = cipher
        .encrypt(nonce, chacha20poly1305::aead::Payload { msg: plaintext_key, aad })
        .map_err(|_| AegisError::EncryptFailed)?;

    let mut out = Vec::with_capacity(12 + ct.len());
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ct);
    Ok(out)
}

fn unwrap_key(master: &SecretKey, wrapped: &[u8], aad: &[u8]) -> Result<SecretKey, AegisError> {
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce, KeyInit};
    use chacha20poly1305::aead::Aead;

    if wrapped.len() < 12 + 16 {
        return Err(AegisError::ShortCiphertext);
    }
    let key = Key::from_slice(master.as_slice());
    let cipher = ChaCha20Poly1305::new(key);
    let nonce = Nonce::from_slice(&wrapped[..12]);
    let pt = cipher
        .decrypt(nonce, chacha20poly1305::aead::Payload { msg: &wrapped[12..], aad })
        .map_err(|_| AegisError::DecryptFailed)?;
    Ok(SecretKey(pt))
}

// ---------------------------------------------------------------------------
// Key lookup from pg_aegis_keys
// ---------------------------------------------------------------------------

struct LoadedKey {
    version: i32,
    key: SecretKey,
}

fn load_key(key_name: &str) -> Result<LoadedKey, AegisError> {
    let master = get_master_key()?;
    let args: [DatumWithOid; 1] = [key_name.into()];
    let row: Option<(i32, Vec<u8>)> = Spi::get_two_with_args(
        "SELECT key_version, encrypted_key FROM pg_aegis_keys WHERE key_name = $1",
        &args,
    ).ok().and_then(|(v, k)| match (v, k) {
        (Some(v), Some(k)) => Some((v, k)),
        _ => None,
    });

    let (version, wrapped) = row.ok_or_else(|| AegisError::KeyNotFound(key_name.to_string()))?;
    let aad = key_name.as_bytes();
    let key = unwrap_key(&master, &wrapped, aad)?;
    Ok(LoadedKey { version, key })
}

// ---------------------------------------------------------------------------
// AEAD encryption primitives
// ---------------------------------------------------------------------------

fn aead_encrypt_aegis128l(key: &[u8], nonce: &[u8], pt: &[u8], aad: &[u8]) -> Result<(Vec<u8>, [u8; 16]), AegisError> {
    use aegis::aegis128l::Aegis128L;
    if key.len() != AEGIS_KEY_LEN || nonce.len() != AEGIS_NONCE_LEN {
        return Err(AegisError::EncryptFailed);
    }
    let key_arr: &[u8; 16] = key.try_into().map_err(|_| AegisError::EncryptFailed)?;
    let nonce_arr: &[u8; 16] = nonce.try_into().map_err(|_| AegisError::EncryptFailed)?;
    let cipher = Aegis128L::<16>::new(key_arr, nonce_arr);
    let (ct, tag) = cipher.encrypt(pt, aad);
    Ok((ct, tag))
}

fn aead_decrypt_aegis128l(key: &[u8], nonce: &[u8], ct: &[u8], tag: &[u8; 16], aad: &[u8]) -> Result<Vec<u8>, AegisError> {
    use aegis::aegis128l::Aegis128L;
    if key.len() != AEGIS_KEY_LEN || nonce.len() != AEGIS_NONCE_LEN {
        return Err(AegisError::DecryptFailed);
    }
    let key_arr: &[u8; 16] = key.try_into().map_err(|_| AegisError::DecryptFailed)?;
    let nonce_arr: &[u8; 16] = nonce.try_into().map_err(|_| AegisError::DecryptFailed)?;
    let cipher = Aegis128L::<16>::new(key_arr, nonce_arr);
    cipher.decrypt(ct, tag, aad).map_err(|_| AegisError::DecryptFailed)
}

fn aead_encrypt_chacha(key: &[u8], nonce: &[u8], pt: &[u8], aad: &[u8]) -> Result<Vec<u8>, AegisError> {
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce, KeyInit};
    use chacha20poly1305::aead::Aead;
    if key.len() != CHACHA_KEY_LEN || nonce.len() != CHACHA_NONCE_LEN {
        return Err(AegisError::EncryptFailed);
    }
    let cipher = ChaCha20Poly1305::new(Key::from_slice(key));
    cipher.encrypt(Nonce::from_slice(nonce),
                   chacha20poly1305::aead::Payload { msg: pt, aad })
        .map_err(|_| AegisError::EncryptFailed)
}

fn aead_decrypt_chacha(key: &[u8], nonce: &[u8], ct_and_tag: &[u8], aad: &[u8]) -> Result<Vec<u8>, AegisError> {
    use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce, KeyInit};
    use chacha20poly1305::aead::Aead;
    if key.len() != CHACHA_KEY_LEN || nonce.len() != CHACHA_NONCE_LEN {
        return Err(AegisError::DecryptFailed);
    }
    let cipher = ChaCha20Poly1305::new(Key::from_slice(key));
    cipher.decrypt(Nonce::from_slice(nonce),
                   chacha20poly1305::aead::Payload { msg: ct_and_tag, aad })
        .map_err(|_| AegisError::DecryptFailed)
}

// ---------------------------------------------------------------------------
// Wire format helpers
// ---------------------------------------------------------------------------

fn pack_wire(alg: u8, key_version: i32, nonce: &[u8], ct: &[u8], tag: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(WIRE_HEADER_LEN + nonce.len() + ct.len() + tag.len());
    out.push(alg);
    out.extend_from_slice(&key_version.to_be_bytes());
    out.extend_from_slice(nonce);
    out.extend_from_slice(ct);
    out.extend_from_slice(tag);
    out
}

fn parse_wire(wire: &[u8]) -> Result<(u8, i32, &[u8]), AegisError> {
    if wire.len() < WIRE_HEADER_LEN + 16 + 16 {
        return Err(AegisError::ShortCiphertext);
    }
    let alg = wire[0];
    let ver = i32::from_be_bytes([wire[1], wire[2], wire[3], wire[4]]);
    Ok((alg, ver, &wire[WIRE_HEADER_LEN..]))
}

// ---------------------------------------------------------------------------
// Encrypt / decrypt core (with AAD)
// ---------------------------------------------------------------------------

fn encrypt_core(plaintext: &[u8], key_name: &str, aad: &[u8], alg: u8) -> Result<Vec<u8>, AegisError> {
    let loaded = load_key(key_name)?;

    match alg {
        ALG_AEGIS_128L => {
            // AEGIS-128L wants a 16-byte key. Truncate from the 32-byte
            // stored key deterministically (first 16 bytes).
            let k = &loaded.key.as_slice()[..AEGIS_KEY_LEN];
            let mut nonce = [0u8; AEGIS_NONCE_LEN];
            rand::thread_rng().fill_bytes(&mut nonce);
            let (ct, tag) = aead_encrypt_aegis128l(k, &nonce, plaintext, aad)?;
            Ok(pack_wire(ALG_AEGIS_128L, loaded.version, &nonce, &ct, &tag))
        }
        ALG_CHACHA20_POLY1305 => {
            let k = loaded.key.as_slice();
            let mut nonce_full = [0u8; 16];
            rand::thread_rng().fill_bytes(&mut nonce_full);
            // Use first 12 bytes for ChaCha20 nonce; remaining bytes of
            // the 16-byte field are zero-padded on the wire so the wire
            // format stays uniform across algorithms.
            let nonce12 = &nonce_full[..CHACHA_NONCE_LEN];
            let ct = aead_encrypt_chacha(k, nonce12, plaintext, aad)?;
            // Split ct+tag into ct and tag for uniform packing.
            if ct.len() < CHACHA_TAG_LEN { return Err(AegisError::EncryptFailed); }
            let (body, tag) = ct.split_at(ct.len() - CHACHA_TAG_LEN);
            // Pad nonce to 16 bytes for wire uniformity.
            let mut nonce_wire = [0u8; 16];
            nonce_wire[..CHACHA_NONCE_LEN].copy_from_slice(nonce12);
            Ok(pack_wire(ALG_CHACHA20_POLY1305, loaded.version, &nonce_wire, body, tag))
        }
        _ => Err(AegisError::UnknownAlg(alg)),
    }
}

fn decrypt_core(wire: &[u8], key_name: &str, aad: &[u8]) -> Result<Vec<u8>, AegisError> {
    let (alg, _ver, rest) = parse_wire(wire)?;
    // Note: we currently ignore the version field and always load the
    // current key. A full implementation would look up historical
    // versions — left as an exercise for key rotation with overlap.
    let loaded = load_key(key_name)?;
    match alg {
        ALG_AEGIS_128L => {
            let nonce = &rest[..AEGIS_NONCE_LEN];
            let body = &rest[AEGIS_NONCE_LEN..rest.len() - AEGIS_TAG_LEN];
            let tag_slice = &rest[rest.len() - AEGIS_TAG_LEN..];
            let mut tag = [0u8; AEGIS_TAG_LEN];
            tag.copy_from_slice(tag_slice);
            let k = &loaded.key.as_slice()[..AEGIS_KEY_LEN];
            aead_decrypt_aegis128l(k, nonce, body, &tag, aad)
        }
        ALG_CHACHA20_POLY1305 => {
            let nonce = &rest[..CHACHA_NONCE_LEN]; // 12 bytes
            // Wire nonce field is 16 bytes; body starts at 16.
            let body_and_tag = &rest[16..];
            let k = loaded.key.as_slice();
            aead_decrypt_chacha(k, nonce, body_and_tag, aad)
        }
        _ => Err(AegisError::UnknownAlg(alg)),
    }
}

// ---------------------------------------------------------------------------
// SQL-facing functions
// ---------------------------------------------------------------------------

#[pg_extern]
fn aegis_encrypt(plaintext: &str, key_name: &str) -> Vec<u8> {
    aegis_encrypt_aad(plaintext, key_name, "")
}

#[pg_extern]
fn aegis_encrypt_aad(plaintext: &str, key_name: &str, aad: &str) -> Vec<u8> {
    match encrypt_core(plaintext.as_bytes(), key_name, aad.as_bytes(), ALG_AEGIS_128L) {
        Ok(v) => v,
        Err(e) => error!("pg_aegis: {}", e),
    }
}

#[pg_extern]
fn aegis_decrypt(ciphertext: &[u8], key_name: &str) -> String {
    aegis_decrypt_aad(ciphertext, key_name, "")
}

#[pg_extern]
fn aegis_decrypt_aad(ciphertext: &[u8], key_name: &str, aad: &str) -> String {
    match decrypt_core(ciphertext, key_name, aad.as_bytes()) {
        Ok(v) => match String::from_utf8(v) {
            Ok(s) => s,
            Err(_) => error!("pg_aegis: decrypted value is not valid UTF-8"),
        },
        Err(e) => error!("pg_aegis: {}", e),
    }
}

#[pg_extern]
fn chacha_encrypt(plaintext: &str, key_name: &str) -> Vec<u8> {
    match encrypt_core(plaintext.as_bytes(), key_name, b"", ALG_CHACHA20_POLY1305) {
        Ok(v) => v,
        Err(e) => error!("pg_aegis: {}", e),
    }
}

// ---------------------------------------------------------------------------
// Key management
// ---------------------------------------------------------------------------

#[pg_extern]
fn aegis_generate_key(key_name: &str) -> bool {
    let master = match get_master_key() {
        Ok(m) => m,
        Err(e) => error!("pg_aegis: {}", e),
    };
    // Generate a 32-byte data key (used as-is for ChaCha, truncated to 16 for AEGIS).
    let mut raw = vec![0u8; 32];
    rand::thread_rng().fill_bytes(&mut raw);
    let wrapped = match wrap_key(&master, &raw, key_name.as_bytes()) {
        Ok(w) => w,
        Err(e) => error!("pg_aegis: {}", e),
    };
    // Zero the plaintext key immediately.
    raw.zeroize();

    let args: [DatumWithOid; 2] = [key_name.into(), wrapped.into()];
    let res = Spi::run_with_args(
        "INSERT INTO pg_aegis_keys(key_name, key_version, encrypted_key) \
         VALUES ($1, 1, $2) \
         ON CONFLICT (key_name) DO NOTHING",
        &args,
    );
    res.is_ok()
}

#[pg_extern]
fn aegis_rotate_key(key_name: &str) -> i32 {
    let master = match get_master_key() {
        Ok(m) => m,
        Err(e) => error!("pg_aegis: {}", e),
    };
    let mut raw = vec![0u8; 32];
    rand::thread_rng().fill_bytes(&mut raw);
    let wrapped = match wrap_key(&master, &raw, key_name.as_bytes()) {
        Ok(w) => w,
        Err(e) => error!("pg_aegis: {}", e),
    };
    raw.zeroize();

    let args: [DatumWithOid; 2] = [key_name.into(), wrapped.into()];
    let new_version: Option<i32> = Spi::get_one_with_args(
        "UPDATE pg_aegis_keys \
         SET key_version = key_version + 1, encrypted_key = $2, rotated_at = NOW() \
         WHERE key_name = $1 \
         RETURNING key_version",
        &args,
    ).ok().flatten();
    new_version.unwrap_or_else(|| error!("pg_aegis: key '{}' not found", key_name))
}

#[pg_extern]
fn aegis_version() -> &'static str {
    concat!("pg_aegis ", env!("CARGO_PKG_VERSION"),
            " (AEGIS-128L + ChaCha20-Poly1305)")
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(any(test, feature = "pg_test"))]
#[pg_schema]
mod tests {
    use pgrx::prelude::*;

    const B64_MASTER: &str = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=";

    fn setup_master_key() {
        Spi::run(&format!("SET pg_aegis.master_key_b64 TO '{}'", B64_MASTER)).unwrap();
    }

    #[pg_test]
    fn test_roundtrip_aegis() {
        setup_master_key();
        Spi::run("SELECT aegis_generate_key('t1')").unwrap();
        let ct: Option<Vec<u8>> = Spi::get_one("SELECT aegis_encrypt('hello', 't1')").unwrap();
        assert!(ct.is_some());
        let ct_bytes: Vec<u8> = ct.unwrap();
        let args: [DatumWithOid; 1] = [ct_bytes.into()];
        let pt: Option<String> = Spi::get_one_with_args(
            "SELECT aegis_decrypt($1, 't1')",
            &args,
        ).unwrap();
        assert_eq!(pt.as_deref(), Some("hello"));
    }

    #[pg_test]
    fn test_tamper_detection() {
        setup_master_key();
        Spi::run("SELECT aegis_generate_key('t2')").unwrap();
        let mut ct: Vec<u8> = Spi::get_one("SELECT aegis_encrypt('secret', 't2')").unwrap().unwrap();
        // Flip a byte in the ciphertext body.
        let last = ct.len() - 20;
        ct[last] ^= 0x01;
        let result = std::panic::catch_unwind(|| {
            let args: [DatumWithOid; 1] = [ct.into()];
            let _: Option<String> = Spi::get_one_with_args(
                "SELECT aegis_decrypt($1, 't2')",
                &args,
            ).unwrap();
        });
        assert!(result.is_err(), "tampered ciphertext must fail to decrypt");
    }

    #[pg_test]
    fn test_aad_binding() {
        setup_master_key();
        Spi::run("SELECT aegis_generate_key('t3')").unwrap();
        let ct: Vec<u8> = Spi::get_one(
            "SELECT aegis_encrypt_aad('pii', 't3', 'players:email:42')"
        ).unwrap().unwrap();
        let ct_clone = ct.clone();
        // Decrypting with wrong AAD must fail.
        let result = std::panic::catch_unwind(|| {
            let args: [DatumWithOid; 1] = [ct_clone.into()];
            let _: Option<String> = Spi::get_one_with_args(
                "SELECT aegis_decrypt_aad($1, 't3', 'players:email:43')",
                &args,
            ).unwrap();
        });
        assert!(result.is_err(), "wrong AAD must fail to decrypt");
        let args: [DatumWithOid; 1] = [ct.into()];
        let pt: Option<String> = Spi::get_one_with_args(
            "SELECT aegis_decrypt_aad($1, 't3', 'players:email:42')",
            &args,
        ).unwrap();
        assert_eq!(pt.as_deref(), Some("pii"));
    }
}

#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {}
    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![
            "shared_preload_libraries = 'pg_aegis'",
            "pg_aegis.master_key_b64 = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='",
        ]
    }
}
