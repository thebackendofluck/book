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
# Password Service (Python / FastAPI)
# Source: Production casino platform (sanitized)
# Chapter 24 - Security and Compliance
#
# Python equivalent of the original Scala/HTTP4s PBKDF2 password hashing
# microservice. Implements the same PBKDF2-SHA256 algorithm, parameters,
# and API contract so hashes produced by both services are interchangeable
# during migration.
#
# PBKDF2 parameters (matching original Scala implementation):
#   algorithm  : PBKDF2-HMAC-SHA256
#   iterations : 100,100
#   key_length : 32 bytes (256 bits)
#   encoding   : uppercase hex
#   salt       : UTF-8 encoding of the password (legacy behaviour — see note)
#
# NOTE: Using the password as its own salt is a legacy behaviour preserved
# for migration compatibility with the existing Go and Scala implementations.
# New systems MUST use a random per-password salt. See password-service/ for
# the Argon2id implementation with proper random salts.
#
# Endpoints:
#   POST /hashPassword  — PBKDF2-SHA256 hash (same contract as Scala service)
#   GET  /health        — Kubernetes liveness / readiness probe
#   GET  /version       — build metadata (branch, commit, timestamp)
#
# Run: uvicorn main:app --host 0.0.0.0 --port 8080
# =============================================================================

from __future__ import annotations

import hashlib
import logging
import os

import structlog
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
)
log = structlog.get_logger()

app = FastAPI(
    title="Password Service",
    description=(
        "PBKDF2-SHA256 password hashing microservice. "
        "Drop-in Python replacement for the original Scala/HTTP4s service."
    ),
    version=os.getenv("BUILD_VERSION", "dev"),
)

# ---------------------------------------------------------------------------
# Configuration — identical parameters to the Scala/Go implementations
# ---------------------------------------------------------------------------

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 100_100
PBKDF2_KEY_LEN = 32  # bytes (256 bits)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PasswordHashRequest(BaseModel):
    password: str = Field(..., min_length=1, description="Plaintext password to hash")


class PasswordHashResponse(BaseModel):
    hash: str = Field(
        ...,
        description=(
            "Uppercase hex-encoded PBKDF2-SHA256 hash. "
            "Interchangeable with hashes from the Go and Scala implementations."
        ),
    )


class VersionResponse(BaseModel):
    branch: str | None = None
    revision: str | None = None
    timestamp: str | None = None


# ---------------------------------------------------------------------------
# Hashing logic
# ---------------------------------------------------------------------------


def _hash_pbkdf2(password: str) -> str:
    """
    Hash password with PBKDF2-HMAC-SHA256 using the password itself as salt.

    Matches the Scala implementation exactly:
      - Salt = UTF-8 encoding of the password string
      - 100,100 iterations
      - 256-bit output
      - Uppercase hex encoding (Guava BaseEncoding.base16() equivalent)

    Security note: using the password as its own salt is intentional here
    only for backward-compatibility with the existing platform. For new
    registrations, use the Argon2id endpoint in password-service/.
    """
    salt = password.encode("utf-8")
    derived = hashlib.pbkdf2_hmac(
        hash_name=PBKDF2_ALGORITHM,
        password=password.encode("utf-8"),
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
        dklen=PBKDF2_KEY_LEN,
    )
    return derived.hex().upper()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/hashPassword", response_model=PasswordHashResponse)
def hash_password(request: PasswordHashRequest) -> PasswordHashResponse:
    """Hash a password using PBKDF2-HMAC-SHA256 (legacy migration endpoint)."""
    log.info("pbkdf2_hash_request")
    hashed = _hash_pbkdf2(request.password)
    return PasswordHashResponse(hash=hashed)


@app.get("/health")
def health() -> dict[str, str]:
    """Kubernetes liveness / readiness probe."""
    return {"status": "Ok"}


@app.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    """Build metadata — injected via environment variables at container build time."""
    return VersionResponse(
        branch=os.getenv("BUILD_BRANCH"),
        revision=os.getenv("BUILD_REVISION"),
        timestamp=os.getenv("BUILD_TIMESTAMP"),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
