# Companion code for "The Backend of Luck" - Chapter 28a, Distributed Systems Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
FastAPI Idempotency-Key middleware.

Algorithm (corrected to address "first-wins with in-progress state" gap):

1. On a write request (POST/PUT/PATCH/DELETE):
   a. If the path matches the REQUIRED whitelist and no Idempotency-Key header is
      present, reject with 400.
   b. If the header is absent and the path is not required, pass through.

2. Canonicalize the body (SHA-256 over JSON with sorted keys; raw bytes if not JSON).

3. Attempt to acquire the key via INSERT-as-lock:

       INSERT INTO idempotency_records (key, user_id, path, body_hash, state, ...)
       VALUES (..., 'in_progress')
       ON CONFLICT (key) DO NOTHING
       RETURNING key

   - If RETURNING returned the key, this request is the OWNER. Execute the handler.
     When complete and the response is terminal (2xx final, 4xx client-error, 5xx
     final), persist: state='terminal', response_status, response_body. If the
     response is non-terminal (202 Accepted, 102 Processing), do NOT cache; delete
     the in_progress record so a retry can be a new owner.

   - If RETURNING returned nothing, another request won. Fetch current record:
       * If body_hash differs -> 409 Conflict (key reuse).
       * If state='terminal' -> replay cached response_status + response_body.
       * If state='in_progress' -> poll with exponential backoff until either
         terminal or TIMEOUT (default 30s). On timeout -> 409 Conflict
         ("original request still processing").

4. TTLs:
   - wallet/payment endpoints: 7 days
   - everything else: 24 hours

A background task (pg_cron or k8s CronJob) cleans expired rows daily; this module
provides `purge_expired()` for the cron wrapper.

This implementation is intentionally datastore-agnostic: it accepts a callable
`store` that implements the IdempotencyStore protocol, so tests can use a
fake in-memory store and prod uses a psycopg-backed implementation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic_settings import BaseSettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


STATE_IN_PROGRESS = "in_progress"
STATE_TERMINAL = "terminal"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class IdempotencySettings(BaseSettings):
    """Configuration for the idempotency middleware."""

    enabled: bool = True

    required_path_prefixes: tuple[str, ...] = (
        "/wallet/",
        "/gal/",
        "/payments/",
        "/kyc/",
        "/bonus/",
        "/responsible-gaming/self-exclusion",
        "/dsr/",
        "/consent/record",
    )

    long_ttl_path_prefixes: tuple[str, ...] = (
        "/wallet/",
        "/payments/",
    )

    long_ttl_seconds: int = 60 * 60 * 24 * 7
    default_ttl_seconds: int = 60 * 60 * 24

    inflight_poll_interval_ms: int = 100
    inflight_poll_timeout_seconds: int = 30

    max_key_length: int = 255
    max_cached_body_bytes: int = 1024 * 1024

    idem_methods: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    model_config = {"env_prefix": "IDEMPOTENCY_"}


# ---------------------------------------------------------------------------
# Records & Store protocol
# ---------------------------------------------------------------------------


@dataclass
class IdempotencyRecord:
    key: str
    user_id: Optional[str]
    path: str
    body_hash: str
    state: str
    response_status: Optional[int] = None
    response_body: Optional[bytes] = None
    response_headers: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    expires_at: float = 0.0


class IdempotencyStore(Protocol):
    """Abstract store. Implementations must be atomic on try_acquire."""

    async def try_acquire(self, record: IdempotencyRecord) -> bool:
        """
        Insert in_progress record atomically. Return True if this caller is the
        owner; False if another caller already holds the key.
        """

    async def fetch(self, key: str) -> Optional[IdempotencyRecord]:
        ...

    async def mark_terminal(
        self,
        key: str,
        status: int,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        ...

    async def release_inflight(self, key: str) -> None:
        """Delete an in_progress row (used when the response is non-terminal)."""

    async def purge_expired(self, now: float) -> int:
        ...


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def canonical_body_hash(body: bytes) -> str:
    """SHA-256 over canonicalized body.

    - If body is valid JSON: re-serialize with sorted keys + no whitespace.
    - Otherwise: hash raw bytes.
    """
    if not body:
        return hashlib.sha256(b"").hexdigest()
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return hashlib.sha256(body).hexdigest()
    canon = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canon).hexdigest()


def is_terminal_status(status: int) -> bool:
    """
    A response is terminal (cacheable) if it represents a final resolution of the
    request. 202 Accepted and 102 Processing are NOT terminal.
    """
    if status == 202 or status == 102:
        return False
    return 200 <= status < 600


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


UserExtractor = Callable[[Request], Optional[str]]


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        store: IdempotencyStore,
        settings: Optional[IdempotencySettings] = None,
        user_id_extractor: Optional[UserExtractor] = None,
    ) -> None:
        super().__init__(app)
        self.store = store
        self.settings = settings or IdempotencySettings()
        self.user_id_extractor = user_id_extractor or (
            lambda r: r.headers.get("x-user-id")
        )

    def _ttl_for(self, path: str) -> int:
        for prefix in self.settings.long_ttl_path_prefixes:
            if path.startswith(prefix):
                return self.settings.long_ttl_seconds
        return self.settings.default_ttl_seconds

    def _path_requires_key(self, path: str) -> bool:
        return any(
            path.startswith(p) for p in self.settings.required_path_prefixes
        )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self.settings.enabled:
            return await call_next(request)

        if request.method not in self.settings.idem_methods:
            return await call_next(request)

        key = request.headers.get("idempotency-key")
        path = request.url.path

        if not key:
            if self._path_requires_key(path):
                return JSONResponse(
                    {
                        "error": "idempotency_key_required",
                        "message": (
                            "Idempotency-Key header is required for this endpoint."
                        ),
                    },
                    status_code=400,
                )
            return await call_next(request)

        if len(key) > self.settings.max_key_length:
            return JSONResponse(
                {"error": "idempotency_key_too_long"}, status_code=400
            )

        body = await request.body()
        body_hash = canonical_body_hash(body)
        user_id = self.user_id_extractor(request)

        now = time.time()
        record = IdempotencyRecord(
            key=key,
            user_id=user_id,
            path=path,
            body_hash=body_hash,
            state=STATE_IN_PROGRESS,
            created_at=now,
            expires_at=now + self._ttl_for(path),
        )

        acquired = await self.store.try_acquire(record)

        if not acquired:
            return await self._handle_non_owner(key, body_hash)

        # We own the key. Rebuild the request so the handler can still read body.
        async def _receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _receive  # type: ignore[attr-defined]

        try:
            response = await call_next(request)
        except Exception:
            await self.store.release_inflight(key)
            raise

        # Capture response body
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            chunks.append(chunk)
        payload = b"".join(chunks)

        status = response.status_code
        headers = {k: v for k, v in response.headers.items()}

        if is_terminal_status(status) and len(payload) <= self.settings.max_cached_body_bytes:
            await self.store.mark_terminal(key, status, payload, headers)
        else:
            # Non-terminal (202 pending, etc.) or body too large: do not cache.
            await self.store.release_inflight(key)

        return Response(
            content=payload,
            status_code=status,
            headers=headers,
            media_type=response.media_type,
        )

    async def _handle_non_owner(self, key: str, body_hash: str) -> Response:
        """Another request owns the key. Validate body and poll for terminal."""
        existing = await self.store.fetch(key)
        if existing is None:
            # Race: owner released between try_acquire and fetch. Safest: 409.
            return JSONResponse(
                {"error": "idempotency_race_retry"}, status_code=409
            )
        if existing.body_hash != body_hash:
            return JSONResponse(
                {
                    "error": "idempotency_key_reused_with_different_body",
                    "message": (
                        "Idempotency-Key was previously used with a different "
                        "request body."
                    ),
                },
                status_code=409,
            )

        if existing.state == STATE_TERMINAL:
            return self._replay(existing)

        # Poll
        deadline = time.time() + self.settings.inflight_poll_timeout_seconds
        interval = self.settings.inflight_poll_interval_ms / 1000.0
        while time.time() < deadline:
            await asyncio.sleep(interval)
            current = await self.store.fetch(key)
            if current is None:
                # Owner released (non-terminal path). Tell client to retry.
                return JSONResponse(
                    {"error": "idempotency_owner_released_retry"}, status_code=409
                )
            if current.state == STATE_TERMINAL:
                return self._replay(current)
            interval = min(interval * 1.5, 1.0)

        return JSONResponse(
            {
                "error": "idempotency_original_request_timeout",
                "message": "Original request still processing after timeout.",
            },
            status_code=409,
        )

    def _replay(self, rec: IdempotencyRecord) -> Response:
        assert rec.response_status is not None
        return Response(
            content=rec.response_body or b"",
            status_code=rec.response_status,
            headers={**rec.response_headers, "x-idempotent-replay": "true"},
        )
