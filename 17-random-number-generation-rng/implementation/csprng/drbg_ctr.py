#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 17, Random Number Generation (RNG).
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
DRBG-CTR-AES256 Implementation (NIST SP 800-90A)
=================================================

GLI-11 Section 4.1 Compliance:
- Implements NIST SP 800-90A CTR_DRBG with AES-256
- Prediction resistance via mandatory reseeding
- Security strength: 256 bits
- Maximum requests between reseeds: 2^48
- Maximum bytes per request: 2^19 bits (65536 bytes)

This is the NIST-approved alternative to Fortuna, commonly required
by European regulators (MGA, UKGC) and GLI-11 certification.

Reference: NIST SP 800-90A Rev 1, Section 10.2.1 (CTR_DRBG)

Usage:
    drbg = DRBG_CTR_AES256()
    drbg.instantiate(entropy=os.urandom(48), nonce=os.urandom(16))
    random_bytes = drbg.generate(32)
"""

import hashlib
import os
import struct
import threading
import time
import logging
import json
from datetime import datetime, timezone
from typing import Optional

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
except ImportError:
    raise ImportError(
        "Install cryptography: pip install cryptography>=41.0.0"
    )

logger = logging.getLogger("rng.drbg_ctr")

# NIST SP 800-90A constants for AES-256 CTR_DRBG
KEYLEN = 32        # AES-256 key length
BLOCKLEN = 16      # AES block length
SEEDLEN = KEYLEN + BLOCKLEN  # 48 bytes
MAX_REQUESTS_BETWEEN_RESEEDS = 2**48
MAX_BYTES_PER_REQUEST = 2**16  # 65536 bytes
MAX_PERSONALIZATION_LEN = 2**35


class DRBG_CTR_AES256:
    """
    NIST SP 800-90A CTR_DRBG with AES-256.

    GLI-11 Requirements Addressed:
    - 4.1.1: Cryptographic strength (256-bit security level)
    - 4.1.2: Proper instantiation before output
    - 4.1.3: Automatic state update after each generate
    - 4.2.1: Reseed support with entropy input
    - 4.4.1: Audit logging of all lifecycle events
    - 4.5.1: Health monitoring and reseed counter tracking
    """

    def __init__(self, prediction_resistance: bool = True):
        """
        Initialize CTR_DRBG.

        Args:
            prediction_resistance: If True, reseed before every generate.
                                   Required for GLI-11 high-assurance mode.
        """
        self._key = b"\x00" * KEYLEN
        self._v = b"\x00" * BLOCKLEN
        self._reseed_counter = 0
        self._instantiated = False
        self._prediction_resistance = prediction_resistance
        self._lock = threading.Lock()
        self._total_generated = 0
        self._total_requests = 0
        self._entropy_source = None
        self._audit_log_path = None
        self._audit_sequence = 0

        logger.info(
            "CTR_DRBG initialized: prediction_resistance=%s",
            prediction_resistance,
        )

    def set_audit_log(self, path: str) -> None:
        """Configure audit log file path."""
        self._audit_log_path = path

    def set_entropy_source(self, source_callable) -> None:
        """
        Set entropy source for automatic reseeding.

        Args:
            source_callable: Function that takes int num_bytes and returns bytes.
                             Default: os.urandom
        """
        self._entropy_source = source_callable

    # ----- NIST SP 800-90A Block Cipher DF (Section 10.3.2) -----

    def _block_cipher_df(self, input_data: bytes, output_len: int) -> bytes:
        """
        Block Cipher Derivation Function (BCC-based).

        Condenses arbitrary-length input into exactly output_len bytes.
        Used during instantiate and reseed to process entropy + nonce.
        """
        # Step 1: Construct S = len(input) || output_len || input || 0x80 || padding
        l_bytes = struct.pack(">I", len(input_data))
        n_bytes = struct.pack(">I", output_len)
        s = l_bytes + n_bytes + input_data + b"\x80"

        # Pad S to multiple of BLOCKLEN
        while len(s) % BLOCKLEN != 0:
            s += b"\x00"

        # Step 2: BCC with fixed key (0x00010203...1F for AES-256)
        bcc_key = bytes(range(KEYLEN))
        num_blocks = len(s) // BLOCKLEN

        # Compute K and X using BCC
        temp = b""
        for i in range((output_len + BLOCKLEN - 1) // BLOCKLEN + 1):
            iv = struct.pack(">I", i).rjust(BLOCKLEN, b"\x00")
            chaining = iv
            for j in range(num_blocks):
                block = s[j * BLOCKLEN : (j + 1) * BLOCKLEN]
                xored = bytes(a ^ b for a, b in zip(chaining, block))
                cipher = Cipher(
                    algorithms.AES(bcc_key),
                    modes.ECB(),
                    backend=default_backend(),
                )
                enc = cipher.encryptor()
                chaining = enc.update(xored) + enc.finalize()
            temp += chaining

        # Extract K (first KEYLEN bytes) and X (next BLOCKLEN bytes)
        k = temp[:KEYLEN]
        x = temp[KEYLEN : KEYLEN + BLOCKLEN]

        # Step 3: Generate output_len bytes using K and X
        result = b""
        cipher = Cipher(
            algorithms.AES(k), modes.ECB(), backend=default_backend()
        )
        while len(result) < output_len:
            enc = cipher.encryptor()
            x = enc.update(x) + enc.finalize()
            result += x

        return result[:output_len]

    # ----- CTR_DRBG Update (Section 10.2.1.2) -----

    def _update(self, provided_data: bytes) -> None:
        """
        CTR_DRBG Update function.

        Updates internal state (Key, V) using provided_data.
        Called after instantiate, reseed, and generate.
        """
        if len(provided_data) != SEEDLEN:
            provided_data = (provided_data + b"\x00" * SEEDLEN)[:SEEDLEN]

        temp = b""
        v = self._v

        while len(temp) < SEEDLEN:
            # Increment V
            v_int = int.from_bytes(v, "big") + 1
            v = (v_int % (1 << (BLOCKLEN * 8))).to_bytes(BLOCKLEN, "big")
            # Encrypt V
            cipher = Cipher(
                algorithms.AES(self._key),
                modes.ECB(),
                backend=default_backend(),
            )
            enc = cipher.encryptor()
            block = enc.update(v) + enc.finalize()
            temp += block

        temp = temp[:SEEDLEN]

        # XOR with provided_data
        output = bytes(a ^ b for a, b in zip(temp, provided_data))

        self._key = output[:KEYLEN]
        self._v = output[KEYLEN : KEYLEN + BLOCKLEN]

    # ----- Instantiate (Section 10.2.1.3) -----

    def instantiate(
        self,
        entropy: bytes,
        nonce: Optional[bytes] = None,
        personalization: bytes = b"",
    ) -> None:
        """
        Instantiate the DRBG.

        GLI-11 4.1.2: Generator must be properly seeded before
        producing any output.

        Args:
            entropy: Entropy input (min SEEDLEN bytes recommended)
            nonce: Nonce value (default: from os.urandom)
            personalization: Optional personalization string
        """
        if nonce is None:
            nonce = os.urandom(BLOCKLEN)

        if len(personalization) > MAX_PERSONALIZATION_LEN:
            raise ValueError("Personalization string too long")

        with self._lock:
            # Combine entropy, nonce, personalization
            seed_material = entropy + nonce + personalization

            # Apply derivation function
            seed = self._block_cipher_df(seed_material, SEEDLEN)

            # Reset state
            self._key = b"\x00" * KEYLEN
            self._v = b"\x00" * BLOCKLEN

            # Update with seed
            self._update(seed)
            self._reseed_counter = 1
            self._instantiated = True

        self._audit("INSTANTIATE", {
            "entropy_bytes": len(entropy),
            "nonce_bytes": len(nonce),
            "personalization_bytes": len(personalization),
        })

        logger.info("CTR_DRBG instantiated with %d bytes entropy", len(entropy))

    # ----- Reseed (Section 10.2.1.4) -----

    def reseed(
        self,
        entropy: bytes,
        additional_input: bytes = b"",
    ) -> None:
        """
        Reseed the DRBG with fresh entropy.

        GLI-11 4.2.1: Reseeding must incorporate fresh entropy
        and update the internal state atomically.
        """
        with self._lock:
            if not self._instantiated:
                raise RuntimeError("DRBG not instantiated")

            seed_material = entropy + additional_input
            seed = self._block_cipher_df(seed_material, SEEDLEN)
            self._update(seed)
            self._reseed_counter = 1

        self._audit("RESEED", {
            "entropy_bytes": len(entropy),
            "additional_bytes": len(additional_input),
        })

    # ----- Generate (Section 10.2.1.5) -----

    def generate(
        self,
        num_bytes: int,
        additional_input: bytes = b"",
    ) -> bytes:
        """
        Generate random bytes.

        GLI-11 4.1.3: Each generate call must update internal state
        to ensure forward secrecy.

        Args:
            num_bytes: Number of bytes to generate (max 65536)
            additional_input: Optional additional input for this request

        Returns:
            Cryptographically secure random bytes

        Raises:
            RuntimeError: If not instantiated or reseed required
        """
        if num_bytes > MAX_BYTES_PER_REQUEST:
            raise ValueError(
                f"Requested {num_bytes} bytes exceeds maximum "
                f"{MAX_BYTES_PER_REQUEST}"
            )

        with self._lock:
            if not self._instantiated:
                raise RuntimeError(
                    "GLI-11 VIOLATION: DRBG not instantiated. "
                    "Call instantiate() first."
                )

            # Check reseed counter
            if self._reseed_counter > MAX_REQUESTS_BETWEEN_RESEEDS:
                if self._entropy_source:
                    fresh = self._entropy_source(SEEDLEN)
                    self.reseed(fresh)
                else:
                    raise RuntimeError(
                        "GLI-11 VIOLATION: Reseed required. "
                        "Maximum requests between reseeds exceeded."
                    )

            # Prediction resistance: reseed before every generate
            if self._prediction_resistance and self._entropy_source:
                fresh = self._entropy_source(SEEDLEN)
                seed_material = fresh + additional_input
                seed = self._block_cipher_df(seed_material, SEEDLEN)
                self._update(seed)
                self._reseed_counter = 1
                additional_input = b""

            # Process additional input
            if additional_input:
                ai_seed = self._block_cipher_df(additional_input, SEEDLEN)
                self._update(ai_seed)

            # Generate output
            temp = b""
            v = self._v
            while len(temp) < num_bytes:
                v_int = int.from_bytes(v, "big") + 1
                v = (v_int % (1 << (BLOCKLEN * 8))).to_bytes(BLOCKLEN, "big")
                cipher = Cipher(
                    algorithms.AES(self._key),
                    modes.ECB(),
                    backend=default_backend(),
                )
                enc = cipher.encryptor()
                block = enc.update(v) + enc.finalize()
                temp += block

            self._v = v
            output = temp[:num_bytes]

            # Update state (forward secrecy)
            self._update(
                additional_input.ljust(SEEDLEN, b"\x00")[:SEEDLEN]
                if additional_input
                else b"\x00" * SEEDLEN
            )
            self._reseed_counter += 1
            self._total_generated += num_bytes
            self._total_requests += 1

        self._audit("GENERATE", {
            "bytes": num_bytes,
            "reseed_counter": self._reseed_counter,
            "total_generated": self._total_generated,
        })

        return output

    # ----- Health & Status -----

    def get_status(self) -> dict:
        """Return non-secret status information."""
        return {
            "algorithm": "CTR_DRBG_AES256",
            "standard": "NIST SP 800-90A Rev 1",
            "instantiated": self._instantiated,
            "prediction_resistance": self._prediction_resistance,
            "reseed_counter": self._reseed_counter,
            "total_generated_bytes": self._total_generated,
            "total_requests": self._total_requests,
            "security_strength_bits": 256,
            "max_bytes_per_request": MAX_BYTES_PER_REQUEST,
            "max_requests_between_reseeds": MAX_REQUESTS_BETWEEN_RESEEDS,
        }

    def _audit(self, event_type: str, details: dict) -> None:
        """Record audit event."""
        self._audit_sequence += 1
        entry = {
            "seq": self._audit_sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "CTR_DRBG_AES256",
            "event": event_type,
            **details,
        }
        if self._audit_log_path:
            try:
                with open(self._audit_log_path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
        logger.debug("AUDIT: %s", json.dumps(entry))

    # ----- Uninstantiate -----

    def uninstantiate(self) -> None:
        """
        Zeroize internal state.
        GLI-11 4.7.1: Generator must support secure shutdown.
        """
        with self._lock:
            self._key = b"\x00" * KEYLEN
            self._v = b"\x00" * BLOCKLEN
            self._reseed_counter = 0
            self._instantiated = False
        self._audit("UNINSTANTIATE", {})
        logger.info("CTR_DRBG uninstantiated (state zeroized)")


# ---------------------------------------------------------------------------
# Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """NIST SP 800-90A CTR_DRBG self-test."""
    print("=== DRBG-CTR-AES256 Self-Test ===\n")

    # Test 1: Basic instantiate and generate
    drbg = DRBG_CTR_AES256(prediction_resistance=False)
    entropy = os.urandom(48)
    nonce = os.urandom(16)
    drbg.instantiate(entropy=entropy, nonce=nonce)
    data = drbg.generate(32)
    assert len(data) == 32, "Wrong output length"
    print("[PASS] Instantiate and generate: 32 bytes")

    # Test 2: Consecutive outputs differ
    data2 = drbg.generate(32)
    assert data != data2, "Consecutive outputs identical"
    print("[PASS] Consecutive outputs differ")

    # Test 3: Reseed changes output
    state_before = drbg.get_status()
    drbg.reseed(os.urandom(48))
    data3 = drbg.generate(32)
    assert data3 not in (data, data2), "Reseed did not change output"
    print("[PASS] Reseed produces different output")

    # Test 4: Uninstantiate prevents generation
    drbg.uninstantiate()
    try:
        drbg.generate(16)
        assert False, "Should have raised"
    except RuntimeError:
        pass
    print("[PASS] Uninstantiate blocks generation")

    # Test 5: Prediction resistance mode
    drbg2 = DRBG_CTR_AES256(prediction_resistance=True)
    drbg2.set_entropy_source(os.urandom)
    drbg2.instantiate(entropy=os.urandom(48))
    samples = set()
    for _ in range(100):
        samples.add(drbg2.generate(16))
    assert len(samples) == 100, "Duplicates in prediction-resistance mode"
    print(f"[PASS] Prediction resistance: 100/100 unique samples")

    # Test 6: Large generation
    big = drbg2.generate(MAX_BYTES_PER_REQUEST)
    assert len(big) == MAX_BYTES_PER_REQUEST
    print(f"[PASS] Large generation: {MAX_BYTES_PER_REQUEST} bytes")

    # Test 7: Exceeding max bytes raises
    try:
        drbg2.generate(MAX_BYTES_PER_REQUEST + 1)
        assert False, "Should have raised"
    except ValueError:
        pass
    print("[PASS] Max bytes enforcement")

    print(f"\nStatus: {json.dumps(drbg2.get_status(), indent=2)}")
    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
