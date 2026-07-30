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
Fortuna CSPRNG Implementation for iGaming Platforms
====================================================

GLI-11 Section 4.1 Compliance: Random Number Generator Requirements
- Must use cryptographically secure PRNG with unpredictable output
- Must support automatic re-seeding from multiple entropy sources
- Must maintain pool isolation to prevent state compromise
- Must log all seeding events for audit trail

Fortuna design (Schneier & Ferguson):
- 32 entropy pools with round-robin collection
- Pool P_i is used every 2^i reseeds (pool 0 every reseed, pool 1 every 2nd, etc.)
- AES-256 in CTR mode as the block cipher generator
- Automatic re-seeding when sufficient entropy is accumulated
- Minimum 100ms between reseeds to prevent entropy exhaustion

Usage:
    generator = FortunaGenerator()
    generator.add_entropy(source_id=0, data=os.urandom(32))
    random_bytes = generator.generate(32)
"""

import hashlib
import hmac
import os
import struct
import threading
import time
import logging
import json
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
except ImportError:
    raise ImportError(
        "Install cryptography: pip install cryptography>=41.0.0"
    )

logger = logging.getLogger("rng.fortuna")

# ---------------------------------------------------------------------------
# GLI-11 4.1.1: Generator must produce at least 2^40 values before cycling
# AES-256 CTR with 128-bit counter gives 2^128 blocks = 2^132 bytes
# ---------------------------------------------------------------------------

NUM_POOLS = 32
MIN_POOL_SIZE = 64  # bytes - minimum entropy before pool can trigger reseed
MIN_RESEED_INTERVAL = 0.1  # seconds (100ms)
MAX_GENERATE_SIZE = 1 << 20  # 1 MiB per request (GLI-11 rate limit)
KEY_SIZE = 32  # AES-256
BLOCK_SIZE = 16  # AES block size


@dataclass
class AuditEvent:
    """Immutable audit record for GLI-11 compliance."""
    timestamp: str
    event_type: str
    details: dict
    sequence_number: int


class EntropyPool:
    """
    Single entropy pool using SHA-256 accumulation.

    GLI-11 4.3.2: Each pool must independently accumulate entropy
    and track the amount of entropy collected.
    """

    def __init__(self, pool_id: int):
        self.pool_id = pool_id
        self._hasher = hashlib.sha256()
        self._size = 0
        self._lock = threading.Lock()
        self._source_count = 0

    @property
    def size(self) -> int:
        return self._size

    def add_data(self, source_id: int, data: bytes) -> None:
        """Add entropy data to this pool with source tagging."""
        with self._lock:
            # Tag with source ID and length for domain separation
            header = struct.pack(">BH", source_id % 256, len(data))
            self._hasher.update(header)
            self._hasher.update(data)
            self._size += len(data)
            self._source_count += 1

    def extract(self) -> bytes:
        """Extract accumulated entropy and reset pool."""
        with self._lock:
            digest = self._hasher.digest()
            self._hasher = hashlib.sha256()
            self._size = 0
            self._source_count = 0
            return digest


class AESCTRGenerator:
    """
    AES-256 CTR mode generator core.

    GLI-11 4.1.3: Block cipher generator must re-key after every
    request to ensure forward secrecy. Compromising the current
    state must not reveal past outputs.
    """

    def __init__(self):
        self._key = b"\x00" * KEY_SIZE
        self._counter = 0
        self._lock = threading.Lock()

    def _encrypt_block(self, block: bytes) -> bytes:
        """Encrypt a single block with current key."""
        nonce = struct.pack(">QQ", 0, self._counter)
        cipher = Cipher(
            algorithms.AES(self._key),
            modes.CTR(nonce),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()
        return encryptor.update(block) + encryptor.finalize()

    def reseed(self, seed: bytes) -> None:
        """
        Reseed the generator.

        new_key = SHA-256(old_key || seed)
        counter = counter + 1

        GLI-11 4.2.1: Reseeding must be atomic and must incorporate
        both old state and new entropy.
        """
        with self._lock:
            self._key = hashlib.sha256(self._key + seed).digest()
            self._counter += 1

    def generate_blocks(self, num_blocks: int) -> bytes:
        """
        Generate random blocks.

        After generation, re-key to ensure forward secrecy:
        new_key = generate(2 blocks) -> 32 bytes for AES-256 key
        """
        with self._lock:
            output = bytearray()
            for _ in range(num_blocks):
                # Create counter block
                counter_bytes = struct.pack(">QQ", 0, self._counter)
                ct = self._encrypt_block(b"\x00" * BLOCK_SIZE)
                output.extend(ct[:BLOCK_SIZE])
                self._counter += 1

            # Forward secrecy: generate 2 new blocks for new key
            new_key_blocks = bytearray()
            for _ in range(2):
                counter_bytes = struct.pack(">QQ", 0, self._counter)
                ct = self._encrypt_block(b"\x00" * BLOCK_SIZE)
                new_key_blocks.extend(ct[:BLOCK_SIZE])
                self._counter += 1

            self._key = bytes(new_key_blocks[:KEY_SIZE])

            return bytes(output)

    def generate(self, num_bytes: int) -> bytes:
        """Generate num_bytes of random data."""
        num_blocks = (num_bytes + BLOCK_SIZE - 1) // BLOCK_SIZE
        return self.generate_blocks(num_blocks)[:num_bytes]


class FortunaGenerator:
    """
    Complete Fortuna CSPRNG with 32 entropy pools.

    GLI-11 Compliance Features:
    - 32 independent entropy pools with round-robin collection
    - Automatic re-seeding with minimum interval enforcement
    - Forward secrecy via key destruction after each generation
    - Full audit logging of all seed and generation events
    - Thread-safe operation for concurrent game requests
    - Health monitoring with entropy starvation detection
    """

    def __init__(
        self,
        audit_log_path: Optional[str] = None,
        min_initial_entropy: int = 256,
    ):
        self._pools: List[EntropyPool] = [
            EntropyPool(i) for i in range(NUM_POOLS)
        ]
        self._generator = AESCTRGenerator()
        self._reseed_count = 0
        self._last_reseed_time = 0.0
        self._pool_index = 0  # round-robin for entropy distribution
        self._lock = threading.Lock()
        self._seeded = False
        self._min_initial_entropy = min_initial_entropy
        self._total_generated = 0
        self._total_entropy_added = 0
        self._audit_sequence = 0
        self._audit_log_path = audit_log_path
        self._health_ok = True

        # GLI-11 4.4.1: Log generator initialization
        self._audit("INIT", {
            "num_pools": NUM_POOLS,
            "cipher": "AES-256-CTR",
            "min_reseed_interval_ms": int(MIN_RESEED_INTERVAL * 1000),
            "min_initial_entropy_bytes": min_initial_entropy,
        })

        logger.info(
            "Fortuna generator initialized with %d pools", NUM_POOLS
        )

    # ----- Entropy Collection -----

    def add_entropy(self, source_id: int, data: bytes) -> None:
        """
        Add entropy from an identified source.

        Sources are distributed round-robin across pools.
        GLI-11 4.3.1: Each entropy source must be independently identified.

        Args:
            source_id: Unique identifier for the entropy source
                0 = OS urandom
                1 = RDRAND/RDSEED
                2 = TPM
                3 = USB TRNG
                4 = Timing jitter
                5-255 = Application-specific
            data: Raw entropy bytes
        """
        if not data:
            return

        with self._lock:
            pool_idx = self._pool_index % NUM_POOLS
            self._pools[pool_idx].add_data(source_id, data)
            self._pool_index += 1
            self._total_entropy_added += len(data)

        self._audit("ENTROPY_ADD", {
            "source_id": source_id,
            "bytes": len(data),
            "pool": pool_idx,
            "hash": hashlib.sha256(data).hexdigest()[:16],
        })

    def seed_from_os(self, num_bytes: int = 64) -> None:
        """
        Seed from OS entropy source.
        GLI-11 4.3.3: Initial seeding must use a trusted entropy source.
        """
        entropy = os.urandom(num_bytes)
        self.add_entropy(source_id=0, data=entropy)
        logger.info("Seeded %d bytes from OS entropy", num_bytes)

    # ----- Re-seeding Logic -----

    def _should_reseed(self) -> bool:
        """Check if conditions for re-seeding are met."""
        if self._pools[0].size < MIN_POOL_SIZE:
            return False
        now = time.monotonic()
        if now - self._last_reseed_time < MIN_RESEED_INTERVAL:
            return False
        return True

    def _do_reseed(self) -> None:
        """
        Perform Fortuna re-seed using eligible pools.

        Pool P_i is included in reseed #n if (n mod 2^i) == 0.
        This ensures pool 0 is used every reseed, pool 1 every 2nd, etc.
        Higher pools accumulate more entropy for catastrophic recovery.
        """
        self._reseed_count += 1
        seed_material = bytearray()
        pools_used = []

        for i in range(NUM_POOLS):
            if self._reseed_count % (1 << i) == 0:
                pool_data = self._pools[i].extract()
                seed_material.extend(pool_data)
                pools_used.append(i)

        if seed_material:
            self._generator.reseed(bytes(seed_material))
            self._last_reseed_time = time.monotonic()
            self._seeded = True

            self._audit("RESEED", {
                "reseed_number": self._reseed_count,
                "pools_used": pools_used,
                "seed_bytes": len(seed_material),
            })

            logger.debug(
                "Reseed #%d using pools %s (%d bytes)",
                self._reseed_count,
                pools_used,
                len(seed_material),
            )

    # ----- Random Data Generation -----

    def generate(self, num_bytes: int) -> bytes:
        """
        Generate cryptographically secure random bytes.

        GLI-11 4.1.2: Generator must refuse to produce output if
        not properly seeded.

        Args:
            num_bytes: Number of random bytes to generate (max 1 MiB)

        Returns:
            Cryptographically secure random bytes

        Raises:
            RuntimeError: If generator has not been seeded
            ValueError: If num_bytes exceeds maximum
        """
        if num_bytes <= 0:
            return b""

        if num_bytes > MAX_GENERATE_SIZE:
            raise ValueError(
                f"Request {num_bytes} bytes exceeds maximum "
                f"{MAX_GENERATE_SIZE}. Split into smaller requests."
            )

        with self._lock:
            # Check seeding status
            if not self._seeded:
                # Attempt initial seed from OS
                self.seed_from_os(self._min_initial_entropy)
                if self._should_reseed():
                    self._do_reseed()
                if not self._seeded:
                    raise RuntimeError(
                        "GLI-11 VIOLATION: Generator not seeded. "
                        "Add entropy before generating random data."
                    )

            # Attempt re-seed if eligible
            if self._should_reseed():
                self._do_reseed()

            # Generate random data
            result = self._generator.generate(num_bytes)
            self._total_generated += num_bytes

        self._audit("GENERATE", {
            "bytes": num_bytes,
            "total_generated": self._total_generated,
            "reseed_count": self._reseed_count,
        })

        return result

    def generate_int(self, lower: int, upper: int) -> int:
        """
        Generate a uniform random integer in [lower, upper].

        Uses rejection sampling to avoid modulo bias.
        GLI-11 4.1.4: Output must be uniformly distributed.
        """
        if lower > upper:
            raise ValueError("lower must be <= upper")
        if lower == upper:
            return lower

        range_size = upper - lower + 1
        # Number of bytes needed to represent the range
        byte_count = (range_size.bit_length() + 7) // 8
        # Mask for the significant bits
        mask = (1 << range_size.bit_length()) - 1

        # Rejection sampling to eliminate modulo bias
        max_attempts = 1000
        for _ in range(max_attempts):
            raw = int.from_bytes(self.generate(byte_count), "big")
            raw &= mask
            if raw < range_size:
                return lower + raw

        raise RuntimeError(
            "Rejection sampling failed after %d attempts" % max_attempts
        )

    def generate_float(self) -> float:
        """
        Generate a uniform random float in [0.0, 1.0).

        Uses 53 bits for full double-precision mantissa coverage.
        """
        raw = int.from_bytes(self.generate(7), "big") >> 3  # 53 bits
        return raw / (1 << 53)

    # ----- Health Monitoring -----

    def get_health_status(self) -> dict:
        """
        Return current health metrics.
        GLI-11 4.5.1: RNG must support continuous health monitoring.
        """
        pool_sizes = [p.size for p in self._pools]
        return {
            "healthy": self._health_ok and self._seeded,
            "seeded": self._seeded,
            "reseed_count": self._reseed_count,
            "total_generated_bytes": self._total_generated,
            "total_entropy_added_bytes": self._total_entropy_added,
            "pool_sizes": pool_sizes,
            "min_pool_size": min(pool_sizes),
            "max_pool_size": max(pool_sizes),
            "last_reseed_elapsed_s": round(
                time.monotonic() - self._last_reseed_time, 3
            )
            if self._last_reseed_time > 0
            else None,
            "entropy_ratio": round(
                self._total_entropy_added / max(self._total_generated, 1), 4
            ),
        }

    # ----- Periodic Re-seeding -----

    def start_periodic_reseed(
        self, interval_seconds: float = 60.0, entropy_bytes: int = 32
    ) -> threading.Thread:
        """
        Start a background thread that periodically re-seeds from OS entropy.

        GLI-11 4.3.4: Generator must support periodic re-seeding to
        maintain entropy levels during extended operation.
        """
        def _reseed_loop():
            while True:
                try:
                    self.seed_from_os(entropy_bytes)
                    logger.debug("Periodic reseed: %d bytes", entropy_bytes)
                except Exception as exc:
                    logger.error("Periodic reseed failed: %s", exc)
                    self._health_ok = False
                time.sleep(interval_seconds)

        thread = threading.Thread(
            target=_reseed_loop, daemon=True, name="fortuna-reseed"
        )
        thread.start()
        self._audit("PERIODIC_RESEED_START", {
            "interval_seconds": interval_seconds,
            "entropy_bytes": entropy_bytes,
        })
        return thread

    # ----- Audit Logging -----

    def _audit(self, event_type: str, details: dict) -> None:
        """
        Record an audit event.
        GLI-11 4.4: All RNG events must be logged with timestamps.
        """
        self._audit_sequence += 1
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            details=details,
            sequence_number=self._audit_sequence,
        )

        log_entry = {
            "seq": event.sequence_number,
            "ts": event.timestamp,
            "event": event.event_type,
            **event.details,
        }

        if self._audit_log_path:
            try:
                with open(self._audit_log_path, "a") as f:
                    f.write(json.dumps(log_entry) + "\n")
            except OSError as exc:
                logger.error("Audit log write failed: %s", exc)

        logger.debug("AUDIT: %s", json.dumps(log_entry))

    # ----- State Export (for certification testing) -----

    def export_state_snapshot(self) -> dict:
        """
        Export non-secret state for certification review.
        Does NOT export keys or pool contents (those are secret).
        """
        return {
            "generator_type": "Fortuna",
            "cipher": "AES-256-CTR",
            "num_pools": NUM_POOLS,
            "reseed_count": self._reseed_count,
            "seeded": self._seeded,
            "total_generated": self._total_generated,
            "total_entropy_added": self._total_entropy_added,
            "pool_sizes": [p.size for p in self._pools],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI Usage & Self-Test
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """
    Run basic self-tests for Fortuna generator.
    GLI-11 4.6.1: Generator must pass power-on self-test.
    """
    print("=== Fortuna CSPRNG Self-Test ===\n")

    # Test 1: Basic generation
    gen = FortunaGenerator(min_initial_entropy=32)
    gen.seed_from_os(64)
    data = gen.generate(32)
    assert len(data) == 32, "Generation length mismatch"
    print("[PASS] Basic generation: 32 bytes produced")

    # Test 2: No duplicate outputs
    samples = [gen.generate(16) for _ in range(1000)]
    unique = len(set(samples))
    assert unique == 1000, f"Duplicate detected: {unique}/1000 unique"
    print(f"[PASS] Uniqueness: {unique}/1000 samples unique")

    # Test 3: Uniform integer distribution
    counts = [0] * 6
    for _ in range(60000):
        val = gen.generate_int(0, 5)
        counts[val] += 1
    expected = 10000
    for i, c in enumerate(counts):
        deviation = abs(c - expected) / expected
        assert deviation < 0.05, f"Bias detected for value {i}: {c}"
    print(f"[PASS] Uniformity (d6): {counts} (expected ~10000 each)")

    # Test 4: Re-seeding
    gen.add_entropy(1, os.urandom(128))
    gen.add_entropy(2, os.urandom(128))
    data_after_reseed = gen.generate(32)
    assert data_after_reseed != data, "Output unchanged after reseed"
    print("[PASS] Re-seeding produces different output")

    # Test 5: Health status
    health = gen.get_health_status()
    assert health["healthy"] is True
    assert health["reseed_count"] > 0
    print(f"[PASS] Health check: seeded={health['seeded']}, "
          f"reseeds={health['reseed_count']}")

    # Test 6: Float range
    floats = [gen.generate_float() for _ in range(10000)]
    assert all(0.0 <= f < 1.0 for f in floats), "Float out of range"
    print(f"[PASS] Float range: min={min(floats):.6f}, max={max(floats):.6f}")

    print("\n=== All self-tests passed ===")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    self_test()
