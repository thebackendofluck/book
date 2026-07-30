// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

//! C ABI bindings for Python and Go consumers.
//!
//! Return codes:
//! - `>= 0`: number of bytes written to `out`
//! - `-1`: null pointer
//! - `-2`: invalid key length
//! - `-3`: unknown algorithm
//! - `-4`: authentication failed
//! - `-5`: output buffer too small
//! - `-6`: other error

#![allow(clippy::missing_safety_doc)]

use crate::CryptoEngine;

/// Opaque handle to a crypto engine. Allocate via
/// [`crypto_engine_new`], release via [`crypto_engine_free`].
pub struct EngineHandle {
    inner: CryptoEngine,
}

const ERR_NULL_PTR: i32 = -1;
const ERR_KEY_LEN: i32 = -2;
const ERR_UNKNOWN_ALG: i32 = -3;
const ERR_AUTH_FAILED: i32 = -4;
const ERR_BUF_TOO_SMALL: i32 = -5;
const ERR_OTHER: i32 = -6;

/// Create a new engine.
///
/// # Safety
/// `key` must point to `key_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn crypto_engine_new(
    alg: u8,
    key: *const u8,
    key_len: usize,
) -> *mut EngineHandle {
    if key.is_null() {
        return std::ptr::null_mut();
    }
    let key_slice = unsafe { std::slice::from_raw_parts(key, key_len) };
    let engine = match alg {
        0x01 => {
            if key_len != 16 {
                return std::ptr::null_mut();
            }
            let mut k = [0u8; 16];
            k.copy_from_slice(key_slice);
            CryptoEngine::new_aegis128l(&k).ok()
        }
        0x02 => {
            if key_len != 32 {
                return std::ptr::null_mut();
            }
            let mut k = [0u8; 32];
            k.copy_from_slice(key_slice);
            CryptoEngine::new_aegis256(&k).ok()
        }
        0x03 => {
            if key_len != 32 {
                return std::ptr::null_mut();
            }
            let mut k = [0u8; 32];
            k.copy_from_slice(key_slice);
            CryptoEngine::new_aes256gcm(&k).ok()
        }
        0x04 => {
            if key_len != 32 {
                return std::ptr::null_mut();
            }
            let mut k = [0u8; 32];
            k.copy_from_slice(key_slice);
            CryptoEngine::new_chacha20poly1305(&k).ok()
        }
        _ => None,
    };
    match engine {
        Some(e) => Box::into_raw(Box::new(EngineHandle { inner: e })),
        None => std::ptr::null_mut(),
    }
}

/// Encrypt a message. Writes at most `out_len` bytes to `out`, returns the
/// number of bytes written, or a negative error code.
///
/// The output is a self-describing wire payload:
/// `[alg_id | nonce | ciphertext | tag]`.
///
/// # Safety
/// All non-null pointers must point to the number of bytes indicated by
/// their length argument. `out` must be writable for `out_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn crypto_engine_encrypt(
    handle: *mut EngineHandle,
    plaintext: *const u8,
    plaintext_len: usize,
    aad: *const u8,
    aad_len: usize,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    if handle.is_null() || out.is_null() {
        return ERR_NULL_PTR;
    }
    if plaintext.is_null() && plaintext_len != 0 {
        return ERR_NULL_PTR;
    }
    if aad.is_null() && aad_len != 0 {
        return ERR_NULL_PTR;
    }
    let handle = unsafe { &*handle };
    let pt_slice = if plaintext_len == 0 {
        &[][..]
    } else {
        unsafe { std::slice::from_raw_parts(plaintext, plaintext_len) }
    };
    let aad_slice = if aad_len == 0 {
        &[][..]
    } else {
        unsafe { std::slice::from_raw_parts(aad, aad_len) }
    };

    match handle.inner.encrypt(pt_slice, aad_slice) {
        Ok(payload) => {
            if payload.len() > out_len {
                return ERR_BUF_TOO_SMALL;
            }
            let out_slice = unsafe { std::slice::from_raw_parts_mut(out, payload.len()) };
            out_slice.copy_from_slice(&payload);
            payload.len() as i32
        }
        Err(_) => ERR_OTHER,
    }
}

/// Decrypt a wire payload.
///
/// # Safety
/// All non-null pointers must point to the number of bytes indicated by
/// their length argument. `out` must be writable for `out_len` bytes.
#[no_mangle]
pub unsafe extern "C" fn crypto_engine_decrypt(
    handle: *mut EngineHandle,
    payload: *const u8,
    payload_len: usize,
    aad: *const u8,
    aad_len: usize,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    if handle.is_null() || payload.is_null() || out.is_null() {
        return ERR_NULL_PTR;
    }
    if aad.is_null() && aad_len != 0 {
        return ERR_NULL_PTR;
    }
    let handle = unsafe { &*handle };
    let pl = unsafe { std::slice::from_raw_parts(payload, payload_len) };
    let aad_slice = if aad_len == 0 {
        &[][..]
    } else {
        unsafe { std::slice::from_raw_parts(aad, aad_len) }
    };
    match handle.inner.decrypt(pl, aad_slice) {
        Ok(plain) => {
            if plain.len() > out_len {
                return ERR_BUF_TOO_SMALL;
            }
            let out_slice = unsafe { std::slice::from_raw_parts_mut(out, plain.len()) };
            out_slice.copy_from_slice(&plain);
            plain.len() as i32
        }
        Err(crate::CryptoError::AuthenticationFailed) => ERR_AUTH_FAILED,
        Err(crate::CryptoError::UnknownAlgorithm(_)) => ERR_UNKNOWN_ALG,
        Err(crate::CryptoError::InvalidNonceLength { .. }) => ERR_KEY_LEN,
        Err(_) => ERR_OTHER,
    }
}

/// Free a handle allocated by [`crypto_engine_new`].
///
/// # Safety
/// `handle` must have been returned by [`crypto_engine_new`] and not yet freed.
#[no_mangle]
pub unsafe extern "C" fn crypto_engine_free(handle: *mut EngineHandle) {
    if !handle.is_null() {
        unsafe { drop(Box::from_raw(handle)) };
    }
}
