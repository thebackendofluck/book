# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Python wrapper for crypto-engine (Rust) via ctypes.

Usage:
    from crypto_engine import CryptoEngine
    engine = CryptoEngine.aegis128l(os.urandom(16))
    payload = engine.encrypt(b"sensitive data", aad=b"context")
    plain = engine.decrypt(payload, aad=b"context")

Load path:
    The shared library is located via env var CRYPTO_ENGINE_LIB, or by
    searching common release directories next to this file.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import c_int, c_size_t, c_uint8, c_void_p, POINTER

_ERR_MESSAGES = {
    -1: "null pointer",
    -2: "invalid key length",
    -3: "unknown algorithm",
    -4: "authentication failed",
    -5: "output buffer too small",
    -6: "other error",
}


class CryptoError(Exception):
    """Raised on any crypto failure."""


def _find_library() -> str:
    env = os.environ.get("CRYPTO_ENGINE_LIB")
    if env and os.path.exists(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "crypto-engine", "target", "release", "libcrypto_engine.dylib"),
        os.path.join(here, "..", "crypto-engine", "target", "release", "libcrypto_engine.so"),
        os.path.join(here, "..", "crypto-engine", "target", "release", "crypto_engine.dll"),
        os.path.join(here, "libcrypto_engine.dylib"),
        os.path.join(here, "libcrypto_engine.so"),
    ]
    for c in candidates:
        c = os.path.normpath(c)
        if os.path.exists(c):
            return c
    raise FileNotFoundError(
        "crypto-engine shared library not found. Set CRYPTO_ENGINE_LIB or "
        "run `cargo build --release` in crypto-engine/."
    )


_lib = ctypes.CDLL(_find_library())

_lib.crypto_engine_new.argtypes = [c_uint8, POINTER(c_uint8), c_size_t]
_lib.crypto_engine_new.restype = c_void_p

_lib.crypto_engine_encrypt.argtypes = [
    c_void_p, POINTER(c_uint8), c_size_t,
    POINTER(c_uint8), c_size_t,
    POINTER(c_uint8), c_size_t,
]
_lib.crypto_engine_encrypt.restype = c_int

_lib.crypto_engine_decrypt.argtypes = [
    c_void_p, POINTER(c_uint8), c_size_t,
    POINTER(c_uint8), c_size_t,
    POINTER(c_uint8), c_size_t,
]
_lib.crypto_engine_decrypt.restype = c_int

_lib.crypto_engine_free.argtypes = [c_void_p]
_lib.crypto_engine_free.restype = None


def _as_ptr(b: bytes):
    if not b:
        return None
    return (c_uint8 * len(b)).from_buffer_copy(b)


class CryptoEngine:
    """High-level interface to a crypto-engine handle."""

    # alg_id constants (match Rust AlgId)
    AEGIS_128L = 0x01
    AEGIS_256 = 0x02
    AES_256_GCM = 0x03
    CHACHA20_POLY1305 = 0x04

    def __init__(self, alg: int, key: bytes):
        if alg == self.AEGIS_128L and len(key) != 16:
            raise ValueError("AEGIS-128L requires a 16-byte key")
        if alg in (self.AEGIS_256, self.AES_256_GCM, self.CHACHA20_POLY1305) and len(key) != 32:
            raise ValueError("32-byte key required for this algorithm")
        buf = (c_uint8 * len(key)).from_buffer_copy(key)
        self._handle = _lib.crypto_engine_new(alg, buf, len(key))
        if not self._handle:
            raise CryptoError("failed to create engine (invalid key or algorithm)")
        self._alg = alg

    @classmethod
    def aegis128l(cls, key: bytes) -> "CryptoEngine":
        return cls(cls.AEGIS_128L, key)

    @classmethod
    def aegis256(cls, key: bytes) -> "CryptoEngine":
        return cls(cls.AEGIS_256, key)

    @classmethod
    def aes256gcm(cls, key: bytes) -> "CryptoEngine":
        return cls(cls.AES_256_GCM, key)

    @classmethod
    def chacha20poly1305(cls, key: bytes) -> "CryptoEngine":
        return cls(cls.CHACHA20_POLY1305, key)

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        # Max overhead: 1 alg + 32 nonce + 16 tag = 49
        out_len = len(plaintext) + 64
        out = (c_uint8 * out_len)()
        n = _lib.crypto_engine_encrypt(
            self._handle,
            _as_ptr(plaintext), len(plaintext),
            _as_ptr(aad), len(aad),
            out, out_len,
        )
        if n < 0:
            raise CryptoError(f"encrypt failed: {_ERR_MESSAGES.get(n, str(n))}")
        return bytes(out[:n])

    def decrypt(self, payload: bytes, aad: bytes = b"") -> bytes:
        out_len = len(payload)  # plaintext is always <= ciphertext
        out = (c_uint8 * out_len)()
        n = _lib.crypto_engine_decrypt(
            self._handle,
            _as_ptr(payload), len(payload),
            _as_ptr(aad), len(aad),
            out, out_len,
        )
        if n < 0:
            raise CryptoError(f"decrypt failed: {_ERR_MESSAGES.get(n, str(n))}")
        return bytes(out[:n])

    def close(self):
        if self._handle:
            _lib.crypto_engine_free(self._handle)
            self._handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == "__main__":
    import os as _os
    key = _os.urandom(16)
    e = CryptoEngine.aegis128l(key)
    p = e.encrypt(b"hello iGaming", b"ctx=demo")
    print(f"payload: {p.hex()} ({len(p)} bytes)")
    assert e.decrypt(p, b"ctx=demo") == b"hello iGaming"
    try:
        e.decrypt(p, b"ctx=wrong")
        print("FAIL: wrong AAD accepted", file=sys.stderr)
        sys.exit(1)
    except CryptoError:
        pass
    print("ok")
