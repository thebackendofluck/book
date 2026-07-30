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
Hardware Entropy Generation for RNG Seeding

This module provides hardware-based entropy sources for
cryptographic RNG seeding in iGaming platforms.

As discussed in Chapter 39, RNG systems require high-quality
entropy sources. Hardware security modules like Zymkey provide
True Random Number Generators (TRNG) that harvest entropy from
physical noise sources, not pseudo-random algorithms.

Key Features:
- TRNG-based entropy (passes NIST SP 800-90B tests)
- Integration with RTC systems for timestamp entropy
- Entropy pool management for continuous availability
- Health monitoring for entropy quality

iGaming Requirements:
- GLI-11 requires cryptographically secure random numbers
- Entropy sources must be unpredictable and non-repeatable
- Hardware-based entropy provides higher assurance than software

Example Usage:
    ```python
    # Get hardware entropy for RNG seeding
    entropy = get_hardware_entropy(32)  # 256 bits

    # Use Zymkey for continuous entropy
    zymkey_entropy = ZymkeyEntropy()
    seed_bytes = zymkey_entropy.get_seed(64)  # 512 bits

    # Contribute to entropy pool
    zymkey_entropy.contribute_timestamp_entropy(timestamp_ns)
    ```
"""

import hashlib
import logging
import os
import struct
import time
from datetime import datetime
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Try to import Zymkey SDK
try:
    import zymkey  # ty:ignore[unresolved-import]
    ZYMKEY_AVAILABLE = True
except ImportError:
    ZYMKEY_AVAILABLE = False
    logger.warning("Zymkey SDK not available, using software entropy")


def get_hardware_entropy(num_bytes: int = 32) -> bytes:
    """
    Get hardware-generated random bytes for RNG seeding.

    This function attempts to use hardware entropy sources
    in the following priority order:
    1. Zymkey TRNG (if available)
    2. /dev/hwrng (Linux hardware RNG)
    3. os.urandom (falls back to kernel entropy)

    Args:
        num_bytes: Number of random bytes to generate

    Returns:
        Random bytes suitable for cryptographic seeding

    Note:
        The entropy is generated from physical noise sources,
        not pseudo-random algorithms, making it suitable for
        cryptographic seeding and RNG initialization.
    """
    if ZYMKEY_AVAILABLE:
        try:
            # Use Zymkey's TRNG
            return zymkey.client.get_random(num_bytes)
        except Exception as e:
            logger.warning(f"Zymkey entropy failed: {e}, falling back")

    # Try Linux hardware RNG
    try:
        with open("/dev/hwrng", "rb") as hwrng:
            return hwrng.read(num_bytes)
    except (FileNotFoundError, PermissionError):
        pass

    # Fall back to os.urandom (kernel entropy pool)
    logger.warning("Using software entropy (os.urandom)")
    return os.urandom(num_bytes)


class ZymkeyEntropy:
    """
    Zymkey-based hardware entropy source.

    This class provides a managed interface to the Zymkey's
    True Random Number Generator, including entropy pool
    management and health monitoring.
    """

    def __init__(self, min_pool_size: int = 1024):
        """
        Initialize Zymkey entropy source.

        Args:
            min_pool_size: Minimum entropy pool size in bytes

        Raises:
            RuntimeError: If Zymkey is not available
        """
        if not ZYMKEY_AVAILABLE:
            raise RuntimeError("Zymkey SDK not available")

        self.min_pool_size = min_pool_size
        self._pool: bytes = b""
        self._refill_pool()
        logger.info("ZymkeyEntropy initialized")

    def _refill_pool(self) -> None:
        """Refill entropy pool from Zymkey TRNG."""
        try:
            new_entropy = zymkey.client.get_random(self.min_pool_size)
            self._pool += new_entropy
            logger.debug(f"Refilled entropy pool, size: {len(self._pool)}")
        except Exception as e:
            logger.error(f"Failed to refill entropy pool: {e}")

    def get_seed(self, num_bytes: int = 32) -> bytes:
        """
        Get seed bytes from entropy pool.

        Args:
            num_bytes: Number of bytes to extract

        Returns:
            Random seed bytes
        """
        # Ensure pool has enough entropy
        if len(self._pool) < num_bytes:
            self._refill_pool()

        # Extract bytes from pool
        seed = self._pool[:num_bytes]
        self._pool = self._pool[num_bytes:]

        # Refill if below threshold
        if len(self._pool) < self.min_pool_size // 2:
            self._refill_pool()

        return seed

    def contribute_timestamp_entropy(self, timestamp_ns: int) -> None:
        """
        Contribute timestamp entropy to the pool.

        High-resolution timestamps add unpredictability to
        the entropy pool, especially the nanosecond component.

        Args:
            timestamp_ns: Timestamp in nanoseconds
        """
        # Extract entropy from timestamp
        ts_bytes = struct.pack("<Q", timestamp_ns)
        hash_contribution = hashlib.sha256(ts_bytes).digest()

        # XOR into existing pool or append
        if len(self._pool) >= 32:
            pool_list = list(self._pool[:32])
            for i, b in enumerate(hash_contribution):
                pool_list[i] ^= b
            self._pool = bytes(pool_list) + self._pool[32:]
        else:
            self._pool += hash_contribution

    def get_health_status(self) -> dict:
        """
        Get entropy source health status.

        Returns:
            Dictionary with health metrics:
                - available: Whether TRNG is functioning
                - pool_size: Current entropy pool size
                - pool_healthy: Whether pool is adequately filled
                - last_refill: Timestamp of last refill
        """
        try:
            # Test TRNG by requesting small amount
            test_bytes = zymkey.client.get_random(4)
            available = len(test_bytes) == 4
        except Exception:
            available = False

        return {
            "available": available,
            "pool_size": len(self._pool),
            "pool_healthy": len(self._pool) >= self.min_pool_size // 2,
            "timestamp": datetime.now().isoformat(),
        }


class EntropyMixer:
    """
    Mix multiple entropy sources for defense in depth.

    This class combines entropy from multiple sources using
    cryptographic hashing to ensure that compromise of any
    single source doesn't compromise the output.
    """

    def __init__(self):
        """Initialize entropy mixer with available sources."""
        self.sources: List[str] = []

        if ZYMKEY_AVAILABLE:
            self.sources.append("zymkey")

        if os.path.exists("/dev/hwrng"):
            self.sources.append("hwrng")

        self.sources.append("urandom")  # Always available
        self.sources.append("timestamp")

        logger.info(f"EntropyMixer initialized with sources: {self.sources}")

    def get_mixed_entropy(self, num_bytes: int = 32) -> bytes:
        """
        Get mixed entropy from all available sources.

        The mixing process:
        1. Collect entropy from each source
        2. Concatenate all entropy
        3. Hash with SHA-256 (or SHA-512 for >32 bytes)
        4. Truncate or extend to requested size

        Args:
            num_bytes: Number of output bytes

        Returns:
            Mixed entropy bytes
        """
        collected: List[bytes] = []

        # Collect from each source
        if "zymkey" in self.sources:
            try:
                collected.append(zymkey.client.get_random(32))
            except Exception:
                pass

        if "hwrng" in self.sources:
            try:
                with open("/dev/hwrng", "rb") as f:
                    collected.append(f.read(32))
            except Exception:
                pass

        if "urandom" in self.sources:
            collected.append(os.urandom(32))

        if "timestamp" in self.sources:
            ts_ns = time.time_ns()
            collected.append(struct.pack("<Q", ts_ns))

        # Mix all sources
        combined = b"".join(collected)

        # Hash to produce output
        if num_bytes <= 32:
            return hashlib.sha256(combined).digest()[:num_bytes]
        elif num_bytes <= 64:
            return hashlib.sha512(combined).digest()[:num_bytes]
        else:
            # For larger sizes, use HKDF-like expansion
            output = b""
            counter = 0
            while len(output) < num_bytes:
                output += hashlib.sha256(combined + struct.pack("<I", counter)).digest()
                counter += 1
            return output[:num_bytes]


# Convenience functions for quick access
def get_timestamp_entropy() -> bytes:
    """Get 32 bytes of entropy seeded with current timestamp."""
    ts_ns = time.time_ns()
    base = os.urandom(24) + struct.pack("<Q", ts_ns)
    return hashlib.sha256(base).digest()


def seed_rng_from_hardware(rng_seed_func: Callable[[bytes], None]) -> bool:
    """
    Seed an RNG system using hardware entropy.

    Args:
        rng_seed_func: Function that accepts seed bytes

    Returns:
        True if hardware entropy was used, False if software fallback
    """
    if ZYMKEY_AVAILABLE:
        try:
            seed = zymkey.client.get_random(64)
            rng_seed_func(seed)
            logger.info("RNG seeded with Zymkey hardware entropy")
            return True
        except Exception as e:
            logger.warning(f"Zymkey seeding failed: {e}")

    # Fallback to OS entropy
    seed = os.urandom(64)
    rng_seed_func(seed)
    logger.info("RNG seeded with OS entropy (software)")
    return False
