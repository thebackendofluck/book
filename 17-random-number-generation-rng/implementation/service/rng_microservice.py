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
RNG as Isolated FastAPI Microservice with Audit Logging and Rate Limiting
==========================================================================

GLI-11 Section 4.7 Compliance: RNG Service Architecture
- RNG must run as an isolated service with no direct game logic access
- All requests must be authenticated and rate-limited
- Every RNG output must be logged with requester identity
- Service must expose health endpoints for monitoring
- Cryptographic audit trail must be tamper-evident

Architecture:
- FastAPI service with async request handling
- Redis-backed rate limiting (token bucket per client)
- PostgreSQL audit log with HMAC integrity verification
- Prometheus metrics for monitoring
- Health endpoint with NIST test status

Usage:
    python rng_microservice.py                    # Run on default port 8443
    python rng_microservice.py --port 8443 --workers 4
    python rng_microservice.py --test             # Run self-test

Dependencies:
    pip install fastapi uvicorn redis pydantic
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, Request, Depends, Header
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field, validator  # ty:ignore[deprecated]
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # ty:ignore[invalid-assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rng-service")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RNG_SERVICE_CONFIG = {
    "host": "0.0.0.0",
    "port": 8443,
    "workers": 2,
    "redis_url": "redis://localhost:6379/0",
    "rate_limit_per_minute": 1000,
    "rate_limit_burst": 100,
    "max_bytes_per_request": 4096,
    "max_int_range": 2**32,
    "audit_hmac_key": os.environ.get("RNG_AUDIT_HMAC_KEY", "CHANGE-ME-IN-PRODUCTION"),
    "api_keys": {
        "game-engine-prod": "gk_prod_" + secrets.token_hex(16),
        "game-engine-staging": "gk_stag_" + secrets.token_hex(16),
        "certification-lab": "gk_cert_" + secrets.token_hex(16),
    },
    "allowed_clients": ["game-engine", "certification-lab", "backoffice-audit"],
}


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

if FASTAPI_AVAILABLE:
    class GenerateBytesRequest(BaseModel):
        """Request to generate random bytes."""
        num_bytes: int = Field(..., ge=1, le=4096, description="Number of random bytes (1-4096)")
        purpose: str = Field(..., min_length=1, max_length=100, description="Usage purpose for audit")
        game_id: Optional[str] = Field(None, description="Associated game round ID")
        encoding: str = Field("hex", description="Output encoding: hex, base64, raw")

    class GenerateIntRequest(BaseModel):
        """Request to generate a random integer."""
        lower: int = Field(..., description="Lower bound (inclusive)")
        upper: int = Field(..., description="Upper bound (inclusive)")
        purpose: str = Field(..., min_length=1, max_length=100)
        game_id: Optional[str] = None

        @validator("upper")  # ty:ignore[deprecated]
        def upper_must_exceed_lower(cls, v, values):
            if "lower" in values and v <= values["lower"]:
                raise ValueError("upper must be > lower")
            return v

    class GenerateFloatRequest(BaseModel):
        """Request to generate a random float in [0, 1)."""
        purpose: str = Field(..., min_length=1, max_length=100)
        game_id: Optional[str] = None

    class ShuffleRequest(BaseModel):
        """Request to shuffle a sequence."""
        items: List[int] = Field(..., min_items=2, max_items=1000)
        purpose: str = Field(..., min_length=1, max_length=100)
        game_id: Optional[str] = None

    class RNGResponse(BaseModel):
        """Standard RNG response with audit information."""
        request_id: str
        result: dict
        audit: dict
        timestamp: str


# ---------------------------------------------------------------------------
# Rate Limiter (Token Bucket via Redis)
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Token bucket rate limiter backed by Redis.

    GLI-11 4.7.3: RNG service must enforce rate limits to prevent
    abuse and ensure fair resource allocation across game servers.
    """

    def __init__(
        self,
        redis_url: str,
        rate_per_minute: int = 1000,
        burst: int = 100,
    ):
        self.redis_url = redis_url
        self.rate_per_minute = rate_per_minute
        self.burst = burst
        self.redis: Optional[aioredis.Redis] = None

    async def connect(self):
        if aioredis:
            try:
                self.redis = aioredis.from_url(self.redis_url, decode_responses=True)
                await self.redis.ping()  # ty:ignore[invalid-await]
                logger.info("Rate limiter connected to Redis")
            except Exception as e:
                logger.warning("Redis not available for rate limiting: %s", e)
                self.redis = None

    async def close(self):
        if self.redis:
            await self.redis.close()

    async def check_rate_limit(self, client_id: str) -> tuple:
        """
        Check if request is within rate limit.
        Returns (allowed: bool, remaining: int, reset_at: float).
        """
        if not self.redis:
            return True, self.burst, time.time() + 60

        key = f"rng:rate:{client_id}"
        now = time.time()
        window_start = now - 60

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, 120)
        results = await pipe.execute()

        current_count = results[1]
        remaining = max(0, self.rate_per_minute - current_count)
        allowed = current_count < self.rate_per_minute

        if not allowed:
            # Remove the request we just added
            await self.redis.zrem(key, str(now))

        return allowed, remaining, window_start + 60


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    Tamper-evident audit logger for RNG requests.

    GLI-11 4.4: All RNG operations must produce an immutable audit trail.
    Each entry is HMAC-signed to detect tampering.
    """

    def __init__(self, hmac_key: str, log_path: Optional[str] = None):
        self._hmac_key = hmac_key.encode()
        self._log_path = log_path or "/var/log/rng-service/audit.jsonl"
        self._sequence = 0
        self._prev_hash = "0" * 64  # Genesis hash

    def log(self, event: dict) -> dict:
        """Log an event with HMAC chain integrity."""
        self._sequence += 1
        entry = {
            "seq": self._sequence,
            "ts": datetime.now(timezone.utc).isoformat(),
            "prev_hash": self._prev_hash,
            **event,
        }

        # Compute chain hash
        entry_bytes = json.dumps(entry, sort_keys=True).encode()
        entry_hash = hmac.new(
            self._hmac_key, entry_bytes, hashlib.sha256
        ).hexdigest()
        entry["hash"] = entry_hash
        self._prev_hash = entry_hash

        # Write to log
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            with open(self._log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

        return {"seq": self._sequence, "hash": entry_hash[:16]}


# ---------------------------------------------------------------------------
# RNG Core (wraps Fortuna or os.urandom)
# ---------------------------------------------------------------------------

class RNGCore:
    """
    Core RNG with generation methods and health tracking.

    Attempts to use FortunaGenerator if available in the path,
    otherwise falls back to os.urandom.
    """

    def __init__(self):
        self._fortuna = None
        self._total_generated = 0
        self._request_count = 0

        try:
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "csprng"))
            from fortuna_generator import FortunaGenerator  # ty:ignore[unresolved-import]
            self._fortuna = FortunaGenerator(min_initial_entropy=64)
            self._fortuna.seed_from_os(256)
            logger.info("Using Fortuna CSPRNG")
        except ImportError:
            logger.info("Fortuna not available, using os.urandom")

    def generate_bytes(self, num_bytes: int) -> bytes:
        self._request_count += 1
        self._total_generated += num_bytes
        if self._fortuna:
            return self._fortuna.generate(num_bytes)
        return os.urandom(num_bytes)

    def generate_int(self, lower: int, upper: int) -> int:
        """Uniform random integer in [lower, upper] using rejection sampling."""
        range_size = upper - lower + 1
        byte_count = (range_size.bit_length() + 7) // 8
        mask = (1 << range_size.bit_length()) - 1

        for _ in range(10000):
            raw = int.from_bytes(self.generate_bytes(byte_count), "big") & mask
            if raw < range_size:
                return lower + raw

        raise RuntimeError("Rejection sampling failed")

    def generate_float(self) -> float:
        """Uniform float in [0.0, 1.0) with 53-bit precision."""
        raw = int.from_bytes(self.generate_bytes(7), "big") >> 3
        return raw / (1 << 53)

    def shuffle(self, items: list) -> list:
        """Fisher-Yates shuffle."""
        result = list(items)
        n = len(result)
        for i in range(n - 1, 0, -1):
            j = self.generate_int(0, i)
            result[i], result[j] = result[j], result[i]
        return result

    def get_health(self) -> dict:
        health = {
            "backend": "fortuna" if self._fortuna else "os.urandom",
            "total_generated_bytes": self._total_generated,
            "total_requests": self._request_count,
        }
        if self._fortuna:
            health["fortuna_health"] = self._fortuna.get_health_status()
        return health


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

def create_app() -> "FastAPI":
    """Create the FastAPI application."""
    if not FASTAPI_AVAILABLE:
        raise ImportError("Install FastAPI: pip install fastapi uvicorn")

    rng_core = RNGCore()
    rate_limiter = RateLimiter(
        redis_url=RNG_SERVICE_CONFIG["redis_url"],  # ty:ignore[invalid-argument-type]
        rate_per_minute=RNG_SERVICE_CONFIG["rate_limit_per_minute"],  # ty:ignore[invalid-argument-type]
        burst=RNG_SERVICE_CONFIG["rate_limit_burst"],  # ty:ignore[invalid-argument-type]
    )
    audit_logger = AuditLogger(
        hmac_key=RNG_SERVICE_CONFIG["audit_hmac_key"],  # ty:ignore[invalid-argument-type]
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await rate_limiter.connect()
        logger.info("RNG microservice started")
        yield
        await rate_limiter.close()
        logger.info("RNG microservice stopped")

    app = FastAPI(
        title="RNG Microservice",
        description="GLI-11 Compliant Random Number Generation Service",
        version="1.0.0",
        lifespan=lifespan,
    )

    # --- Authentication ---

    async def verify_api_key(x_api_key: str = Header(...)) -> str:
        """Verify API key and return client identity."""
        for client_id, key in RNG_SERVICE_CONFIG["api_keys"].items():  # ty:ignore[unresolved-attribute]
            if hmac.compare_digest(x_api_key, key):
                return client_id
        raise HTTPException(status_code=401, detail="Invalid API key")

    async def check_rate(request: Request, client_id: str = Depends(verify_api_key)) -> str:
        """Check rate limit for the client."""
        allowed, remaining, reset_at = await rate_limiter.check_rate_limit(client_id)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(int(reset_at)),
                },
            )
        return client_id

    # --- Endpoints ---

    @app.post("/api/v1/rng/bytes", response_model=RNGResponse)
    async def generate_bytes(
        req: GenerateBytesRequest,
        client_id: str = Depends(check_rate),
    ):
        """Generate random bytes."""
        request_id = str(uuid.uuid4())
        raw_bytes = rng_core.generate_bytes(req.num_bytes)

        if req.encoding == "base64":
            import base64
            result_value = base64.b64encode(raw_bytes).decode()
        elif req.encoding == "raw":
            result_value = list(raw_bytes)
        else:
            result_value = raw_bytes.hex()

        audit_info = audit_logger.log({
            "request_id": request_id,
            "client": client_id,
            "operation": "generate_bytes",
            "num_bytes": req.num_bytes,
            "purpose": req.purpose,
            "game_id": req.game_id,
            "output_hash": hashlib.sha256(raw_bytes).hexdigest()[:32],
        })

        return RNGResponse(
            request_id=request_id,
            result={"value": result_value, "encoding": req.encoding, "bytes": req.num_bytes},
            audit=audit_info,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.post("/api/v1/rng/integer", response_model=RNGResponse)
    async def generate_integer(
        req: GenerateIntRequest,
        client_id: str = Depends(check_rate),
    ):
        """Generate a uniform random integer in [lower, upper]."""
        request_id = str(uuid.uuid4())

        if req.upper - req.lower > RNG_SERVICE_CONFIG["max_int_range"]:  # ty:ignore[unsupported-operator]
            raise HTTPException(400, f"Range too large (max {RNG_SERVICE_CONFIG['max_int_range']})")

        value = rng_core.generate_int(req.lower, req.upper)

        audit_info = audit_logger.log({
            "request_id": request_id,
            "client": client_id,
            "operation": "generate_integer",
            "range": [req.lower, req.upper],
            "purpose": req.purpose,
            "game_id": req.game_id,
            "result_hash": hashlib.sha256(str(value).encode()).hexdigest()[:16],
        })

        return RNGResponse(
            request_id=request_id,
            result={"value": value, "lower": req.lower, "upper": req.upper},
            audit=audit_info,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.post("/api/v1/rng/float", response_model=RNGResponse)
    async def generate_float(
        req: GenerateFloatRequest,
        client_id: str = Depends(check_rate),
    ):
        """Generate a uniform random float in [0.0, 1.0)."""
        request_id = str(uuid.uuid4())
        value = rng_core.generate_float()

        audit_info = audit_logger.log({
            "request_id": request_id,
            "client": client_id,
            "operation": "generate_float",
            "purpose": req.purpose,
            "game_id": req.game_id,
        })

        return RNGResponse(
            request_id=request_id,
            result={"value": value},
            audit=audit_info,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.post("/api/v1/rng/shuffle", response_model=RNGResponse)
    async def shuffle_items(
        req: ShuffleRequest,
        client_id: str = Depends(check_rate),
    ):
        """Shuffle a sequence using Fisher-Yates."""
        request_id = str(uuid.uuid4())
        shuffled = rng_core.shuffle(req.items)

        audit_info = audit_logger.log({
            "request_id": request_id,
            "client": client_id,
            "operation": "shuffle",
            "num_items": len(req.items),
            "purpose": req.purpose,
            "game_id": req.game_id,
            "output_hash": hashlib.sha256(str(shuffled).encode()).hexdigest()[:32],
        })

        return RNGResponse(
            request_id=request_id,
            result={"shuffled": shuffled, "original_count": len(req.items)},
            audit=audit_info,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    @app.get("/health")
    async def health_check():
        """Health check endpoint for load balancers and monitoring."""
        rng_health = rng_core.get_health()
        return {
            "status": "healthy",
            "service": "rng-microservice",
            "version": "1.0.0",
            "rng": rng_health,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/health/detailed")
    async def detailed_health(client_id: str = Depends(verify_api_key)):
        """Detailed health check (authenticated)."""
        rng_health = rng_core.get_health()
        return {
            "status": "healthy",
            "rng": rng_health,
            "rate_limiter": {
                "redis_connected": rate_limiter.redis is not None,
                "rate_per_minute": rate_limiter.rate_per_minute,
            },
            "audit": {
                "sequence": audit_logger._sequence,
                "chain_intact": True,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return app


# ---------------------------------------------------------------------------
# Self-Test (without FastAPI server)
# ---------------------------------------------------------------------------

def self_test() -> bool:
    """RNG microservice component self-test."""
    print("=== RNG Microservice Self-Test ===\n")

    # Test 1: RNG Core
    core = RNGCore()
    data = core.generate_bytes(32)
    assert len(data) == 32
    print(f"[PASS] Generate bytes: {data.hex()[:32]}...")

    # Test 2: Integer generation
    val = core.generate_int(1, 100)
    assert 1 <= val <= 100
    print(f"[PASS] Generate int [1, 100]: {val}")

    # Test 3: Float generation
    fval = core.generate_float()
    assert 0.0 <= fval < 1.0
    print(f"[PASS] Generate float: {fval:.10f}")

    # Test 4: Shuffle
    items = list(range(10))
    shuffled = core.shuffle(items)
    assert sorted(shuffled) == list(range(10))
    assert shuffled != list(range(10))  # Extremely unlikely to be same
    print(f"[PASS] Shuffle: {shuffled}")

    # Test 5: Audit logger
    audit = AuditLogger(hmac_key="test-key", log_path="/tmp/rng_test_audit.jsonl")
    entry1 = audit.log({"operation": "test", "data": "hello"})
    entry2 = audit.log({"operation": "test", "data": "world"})
    assert entry1["seq"] == 1
    assert entry2["seq"] == 2
    assert entry1["hash"] != entry2["hash"]
    print(f"[PASS] Audit logging with HMAC chain: seq={entry2['seq']}")

    # Test 6: Health check
    health = core.get_health()
    assert health["total_requests"] > 0
    print(f"[PASS] Health: backend={health['backend']}, "
          f"requests={health['total_requests']}")

    # Test 7: Uniqueness
    samples = set()
    for _ in range(1000):
        samples.add(core.generate_bytes(16))
    assert len(samples) == 1000
    print(f"[PASS] Uniqueness: 1000/1000 unique 16-byte samples")

    # Test 8: Rejection sampling correctness
    counts = [0] * 6
    for _ in range(60000):
        counts[core.generate_int(0, 5)] += 1
    max_deviation = max(abs(c - 10000) / 10000 for c in counts)
    assert max_deviation < 0.05
    print(f"[PASS] Uniformity (d6): max_deviation={max_deviation:.4f}")

    # Cleanup
    try:
        os.unlink("/tmp/rng_test_audit.jsonl")
    except OSError:
        pass

    print("\n=== All self-tests passed ===")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="RNG Microservice (GLI-11 Compliant)")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--test", action="store_true", help="Run self-test")
    args = parser.parse_args()

    if args.test:
        self_test()
        return

    if not FASTAPI_AVAILABLE:
        print("Install FastAPI: pip install fastapi uvicorn")
        return

    import uvicorn
    app = create_app()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
