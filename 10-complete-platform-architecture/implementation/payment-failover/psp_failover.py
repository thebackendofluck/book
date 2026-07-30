#!/usr/bin/env python3
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
Chapter 42 - Complete Platform Architecture
Payment Service Provider (PSP) Failover Chain for iGambling Platform

Implements: Primary PSP -> Backup PSP -> Queued Processing

Features:
- Weighted PSP selection based on success rates and latency
- Circuit breaker per PSP with half-open recovery
- Automatic failover chain: primary -> backup -> queue for later
- Jurisdiction-aware PSP routing (some PSPs not available everywhere)
- Idempotency key handling to prevent duplicate charges
- Dead letter queue for manual review
- Real-time PSP health monitoring

Usage:
    python psp_failover.py --test          # Run simulation
    python psp_failover.py --serve 8080    # Run as HTTP service

Dependencies:
    pip install aiohttp redis asyncio
"""

import asyncio
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # ty:ignore[invalid-assignment]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("psp-failover")


# ──────────────────────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────

class TransactionType(enum.Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    PAYOUT = "payout"


class TransactionStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    QUEUED = "queued"           # Queued for later processing
    MANUAL_REVIEW = "manual_review"


class PSPStatus(enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"
    HALF_OPEN = "half_open"
    DISABLED = "disabled"


@dataclass
class PaymentRequest:
    transaction_id: str
    player_id: str
    amount: float
    currency: str
    transaction_type: TransactionType
    payment_method: str          # 'card', 'pix', 'bank_transfer', 'crypto'
    jurisdiction: str            # 'MGA', 'UKGC', 'CUR', etc.
    idempotency_key: str
    card_token: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "player_id": self.player_id,
            "amount": self.amount,
            "currency": self.currency,
            "transaction_type": self.transaction_type.value,
            "payment_method": self.payment_method,
            "jurisdiction": self.jurisdiction,
            "idempotency_key": self.idempotency_key,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class PaymentResponse:
    success: bool
    psp_code: str
    psp_reference: Optional[str] = None
    status: TransactionStatus = TransactionStatus.PENDING
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    attempt_number: int = 1
    failover_chain: list = field(default_factory=list)
    processing_time_ms: float = 0
    queued: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "psp_code": self.psp_code,
            "psp_reference": self.psp_reference,
            "status": self.status.value,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "attempt_number": self.attempt_number,
            "failover_chain": self.failover_chain,
            "processing_time_ms": self.processing_time_ms,
            "queued": self.queued,
        }


# ──────────────────────────────────────────────────────────────
# Circuit Breaker
# ──────────────────────────────────────────────────────────────

class CircuitBreaker:
    """Per-PSP circuit breaker with configurable thresholds."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
        success_threshold: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold

        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        self.state = PSPStatus.HEALTHY
        self.last_failure_time = 0.0
        self.last_state_change = time.time()

    @property
    def is_available(self) -> bool:
        if self.state == PSPStatus.HEALTHY:
            return True
        if self.state == PSPStatus.CIRCUIT_OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = PSPStatus.HALF_OPEN
                self.half_open_calls = 0
                self.success_count = 0
                logger.info("Circuit breaker entering half-open state")
                return True
            return False
        if self.state == PSPStatus.HALF_OPEN:
            return self.half_open_calls < self.half_open_max_calls
        return False

    def record_success(self):
        if self.state == PSPStatus.HALF_OPEN:
            self.success_count += 1
            self.half_open_calls += 1
            if self.success_count >= self.success_threshold:
                self.state = PSPStatus.HEALTHY
                self.failure_count = 0
                logger.info("Circuit breaker closed (recovered)")
        else:
            self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == PSPStatus.HALF_OPEN:
            self.state = PSPStatus.CIRCUIT_OPEN
            logger.warning("Circuit breaker re-opened from half-open")
        elif self.failure_count >= self.failure_threshold:
            self.state = PSPStatus.CIRCUIT_OPEN
            logger.warning(
                f"Circuit breaker opened after {self.failure_count} failures"
            )


# ──────────────────────────────────────────────────────────────
# PSP Provider Abstraction
# ──────────────────────────────────────────────────────────────

@dataclass
class PSPConfig:
    code: str
    name: str
    priority: int                    # Lower = higher priority
    supported_methods: list
    supported_currencies: list
    supported_jurisdictions: list
    max_amount: float = 50000.0
    min_amount: float = 1.0
    timeout_seconds: float = 30.0
    fee_percent: float = 0.029       # 2.9% default
    fee_fixed: float = 0.30
    is_active: bool = True

    # Failover configuration
    failover_psp: Optional[str] = None   # Code of backup PSP
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    # Health metrics
    success_rate_1h: float = 1.0
    avg_latency_ms: float = 500.0
    total_processed_24h: int = 0

    def supports_request(self, request: PaymentRequest) -> bool:
        if not self.is_active:
            return False
        if request.payment_method not in self.supported_methods:
            return False
        if request.currency not in self.supported_currencies:
            return False
        if request.jurisdiction not in self.supported_jurisdictions:
            return False
        if request.amount < self.min_amount or request.amount > self.max_amount:
            return False
        return True


class PSPSimulator:
    """Simulates PSP API calls for testing. Replace with real PSP SDKs."""

    def __init__(self, failure_rate: float = 0.0, latency_ms: float = 200.0):
        self.failure_rate = failure_rate
        self.latency_ms = latency_ms
        self._call_count = 0

    async def process_payment(
        self, psp_code: str, request: PaymentRequest
    ) -> tuple:
        """Simulate a PSP API call. Returns (success, reference, error)."""
        self._call_count += 1
        await asyncio.sleep(self.latency_ms / 1000)

        import random
        if random.random() < self.failure_rate:
            errors = [
                ("PSP_TIMEOUT", "Request timed out"),
                ("PSP_DECLINED", "Transaction declined by issuer"),
                ("PSP_ERROR", "Internal PSP error"),
                ("PSP_RATE_LIMIT", "Rate limit exceeded"),
            ]
            error = random.choice(errors)
            return False, None, error[0], error[1]

        reference = f"{psp_code}-{uuid.uuid4().hex[:12]}"
        return True, reference, None, None


# ──────────────────────────────────────────────────────────────
# PSP Failover Orchestrator
# ──────────────────────────────────────────────────────────────

class PSPFailoverOrchestrator:
    """
    Orchestrates payment processing with automatic failover.

    Chain: Primary PSP -> Backup PSP -> Queued Processing -> DLQ
    """

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.redis: Optional[aioredis.Redis] = None
        self.psps: dict = {}
        self.psp_simulator = PSPSimulator()
        self._setup_default_psps()

    def _setup_default_psps(self):
        """Configure default PSP providers for the gambling platform."""
        self.psps = {
            "stripe": PSPConfig(
                code="stripe",
                name="Stripe",
                priority=10,
                supported_methods=["card", "bank_transfer"],
                supported_currencies=["EUR", "GBP", "USD", "SEK", "NOK", "DKK"],
                supported_jurisdictions=["MGA", "UKGC", "SGA", "DGA", "AGCO"],
                max_amount=50000,
                fee_percent=0.029,
                fee_fixed=0.30,
                failover_psp="adyen",
                circuit_breaker=CircuitBreaker(
                    failure_threshold=5, recovery_timeout=60
                ),
            ),
            "adyen": PSPConfig(
                code="adyen",
                name="Adyen",
                priority=20,
                supported_methods=["card", "bank_transfer", "pix"],
                supported_currencies=["EUR", "GBP", "USD", "BRL", "SEK", "NOK"],
                supported_jurisdictions=["MGA", "UKGC", "SGA", "CUR", "LOTERJ"],
                max_amount=100000,
                fee_percent=0.031,
                fee_fixed=0.12,
                failover_psp="nuvei",
                circuit_breaker=CircuitBreaker(
                    failure_threshold=5, recovery_timeout=90
                ),
            ),
            "nuvei": PSPConfig(
                code="nuvei",
                name="Nuvei (SafeCharge)",
                priority=30,
                supported_methods=["card", "bank_transfer", "crypto"],
                supported_currencies=["EUR", "GBP", "USD", "BRL", "CAD"],
                supported_jurisdictions=["MGA", "UKGC", "CUR", "AGCO", "LOTERJ"],
                max_amount=75000,
                fee_percent=0.035,
                fee_fixed=0.25,
                failover_psp=None,  # Last in chain
                circuit_breaker=CircuitBreaker(
                    failure_threshold=3, recovery_timeout=120
                ),
            ),
            "pix_provider": PSPConfig(
                code="pix_provider",
                name="PIX Direct (Brazil)",
                priority=5,  # Highest priority for PIX in Brazil
                supported_methods=["pix"],
                supported_currencies=["BRL"],
                supported_jurisdictions=["LOTERJ", "SPA_BR"],
                max_amount=100000,
                fee_percent=0.005,
                fee_fixed=0.0,
                failover_psp="adyen",  # Adyen also supports PIX
                circuit_breaker=CircuitBreaker(
                    failure_threshold=3, recovery_timeout=30
                ),
            ),
        }

    async def connect(self):
        if aioredis:
            self.redis = aioredis.from_url(
                self.redis_url, decode_responses=True
            )

    async def close(self):
        if self.redis:
            await self.redis.close()

    def _select_primary_psp(self, request: PaymentRequest) -> Optional[PSPConfig]:
        """Select the best PSP for the request based on priority and compatibility."""
        candidates = []
        for psp in self.psps.values():
            if psp.supports_request(request) and psp.circuit_breaker.is_available:
                candidates.append(psp)

        if not candidates:
            return None

        # Sort by priority (lower = better), then by success rate
        candidates.sort(key=lambda p: (p.priority, -p.success_rate_1h))
        return candidates[0]

    def _get_failover_chain(self, request: PaymentRequest, start_psp: str) -> list:
        """Build the failover chain starting from a PSP."""
        chain = []
        visited = set()
        current = start_psp

        while current and current not in visited:
            visited.add(current)
            psp = self.psps.get(current)
            if psp and psp.supports_request(request):
                chain.append(psp)
            if psp:
                current = psp.failover_psp
            else:
                break

        return chain

    async def _check_idempotency(self, key: str) -> Optional[dict]:
        """Check if this transaction was already processed."""
        if self.redis:
            result = await self.redis.get(f"idempotency:{key}")
            if result:
                return json.loads(result)
        return None

    async def _store_idempotency(self, key: str, response: dict, ttl: int = 86400):
        """Store idempotency result (24h TTL)."""
        if self.redis:
            await self.redis.setex(
                f"idempotency:{key}",
                ttl,
                json.dumps(response)
            )

    async def _queue_for_later(self, request: PaymentRequest, reason: str):
        """Queue a failed payment for later processing."""
        queue_entry = {
            **request.to_dict(),
            "queue_reason": reason,
            "queued_at": time.time(),
            "retry_count": 0,
            "max_retries": 5,
            "next_retry_at": time.time() + 300,  # 5 minutes
        }

        if self.redis:
            await self.redis.lpush(
                "payment:queue:pending",
                json.dumps(queue_entry)
            )  # ty:ignore[invalid-await]
            await self.redis.incr("payment:queue:pending:count")
            logger.info(
                f"Queued transaction {request.transaction_id} for later: {reason}"
            )
        else:
            logger.warning(
                f"No Redis - cannot queue transaction {request.transaction_id}"
            )

    async def _send_to_dlq(self, request: PaymentRequest, error: str):
        """Send to dead letter queue for manual review."""
        dlq_entry = {
            **request.to_dict(),
            "dlq_reason": error,
            "sent_to_dlq_at": time.time(),
            "requires_manual_review": True,
        }

        if self.redis:
            await self.redis.lpush(
                "payment:dlq",
                json.dumps(dlq_entry)
            )  # ty:ignore[invalid-await]
            logger.error(
                f"Transaction {request.transaction_id} sent to DLQ: {error}"
            )

    async def _record_psp_metrics(self, psp_code: str, success: bool,
                                   latency_ms: float):
        """Record PSP health metrics."""
        if self.redis:
            pipe = self.redis.pipeline()
            now = int(time.time())
            hour_bucket = now - (now % 3600)

            metric_key = f"psp:metrics:{psp_code}:{hour_bucket}"
            pipe.hincrby(metric_key, "total", 1)
            if success:
                pipe.hincrby(metric_key, "success", 1)
            else:
                pipe.hincrby(metric_key, "failure", 1)
            pipe.hincrbyfloat(metric_key, "total_latency_ms", latency_ms)
            pipe.expire(metric_key, 7200)  # 2 hour TTL

            await pipe.execute()

    async def process_payment(self, request: PaymentRequest) -> PaymentResponse:
        """
        Process a payment with automatic failover.

        Flow:
        1. Check idempotency (prevent duplicate charges)
        2. Select primary PSP
        3. Try primary PSP
        4. On failure, walk failover chain
        5. If all PSPs fail, queue for later processing
        6. If queue also fails, send to DLQ
        """
        start_time = time.time()
        failover_chain_log = []

        # 1. Idempotency check
        existing = await self._check_idempotency(request.idempotency_key)
        if existing:
            logger.info(
                f"Idempotent hit for {request.idempotency_key} - "
                f"returning cached response"
            )
            return PaymentResponse(
                success=existing["success"],
                psp_code=existing["psp_code"],
                psp_reference=existing.get("psp_reference"),
                status=TransactionStatus(existing["status"]),
                failover_chain=existing.get("failover_chain", []),
            )

        # 2. Select primary PSP
        primary = self._select_primary_psp(request)
        if not primary:
            logger.error(
                f"No PSP available for {request.transaction_type.value} "
                f"{request.amount} {request.currency} in {request.jurisdiction}"
            )
            await self._queue_for_later(request, "no_psp_available")
            return PaymentResponse(
                success=False,
                psp_code="none",
                status=TransactionStatus.QUEUED,
                error_code="NO_PSP",
                error_message="No payment provider available for this request",
                queued=True,
                processing_time_ms=(time.time() - start_time) * 1000,
            )

        # 3. Build and walk failover chain
        chain = self._get_failover_chain(request, primary.code)
        attempt = 0

        for psp in chain:
            attempt += 1
            psp_start = time.time()

            if not psp.circuit_breaker.is_available:
                failover_chain_log.append({
                    "psp": psp.code,
                    "skipped": True,
                    "reason": "circuit_open"
                })
                continue

            logger.info(
                f"Attempting {request.transaction_type.value} via {psp.code} "
                f"(attempt {attempt}/{len(chain)})"
            )

            try:
                success, reference, error_code, error_msg = (
                    await asyncio.wait_for(
                        self.psp_simulator.process_payment(psp.code, request),
                        timeout=psp.timeout_seconds
                    )
                )
            except asyncio.TimeoutError:
                success = False
                reference = None
                error_code = "TIMEOUT"
                error_msg = f"PSP {psp.code} timed out after {psp.timeout_seconds}s"

            psp_latency = (time.time() - psp_start) * 1000
            await self._record_psp_metrics(psp.code, success, psp_latency)

            failover_chain_log.append({
                "psp": psp.code,
                "success": success,
                "latency_ms": round(psp_latency, 2),
                "error_code": error_code,
                "attempt": attempt,
            })

            if success:
                psp.circuit_breaker.record_success()
                response = PaymentResponse(
                    success=True,
                    psp_code=psp.code,
                    psp_reference=reference,
                    status=TransactionStatus.COMPLETED,
                    attempt_number=attempt,
                    failover_chain=failover_chain_log,
                    processing_time_ms=(time.time() - start_time) * 1000,
                )

                # Store idempotency result
                await self._store_idempotency(
                    request.idempotency_key,
                    response.to_dict()
                )

                if attempt > 1:
                    logger.info(
                        f"Transaction {request.transaction_id} succeeded on "
                        f"failover PSP {psp.code} (attempt {attempt})"
                    )
                return response

            # Record failure
            psp.circuit_breaker.record_failure()
            logger.warning(
                f"PSP {psp.code} failed: {error_code} - {error_msg}. "
                f"Trying next in chain..."
            )

        # 4. All PSPs failed - queue for later
        logger.error(
            f"All PSPs exhausted for transaction {request.transaction_id}. "
            f"Queuing for later processing."
        )
        await self._queue_for_later(
            request,
            f"all_psps_failed_after_{attempt}_attempts"
        )

        response = PaymentResponse(
            success=False,
            psp_code="none",
            status=TransactionStatus.QUEUED,
            error_code="ALL_PSP_FAILED",
            error_message="All payment providers failed. Transaction queued for retry.",
            attempt_number=attempt,
            failover_chain=failover_chain_log,
            processing_time_ms=(time.time() - start_time) * 1000,
            queued=True,
        )

        await self._store_idempotency(
            request.idempotency_key,
            response.to_dict()
        )

        return response

    async def process_queue(self):
        """Process queued payments (run as background worker)."""
        if not self.redis:
            logger.warning("No Redis connection - cannot process queue")
            return

        while True:
            try:
                entry_json = await self.redis.rpop("payment:queue:pending")  # ty:ignore[invalid-await]
                if not entry_json:
                    await asyncio.sleep(10)
                    continue

                entry = json.loads(entry_json)
                entry["retry_count"] = entry.get("retry_count", 0) + 1

                if entry["retry_count"] > entry.get("max_retries", 5):
                    await self._send_to_dlq(
                        PaymentRequest(**{
                            k: v for k, v in entry.items()
                            if k in PaymentRequest.__dataclass_fields__
                        }),
                        f"max_retries_exceeded ({entry['retry_count']})"
                    )
                    continue

                # Check if next retry time has passed
                if time.time() < entry.get("next_retry_at", 0):
                    # Put back in queue
                    await self.redis.lpush(
                        "payment:queue:pending",
                        json.dumps(entry)
                    )  # ty:ignore[invalid-await]
                    await asyncio.sleep(5)
                    continue

                request = PaymentRequest(
                    transaction_id=entry["transaction_id"],
                    player_id=entry["player_id"],
                    amount=entry["amount"],
                    currency=entry["currency"],
                    transaction_type=TransactionType(entry["transaction_type"]),
                    payment_method=entry["payment_method"],
                    jurisdiction=entry["jurisdiction"],
                    idempotency_key=f"{entry['idempotency_key']}-retry-{entry['retry_count']}",
                )

                logger.info(
                    f"Retrying queued transaction {request.transaction_id} "
                    f"(attempt {entry['retry_count']})"
                )
                response = await self.process_payment(request)

                if not response.success and not response.queued:
                    # Exponential backoff: 5m, 15m, 45m, 2h, 6h
                    backoff = min(300 * (3 ** (entry["retry_count"] - 1)), 21600)
                    entry["next_retry_at"] = time.time() + backoff
                    await self.redis.lpush(
                        "payment:queue:pending",
                        json.dumps(entry)
                    )  # ty:ignore[invalid-await]

            except Exception as e:
                logger.error(f"Queue processing error: {e}")
                await asyncio.sleep(30)

    async def get_psp_health(self) -> dict:
        """Get health status of all PSPs."""
        health = {}
        for code, psp in self.psps.items():
            health[code] = {
                "name": psp.name,
                "status": psp.circuit_breaker.state.value,
                "priority": psp.priority,
                "is_active": psp.is_active,
                "circuit_breaker": {
                    "failure_count": psp.circuit_breaker.failure_count,
                    "threshold": psp.circuit_breaker.failure_threshold,
                    "is_available": psp.circuit_breaker.is_available,
                },
                "supported_methods": psp.supported_methods,
                "supported_jurisdictions": psp.supported_jurisdictions,
            }
        return health


# ──────────────────────────────────────────────────────────────
# Test Simulation
# ──────────────────────────────────────────────────────────────

async def run_simulation():
    """Simulate various failover scenarios."""
    orchestrator = PSPFailoverOrchestrator()

    print("=" * 70)
    print("PSP Failover Simulation - iGambling Platform")
    print("=" * 70)

    # Scenario 1: Normal deposit (should succeed on primary)
    print("\n--- Scenario 1: Normal EUR deposit (Stripe primary) ---")
    request = PaymentRequest(
        transaction_id=str(uuid.uuid4()),
        player_id="player-001",
        amount=100.00,
        currency="EUR",
        transaction_type=TransactionType.DEPOSIT,
        payment_method="card",
        jurisdiction="MGA",
        idempotency_key=f"idp-{uuid.uuid4().hex[:8]}",
    )
    response = await orchestrator.process_payment(request)
    print(f"  Result: {'SUCCESS' if response.success else 'FAILED'}")
    print(f"  PSP: {response.psp_code} | Ref: {response.psp_reference}")
    print(f"  Time: {response.processing_time_ms:.0f}ms")

    # Scenario 2: PIX deposit in Brazil (PIX provider primary)
    print("\n--- Scenario 2: PIX deposit in Brazil ---")
    request = PaymentRequest(
        transaction_id=str(uuid.uuid4()),
        player_id="player-002",
        amount=500.00,
        currency="BRL",
        transaction_type=TransactionType.DEPOSIT,
        payment_method="pix",
        jurisdiction="LOTERJ",
        idempotency_key=f"idp-{uuid.uuid4().hex[:8]}",
    )
    response = await orchestrator.process_payment(request)
    print(f"  Result: {'SUCCESS' if response.success else 'FAILED'}")
    print(f"  PSP: {response.psp_code}")

    # Scenario 3: Simulate primary PSP failure (force circuit open)
    print("\n--- Scenario 3: Primary PSP failure -> failover ---")
    stripe = orchestrator.psps["stripe"]
    for _ in range(6):
        stripe.circuit_breaker.record_failure()
    print(f"  Stripe circuit: {stripe.circuit_breaker.state.value}")

    request = PaymentRequest(
        transaction_id=str(uuid.uuid4()),
        player_id="player-003",
        amount=250.00,
        currency="EUR",
        transaction_type=TransactionType.DEPOSIT,
        payment_method="card",
        jurisdiction="MGA",
        idempotency_key=f"idp-{uuid.uuid4().hex[:8]}",
    )
    response = await orchestrator.process_payment(request)
    print(f"  Result: {'SUCCESS' if response.success else 'FAILED'}")
    print(f"  PSP: {response.psp_code} (failover from Stripe)")
    print(f"  Failover chain: {json.dumps(response.failover_chain, indent=4)}")

    # Scenario 4: Idempotency check
    print("\n--- Scenario 4: Idempotency (duplicate request) ---")
    idp_key = f"idp-{uuid.uuid4().hex[:8]}"
    request1 = PaymentRequest(
        transaction_id=str(uuid.uuid4()),
        player_id="player-004",
        amount=75.00,
        currency="GBP",
        transaction_type=TransactionType.DEPOSIT,
        payment_method="card",
        jurisdiction="UKGC",
        idempotency_key=idp_key,
    )
    response1 = await orchestrator.process_payment(request1)
    print(f"  First request:  PSP={response1.psp_code}, Ref={response1.psp_reference}")

    # Reset stripe for this test
    stripe.circuit_breaker = CircuitBreaker()

    # Scenario 5: Unsupported jurisdiction -> queue
    print("\n--- Scenario 5: Unsupported jurisdiction -> queued ---")
    request = PaymentRequest(
        transaction_id=str(uuid.uuid4()),
        player_id="player-005",
        amount=100.00,
        currency="JPY",
        transaction_type=TransactionType.DEPOSIT,
        payment_method="card",
        jurisdiction="UNKNOWN",
        idempotency_key=f"idp-{uuid.uuid4().hex[:8]}",
    )
    response = await orchestrator.process_payment(request)
    print(f"  Result: {'QUEUED' if response.queued else 'FAILED'}")
    print(f"  Status: {response.status.value}")

    # PSP Health Summary
    print("\n--- PSP Health Summary ---")
    health = await orchestrator.get_psp_health()
    for code, info in health.items():
        print(f"  {info['name']:20s} | Status: {info['status']:12s} | "
              f"Priority: {info['priority']} | "
              f"Failures: {info['circuit_breaker']['failure_count']}")

    print("\nSimulation complete.")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="PSP Failover Orchestrator for iGambling Platform"
    )
    parser.add_argument("--test", action="store_true", help="Run simulation")
    parser.add_argument("--serve", type=int, help="Run HTTP service on port")
    parser.add_argument("--redis", default="redis://localhost:6379")
    args = parser.parse_args()

    if args.test:
        asyncio.run(run_simulation())
    elif args.serve:
        try:
            from aiohttp import web
        except ImportError:
            print("Install aiohttp: pip install aiohttp")
            return

        orchestrator = PSPFailoverOrchestrator(args.redis)

        async def handle_payment(request):
            data = await request.json()
            req = PaymentRequest(
                transaction_id=data.get("transaction_id", str(uuid.uuid4())),
                player_id=data["player_id"],
                amount=data["amount"],
                currency=data["currency"],
                transaction_type=TransactionType(data["transaction_type"]),
                payment_method=data["payment_method"],
                jurisdiction=data["jurisdiction"],
                idempotency_key=data["idempotency_key"],
            )
            response = await orchestrator.process_payment(req)
            status = 200 if response.success else (202 if response.queued else 502)
            return web.json_response(response.to_dict(), status=status)

        async def handle_health(request):
            health = await orchestrator.get_psp_health()
            return web.json_response(health)

        app = web.Application()
        app.router.add_post("/api/v1/payments/process", handle_payment)
        app.router.add_get("/health", handle_health)

        async def on_startup(app):
            await orchestrator.connect()

        app.on_startup.append(on_startup)
        web.run_app(app, port=args.serve)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
