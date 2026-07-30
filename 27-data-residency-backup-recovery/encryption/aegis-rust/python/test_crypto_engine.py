# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Unit tests for the Python wrapper."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from crypto_engine import CryptoEngine, CryptoError


class TestCryptoEngine(unittest.TestCase):
    def test_aegis128l_roundtrip(self):
        e = CryptoEngine.aegis128l(os.urandom(16))
        pt = b"high-throughput game event"
        payload = e.encrypt(pt, b"ctx=x")
        self.assertEqual(payload[0], CryptoEngine.AEGIS_128L)
        self.assertEqual(e.decrypt(payload, b"ctx=x"), pt)

    def test_aad_mismatch_rejects(self):
        e = CryptoEngine.aegis128l(os.urandom(16))
        payload = e.encrypt(b"balance=5000", b"user=A")
        with self.assertRaises(CryptoError):
            e.decrypt(payload, b"user=B")

    def test_tampered_rejects(self):
        e = CryptoEngine.aegis128l(os.urandom(16))
        payload = bytearray(e.encrypt(b"secret payload", b"aad"))
        payload[-1] ^= 0xFF
        with self.assertRaises(CryptoError):
            e.decrypt(bytes(payload), b"aad")

    def test_all_algorithms(self):
        for ctor, keylen in [
            (CryptoEngine.aegis128l, 16),
            (CryptoEngine.aegis256, 32),
            (CryptoEngine.aes256gcm, 32),
            (CryptoEngine.chacha20poly1305, 32),
        ]:
            e = ctor(os.urandom(keylen))
            payload = e.encrypt(b"hello", b"ctx")
            self.assertEqual(e.decrypt(payload, b"ctx"), b"hello")

    def test_invalid_key_length(self):
        with self.assertRaises(ValueError):
            CryptoEngine.aegis128l(b"short")


if __name__ == "__main__":
    unittest.main()
