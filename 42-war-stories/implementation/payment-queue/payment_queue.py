#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 42, War Stories.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Payment Queueing System for Provider Outages
===============================================

Durable payment queue with retry logic, dead-letter handling, and
provider failover for iGaming payment processing during outages.

Usage:
    python payment_queue.py --demo
    python payment_queue.py --status
"""

import json
import time
import logging
import argparse
import uuid
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from collections import defaultdict
from typing import Optional, Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class TransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    REFUND = "refund"
    PAYOUT = "payout"  # winnings payout


class TransactionStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class RetryStrategy(Enum):
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIXED = "fixed"


@dataclass
class PaymentTransaction:
    id: str
    player_id: str
    type: TransactionType
    amount: float
    currency: str
    provider: str
    method: str              # card, ewallet, bank_transfer, crypto
    status: TransactionStatus = TransactionStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    attempts: int = 0
    max_attempts: int = 5
    last_error: str = ""
    next_retry_at: Optional[str] = None
    provider_ref: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
        self.updated_at = self.created_at


@dataclass
class RetryConfig:
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    base_delay_seconds: float = 5
    max_delay_seconds: float = 300
    max_attempts: int = 5
    multiplier: float = 2.0
    jitter: bool = True

    def get_delay(self, attempt: int) -> float:
        import random
        if self.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.base_delay_seconds * (self.multiplier ** attempt)
        elif self.strategy == RetryStrategy.LINEAR:
            delay = self.base_delay_seconds * (attempt + 1)
        else:
            delay = self.base_delay_seconds
        delay = min(delay, self.max_delay_seconds)
        if self.jitter:
            delay *= (0.5 + random.random())
        return delay


@dataclass
class ProviderStatus:
    name: str
    healthy: bool = True
    last_check: str = ""
    consecutive_failures: int = 0
    circuit_open: bool = False
    failover_provider: Optional[str] = None
    supported_methods: list = field(default_factory=list)


class PaymentQueue:
    """Durable payment queue with retry and failover."""

    def __init__(self, retry_config: Optional[RetryConfig] = None):
        self.retry_config = retry_config or RetryConfig()
        self.queue: list[PaymentTransaction] = []
        self.completed: list[PaymentTransaction] = []
        self.dead_letter: list[PaymentTransaction] = []
        self.providers: dict[str, ProviderStatus] = {}
        self._lock = threading.Lock()
        self._setup_providers()

    def _setup_providers(self):
        self.providers = {
            "stripe": ProviderStatus("stripe", supported_methods=["card"],
                                      failover_provider="adyen"),
            "adyen": ProviderStatus("adyen", supported_methods=["card", "ewallet"],
                                     failover_provider="stripe"),
            "skrill": ProviderStatus("skrill", supported_methods=["ewallet"],
                                      failover_provider="neteller"),
            "neteller": ProviderStatus("neteller", supported_methods=["ewallet"],
                                        failover_provider="skrill"),
            "trustly": ProviderStatus("trustly", supported_methods=["bank_transfer"]),
            "coinbase": ProviderStatus("coinbase", supported_methods=["crypto"]),
        }

    def enqueue(self, tx: PaymentTransaction) -> str:
        """Add transaction to the queue."""
        with self._lock:
            tx.status = TransactionStatus.QUEUED
            tx.updated_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
            self.queue.append(tx)
            logger.info("Queued %s %s: %s %.2f %s (provider: %s)",
                        tx.type.value, tx.id, tx.currency, tx.amount, tx.method, tx.provider)
            return tx.id

    def process_next(self, processor: Callable) -> Optional[dict]:
        """Process the next transaction in the queue."""
        with self._lock:
            ready = [tx for tx in self.queue
                     if tx.status in (TransactionStatus.QUEUED, TransactionStatus.RETRYING)
                     and self._is_ready(tx)]
            if not ready:
                return None
            tx = ready[0]
            tx.status = TransactionStatus.PROCESSING
            tx.attempts += 1
            tx.updated_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]

        # Check provider health and failover if needed
        provider = self._get_active_provider(tx)

        try:
            result = processor(tx, provider)
            with self._lock:
                tx.status = TransactionStatus.COMPLETED
                tx.provider_ref = result.get("provider_ref", "")
                tx.updated_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
                self.queue.remove(tx)
                self.completed.append(tx)
                self._record_provider_success(provider)
            logger.info("Completed %s %s via %s", tx.type.value, tx.id, provider)
            return {"status": "completed", "tx_id": tx.id, "provider": provider}
        except Exception as e:
            with self._lock:
                tx.last_error = str(e)
                tx.updated_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
                self._record_provider_failure(provider)

                if tx.attempts >= tx.max_attempts:
                    tx.status = TransactionStatus.DEAD_LETTER
                    self.queue.remove(tx)
                    self.dead_letter.append(tx)
                    logger.error("Dead-lettered %s %s after %d attempts: %s",
                                 tx.type.value, tx.id, tx.attempts, e)
                    return {"status": "dead_letter", "tx_id": tx.id, "error": str(e)}
                else:
                    delay = self.retry_config.get_delay(tx.attempts)
                    retry_at = datetime.utcnow() + timedelta(seconds=delay)  # ty:ignore[deprecated]
                    tx.status = TransactionStatus.RETRYING
                    tx.next_retry_at = retry_at.isoformat()
                    logger.warning("Retry scheduled for %s %s in %.1fs (attempt %d/%d)",
                                   tx.type.value, tx.id, delay, tx.attempts, tx.max_attempts)
                    return {"status": "retrying", "tx_id": tx.id, "retry_at": tx.next_retry_at}

    def _is_ready(self, tx: PaymentTransaction) -> bool:
        if tx.next_retry_at:
            retry_at = datetime.fromisoformat(tx.next_retry_at)
            return datetime.utcnow() >= retry_at  # ty:ignore[deprecated]
        return True

    def _get_active_provider(self, tx: PaymentTransaction) -> str:
        provider = self.providers.get(tx.provider)
        if provider and provider.circuit_open and provider.failover_provider:
            failover = self.providers.get(provider.failover_provider)
            if failover and not failover.circuit_open:
                logger.info("Failover: %s -> %s for %s", tx.provider, provider.failover_provider, tx.id)
                return provider.failover_provider
        return tx.provider

    def _record_provider_success(self, provider_name: str):
        p = self.providers.get(provider_name)
        if p:
            p.consecutive_failures = 0
            p.healthy = True
            p.circuit_open = False
            p.last_check = datetime.utcnow().isoformat()  # ty:ignore[deprecated]

    def _record_provider_failure(self, provider_name: str):
        p = self.providers.get(provider_name)
        if p:
            p.consecutive_failures += 1
            p.last_check = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
            if p.consecutive_failures >= 3:
                p.circuit_open = True
                p.healthy = False
                logger.warning("Provider %s circuit OPENED after %d failures",
                               provider_name, p.consecutive_failures)

    def get_status(self) -> dict:
        with self._lock:
            return {
                "queue_depth": len(self.queue),
                "completed": len(self.completed),
                "dead_letter": len(self.dead_letter),
                "pending": sum(1 for t in self.queue if t.status == TransactionStatus.QUEUED),
                "retrying": sum(1 for t in self.queue if t.status == TransactionStatus.RETRYING),
                "processing": sum(1 for t in self.queue if t.status == TransactionStatus.PROCESSING),
                "providers": {name: {"healthy": p.healthy, "circuit_open": p.circuit_open,
                                      "failures": p.consecutive_failures}
                              for name, p in self.providers.items()},
                "queue_items": [{"id": t.id, "type": t.type.value, "amount": t.amount,
                                  "status": t.status.value, "attempts": t.attempts,
                                  "provider": t.provider} for t in self.queue[:20]],
            }

    def reprocess_dead_letters(self, processor: Callable) -> list[dict]:
        """Attempt to reprocess dead-lettered transactions."""
        results = []
        with self._lock:
            to_retry = list(self.dead_letter)
            self.dead_letter.clear()
        for tx in to_retry:
            tx.attempts = 0
            tx.status = TransactionStatus.QUEUED
            tx.max_attempts = 3
            self.enqueue(tx)
            result = self.process_next(processor)
            if result:
                results.append(result)
        return results


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    """Simulate payment queue with provider outages."""
    import random

    queue = PaymentQueue(RetryConfig(
        strategy=RetryStrategy.EXPONENTIAL, base_delay_seconds=1,
        max_delay_seconds=10, max_attempts=4))

    def mock_processor(tx: PaymentTransaction, provider: str) -> dict:
        # Simulate provider behavior
        if provider == "stripe" and random.random() < 0.6:
            raise ConnectionError(f"Stripe timeout for ${tx.amount}")
        if provider == "adyen" and random.random() < 0.2:
            raise ConnectionError(f"Adyen error for ${tx.amount}")
        return {"provider_ref": f"{provider}-{uuid.uuid4().hex[:8]}", "status": "success"}

    print("=== Payment Queue Demo: Provider Outage Simulation ===\n")

    # Enqueue transactions
    for i in range(15):
        tx = PaymentTransaction(
            id=f"TX-{i+1:04d}",
            player_id=f"PLR-{random.randint(1000,9999)}",
            type=random.choice([TransactionType.DEPOSIT, TransactionType.WITHDRAWAL]),
            amount=round(random.uniform(10, 500), 2),
            currency="USD",
            provider=random.choice(["stripe", "stripe", "adyen"]),
            method="card",
        )
        queue.enqueue(tx)

    # Process queue
    for round_num in range(25):
        result = queue.process_next(mock_processor)
        if result:
            print(f"  Round {round_num+1:2d}: {result['status']:12s} | {result['tx_id']}")
        else:
            print(f"  Round {round_num+1:2d}: queue empty or waiting for retry")
        time.sleep(0.3)

    print(f"\n=== Final Status ===")
    print(json.dumps(queue.get_status(), indent=2))


def main():
    parser = argparse.ArgumentParser(description="iGaming Payment Queue")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.demo:
        demo()
    else:
        print("Usage: python payment_queue.py --demo")


if __name__ == "__main__":
    main()
