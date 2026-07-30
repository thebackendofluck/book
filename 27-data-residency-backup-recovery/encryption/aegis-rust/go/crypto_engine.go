// Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// Package cryptoengine wraps the crypto-engine Rust library via CGO.
//
// Build requires CGO_LDFLAGS to point at the shared library directory:
//
//	export CGO_LDFLAGS="-L/path/to/crypto-engine/target/release -lcrypto_engine"
//	export LD_LIBRARY_PATH="/path/to/crypto-engine/target/release"  # Linux
//	export DYLD_LIBRARY_PATH="/path/to/crypto-engine/target/release" # macOS
//	go build .
package cryptoengine

/*
#cgo LDFLAGS: -lcrypto_engine
#include <stdint.h>
#include <stdlib.h>

typedef struct EngineHandle EngineHandle;

EngineHandle* crypto_engine_new(uint8_t alg, const uint8_t* key, size_t key_len);
int crypto_engine_encrypt(EngineHandle* h,
    const uint8_t* pt, size_t pt_len,
    const uint8_t* aad, size_t aad_len,
    uint8_t* out, size_t out_len);
int crypto_engine_decrypt(EngineHandle* h,
    const uint8_t* pl, size_t pl_len,
    const uint8_t* aad, size_t aad_len,
    uint8_t* out, size_t out_len);
void crypto_engine_free(EngineHandle* h);
*/
import "C"

import (
	"errors"
	"fmt"
	"runtime"
	"unsafe"
)

// Algorithm identifiers (match Rust AlgId).
const (
	AlgAegis128L        uint8 = 0x01
	AlgAegis256         uint8 = 0x02
	AlgAes256Gcm        uint8 = 0x03
	AlgChaCha20Poly1305 uint8 = 0x04
)

// ErrAuthFailed is returned when a ciphertext fails authentication (wrong
// key, tampered payload, or mismatched AAD).
var ErrAuthFailed = errors.New("crypto-engine: authentication failed")

// Engine is a handle to a Rust-side crypto engine.
type Engine struct {
	handle *C.EngineHandle
}

// NewAegis128L creates an AEGIS-128L engine with a 16-byte key.
func NewAegis128L(key []byte) (*Engine, error) {
	return newEngine(AlgAegis128L, key, 16)
}

// NewAegis256 creates an AEGIS-256 engine with a 32-byte key.
func NewAegis256(key []byte) (*Engine, error) {
	return newEngine(AlgAegis256, key, 32)
}

// NewAes256Gcm creates an AES-256-GCM engine with a 32-byte key.
func NewAes256Gcm(key []byte) (*Engine, error) {
	return newEngine(AlgAes256Gcm, key, 32)
}

// NewChaCha20Poly1305 creates a ChaCha20-Poly1305 engine with a 32-byte key.
func NewChaCha20Poly1305(key []byte) (*Engine, error) {
	return newEngine(AlgChaCha20Poly1305, key, 32)
}

func newEngine(alg uint8, key []byte, expectedLen int) (*Engine, error) {
	if len(key) != expectedLen {
		return nil, fmt.Errorf("crypto-engine: key must be %d bytes, got %d", expectedLen, len(key))
	}
	var keyPtr *C.uint8_t
	if len(key) > 0 {
		keyPtr = (*C.uint8_t)(unsafe.Pointer(&key[0]))
	}
	h := C.crypto_engine_new(C.uint8_t(alg), keyPtr, C.size_t(len(key)))
	if h == nil {
		return nil, errors.New("crypto-engine: failed to create engine")
	}
	e := &Engine{handle: h}
	runtime.SetFinalizer(e, func(e *Engine) { e.Close() })
	return e, nil
}

// Encrypt produces a self-describing wire payload:
// [alg_id | nonce | ciphertext | tag].
func (e *Engine) Encrypt(plaintext, aad []byte) ([]byte, error) {
	outLen := len(plaintext) + 64 // max AEAD overhead
	out := make([]byte, outLen)
	var ptPtr, aadPtr *C.uint8_t
	if len(plaintext) > 0 {
		ptPtr = (*C.uint8_t)(unsafe.Pointer(&plaintext[0]))
	}
	if len(aad) > 0 {
		aadPtr = (*C.uint8_t)(unsafe.Pointer(&aad[0]))
	}
	outPtr := (*C.uint8_t)(unsafe.Pointer(&out[0]))
	n := C.crypto_engine_encrypt(
		e.handle,
		ptPtr, C.size_t(len(plaintext)),
		aadPtr, C.size_t(len(aad)),
		outPtr, C.size_t(outLen),
	)
	if n < 0 {
		return nil, fmt.Errorf("crypto-engine: encrypt failed (code=%d)", int(n))
	}
	return out[:int(n)], nil
}

// Decrypt verifies and decrypts a wire payload.
func (e *Engine) Decrypt(payload, aad []byte) ([]byte, error) {
	if len(payload) == 0 {
		return nil, errors.New("crypto-engine: empty payload")
	}
	outLen := len(payload)
	out := make([]byte, outLen)
	plPtr := (*C.uint8_t)(unsafe.Pointer(&payload[0]))
	var aadPtr *C.uint8_t
	if len(aad) > 0 {
		aadPtr = (*C.uint8_t)(unsafe.Pointer(&aad[0]))
	}
	outPtr := (*C.uint8_t)(unsafe.Pointer(&out[0]))
	n := C.crypto_engine_decrypt(
		e.handle,
		plPtr, C.size_t(len(payload)),
		aadPtr, C.size_t(len(aad)),
		outPtr, C.size_t(outLen),
	)
	if n == -4 {
		return nil, ErrAuthFailed
	}
	if n < 0 {
		return nil, fmt.Errorf("crypto-engine: decrypt failed (code=%d)", int(n))
	}
	return out[:int(n)], nil
}

// Close releases the Rust-side handle. Safe to call multiple times.
func (e *Engine) Close() {
	if e.handle != nil {
		C.crypto_engine_free(e.handle)
		e.handle = nil
	}
}
