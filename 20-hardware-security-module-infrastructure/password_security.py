# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
password_security.py — CasinoPasswordSecurity: Argon2id hashing with bcrypt migration.

Implements OWASP 2024-recommended Argon2id password hashing for the on-premises
Python platform (PostgreSQL TDE host tde-pg001 and MariaDB TDE host tde-maria001).

Stores passwords in PHC string format:
  $argon2id$v=19$m=65536,t=3,p=4$<base64-salt>$<base64-hash>

Parameters (OWASP 2024 minimum for Argon2id):
  m = 65536  — 64 MB memory per hash attempt
  t = 3      — 3 iterations
  p = 4      — 4 parallel threads

GPU resistance: A100 GPU (24 GB VRAM) can compute ~375 Argon2id hashes/second
(24 GB / 64 MB = 375 parallel lanes).  Compare to ~1.5M bcrypt or ~10B SHA-256
hashes/second on the same hardware.  A 10-character password breach is effectively
worthless for offline cracking — the cost exceeds $10M in cloud GPU time.

Migration path:
  Legacy bcrypt hashes (prefix $2b$, $2a$, $2y$) are verified on login and
  transparently re-hashed to Argon2id.  No forced password reset required.

Usage:
    from password_security import CasinoPasswordSecurity

    svc = CasinoPasswordSecurity()
    stored_hash = svc.hash_password("hunter2")
    ok, new_hash = svc.verify_and_migrate(stored_hash, "hunter2")
    # ok=True, new_hash=None (already Argon2id)

    # Bcrypt migration — first login after migration
    old_hash = "$2b$12$..."
    ok, new_hash = svc.verify_and_migrate(old_hash, "hunter2")
    # ok=True, new_hash="$argon2id$..." → persist new_hash to DB

Reference: Chapter 20 — Hardware Security Module Infrastructure / Password Hashing
Script path: new-platform/app/auth/password_security.py
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# argon2-cffi — https://pypi.org/project/argon2-cffi/
try:
    from argon2 import PasswordHasher, Type as Argon2Type
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    _ARGON2_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ARGON2_AVAILABLE = False

# bcrypt — https://pypi.org/project/bcrypt/
try:
    import bcrypt as _bcrypt_lib
    _BCRYPT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _BCRYPT_AVAILABLE = False

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — OWASP 2024
# ---------------------------------------------------------------------------

ARGON2_MEMORY_COST: int = 65_536   # 64 MB (kibibytes)
ARGON2_TIME_COST: int = 3          # iterations
ARGON2_PARALLELISM: int = 4        # threads
ARGON2_HASH_LEN: int = 32          # output bytes
ARGON2_SALT_LEN: int = 16          # salt bytes

# Minimum acceptable password length (PCI-DSS 8.3.6 / NIST SP 800-63B)
MIN_PASSWORD_LENGTH: int = 8

# Bcrypt work factor — preserved during legacy verification (not used for new hashes)
BCRYPT_WORK_FACTOR: int = 12

# PBKDF2 fallback (Cloudflare Workers export compatibility, OWASP 2024 minimum)
PBKDF2_ITERATIONS: int = 600_000
PBKDF2_HASH: str = "sha256"
PBKDF2_SALT_LEN: int = 16
PBKDF2_DK_LEN: int = 32


class HashAlgorithm(str, Enum):
    ARGON2ID = "argon2id"
    BCRYPT = "bcrypt"
    PBKDF2 = "pbkdf2"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a password verification attempt."""
    verified: bool
    algorithm: HashAlgorithm
    needs_migration: bool
    new_hash: Optional[str]  # Non-None when needs_migration=True and verified=True


class PasswordStrengthError(ValueError):
    """Raised when the provided password does not meet minimum strength requirements."""


class CasinoPasswordSecurity:
    """
    Password hashing service for the on-premises iGaming platform.

    Implements Argon2id as the primary algorithm with transparent bcrypt migration.
    Designed for use with PostgreSQL (tde-pg001) and MariaDB (tde-maria001) where
    the password_hash column stores PHC-format strings.

    The class requires `argon2-cffi` for production use.  If only legacy hashes
    need verification, `bcrypt` is required for bcrypt hashes.  A pure-stdlib
    PBKDF2 fallback is available for testing environments without compiled deps.
    """

    def __init__(
        self,
        memory_cost: int = ARGON2_MEMORY_COST,
        time_cost: int = ARGON2_TIME_COST,
        parallelism: int = ARGON2_PARALLELISM,
        hash_len: int = ARGON2_HASH_LEN,
        salt_len: int = ARGON2_SALT_LEN,
        min_length: int = MIN_PASSWORD_LENGTH,
    ) -> None:
        self._memory_cost = memory_cost
        self._time_cost = time_cost
        self._parallelism = parallelism
        self._hash_len = hash_len
        self._salt_len = salt_len
        self._min_length = min_length

        if _ARGON2_AVAILABLE:
            self._hasher = PasswordHasher(
                memory_cost=memory_cost,
                time_cost=time_cost,
                parallelism=parallelism,
                hash_len=hash_len,
                salt_len=salt_len,
                type=Argon2Type.ID,
            )
        else:  # pragma: no cover
            self._hasher = None
            log.warning(
                "argon2-cffi not installed — Argon2id hashing unavailable. "
                "Install with: pip install argon2-cffi"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def hash_password(self, password: str) -> str:
        """
        Hash a plaintext password using Argon2id.

        Returns the PHC-format hash string:
            $argon2id$v=19$m=65536,t=3,p=4$<base64-salt>$<base64-hash>

        Raises:
            PasswordStrengthError: If the password is too short.
            RuntimeError: If argon2-cffi is not installed.
        """
        self._validate_strength(password)

        if not _ARGON2_AVAILABLE or self._hasher is None:
            raise RuntimeError(
                "argon2-cffi is required for hashing. "
                "Install with: pip install argon2-cffi"
            )

        return self._hasher.hash(password)

    def verify_password(self, stored_hash: str, password: str) -> bool:
        """
        Verify a password against a stored hash.

        Supports Argon2id, bcrypt, and PBKDF2 hashes.  Does NOT migrate.
        Use verify_and_migrate() for login flows that should upgrade algorithms.

        Returns True if the password matches, False otherwise.
        """
        try:
            result = self.verify_and_migrate(stored_hash, password)
            return result.verified
        except Exception:  # noqa: BLE001
            return False

    def verify_and_migrate(self, stored_hash: str, password: str) -> VerificationResult:
        """
        Verify a password and transparently migrate legacy hashes to Argon2id.

        Algorithm:
          1. Detect the hash algorithm by prefix.
          2. Verify the password using the appropriate algorithm.
          3. If verified AND the algorithm is not Argon2id, generate a new Argon2id hash.
          4. Return VerificationResult with needs_migration=True and new_hash set.

        Callers (login endpoints) should persist new_hash to the DB when it is not None:

            result = svc.verify_and_migrate(player.password_hash, entered_password)
            if result.verified:
                if result.new_hash:
                    db.update_password_hash(player.id, result.new_hash)
                grant_session(player)

        Raises:
            Exception: Only for unexpected internal errors, not for wrong passwords.
        """
        algorithm = self._detect_algorithm(stored_hash)
        log.debug("verify_and_migrate algorithm=%s", algorithm.value)

        if algorithm == HashAlgorithm.ARGON2ID:
            return self._verify_argon2id(stored_hash, password)

        if algorithm == HashAlgorithm.BCRYPT:
            return self._verify_bcrypt(stored_hash, password)

        if algorithm == HashAlgorithm.PBKDF2:
            return self._verify_pbkdf2(stored_hash, password)

        log.warning("Unknown hash algorithm for stored_hash prefix")
        return VerificationResult(
            verified=False,
            algorithm=HashAlgorithm.UNKNOWN,
            needs_migration=False,
            new_hash=None,
        )

    def check_needs_rehash(self, stored_hash: str) -> bool:
        """
        Return True if the stored hash should be upgraded to current parameters.

        Checks both algorithm (non-Argon2id) and parameter drift (older Argon2id
        hashes with lower m/t values than the current configuration).
        """
        algorithm = self._detect_algorithm(stored_hash)
        if algorithm != HashAlgorithm.ARGON2ID:
            return True
        if not _ARGON2_AVAILABLE or self._hasher is None:
            return False
        return self._hasher.check_needs_rehash(stored_hash)

    def validate_strength(self, password: str) -> None:
        """
        Validate password strength.  Raises PasswordStrengthError if weak.

        Rules (PCI-DSS 8.3.6 / NIST SP 800-63B):
          - Minimum 8 characters
          - No whitespace-only passwords
        """
        self._validate_strength(password)

    # ------------------------------------------------------------------
    # Algorithm detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_algorithm(stored_hash: str) -> HashAlgorithm:
        """Detect the hash algorithm by inspecting the stored_hash prefix."""
        if stored_hash.startswith("$argon2id$"):
            return HashAlgorithm.ARGON2ID
        if stored_hash.startswith(("$2b$", "$2a$", "$2y$")):
            return HashAlgorithm.BCRYPT
        # PBKDF2 hashes encoded as: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
        if stored_hash.startswith("pbkdf2_sha256$"):
            return HashAlgorithm.PBKDF2
        return HashAlgorithm.UNKNOWN

    # ------------------------------------------------------------------
    # Argon2id verification
    # ------------------------------------------------------------------

    def _verify_argon2id(self, stored_hash: str, password: str) -> VerificationResult:
        if not _ARGON2_AVAILABLE or self._hasher is None:
            raise RuntimeError("argon2-cffi required for Argon2id verification")
        try:
            self._hasher.verify(stored_hash, password)
        except VerifyMismatchError:
            return VerificationResult(
                verified=False,
                algorithm=HashAlgorithm.ARGON2ID,
                needs_migration=False,
                new_hash=None,
            )
        except (VerificationError, InvalidHashError) as exc:
            log.warning("Argon2id verification error: %s", exc)
            return VerificationResult(
                verified=False,
                algorithm=HashAlgorithm.ARGON2ID,
                needs_migration=False,
                new_hash=None,
            )

        needs_rehash = self._hasher.check_needs_rehash(stored_hash)
        new_hash: Optional[str] = None
        if needs_rehash:
            try:
                new_hash = self.hash_password(password)
                log.info("Argon2id parameter upgrade triggered")
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to re-hash during parameter upgrade: %s", exc)

        return VerificationResult(
            verified=True,
            algorithm=HashAlgorithm.ARGON2ID,
            needs_migration=needs_rehash,
            new_hash=new_hash,
        )

    # ------------------------------------------------------------------
    # Bcrypt verification and migration
    # ------------------------------------------------------------------

    def _verify_bcrypt(self, stored_hash: str, password: str) -> VerificationResult:
        if not _BCRYPT_AVAILABLE:
            raise RuntimeError("bcrypt package required for legacy bcrypt verification")

        password_bytes = password.encode("utf-8")
        stored_bytes = stored_hash.encode("utf-8")

        try:
            matched = _bcrypt_lib.checkpw(password_bytes, stored_bytes)
        except Exception as exc:  # noqa: BLE001
            log.warning("bcrypt verification error: %s", exc)
            return VerificationResult(
                verified=False,
                algorithm=HashAlgorithm.BCRYPT,
                needs_migration=False,
                new_hash=None,
            )

        if not matched:
            return VerificationResult(
                verified=False,
                algorithm=HashAlgorithm.BCRYPT,
                needs_migration=False,
                new_hash=None,
            )

        # Verified — migrate to Argon2id
        new_hash: Optional[str] = None
        if _ARGON2_AVAILABLE and self._hasher is not None:
            try:
                new_hash = self.hash_password(password)
                log.info("bcrypt → Argon2id migration triggered for player")
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to generate Argon2id hash during bcrypt migration: %s", exc)

        return VerificationResult(
            verified=True,
            algorithm=HashAlgorithm.BCRYPT,
            needs_migration=True,
            new_hash=new_hash,
        )

    # ------------------------------------------------------------------
    # PBKDF2 verification and migration (legacy compatibility)
    # ------------------------------------------------------------------

    def _verify_pbkdf2(self, stored_hash: str, password: str) -> VerificationResult:
        """
        Verify a PBKDF2-SHA256 hash in Django-compatible format:
            pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>
        """
        import base64

        parts = stored_hash.split("$")
        if len(parts) != 4:
            return VerificationResult(
                verified=False,
                algorithm=HashAlgorithm.PBKDF2,
                needs_migration=False,
                new_hash=None,
            )

        _, iterations_str, salt_b64, hash_b64 = parts

        try:
            iterations = int(iterations_str)
            salt = base64.b64decode(salt_b64 + "==")
            expected_dk = base64.b64decode(hash_b64 + "==")
        except (ValueError, Exception) as exc:  # noqa: BLE001
            log.warning("PBKDF2 hash parsing error: %s", exc)
            return VerificationResult(
                verified=False,
                algorithm=HashAlgorithm.PBKDF2,
                needs_migration=False,
                new_hash=None,
            )

        dk = hashlib.pbkdf2_hmac(
            PBKDF2_HASH,
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected_dk),
        )

        # Constant-time comparison
        if not self._constant_time_compare(dk, expected_dk):
            return VerificationResult(
                verified=False,
                algorithm=HashAlgorithm.PBKDF2,
                needs_migration=False,
                new_hash=None,
            )

        # Verified — migrate to Argon2id
        new_hash: Optional[str] = None
        if _ARGON2_AVAILABLE and self._hasher is not None:
            try:
                new_hash = self.hash_password(password)
                log.info("PBKDF2 → Argon2id migration triggered")
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to generate Argon2id hash during PBKDF2 migration: %s", exc)

        return VerificationResult(
            verified=True,
            algorithm=HashAlgorithm.PBKDF2,
            needs_migration=True,
            new_hash=new_hash,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_strength(self, password: str) -> None:
        if len(password) < self._min_length:
            raise PasswordStrengthError(
                f"Password must be at least {self._min_length} characters long"
            )
        if not password.strip():
            raise PasswordStrengthError("Password must not be whitespace-only")

    @staticmethod
    def _constant_time_compare(a: bytes, b: bytes) -> bool:
        """Constant-time bytes comparison using XOR accumulator."""
        if len(a) != len(b):
            return False
        result = 0
        for x, y in zip(a, b):
            result |= x ^ y
        return result == 0


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

_default_service: Optional[CasinoPasswordSecurity] = None


def _get_default_service() -> CasinoPasswordSecurity:
    global _default_service  # noqa: PLW0603
    if _default_service is None:
        _default_service = CasinoPasswordSecurity()
    return _default_service


def hash_password(password: str) -> str:
    """Hash a password using the default CasinoPasswordSecurity instance."""
    return _get_default_service().hash_password(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify a password against a stored hash (no migration)."""
    return _get_default_service().verify_password(stored_hash, password)


def verify_and_migrate(stored_hash: str, password: str) -> VerificationResult:
    """Verify and transparently migrate legacy hashes to Argon2id."""
    return _get_default_service().verify_and_migrate(stored_hash, password)


# ---------------------------------------------------------------------------
# CLI — for testing and ops verification
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="CasinoPasswordSecurity — Argon2id password hashing CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Hash a password:
    python password_security.py hash "MySecret123"

  Verify a password:
    python password_security.py verify '$argon2id$...' "MySecret123"

  Verify and migrate (outputs new hash if migration needed):
    python password_security.py migrate '$2b$12$...' "MySecret123"

  Check if a hash needs rehashing:
    python password_security.py needs-rehash '$argon2id$...'
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # hash
    p_hash = subparsers.add_parser("hash", help="Hash a password")
    p_hash.add_argument("password", help="Plaintext password")

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify a password (no migration)")
    p_verify.add_argument("stored_hash", help="Stored PHC hash string")
    p_verify.add_argument("password", help="Plaintext password to verify")

    # migrate
    p_migrate = subparsers.add_parser("migrate", help="Verify and migrate legacy hashes")
    p_migrate.add_argument("stored_hash", help="Stored hash string (any algorithm)")
    p_migrate.add_argument("password", help="Plaintext password to verify")

    # needs-rehash
    p_rehash = subparsers.add_parser("needs-rehash", help="Check if hash needs upgrading")
    p_rehash.add_argument("stored_hash", help="Stored PHC hash string")

    # detect
    p_detect = subparsers.add_parser("detect", help="Detect algorithm of a stored hash")
    p_detect.add_argument("stored_hash", help="Stored hash string")

    args = parser.parse_args()
    svc = CasinoPasswordSecurity()

    if args.command == "hash":
        try:
            h = svc.hash_password(args.password)
            print(h)
        except PasswordStrengthError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "verify":
        ok = svc.verify_password(args.stored_hash, args.password)
        print(json.dumps({"verified": ok}))
        sys.exit(0 if ok else 1)

    elif args.command == "migrate":
        result = svc.verify_and_migrate(args.stored_hash, args.password)
        output = {
            "verified": result.verified,
            "algorithm": result.algorithm.value,
            "needs_migration": result.needs_migration,
            "new_hash": result.new_hash,
        }
        print(json.dumps(output, indent=2))
        sys.exit(0 if result.verified else 1)

    elif args.command == "needs-rehash":
        needs = svc.check_needs_rehash(args.stored_hash)
        print(json.dumps({"needs_rehash": needs}))

    elif args.command == "detect":
        alg = CasinoPasswordSecurity._detect_algorithm(args.stored_hash)
        print(json.dumps({"algorithm": alg.value}))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _cli()
