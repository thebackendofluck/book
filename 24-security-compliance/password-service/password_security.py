#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Password hashing service with bcrypt-to-Argon2id migration support.

Implements the password security strategy for iGaming platforms migrating
from legacy bcrypt/PBKDF2 hashes to the OWASP-recommended Argon2id.

Migration strategy:
  1. New registrations: Argon2id immediately
  2. Existing users: verify with current algorithm on next login;
     if valid, re-hash with Argon2id transparently
  3. Legacy systems: PBKDF2-HMAC-SHA256 endpoint retained for compatibility

Argon2id parameters (OWASP 2024 recommendations):
  memory_cost = 65,536 KiB (64 MB) — makes GPU cracking ~375 hashes/s (vs 10B/s SHA256)
  time_cost   = 3 iterations
  parallelism = 4 threads
  hash_len    = 32 bytes
  salt_len    = 16 bytes (random, included in PHC string)

Reference: Chapter 24 — Security and Compliance / HSM and Password Services
           Chapter 20 — HSM Infrastructure
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
from typing import Any

try:
    from argon2 import PasswordHasher, Type
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    _ARGON2_AVAILABLE = True
except ImportError:
    _ARGON2_AVAILABLE = False

try:
    import bcrypt as _bcrypt
    _BCRYPT_AVAILABLE = True
except ImportError:
    _BCRYPT_AVAILABLE = False

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, status
    from pydantic import BaseModel, Field
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
)
log = logging.getLogger("password_security")


# ---------------------------------------------------------------------------
# Argon2id configuration (OWASP 2024)
# ---------------------------------------------------------------------------

ARGON2_MEMORY_COST = int(os.environ.get("ARGON2_MEMORY_COST", "65536"))  # KiB
ARGON2_TIME_COST = int(os.environ.get("ARGON2_TIME_COST", "3"))
ARGON2_PARALLELISM = int(os.environ.get("ARGON2_PARALLELISM", "4"))
ARGON2_HASH_LEN = 32    # bytes
ARGON2_SALT_LEN = 16    # bytes

# Legacy PBKDF2 (migration compatibility — see chapter-24/password-service-python/)
PBKDF2_ITERATIONS = 100_100
PBKDF2_HASH_BYTES = 32
PBKDF2_ALGORITHM = "sha256"

# bcrypt cost factor — preserved for verifying legacy hashes during migration
BCRYPT_ROUNDS = 12


# ---------------------------------------------------------------------------
# Hash algorithm detection
# ---------------------------------------------------------------------------

def _detect_algorithm(hash_str: str) -> str:
    """
    Detect the hashing algorithm from a stored hash string.

    Args:
        hash_str: Stored hash (PHC format, bcrypt, or hex).

    Returns:
        One of: "argon2id", "bcrypt", "pbkdf2", "unknown".
    """
    if hash_str.startswith("$argon2id$"):
        return "argon2id"
    if hash_str.startswith("$argon2i$"):
        return "argon2i"
    if hash_str.startswith("$2b$") or hash_str.startswith("$2a$"):
        return "bcrypt"
    # PBKDF2 hashes from our Scala/Go/Python services are uppercase hex, 64 chars
    if len(hash_str) == 64 and hash_str == hash_str.upper() and all(c in "0123456789ABCDEF" for c in hash_str):
        return "pbkdf2"
    return "unknown"


# ---------------------------------------------------------------------------
# Password service
# ---------------------------------------------------------------------------

class PasswordService:
    """
    Password hashing service with multi-algorithm support and migration helper.

    Supports:
      - Argon2id (primary — OWASP 2024 recommended)
      - bcrypt (legacy verification during migration)
      - PBKDF2-HMAC-SHA256 (legacy compatibility with Scala/Go services)

    Args:
        memory_cost:    Argon2id memory cost in KiB.
        time_cost:      Argon2id time cost (iterations).
        parallelism:    Argon2id parallelism factor.
        pbkdf2_iters:   PBKDF2 iteration count for legacy compatibility.
    """

    def __init__(
        self,
        memory_cost: int = ARGON2_MEMORY_COST,
        time_cost: int = ARGON2_TIME_COST,
        parallelism: int = ARGON2_PARALLELISM,
        pbkdf2_iters: int = PBKDF2_ITERATIONS,
    ) -> None:
        self._pbkdf2_iters = pbkdf2_iters

        if not _ARGON2_AVAILABLE:
            raise RuntimeError(
                "argon2-cffi is required: pip install argon2-cffi"
            )

        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=ARGON2_HASH_LEN,
            salt_len=ARGON2_SALT_LEN,
            type=Type.ID,
        )

    # --- Argon2id -----------------------------------------------------------

    def hash_argon2id(self, password: str) -> str:
        """
        Hash a password with Argon2id (OWASP 2024 recommended).

        Returns a PHC-format string that includes the algorithm, parameters,
        salt, and hash — everything needed for verification.

        Args:
            password: Plaintext password.

        Returns:
            PHC-format Argon2id hash string.
        """
        return self._hasher.hash(password)

    def verify_argon2id(self, password: str, hash_str: str) -> tuple[bool, bool]:
        """
        Verify a password against an Argon2id hash.

        Args:
            password: Plaintext password.
            hash_str: Argon2id PHC-format hash.

        Returns:
            Tuple of (is_valid, needs_rehash).
            needs_rehash is True when parameters are outdated.
        """
        try:
            valid = self._hasher.verify(hash_str, password)
            needs_rehash = valid and self._hasher.check_needs_rehash(hash_str)
            return valid, needs_rehash
        except VerifyMismatchError:
            return False, False
        except (VerificationError, InvalidHashError):
            log.warning("argon2id_verification_error — invalid hash format")
            return False, False

    # --- bcrypt (legacy migration) -----------------------------------------

    def verify_bcrypt(self, password: str, hash_str: str) -> bool:
        """
        Verify a password against a bcrypt hash (migration verification only).

        Args:
            password: Plaintext password.
            hash_str: bcrypt hash string ($2b$ prefix).

        Returns:
            True if the password matches.
        """
        if not _BCRYPT_AVAILABLE:
            log.error("bcrypt not available: pip install bcrypt")
            return False
        try:
            return _bcrypt.checkpw(
                password.encode("utf-8"),
                hash_str.encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("bcrypt_verify_error: %s", exc)
            return False

    # --- PBKDF2 (legacy compatibility) -------------------------------------

    def hash_pbkdf2(self, password: str, salt: str | None = None) -> str:
        """
        Hash with PBKDF2-HMAC-SHA256 (legacy compatibility).

        Args:
            password: Plaintext password.
            salt:     Explicit salt value. Required - the caller must supply a
                      unique, randomly generated salt per credential and persist
                      it alongside the hash (matching the sibling Go/Scala
                      PBKDF2 implementations, where the salt is a mandatory,
                      caller-supplied argument with no implicit default).

        Returns:
            Uppercase hex-encoded PBKDF2 hash (64 characters).

        Raises:
            ValueError: If salt is not provided. Silently falling back to the
                password as its own salt means identical passwords always hash
                identically and precomputed dictionary attacks become viable -
                that must be an explicit, visible choice by the caller, not a
                silent default.
        """
        if not salt:
            raise ValueError(
                "hash_pbkdf2 requires an explicit salt; using the password as "
                "its own salt is insecure and must not happen implicitly"
            )
        salt_bytes = salt.encode("utf-8")
        derived = hashlib.pbkdf2_hmac(
            hash_name=PBKDF2_ALGORITHM,
            password=password.encode("utf-8"),
            salt=salt_bytes,
            iterations=self._pbkdf2_iters,
            dklen=PBKDF2_HASH_BYTES,
        )
        return derived.hex().upper()

    def verify_pbkdf2(self, password: str, hash_str: str, salt: str | None = None) -> bool:
        """
        Verify a password against a PBKDF2 hash (constant-time comparison).

        Args:
            password: Plaintext password.
            hash_str: Expected PBKDF2 hash (uppercase hex).
            salt:     Explicit salt value. Required - see hash_pbkdf2.

        Returns:
            True if the computed hash matches.

        Raises:
            ValueError: If salt is not provided.
        """
        computed = self.hash_pbkdf2(password, salt)
        return hmac.compare_digest(computed.upper(), hash_str.upper())

    # --- Migration helper --------------------------------------------------

    def verify_and_migrate(
        self,
        password: str,
        stored_hash: str,
        salt: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        Verify a password against any supported algorithm and re-hash if valid.

        Implements the transparent migration strategy:
          - Detects the stored hash algorithm automatically
          - If the password is valid AND the algorithm is not Argon2id, returns
            a new Argon2id hash for the application to persist
          - If the password is valid AND already Argon2id but needs rehash,
            returns a new Argon2id hash with current parameters

        Args:
            password:     Plaintext password from the login form.
            stored_hash:  Hash retrieved from the database.
            salt:         Optional salt (PBKDF2 legacy only).

        Returns:
            Tuple of (is_valid, new_hash_or_None).
            new_hash is non-None when the caller should update the stored hash.
        """
        algorithm = _detect_algorithm(stored_hash)

        if algorithm == "argon2id":
            valid, needs_rehash = self.verify_argon2id(password, stored_hash)
            if valid and needs_rehash:
                log.info("password_rehash_required algorithm=argon2id params_outdated=true")
                return True, self.hash_argon2id(password)
            return valid, None

        if algorithm in ("argon2i",):
            # Older Argon2i — still verify, but upgrade to Argon2id
            try:
                valid = self._hasher.verify(stored_hash, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                valid = False
            if valid:
                log.info("password_migrate algorithm=argon2i→argon2id")
                return True, self.hash_argon2id(password)
            return False, None

        if algorithm == "bcrypt":
            valid = self.verify_bcrypt(password, stored_hash)
            if valid:
                log.info("password_migrate algorithm=bcrypt→argon2id")
                return True, self.hash_argon2id(password)
            return False, None

        if algorithm == "pbkdf2":
            try:
                valid = self.verify_pbkdf2(password, stored_hash, salt)
            except ValueError:
                log.warning("password_pbkdf2_verify_missing_salt — cannot verify without an explicit salt")
                return False, None
            if valid:
                log.info("password_migrate algorithm=pbkdf2→argon2id")
                return True, self.hash_argon2id(password)
            return False, None

        log.warning("password_unknown_algorithm hash_prefix=%s", stored_hash[:12])
        return False, None


# ---------------------------------------------------------------------------
# FastAPI service (optional, requires fastapi + uvicorn)
# ---------------------------------------------------------------------------

def _build_app() -> Any:
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError("fastapi is required: pip install fastapi uvicorn")

    app = FastAPI(
        title="Password Security Service",
        description=(
            "Argon2id password hashing with bcrypt/PBKDF2 migration support. "
            "Chapter 24 — Security and Compliance."
        ),
        version=os.getenv("BUILD_VERSION", "dev"),
    )

    svc = PasswordService()

    class HashRequest(BaseModel):
        password: str = Field(..., min_length=1)

    class HashResponse(BaseModel):
        hash: str
        algorithm: str = "argon2id"

    class VerifyRequest(BaseModel):
        password: str = Field(..., min_length=1)
        hash: str = Field(..., min_length=1)
        salt: str | None = None

    class VerifyResponse(BaseModel):
        valid: bool
        needs_rehash: bool = False
        new_hash: str | None = None

    class LegacyHashRequest(BaseModel):
        password: str = Field(..., min_length=1)
        salt: str | None = None

    class VersionResponse(BaseModel):
        branch: str | None = None
        revision: str | None = None
        timestamp: str | None = None

    @app.post("/hashPasswordArgon2id", response_model=HashResponse)
    def hash_argon2id(req: HashRequest) -> HashResponse:
        """Hash a password with Argon2id (primary endpoint)."""
        log.info("argon2id_hash_request")
        return HashResponse(hash=svc.hash_argon2id(req.password))

    @app.post("/verifyAndMigrate", response_model=VerifyResponse)
    def verify_and_migrate(req: VerifyRequest) -> VerifyResponse:
        """
        Verify a password and transparently migrate legacy hashes to Argon2id.

        Returns the new Argon2id hash if migration is needed — the caller
        should persist this to replace the stored hash.
        """
        log.info("verify_and_migrate_request")
        valid, new_hash = svc.verify_and_migrate(req.password, req.hash, req.salt)
        return VerifyResponse(valid=valid, new_hash=new_hash, needs_rehash=new_hash is not None)

    @app.post("/hashPassword", response_model=HashResponse)
    def hash_pbkdf2_legacy(req: LegacyHashRequest) -> HashResponse:
        """Legacy: Hash with PBKDF2-SHA256. DEPRECATED — use /hashPasswordArgon2id."""
        log.warning("legacy_pbkdf2_hash_request")
        try:
            hashed = svc.hash_pbkdf2(req.password, req.salt)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return HashResponse(hash=hashed, algorithm="pbkdf2-sha256")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "Ok"}

    @app.get("/version", response_model=VersionResponse)
    def version() -> VersionResponse:
        return VersionResponse(
            branch=os.getenv("BUILD_BRANCH"),
            revision=os.getenv("BUILD_REVISION"),
            timestamp=os.getenv("BUILD_TIMESTAMP"),
        )

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not _FASTAPI_AVAILABLE:
        log.error("fastapi and uvicorn are required to run the HTTP service")
        sys.exit(1)
    app = _build_app()
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


app = _build_app() if _FASTAPI_AVAILABLE else None  # type: ignore[assignment]

if __name__ == "__main__":
    main()
