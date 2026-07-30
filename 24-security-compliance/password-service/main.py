# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Password Service
# Source: Production casino platform (sanitized)
# Chapter 24 - Security
#
# Microservice that hashes passwords using Argon2id (primary, GPU-resistant)
# with PBKDF2-HMAC-SHA256 legacy support for migration.
#
# Argon2id parameters (OWASP 2024):
#   memory_cost = 65,536 KiB (64 MB) — forces 64MB RAM per hash attempt
#   time_cost   = 3 iterations
#   parallelism = 4 threads
#   hash_len    = 32 bytes
#   salt_len    = 16 bytes (random per hash)
#
# Legacy PBKDF2 endpoint retained for backward compatibility during
# migration. New registrations MUST use /hashPasswordArgon2id.
#
# Endpoints:
#   POST /hashPasswordArgon2id  — Argon2id hash (recommended)
#   POST /verifyArgon2id        — verify password against Argon2id hash
#   POST /hashPassword          — legacy PBKDF2-SHA256 (migration only)
#   GET  /health                — liveness probe
#   GET  /version               — build metadata
#
# Run: uvicorn main:app --host 0.0.0.0 --port 8080
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

import structlog
import uvicorn
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
)
log = structlog.get_logger()

app = FastAPI(
    title="Password Service",
    description=(
        "Argon2id password hashing microservice (GPU-resistant). "
        "Legacy PBKDF2-SHA256 endpoint retained for migration."
    ),
    version=os.getenv("BUILD_VERSION", "dev"),
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Argon2id parameters (OWASP 2024 recommended)
ARGON2_MEMORY_COST = 65_536   # 64 MiB — each hash attempt requires 64MB RAM
ARGON2_TIME_COST = 3          # iterations
ARGON2_PARALLELISM = 4        # threads
ARGON2_HASH_LEN = 32          # bytes
ARGON2_SALT_LEN = 16          # bytes

# Legacy PBKDF2 (retained for backward compatibility during migration)
PBKDF2_ITERATIONS = 100_100
PBKDF2_HASH_BYTES = 32
PBKDF2_ALGORITHM = "sha256"

# Argon2id hasher singleton
_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=Type.ID,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class Argon2idHashRequest(BaseModel):
    password: str = Field(..., min_length=1, description="Plaintext password to hash")


class Argon2idHashResponse(BaseModel):
    hash: str = Field(
        ...,
        description="Argon2id PHC-format hash ($argon2id$v=19$m=65536,t=3,p=4$...)",
    )
    algorithm: str = "argon2id"
    memory_cost_kib: int = ARGON2_MEMORY_COST
    time_cost: int = ARGON2_TIME_COST
    parallelism: int = ARGON2_PARALLELISM


class Argon2idVerifyRequest(BaseModel):
    password: str = Field(..., min_length=1, description="Plaintext password to verify")
    hash: str = Field(..., description="Argon2id PHC-format hash to verify against")


class Argon2idVerifyResponse(BaseModel):
    valid: bool
    needs_rehash: bool = False


class PasswordHashRequest(BaseModel):
    salt: str = Field(..., description="Hex or UTF-8 encoded salt value")
    password: str = Field(..., min_length=1, description="Plaintext password to hash")


class PasswordHashResponse(BaseModel):
    hash: str = Field(..., description="Hex-encoded PBKDF2 hash of the password")
    deprecated: bool = Field(
        default=True,
        description="This endpoint uses legacy PBKDF2. Migrate to /hashPasswordArgon2id.",
    )


class VersionResponse(BaseModel):
    branch: Optional[str] = None
    date: Optional[str] = None
    revision: Optional[str] = None


# ---------------------------------------------------------------------------
# Argon2id hashing logic
# ---------------------------------------------------------------------------


def _hash_argon2id(password: str) -> str:
    """
    Hash password with Argon2id (OWASP 2024 recommended).

    Why Argon2id over PBKDF2/bcrypt:
    - NVIDIA A100 GPU: 10 billion SHA256/s, ~1.5M bcrypt/s, but only ~375 Argon2id/s
    - Each Argon2id attempt requires 64MB RAM, limiting GPU parallelism
    - A 10-char password: seconds to crack with SHA256, centuries with Argon2id

    Returns PHC-format string containing algorithm, parameters, salt, and hash.
    """
    return _hasher.hash(password)


def _verify_argon2id(password: str, hash_str: str) -> bool:
    """Verify password against Argon2id hash with constant-time comparison."""
    try:
        return _hasher.verify(hash_str, password)
    except VerifyMismatchError:
        return False


def _hash_pbkdf2_legacy(salt: str, password: str) -> str:
    """
    Legacy PBKDF2-HMAC-SHA256 with 100,100 iterations.
    DEPRECATED: Use Argon2id for new hashes. This exists only for migration.
    """
    derived = hashlib.pbkdf2_hmac(
        hash_name=PBKDF2_ALGORITHM,
        password=password.encode("utf-8"),
        salt=salt.encode("utf-8"),
        iterations=PBKDF2_ITERATIONS,
        dklen=PBKDF2_HASH_BYTES,
    )
    return derived.hex().upper()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/hashPasswordArgon2id", response_model=Argon2idHashResponse)
def hash_password_argon2id(request: Argon2idHashRequest) -> Argon2idHashResponse:
    """Hash a password using Argon2id (GPU-resistant, memory-hard)."""
    log.info("argon2id_hash_request")
    hashed = _hash_argon2id(request.password)
    return Argon2idHashResponse(hash=hashed)


@app.post("/verifyArgon2id", response_model=Argon2idVerifyResponse)
def verify_argon2id(request: Argon2idVerifyRequest) -> Argon2idVerifyResponse:
    """Verify a password against an Argon2id hash."""
    valid = _verify_argon2id(request.password, request.hash)
    needs_rehash = valid and _hasher.check_needs_rehash(request.hash)
    return Argon2idVerifyResponse(valid=valid, needs_rehash=needs_rehash)


@app.post("/hashPassword", response_model=PasswordHashResponse)
def hash_password_legacy(request: PasswordHashRequest) -> PasswordHashResponse:
    """
    Legacy: Hash a password using PBKDF2-HMAC-SHA256.
    DEPRECATED — migrate callers to /hashPasswordArgon2id.
    """
    log.warn("legacy_pbkdf2_hash_request", salt_prefix=request.salt[:4] + "...")
    hashed = _hash_pbkdf2_legacy(request.salt, request.password)
    return PasswordHashResponse(hash=hashed)


@app.get("/health")
def health():
    return {"status": "Ok"}


@app.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        branch=os.getenv("BUILD_BRANCH"),
        date=os.getenv("BUILD_DATE"),
        revision=os.getenv("BUILD_REVISION"),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
