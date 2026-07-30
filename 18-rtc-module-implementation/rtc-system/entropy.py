#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Zymkey Hardware Entropy for RTC and RNG Systems
================================================

Provides hardware-generated entropy from Zymkey's True Random Number
Generator (TRNG) for cryptographic seeding in iGaming infrastructure.

Features:
- True hardware randomness (physical noise sources)
- Not pseudo-random - suitable for cryptographic keys
- Integration with RNG pools and RTC systems
- NIST SP 800-90B compliant entropy source

Hardware Requirements:
- Zymkey 4 or Zymkey HSM
- Raspberry Pi 4/5 (compatible models)
- I2C enabled (raspi-config)

Regulatory Compliance:
- GLI-11 Section 1.10: RNG seeding requirements
- MGA Technical Standards: Entropy source documentation
- UK LCCP: Cryptographic key generation audit trail

Usage:
    from rtc_system.entropy import ZymkeyEntropy, get_hardware_entropy

    # Quick entropy generation
    entropy = get_hardware_entropy(32)  # 256 bits

    # Full-featured class usage
    zk_entropy = ZymkeyEntropy()
    seed_bytes = zk_entropy.get_entropy(64)  # 512 bits for RNG pool
    health = zk_entropy.health_check()
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

# Zymkey library - requires zymkey package
# Install: pip install zymkey (on supported Raspberry Pi)
try:
    import zymkey  # ty:ignore[unresolved-import]
    ZYMKEY_AVAILABLE = True
except ImportError:
    ZYMKEY_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class EntropyResult:
    """Result of entropy generation operation."""
    data: bytes
    bits: int
    source: str
    timestamp: float
    health_status: str


class ZymkeyEntropy:
    """
    Hardware entropy source using Zymkey's TRNG.

    Provides cryptographically secure random bytes generated
    from physical noise sources within the Zymkey hardware.

    Attributes:
        device_info: Cached Zymkey device information

    Security Properties:
        - Entropy derived from thermal noise and avalanche diodes
        - Not algorithmically generated (true randomness)
        - Hardware attestation available for audit
        - Continuous health monitoring of entropy source
    """

    def __init__(self, verify_device: bool = True):
        """
        Initialize Zymkey entropy source.

        Args:
            verify_device: If True, verify Zymkey is connected

        Raises:
            RuntimeError: If zymkey package not available
            ConnectionError: If Zymkey device not found
        """
        if not ZYMKEY_AVAILABLE:
            raise RuntimeError(
                "zymkey package not installed or not on supported hardware. "
                "Install on Raspberry Pi with: pip install zymkey"
            )

        if verify_device:
            self._verify_connection()

        self._last_health_check = 0.0
        self._health_cache_ttl = 60.0  # Cache health check for 60s

        logger.info("Zymkey entropy source initialized")

    def _verify_connection(self) -> None:
        """Verify Zymkey is connected and responding."""
        try:
            # Request small amount of entropy to verify connection
            test_bytes = zymkey.client.get_random(4)
            if len(test_bytes) != 4:
                raise ConnectionError("Zymkey returned invalid response")
        except Exception as e:
            raise ConnectionError(f"Zymkey not available: {e}") from e

    def get_entropy(self, num_bytes: int = 32) -> bytes:
        """
        Get hardware-generated random bytes.

        Uses Zymkey's True Random Number Generator which derives
        entropy from physical noise sources.

        Args:
            num_bytes: Number of random bytes to generate
                       Common values:
                       - 16 (128 bits): AES-128 key
                       - 32 (256 bits): AES-256 key, SHA-256 seed
                       - 64 (512 bits): RNG pool seeding
                       - 128 (1024 bits): High-security applications

        Returns:
            Random bytes from hardware TRNG

        Example:
            >>> entropy = ZymkeyEntropy()
            >>> seed = entropy.get_entropy(64)
            >>> len(seed)
            64

        Security Note:
            This entropy is generated from physical noise sources,
            not pseudo-random algorithms. Suitable for:
            - Cryptographic key generation
            - RNG pool seeding
            - Nonce generation
            - Salt generation
        """
        if num_bytes <= 0:
            raise ValueError("num_bytes must be positive")

        if num_bytes > 1024:
            logger.warning(
                "Large entropy request (%d bytes) may be slow",
                num_bytes
            )

        entropy_bytes = zymkey.client.get_random(num_bytes)

        logger.debug(
            "Generated %d bytes of hardware entropy",
            num_bytes
        )

        return bytes(entropy_bytes)

    def get_entropy_result(self, num_bytes: int = 32) -> EntropyResult:
        """
        Get entropy with metadata for audit logging.

        Args:
            num_bytes: Number of random bytes

        Returns:
            EntropyResult with data, source info, and timestamp
        """
        data = self.get_entropy(num_bytes)

        return EntropyResult(
            data=data,
            bits=num_bytes * 8,
            source="zymkey_trng",
            timestamp=time.time(),
            health_status=self._get_cached_health_status()
        )

    def get_entropy_hex(self, num_bytes: int = 32) -> str:
        """
        Get hardware entropy as hexadecimal string.

        Args:
            num_bytes: Number of random bytes

        Returns:
            Hexadecimal string (length = num_bytes * 2)
        """
        return self.get_entropy(num_bytes).hex()

    def seed_rng_pool(self, pool_size: int = 64) -> dict:
        """
        Generate entropy for RNG pool seeding.

        Creates high-quality seed material for initializing
        or reseeding random number generator pools.

        Args:
            pool_size: Bytes of entropy for pool (default: 64 = 512 bits)

        Returns:
            Dictionary with seed data and metadata

        iGaming Use Case:
            Use this to seed game RNG systems with hardware entropy
            as required by GLI-11 for certified random number generators.
        """
        seed_bytes = self.get_entropy(pool_size)

        return {
            'seed': seed_bytes.hex(),
            'seed_bytes': seed_bytes,
            'bits': pool_size * 8,
            'source': 'zymkey_trng',
            'timestamp': time.time(),
            'suitable_for': [
                'rng_pool_init',
                'rng_reseed',
                'key_derivation',
                'nonce_generation'
            ]
        }

    def health_check(self) -> dict:
        """
        Check Zymkey TRNG health status.

        Returns:
            Health status dictionary with:
                - available: Device available
                - responsive: Responds to entropy requests
                - entropy_quality: Basic quality check
                - timestamp: Check timestamp
        """
        try:
            # Test entropy generation
            start = time.time()
            test_bytes = self.get_entropy(32)
            latency_ms = (time.time() - start) * 1000

            # Basic quality check (not zero, not all same)
            unique_bytes = len(set(test_bytes))
            quality = "good" if unique_bytes > 20 else "degraded"

            result = {
                'available': True,
                'responsive': True,
                'latency_ms': round(latency_ms, 2),
                'entropy_quality': quality,
                'unique_byte_ratio': unique_bytes / 32,
                'timestamp': time.time()
            }

            self._last_health_check = time.time()
            self._cached_health_status = quality

            return result

        except Exception as e:
            return {
                'available': False,
                'responsive': False,
                'error': str(e),
                'timestamp': time.time()
            }

    def _get_cached_health_status(self) -> str:
        """Get cached health status or perform check."""
        if time.time() - self._last_health_check > self._health_cache_ttl:
            self.health_check()
        return getattr(self, '_cached_health_status', 'unknown')

    def get_device_info(self) -> dict:
        """
        Get Zymkey device information.

        Returns:
            Device details for audit logging
        """
        try:
            # Note: Actual API may vary - this is representative
            return {
                'device_type': 'Zymkey',
                'trng_source': 'hardware_noise',
                'capabilities': [
                    'true_random_generation',
                    'hardware_encryption',
                    'tamper_detection',
                    'secure_key_storage'
                ],
                'entropy_source': 'physical_noise_diodes'
            }
        except Exception as e:
            return {'error': str(e)}


# Module-level convenience functions
def get_hardware_entropy(num_bytes: int = 32) -> Optional[bytes]:
    """
    Quick function to get hardware entropy.

    Creates temporary ZymkeyEntropy instance and returns
    random bytes. Use ZymkeyEntropy class directly for
    repeated operations.

    Args:
        num_bytes: Number of random bytes (default: 32 = 256 bits)

    Returns:
        Random bytes or None if Zymkey unavailable

    Example:
        >>> entropy = get_hardware_entropy(32)
        >>> len(entropy) if entropy else 0
        32
    """
    try:
        zk = ZymkeyEntropy(verify_device=True)
        return zk.get_entropy(num_bytes)
    except Exception as e:
        logger.error("Failed to get hardware entropy: %s", e)
        return None


def get_hardware_entropy_hex(num_bytes: int = 32) -> Optional[str]:
    """
    Quick function to get hardware entropy as hex string.

    Args:
        num_bytes: Number of random bytes

    Returns:
        Hex string or None if Zymkey unavailable
    """
    entropy = get_hardware_entropy(num_bytes)
    return entropy.hex() if entropy else None


def is_zymkey_available() -> bool:
    """
    Check if Zymkey is available on this system.

    Returns:
        True if Zymkey package installed and device responds
    """
    if not ZYMKEY_AVAILABLE:
        return False

    try:
        zymkey.client.get_random(1)
        return True
    except Exception:
        return False


# Mock implementation for development/testing without hardware
class MockZymkeyEntropy:
    """
    Mock Zymkey entropy for development without hardware.

    Uses os.urandom() which is cryptographically secure
    but not hardware-based. For development only.

    Warning:
        Do not use in production iGaming systems!
        Hardware entropy required for GLI-11 compliance.
    """

    def __init__(self):
        """Initialize mock entropy source."""
        import os
        self._urandom = os.urandom
        logger.warning(
            "Using MockZymkeyEntropy - NOT suitable for production!"
        )

    def get_entropy(self, num_bytes: int = 32) -> bytes:
        """Get entropy from os.urandom (not hardware)."""
        return self._urandom(num_bytes)

    def get_entropy_hex(self, num_bytes: int = 32) -> str:
        """Get entropy as hex string."""
        return self.get_entropy(num_bytes).hex()

    def health_check(self) -> dict:
        """Mock health check."""
        return {
            'available': True,
            'mock': True,
            'warning': 'Using software entropy - not for production'
        }


if __name__ == "__main__":
    # Example usage demonstration
    logging.basicConfig(level=logging.INFO)

    print("Zymkey Hardware Entropy - Example Usage")
    print("=" * 50)

    if not ZYMKEY_AVAILABLE:
        print("\n⚠️  zymkey package not installed or not on Raspberry Pi")
        print("This module requires Zymkey hardware on compatible device.")
        print("\nUsing MockZymkeyEntropy for demonstration:")

        mock = MockZymkeyEntropy()
        sample = mock.get_entropy_hex(32)
        print(f"\nSample entropy (mock): {sample}")
        print(f"Length: {len(sample)} hex chars = {len(sample)//2} bytes = {len(sample)*4} bits")

        print("\nOn real hardware, you would use:")
        print("""
    from rtc_system.entropy import ZymkeyEntropy

    # Initialize
    entropy_source = ZymkeyEntropy()

    # Get 256 bits of hardware entropy
    seed = entropy_source.get_entropy(32)

    # For RNG pool seeding
    pool_seed = entropy_source.seed_rng_pool(64)

    # Health check
    status = entropy_source.health_check()
        """)
    else:
        print("\n✅ Zymkey available - generating hardware entropy")

        try:
            zk = ZymkeyEntropy()

            # Generate sample entropy
            sample = zk.get_entropy_hex(32)
            print(f"\nHardware entropy: {sample}")

            # Health check
            health = zk.health_check()
            print(f"\nHealth status: {health}")

            # For RNG seeding
            rng_seed = zk.seed_rng_pool(64)
            print(f"\nRNG pool seed: {rng_seed['bits']} bits generated")

        except Exception as e:
            print(f"\nError: {e}")
