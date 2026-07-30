# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
callback_handler.py
-------------------
Unified Callback Processing for the Supplier Integration Control Plane.

Supplier callbacks (also called "webhooks" or "server-to-server notifications")
are the primary mechanism through which game suppliers communicate round results,
wallet operations, and bonus events back to the platform.

Architecture
------------
1. **Signature validation** — Each callback carries an HMAC-SHA256 or RSA
   signature. The handler validates the signature against the supplier's
   registered credentials before processing.

2. **Idempotency** — Every callback must include a unique transaction_id.
   The handler checks an in-memory (or Redis-backed) deduplication store
   before processing. Duplicate callbacks receive 200 OK with the original
   response, preventing double-crediting.

3. **Routing** — After validation and dedup, the callback payload is routed
   to the appropriate domain handler based on callback_type:
     - game_round  -> Game Round Service
     - wallet      -> Wallet Service
     - bonus       -> Bonus Engine

4. **Dead letter queue** — Callbacks that fail processing after all retries
   are routed to a dead-letter queue for manual inspection and replay.

Usage:
    handler = CallbackHandler(registry=registry, credential_manager=cred_mgr)
    result = handler.process_callback(
        supplier_id="evolution",
        brand_id="brand1",
        jurisdiction="GB",
        headers={"X-Signature": "abc123..."},
        payload={"transaction_id": "TXN-001", "callback_type": "game_round", ...},
    )
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

from models import CallbackPolicy
from registry import SupplierRegistry, registry as default_registry
from credential_manager import CredentialManager, credential_manager as default_cred_mgr
from rsa_validator import validate_rsa_sha256

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CallbackType(str, Enum):
    GAME_ROUND = "game_round"
    WALLET = "wallet"
    BONUS = "bonus"
    JACKPOT = "jackpot"
    FREE_SPINS = "free_spins"
    SESSION = "session"


class CallbackStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class SignatureMethod(str, Enum):
    HMAC_SHA256 = "hmac_sha256"
    HMAC_SHA512 = "hmac_sha512"
    RSA_SHA256 = "rsa_sha256"
    NONE = "none"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class CallbackResult:
    """Result of processing a single callback."""
    transaction_id: str
    status: CallbackStatus
    supplier_id: str
    callback_type: str
    response_payload: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    processed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing_time_ms: float = 0.0

    def is_success(self) -> bool:
        return self.status in (CallbackStatus.ACCEPTED, CallbackStatus.DUPLICATE)


@dataclass
class DeadLetterEntry:
    """A callback that failed all processing attempts."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    supplier_id: str = ""
    brand_id: str = ""
    jurisdiction: str = ""
    transaction_id: str = ""
    callback_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    attempts: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------


class SignatureValidator:
    """
    Validates callback signatures using the supplier's registered secret.

    Supported methods:
    - HMAC-SHA256: Header contains hex digest of HMAC(secret, body)
    - HMAC-SHA512: Same as above with SHA-512
    - RSA-SHA256:  Header contains base64-encoded PKCS#1 v1.5 RSA signature.
                   For RSA-configured suppliers, ``Credentials.api_secret``
                   holds the supplier's PEM-encoded RSA public key rather
                   than a symmetric secret; verification is delegated to
                   ``rsa_validator.validate_rsa_sha256``.
    - NONE:        No signature validation (development only)
    """

    def validate_hmac_sha256(
        self,
        body: str,
        secret: str,
        provided_signature: str,
    ) -> bool:
        """Validate HMAC-SHA256 signature."""
        expected = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, provided_signature)

    def validate_hmac_sha512(
        self,
        body: str,
        secret: str,
        provided_signature: str,
    ) -> bool:
        """Validate HMAC-SHA512 signature."""
        expected = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, provided_signature)

    def validate(
        self,
        method: SignatureMethod,
        body: str,
        secret: str,
        provided_signature: str,
    ) -> bool:
        """Dispatch to the appropriate validation method."""
        if method == SignatureMethod.NONE:
            return True
        if method == SignatureMethod.HMAC_SHA256:
            return self.validate_hmac_sha256(body, secret, provided_signature)
        if method == SignatureMethod.HMAC_SHA512:
            return self.validate_hmac_sha512(body, secret, provided_signature)
        if method == SignatureMethod.RSA_SHA256:
            if not secret:
                logger.error(
                    "RSA signature validation attempted with no public key "
                    "configured for this supplier; rejecting"
                )
                return False
            return validate_rsa_sha256(body, secret, provided_signature)
        return False


# ---------------------------------------------------------------------------
# Idempotency store
# ---------------------------------------------------------------------------


class IdempotencyStore:
    """
    In-memory deduplication store for callback transaction IDs.

    Keys are namespaced as (supplier_id, brand_id, transaction_id) rather
    than the bare transaction_id. Suppliers issue transaction IDs from
    their own namespace, which is not guaranteed to be unique across
    brands (two brands on the same supplier, or a supplier reusing IDs
    per-merchant, can otherwise collide). A bare-transaction_id key would
    let a second brand's legitimate callback be treated as a duplicate of
    the first brand's — returning the first brand's cached response
    (cross-tenant leak) and silently dropping a real wallet event.

    Production implementations should use Redis with TTL-based expiry
    to handle multi-instance deployments.

    Parameters
    ----------
    ttl_seconds: How long to remember a transaction ID (default 24h).
    max_entries: Maximum entries before LRU eviction kicks in.
    """

    def __init__(
        self,
        ttl_seconds: int = 86400,
        max_entries: int = 100_000,
    ) -> None:
        self._store: dict[tuple[str, str, str], tuple[datetime, CallbackResult]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_entries = max_entries

    @staticmethod
    def _key(supplier_id: str, brand_id: str, transaction_id: str) -> tuple[str, str, str]:
        return (supplier_id, brand_id, transaction_id)

    def has_seen(self, supplier_id: str, brand_id: str, transaction_id: str) -> bool:
        """Return True if this (supplier, brand, transaction_id) was already processed."""
        key = self._key(supplier_id, brand_id, transaction_id)
        entry = self._store.get(key)
        if entry is None:
            return False
        stored_at, _ = entry
        if datetime.now(timezone.utc) - stored_at > self._ttl:
            del self._store[key]
            return False
        return True

    def get_previous_result(
        self, supplier_id: str, brand_id: str, transaction_id: str
    ) -> Optional[CallbackResult]:
        """Return the cached result for a previously-seen transaction."""
        key = self._key(supplier_id, brand_id, transaction_id)
        entry = self._store.get(key)
        if entry is None:
            return None
        stored_at, result = entry
        if datetime.now(timezone.utc) - stored_at > self._ttl:
            del self._store[key]
            return None
        return result

    def record(
        self, supplier_id: str, brand_id: str, transaction_id: str, result: CallbackResult
    ) -> None:
        """Store the result of a processed callback for deduplication."""
        self._evict_if_needed()
        key = self._key(supplier_id, brand_id, transaction_id)
        self._store[key] = (datetime.now(timezone.utc), result)

    def _evict_if_needed(self) -> None:
        """Remove expired entries or oldest entries if over capacity."""
        now = datetime.now(timezone.utc)
        # First pass: remove expired
        expired = [
            tid for tid, (stored_at, _) in self._store.items()
            if now - stored_at > self._ttl
        ]
        for tid in expired:
            del self._store[tid]

        # Second pass: evict oldest if still over capacity
        if len(self._store) >= self._max_entries:
            sorted_entries = sorted(
                self._store.items(), key=lambda x: x[1][0]
            )
            to_remove = len(self._store) - self._max_entries + 1
            for tid, _ in sorted_entries[:to_remove]:
                del self._store[tid]

    def size(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


# ---------------------------------------------------------------------------
# Domain handler protocol
# ---------------------------------------------------------------------------


class CrossTenantViolation(ValueError):
    """
    Raised when a callback's player_id does not belong to the brand that
    signed the request.

    This is a rejection, not a transient failure — it must not be routed
    to the dead-letter queue for retry.
    """


class PlayerBrandRegistry:
    """
    Maps player_id -> owning brand_id, used to prevent a callback signed
    by one brand's credentials from touching another brand's player.

    Base class always returns None (tenancy unknown / not enforced).
    Wire in a real implementation (backed by the player directory) in
    production.
    """

    def get_owning_brand(self, player_id: str) -> Optional[str]:
        return None


class InMemoryPlayerBrandRegistry(PlayerBrandRegistry):
    """Test/dev registry — explicit player_id -> brand_id assignments."""

    def __init__(self) -> None:
        self._by_player: dict[str, str] = {}

    def assign(self, player_id: str, brand_id: str) -> None:
        self._by_player[player_id] = brand_id

    def get_owning_brand(self, player_id: str) -> Optional[str]:
        return self._by_player.get(player_id)


class DomainHandler:
    """
    Base class for domain-specific callback handlers.

    Each subclass processes a specific callback_type and returns a
    response payload to send back to the supplier.
    """

    def handle(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Process the callback and return a response dict."""
        raise NotImplementedError


def _check_player_belongs_to_brand(
    registry: Optional[PlayerBrandRegistry],
    player_id: str,
    brand_id: str,
) -> None:
    """
    Raise CrossTenantViolation if the registry knows this player belongs
    to a different brand than the one that signed the callback.

    A player unknown to the registry is allowed through (e.g. first
    callback for a not-yet-provisioned player); only a *known* mismatch
    is rejected.
    """
    if registry is None or not player_id:
        return
    owning_brand = registry.get_owning_brand(player_id)
    if owning_brand is not None and owning_brand != brand_id:
        raise CrossTenantViolation(
            f"player {player_id!r} belongs to brand {owning_brand!r}, "
            f"not signing brand {brand_id!r}"
        )


class GameRoundHandler(DomainHandler):
    """Handles game_round callbacks (bet, result, refund)."""

    def __init__(self, player_brand_registry: Optional[PlayerBrandRegistry] = None) -> None:
        self._player_brand_registry = player_brand_registry

    def handle(
        self, supplier_id: str, brand_id: str, jurisdiction: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        action = payload.get("action", "unknown")
        round_id = payload.get("round_id", "")
        player_id = payload.get("player_id", "")
        amount = payload.get("amount", 0)

        _check_player_belongs_to_brand(self._player_brand_registry, player_id, brand_id)

        logger.info(
            "Game round callback: supplier=%s brand=%s action=%s round=%s player=%s amount=%s",
            supplier_id, brand_id, action, round_id, player_id, amount,
        )
        return {
            "status": "ok",
            "round_id": round_id,
            "balance": 0.0,  # placeholder — real impl queries wallet
        }


class WalletHandler(DomainHandler):
    """Handles wallet callbacks (credit, debit, rollback)."""

    def __init__(self, player_brand_registry: Optional[PlayerBrandRegistry] = None) -> None:
        self._player_brand_registry = player_brand_registry

    def handle(
        self, supplier_id: str, brand_id: str, jurisdiction: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        action = payload.get("action", "unknown")
        player_id = payload.get("player_id", "")
        amount = payload.get("amount", 0)

        _check_player_belongs_to_brand(self._player_brand_registry, player_id, brand_id)

        logger.info(
            "Wallet callback: supplier=%s brand=%s action=%s player=%s amount=%s",
            supplier_id, brand_id, action, player_id, amount,
        )
        return {
            "status": "ok",
            "balance": 0.0,
        }


class BonusHandler(DomainHandler):
    """Handles bonus callbacks (award, cancel, expire)."""

    def handle(
        self, supplier_id: str, brand_id: str, jurisdiction: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        action = payload.get("action", "unknown")
        bonus_id = payload.get("bonus_id", "")

        logger.info(
            "Bonus callback: supplier=%s brand=%s action=%s bonus=%s",
            supplier_id, brand_id, action, bonus_id,
        )
        return {
            "status": "ok",
            "bonus_id": bonus_id,
        }


# ---------------------------------------------------------------------------
# Callback Handler (main orchestrator)
# ---------------------------------------------------------------------------


class CallbackHandler:
    """
    Unified callback processor for all supplier integrations.

    Orchestrates:
    1. Signature validation
    2. Idempotency check
    3. Routing to domain handler
    4. Dead letter queueing on failure

    Parameters
    ----------
    registry:               SupplierRegistry for supplier lookups.
    credential_manager:     CredentialManager for retrieving signing secrets.
    idempotency_store:      Store for deduplication (default: in-memory),
                            namespaced on (supplier_id, brand_id, transaction_id).
    signature_method:       Default signature validation method.
    player_brand_registry:  Optional player_id -> owning brand_id lookup,
                            used to reject callbacks whose payload player_id
                            does not belong to the signing brand.
    """

    def __init__(
        self,
        registry: SupplierRegistry = default_registry,
        credential_manager: CredentialManager = default_cred_mgr,
        idempotency_store: Optional[IdempotencyStore] = None,
        signature_method: SignatureMethod = SignatureMethod.HMAC_SHA256,
        player_brand_registry: Optional[PlayerBrandRegistry] = None,
    ) -> None:
        self._registry = registry
        self._cred_mgr = credential_manager
        self._idem_store = idempotency_store or IdempotencyStore()
        self._sig_validator = SignatureValidator()
        self._default_sig_method = signature_method
        self._dead_letter_queue: list[DeadLetterEntry] = []
        self._handlers: dict[str, DomainHandler] = {
            CallbackType.GAME_ROUND.value: GameRoundHandler(player_brand_registry),
            CallbackType.WALLET.value: WalletHandler(player_brand_registry),
            CallbackType.BONUS.value: BonusHandler(),
        }
        self._metrics: dict[str, int] = {
            "total_received": 0,
            "accepted": 0,
            "duplicates": 0,
            "rejected": 0,
            "failed": 0,
            "dead_lettered": 0,
        }

    def register_handler(self, callback_type: str, handler: DomainHandler) -> None:
        """Register a domain handler for a specific callback type."""
        self._handlers[callback_type] = handler

    def process_callback(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        raw_body: str = "",
        signature_method: Optional[SignatureMethod] = None,
    ) -> CallbackResult:
        """
        Process an incoming supplier callback.

        Steps:
        1. Extract transaction_id from payload.
        2. Check idempotency store, keyed on (supplier_id, brand_id,
           transaction_id) — return cached result if duplicate.
        3. Validate signature.
        4. Route to domain handler with brand_id/jurisdiction context.
        5. On failure, add to dead letter queue.
        """
        t0 = time.monotonic()
        self._metrics["total_received"] += 1

        transaction_id = payload.get("transaction_id", "")
        callback_type = payload.get("callback_type", "unknown")

        if not transaction_id:
            result = CallbackResult(
                transaction_id="",
                status=CallbackStatus.REJECTED,
                supplier_id=supplier_id,
                callback_type=callback_type,
                error_message="Missing transaction_id in callback payload",
            )
            self._metrics["rejected"] += 1
            return result

        # Step 2: Idempotency check (namespaced by supplier + brand)
        if self._idem_store.has_seen(supplier_id, brand_id, transaction_id):
            previous = self._idem_store.get_previous_result(supplier_id, brand_id, transaction_id)
            if previous is not None:
                dup_result = CallbackResult(
                    transaction_id=transaction_id,
                    status=CallbackStatus.DUPLICATE,
                    supplier_id=supplier_id,
                    callback_type=callback_type,
                    response_payload=previous.response_payload,
                    processing_time_ms=(time.monotonic() - t0) * 1000,
                )
                self._metrics["duplicates"] += 1
                logger.info(
                    "Duplicate callback: supplier=%s txn=%s",
                    supplier_id, transaction_id,
                )
                return dup_result

        # Step 3: Signature validation
        sig_method = signature_method or self._default_sig_method
        if sig_method != SignatureMethod.NONE:
            try:
                creds = self._cred_mgr.get_credentials(
                    supplier_id, brand_id, jurisdiction,
                )
                provided_sig = headers.get("X-Signature", headers.get("x-signature", ""))
                body_to_verify = raw_body or json.dumps(payload, sort_keys=True)

                if not self._sig_validator.validate(
                    sig_method, body_to_verify, creds.api_secret, provided_sig,
                ):
                    result = CallbackResult(
                        transaction_id=transaction_id,
                        status=CallbackStatus.REJECTED,
                        supplier_id=supplier_id,
                        callback_type=callback_type,
                        error_message="Invalid callback signature",
                        processing_time_ms=(time.monotonic() - t0) * 1000,
                    )
                    self._metrics["rejected"] += 1
                    logger.warning(
                        "Signature validation failed: supplier=%s txn=%s",
                        supplier_id, transaction_id,
                    )
                    return result
            except (ValueError, KeyError) as exc:
                result = CallbackResult(
                    transaction_id=transaction_id,
                    status=CallbackStatus.REJECTED,
                    supplier_id=supplier_id,
                    callback_type=callback_type,
                    error_message=f"Credential lookup failed: {exc}",
                    processing_time_ms=(time.monotonic() - t0) * 1000,
                )
                self._metrics["rejected"] += 1
                return result

        # Step 4: Route to domain handler
        handler = self._handlers.get(callback_type)
        if handler is None:
            result = CallbackResult(
                transaction_id=transaction_id,
                status=CallbackStatus.REJECTED,
                supplier_id=supplier_id,
                callback_type=callback_type,
                error_message=f"Unknown callback_type: {callback_type!r}",
                processing_time_ms=(time.monotonic() - t0) * 1000,
            )
            self._metrics["rejected"] += 1
            return result

        try:
            response_payload = handler.handle(supplier_id, brand_id, jurisdiction, payload)
            result = CallbackResult(
                transaction_id=transaction_id,
                status=CallbackStatus.ACCEPTED,
                supplier_id=supplier_id,
                callback_type=callback_type,
                response_payload=response_payload,
                processing_time_ms=(time.monotonic() - t0) * 1000,
            )
            self._idem_store.record(supplier_id, brand_id, transaction_id, result)
            self._metrics["accepted"] += 1
            return result

        except CrossTenantViolation as exc:
            # Rejection, not a transient failure — never dead-lettered/retried.
            logger.warning(
                "Cross-tenant callback rejected: supplier=%s brand=%s txn=%s error=%s",
                supplier_id, brand_id, transaction_id, exc,
            )
            self._metrics["rejected"] += 1
            return CallbackResult(
                transaction_id=transaction_id,
                status=CallbackStatus.REJECTED,
                supplier_id=supplier_id,
                callback_type=callback_type,
                error_message=str(exc),
                processing_time_ms=(time.monotonic() - t0) * 1000,
            )

        except Exception as exc:
            logger.error(
                "Callback processing failed: supplier=%s txn=%s error=%s",
                supplier_id, transaction_id, exc,
            )
            self._metrics["failed"] += 1
            self._dead_letter(
                supplier_id=supplier_id,
                brand_id=brand_id,
                jurisdiction=jurisdiction,
                transaction_id=transaction_id,
                callback_type=callback_type,
                payload=payload,
                headers=headers,
                error_message=str(exc),
            )
            result = CallbackResult(
                transaction_id=transaction_id,
                status=CallbackStatus.DEAD_LETTERED,
                supplier_id=supplier_id,
                callback_type=callback_type,
                error_message=str(exc),
                processing_time_ms=(time.monotonic() - t0) * 1000,
            )
            return result

    # ------------------------------------------------------------------
    # Dead letter queue
    # ------------------------------------------------------------------

    def _dead_letter(
        self,
        supplier_id: str,
        brand_id: str,
        jurisdiction: str,
        transaction_id: str,
        callback_type: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        error_message: str,
    ) -> DeadLetterEntry:
        entry = DeadLetterEntry(
            supplier_id=supplier_id,
            brand_id=brand_id,
            jurisdiction=jurisdiction,
            transaction_id=transaction_id,
            callback_type=callback_type,
            payload=payload,
            error_message=error_message,
            attempts=1,
            headers=headers,
        )
        self._dead_letter_queue.append(entry)
        self._metrics["dead_lettered"] += 1
        logger.warning(
            "Callback dead-lettered: supplier=%s txn=%s error=%s",
            supplier_id, transaction_id, error_message,
        )
        return entry

    def get_dead_letter_queue(self) -> list[DeadLetterEntry]:
        """Return all entries currently in the dead letter queue."""
        return list(self._dead_letter_queue)

    def get_dead_letter_count(self) -> int:
        return len(self._dead_letter_queue)

    def replay_dead_letter(self, entry_id: str) -> Optional[CallbackResult]:
        """
        Retry processing a dead-lettered callback.

        Removes the entry from the DLQ if processing succeeds.
        """
        entry = None
        for e in self._dead_letter_queue:
            if e.id == entry_id:
                entry = e
                break

        if entry is None:
            return None

        # Re-process with no signature validation (already validated once).
        # brand_id/jurisdiction are preserved from the original callback so
        # the idempotency key and tenant check stay consistent on replay.
        result = self.process_callback(
            supplier_id=entry.supplier_id,
            brand_id=entry.brand_id,
            jurisdiction=entry.jurisdiction,
            headers=entry.headers,
            payload=entry.payload,
            signature_method=SignatureMethod.NONE,
        )

        if result.is_success():
            self._dead_letter_queue = [
                e for e in self._dead_letter_queue if e.id != entry_id
            ]

        return result

    def clear_dead_letter_queue(self) -> int:
        """Remove all entries from the DLQ. Returns number removed."""
        count = len(self._dead_letter_queue)
        self._dead_letter_queue.clear()
        return count

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_metrics(self) -> dict[str, int]:
        return dict(self._metrics)

    def reset_metrics(self) -> None:
        for key in self._metrics:
            self._metrics[key] = 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

callback_handler = CallbackHandler()
