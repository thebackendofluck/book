// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/// Integration tests — dev mode only (no physical HSM required).
///
/// Run with:
///   cargo test --no-default-features
///
/// This disables the `hsm` feature so the yubihsm crate is not compiled,
/// meaning no USB libraries need to be present on the build host.
use std::fs;
use tempfile::TempDir;

// ── helpers ──────────────────────────────────────────────────────────────────

/// A stable 32-byte test master key (hex). Safe to use in tests only.
const TEST_MASTER_KEY: &str = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";

fn with_master_key<F: FnOnce() -> R, R>(key_hex: &str, f: F) -> R {
    // Each test sets this before using dev:: functions.
    // Tests that depend on key isolation use different fixed keys.
    std::env::set_var("DEV_MASTER_KEY", key_hex);
    f()
}

// ── aegis roundtrip ──────────────────────────────────────────────────────────

#[test]
fn test_aegis_roundtrip() {
    use hsm_keymgr::aegis_ops;
    use aes_gcm::aead::OsRng;
    use rand::RngCore;

    let mut key = [0u8; 32];
    OsRng.fill_bytes(&mut key);

    let plaintext = b"Hello, AEGIS-128L! This is a test message.";
    let ct = aegis_ops::encrypt(&key, plaintext).expect("encrypt failed");
    let pt = aegis_ops::decrypt(&key, &ct).expect("decrypt failed");
    assert_eq!(pt, plaintext);
}

#[test]
fn test_aegis_wrong_key_fails() {
    use hsm_keymgr::aegis_ops;
    use aes_gcm::aead::OsRng;
    use rand::RngCore;

    let mut key = [0u8; 32];
    OsRng.fill_bytes(&mut key);
    let mut wrong_key = key;
    wrong_key[0] ^= 0xFF;

    let ct = aegis_ops::encrypt(&key, b"secret").expect("encrypt failed");
    let result = aegis_ops::decrypt(&wrong_key, &ct);
    assert!(result.is_err(), "decryption with wrong key should fail");
}

// ── dev wrap/unwrap roundtrip ─────────────────────────────────────────────────

#[test]
fn test_dev_generate_and_unwrap() {
    use hsm_keymgr::dev;

    // Use a fixed key so generate + unwrap see the same value in the same thread.
    with_master_key(TEST_MASTER_KEY, || {
        let blob = dev::generate_and_wrap("test-label").expect("generate_and_wrap failed");
        let key = dev::unwrap_key(&blob).expect("unwrap_key failed");
        assert_eq!(key.as_ref().len(), 32, "unwrapped key should be 32 bytes");
    });
}

#[test]
fn test_dev_wrong_master_key_fails() {
    use hsm_keymgr::dev;

    let wrap_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    let wrong_key = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    // Wrap with one key, attempt unwrap with another
    std::env::set_var("DEV_MASTER_KEY", wrap_key);
    let blob = dev::generate_and_wrap("test-label").expect("generate_and_wrap failed");

    std::env::set_var("DEV_MASTER_KEY", wrong_key);
    let result = dev::unwrap_key(&blob);
    assert!(result.is_err(), "unwrap with wrong master key should fail");
}

// ── full end-to-end: generate → unwrap → encrypt → decrypt ──────────────────

#[test]
fn test_full_roundtrip_dev_mode() {
    use hsm_keymgr::{dev, aegis_ops};

    with_master_key(TEST_MASTER_KEY, || {
        // 1. Generate a data key (wrapped)
        let wrapped_blob = dev::generate_and_wrap("roundtrip-test").expect("generate failed");

        // 2. Unwrap to get the raw key
        let data_key = dev::unwrap_key(&wrapped_blob).expect("unwrap failed");

        // 3. Encrypt some data
        let plaintext = b"Chapter 27: YubiHSM + AEGIS key management";
        let ciphertext = aegis_ops::encrypt(data_key.as_ref(), plaintext).expect("encrypt failed");

        // 4. Decrypt and verify
        let recovered = aegis_ops::decrypt(data_key.as_ref(), &ciphertext).expect("decrypt failed");
        assert_eq!(recovered, plaintext);
    });
}

// ── keymgr::cmd_* with temp files ────────────────────────────────────────────

#[test]
fn test_cmd_generate_and_unwrap_files() {
    use hsm_keymgr::keymgr;

    with_master_key(TEST_MASTER_KEY, || {
        let dir = TempDir::new().unwrap();
        let wrapped_path = dir.path().join("wrapped.bin");
        let unwrapped_path = dir.path().join("key.bin");

        keymgr::cmd_generate("file-test", true, &wrapped_path).expect("cmd_generate failed");
        assert!(wrapped_path.exists(), "wrapped blob should exist");

        keymgr::cmd_unwrap(&wrapped_path, &unwrapped_path, true).expect("cmd_unwrap failed");
        let raw_key = fs::read(&unwrapped_path).unwrap();
        assert_eq!(raw_key.len(), 32);
    });
}

#[test]
fn test_cmd_encrypt_decrypt_files() {
    use hsm_keymgr::keymgr;
    use aes_gcm::aead::OsRng;
    use rand::RngCore;

    let dir = TempDir::new().unwrap();

    let key_path = dir.path().join("key.bin");
    let mut key = [0u8; 32];
    OsRng.fill_bytes(&mut key);
    fs::write(&key_path, key).unwrap();

    let input_path = dir.path().join("plain.txt");
    let output_path = dir.path().join("cipher.bin");
    let decrypted_path = dir.path().join("decrypted.txt");

    let original = b"Top-secret iGaming data";
    fs::write(&input_path, original).unwrap();

    keymgr::cmd_encrypt(&key_path, &input_path, &output_path).expect("encrypt failed");
    keymgr::cmd_decrypt(&key_path, &output_path, &decrypted_path).expect("decrypt failed");

    let recovered = fs::read(&decrypted_path).unwrap();
    assert_eq!(recovered, original);
}
