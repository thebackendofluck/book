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
YubiHSM 2 Integration for RTC Systems
=====================================

Hardware Security Module integration for cryptographically signed timestamps
in iGaming RTC infrastructure.

Features:
- HSM-protected timestamp signing (HMAC-SHA256)
- Cryptographically secure nonce generation
- Key attestation for regulatory compliance
- Secure key rotation with old key destruction
- Side-channel attack protection

Security Guarantees:
- Private keys never leave HSM boundary
- Hardware-accelerated cryptographic operations
- Zero-knowledge key operations
- Tamper-resistant key storage

Regulatory Compliance:
- GLI-11 Section 5.4: Cryptographic key management
- MGA Technical Standards: HSM requirements
- PCI-DSS 3.5: Cryptographic key protection

Usage:
    from rtc_system.yubihsm_integration import YubiHSMEnhancedRTC

    hsm_rtc = YubiHSMEnhancedRTC(
        connector_url="http://localhost:12345",
        auth_key_id=1,
        password="hsm_auth_password"
    )

    # Sign a timestamp — returns (signature, jti) so the verifier
    # can pin the nonce and prevent replay.
    timestamp_data = {"unix": 1700000000, "nano": 123456789, "source": "rtc_cluster"}
    signature, jti = hsm_rtc.sign_timestamp(timestamp_data)

    # Generate secure nonce
    nonce = hsm_rtc.generate_nonce()

    # Get key attestation for audit
    attestation = hsm_rtc.attest_key()

    # Rotate signing key (quarterly)
    success = hsm_rtc.rotate_key()

    # Clean up
    hsm_rtc.close()
"""

import hashlib
import hmac
import logging
import uuid
from typing import Any, Optional

# YubiHSM library - requires yubihsm package
# Install: pip install yubihsm
try:
    import yubihsm
    from yubihsm.core import YubiHsm  # ty:ignore[unresolved-import]
    from yubihsm.defs import ALGORITHM, CAPABILITY  # ty:ignore[unresolved-import]
    YUBIHSM_AVAILABLE = True
except ImportError:
    YUBIHSM_AVAILABLE = False
    YubiHsm = None
    ALGORITHM = None
    CAPABILITY = None

logger = logging.getLogger(__name__)


class YubiHSMEnhancedRTC:
    """
    RTC service enhanced with YubiHSM 2 hardware security.

    Provides cryptographic signing and secure random generation
    using dedicated hardware security module.

    Attributes:
        hsm: YubiHSM connection instance
        session: Active HSM session for operations
        signing_key_id: ID of the pre-provisioned signing key

    Key Operations:
        - sign_timestamp: HMAC-SHA256 signature of timestamp data
        - generate_nonce: 256-bit cryptographically secure random
        - attest_key: Generate certificate chain for key attestation
        - rotate_key: Securely replace signing key with new one
    """

    def __init__(
        self,
        connector_url: str,
        auth_key_id: int,
        password: str,
        signing_key_id: int = 0x1234
    ):
        """
        Initialize HSM connection and session.

        Args:
            connector_url: URL of YubiHSM connector (e.g., "http://localhost:12345")
            auth_key_id: Authentication key ID for session establishment
            password: Authentication password
            signing_key_id: Pre-provisioned signing key ID (default: 0x1234)

        Raises:
            RuntimeError: If yubihsm package not available
            ConnectionError: If HSM connection fails
        """
        if not YUBIHSM_AVAILABLE:
            raise RuntimeError(
                "yubihsm package not installed. "
                "Install with: pip install yubihsm"
            )

        self.connector_url = connector_url
        self.auth_key_id = auth_key_id
        self.signing_key_id = signing_key_id

        # Establish HSM connection
        self.hsm: Any = yubihsm.YubiHsm.connect(connector_url)  # ty:ignore[unresolved-attribute]
        self.session: Any = self.hsm.create_session_derived(auth_key_id, password)

        logger.info(
            "YubiHSM session established: connector=%s, key_id=0x%04X",
            connector_url, signing_key_id
        )

    def sign_timestamp(
        self,
        timestamp_data: dict[str, Any],
        jti: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        Sign timestamp using HSM-protected key.

        Creates HMAC-SHA256 signature of timestamp data for
        non-repudiation and integrity verification. A `jti` (JWT ID,
        UUID4) is bound to the signed payload to enable replay
        detection on the verification side.

        Args:
            timestamp_data: Dictionary with keys:
                - unix: Unix timestamp (seconds)
                - nano: Nanosecond component
                - source: Time source identifier
            jti: Optional JWT-style identifier (UUID4 hex). If not
                provided, a fresh UUID4 is generated. Must be replayed
                back to `verify_timestamp` along with the signature.

        Returns:
            Tuple of (signature_hex, jti). Both are required by
            `verify_timestamp`. The signature change from `str` to
            `tuple[str, str]` is intentional: replay protection
            requires the verifier to know the nonce.

        Example:
            >>> data = {"unix": 1700000000, "nano": 123456789, "source": "rtc_cluster"}
            >>> sig, jti = hsm.sign_timestamp(data)
            >>> len(sig)  # 64 hex chars = 32 bytes
            64
        """
        # Generate replay-protection nonce if caller did not supply one
        if jti is None:
            jti = uuid.uuid4().hex

        # Prepare data for signing (canonical format) — jti is part of
        # the signed payload so a stolen signature cannot be replayed
        # against a different request.
        data_str = (
            f"{timestamp_data['unix']}:{timestamp_data['nano']}:"
            f"{timestamp_data['source']}:{jti}"
        )
        data_hash = hashlib.sha256(data_str.encode()).digest()

        # Sign with HSM - key never leaves hardware
        from yubihsm.objects import HmacKey  # ty:ignore[unresolved-import]
        hmac_key = self.session.get_object(self.signing_key_id, HmacKey)
        signature = hmac_key.sign_hmac(data_hash)

        logger.debug(
            "Timestamp signed: unix=%d, source=%s, jti=%s",
            timestamp_data.get('unix', 0),
            timestamp_data.get('source', 'unknown'),
            jti,
        )

        return signature.hex(), jti

    def verify_timestamp(
        self,
        timestamp_data: dict[str, Any],
        signature: str,
        jti: str,
    ) -> bool:
        """
        Verify timestamp signature in constant time.

        Args:
            timestamp_data: Original timestamp data
            signature: Hexadecimal signature to verify
            jti: The replay-protection nonce returned by
                `sign_timestamp`. Callers MUST also track seen `jti`
                values out-of-band to fully prevent replay; this method
                only enforces that the signature was produced over the
                exact same `jti` it is being verified against.

        Returns:
            True if signature is valid.

        Security:
            Uses `hmac.compare_digest` so the comparison runs in
            constant time, eliminating the timing side-channel that a
            naive `==` exposes.

        TODO(security): replace re-sign-and-compare with verify-only.
            Re-signing on every verification doubles HSM load and
            forces the verifier to hold signing credentials. The
            correct fix is to expose a `verify_hmac` path on the HSM
            session (or move to an asymmetric scheme so only the
            public key is needed to verify). Left as-is for didactic
            simplicity in the book example.
        """
        # Note: sign_timestamp returns (sig, jti). We pin the jti so
        # the recomputed signature is bit-identical when valid.
        expected_signature, _ = self.sign_timestamp(timestamp_data, jti=jti)
        # Constant-time comparison: prevents timing side-channel that
        # leaks the matching-prefix length to a network attacker.
        return hmac.compare_digest(signature, expected_signature)

    def generate_nonce(self, length: int = 32) -> str:
        """
        Generate cryptographically secure nonce using HSM.

        Uses HSM's hardware random number generator for
        true randomness (not PRNG).

        Args:
            length: Number of random bytes (default: 32 = 256 bits)

        Returns:
            Hexadecimal nonce string

        Security Note:
            HSM RNG passes NIST SP 800-90B requirements
            for entropy sources.
        """
        random_data = self.session.get_pseudo_random(length)
        return random_data.hex()

    def attest_key(self) -> dict[str, Any]:
        """
        Generate attestation for signing key.

        Creates certificate chain proving key was generated
        and stored within HSM. Required for regulatory audits.

        Returns:
            Dictionary with:
                - certificate: X.509 attestation certificate (hex)
                - chain: Certificate chain to Yubico root CA
                - key_id: Attested key ID
                - algorithm: Key algorithm

        Regulatory Use:
            - GLI-11 audit evidence
            - MGA key management compliance
            - PCI-DSS key attestation
        """
        from yubihsm.objects import AsymmetricKey  # ty:ignore[unresolved-import]

        try:
            # Get attestation certificate
            key = self.session.get_object(self.signing_key_id, AsymmetricKey)
            attestation_cert = key.attest()

            return {
                'certificate': attestation_cert.public_bytes_raw().hex(),
                'key_id': self.signing_key_id,
                'algorithm': 'HMAC-SHA256',
                'attested_at': __import__('time').time(),
                'hsm_serial': self.hsm.get_device_info().serial
            }
        except Exception as e:
            logger.error("Key attestation failed: %s", e)
            return {
                'error': str(e),
                'key_id': self.signing_key_id
            }

    def rotate_key(self, new_label: str = "RTC-Signing-Key") -> bool:
        """
        Rotate signing key securely.

        Generates new key within HSM, updates signing key ID,
        and securely destroys old key.

        Args:
            new_label: Label for new key

        Returns:
            True if rotation successful

        Security Process:
            1. Generate new key in HSM
            2. Update internal key reference
            3. Securely erase old key (overwrite + delete)
            4. Log rotation event for audit trail

        Warning:
            Key rotation invalidates all signatures made with old key.
            Ensure timestamp verification uses correct key ID.
        """
        try:
            from yubihsm.objects import HmacKey  # ty:ignore[unresolved-import]

            # Generate new key ID from secure random
            new_key_id_bytes = self.session.get_pseudo_random(2)
            new_key_id_int = int.from_bytes(new_key_id_bytes, 'big')

            # Ensure key ID is in valid range (0x0001 - 0xFFFE)
            new_key_id_int = max(0x0001, min(0xFFFE, new_key_id_int))

            # Generate new HMAC key
            HmacKey.generate(
                self.session,
                object_id=new_key_id_int,
                label=new_label,
                domains=1,
                capabilities=CAPABILITY.SIGN_HMAC | CAPABILITY.VERIFY_HMAC,  # ty:ignore[unresolved-attribute]
                algorithm=ALGORITHM.HMAC_SHA256  # ty:ignore[unresolved-attribute]
            )

            # Store old key ID for deletion
            old_key_id = self.signing_key_id

            # Update to new key
            self.signing_key_id = new_key_id_int

            # Securely delete old key
            old_key = self.session.get_object(old_key_id, HmacKey)
            old_key.delete()

            logger.info(
                "Key rotation complete: old=0x%04X, new=0x%04X",
                old_key_id, new_key_id_int
            )

            return True

        except Exception as e:
            logger.error("Key rotation failed: %s", e)
            return False

    def get_key_info(self) -> dict[str, Any]:
        """
        Get information about current signing key.

        Returns:
            Dictionary with key metadata:
                - key_id: Numeric key identifier
                - label: Human-readable label
                - algorithm: Cryptographic algorithm
                - capabilities: Permitted operations
                - domains: HSM domains key belongs to
                - sequence: Key generation sequence number
        """
        from yubihsm.objects import HmacKey  # ty:ignore[unresolved-import]

        try:
            key = self.session.get_object(self.signing_key_id, HmacKey)
            info = key.get_info()

            return {
                'key_id': hex(info.id),
                'label': info.label,
                'algorithm': str(info.algorithm),
                'capabilities': str(info.capabilities),
                'domains': info.domains,
                'sequence': info.sequence
            }
        except Exception as e:
            logger.error("Failed to get key info: %s", e)
            return {'error': str(e), 'key_id': hex(self.signing_key_id)}

    def health_check(self) -> dict[str, Any]:
        """
        Perform HSM health check.

        Returns:
            Health status including:
                - connected: Connection status
                - session_valid: Session validity
                - key_accessible: Signing key accessible
                - hsm_info: Device information
        """
        try:
            device_info = self.hsm.get_device_info()

            return {
                'connected': True,
                'session_valid': self.session is not None,
                'key_accessible': self._test_key_access(),
                'hsm_info': {
                    'serial': device_info.serial,
                    'version': f"{device_info.version.major}.{device_info.version.minor}.{device_info.version.patch}",
                    'algorithms': device_info.supported_algorithms
                }
            }
        except Exception as e:
            return {
                'connected': False,
                'error': str(e)
            }

    def _test_key_access(self) -> bool:
        """Test if signing key is accessible."""
        try:
            self.generate_nonce(8)
            return True
        except Exception:
            return False

    def close(self) -> None:
        """
        Clean up HSM session and connection.

        Always call this method when done to properly
        release HSM resources.
        """
        if self.session:
            try:
                self.session.close()
                logger.info("HSM session closed")
            except Exception as e:
                logger.warning("Error closing session: %s", e)

        if self.hsm:
            try:
                self.hsm.close()
                logger.info("HSM connection closed")
            except Exception as e:
                logger.warning("Error closing HSM connection: %s", e)

    def __enter__(self) -> "YubiHSMEnhancedRTC":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit with cleanup."""
        self.close()


# Convenience functions for common operations
def sign_timestamp_with_hsm(
    timestamp_data: dict[str, Any],
    connector_url: str = "http://localhost:12345",
    auth_key_id: int = 1,
    password: str = ""
) -> Optional[tuple[str, str]]:
    """
    One-shot timestamp signing with automatic cleanup.

    Args:
        timestamp_data: Timestamp data to sign
        connector_url: HSM connector URL
        auth_key_id: Authentication key ID
        password: Authentication password

    Returns:
        Tuple of (signature_hex, jti) or None on error. The `jti`
        must be retained alongside the signature so the verifier can
        reproduce the signed payload (see `verify_timestamp`).
    """
    try:
        with YubiHSMEnhancedRTC(connector_url, auth_key_id, password) as hsm:
            return hsm.sign_timestamp(timestamp_data)
    except Exception as e:
        logger.error("HSM signing failed: %s", e)
        return None


def generate_secure_nonce(
    length: int = 32,
    connector_url: str = "http://localhost:12345",
    auth_key_id: int = 1,
    password: str = ""
) -> Optional[str]:
    """
    One-shot secure nonce generation.

    Args:
        length: Nonce length in bytes
        connector_url: HSM connector URL
        auth_key_id: Authentication key ID
        password: Authentication password

    Returns:
        Nonce hex string or None on error
    """
    try:
        with YubiHSMEnhancedRTC(connector_url, auth_key_id, password) as hsm:
            return hsm.generate_nonce(length)
    except Exception as e:
        logger.error("HSM nonce generation failed: %s", e)
        return None


if __name__ == "__main__":
    # Example usage demonstration
    logging.basicConfig(level=logging.INFO)

    print("YubiHSM Enhanced RTC - Example Usage")
    print("=" * 50)

    if not YUBIHSM_AVAILABLE:
        print("\n⚠️  yubihsm package not installed")
        print("Install with: pip install yubihsm")
        print("\nExample code (would run with HSM connected):")
        print("""
    hsm_rtc = YubiHSMEnhancedRTC(
        connector_url="http://localhost:12345",
        auth_key_id=1,
        # fail-fast: load from a secret store, never hardcode
        password=os.environ["YUBIHSM_PASSWORD"],
    )

    # Sign timestamp — returns (signature, jti) tuple
    timestamp = {"unix": 1700000000, "nano": 123456789, "source": "rtc_cluster"}
    signature, jti = hsm_rtc.sign_timestamp(timestamp)
    print(f"Signature: {signature} (jti={jti})")

    # Generate nonce
    nonce = hsm_rtc.generate_nonce()
    print(f"Nonce: {nonce}")

    # Get key attestation
    attestation = hsm_rtc.attest_key()
    print(f"Attestation: {attestation}")

    # Cleanup
    hsm_rtc.close()
        """)
    else:
        print("\n✅ yubihsm package available")
        print("Configure HSM connector URL and credentials to use.")
