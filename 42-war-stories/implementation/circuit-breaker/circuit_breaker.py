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
Circuit Breaker Implementation for Gambling Microservices
==========================================================

Production-ready circuit breaker pattern for iGaming microservices.
Prevents cascade failures across payment providers, game providers,
and internal services with configurable thresholds and recovery.

Usage:
    python circuit_breaker.py --demo
    python circuit_breaker.py --service payment-gateway --test
"""

import json
import time
import logging
import argparse
import threading
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
from typing import Callable, Optional, Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"          # Normal operation
    OPEN = "open"              # Failing, reject requests
    HALF_OPEN = "half_open"    # Testing recovery


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # failures to open circuit
    success_threshold: int = 3          # successes in half-open to close
    timeout_seconds: float = 30         # time in open state before half-open
    window_seconds: float = 60          # sliding window for failure counting
    max_half_open_calls: int = 3        # concurrent calls allowed in half-open
    excluded_exceptions: list = field(default_factory=list)  # exceptions that don't count
    fallback: Optional[Callable] = None


@dataclass
class CircuitMetrics:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    fallback_calls: int = 0
    state_transitions: list = field(default_factory=list)
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    avg_response_ms: float = 0
    _response_times: list = field(default_factory=list)

    def record_response(self, duration_ms: float):
        self._response_times.append(duration_ms)
        if len(self._response_times) > 100:
            self._response_times = self._response_times[-100:]
        self.avg_response_ms = sum(self._response_times) / len(self._response_times)


class CircuitBreaker:
    """Thread-safe circuit breaker for gambling microservices."""

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.metrics = CircuitMetrics()
        self._lock = threading.Lock()
        self._failures = deque()       # timestamps of recent failures
        self._half_open_calls = 0
        self._consecutive_successes = 0
        self._opened_at: Optional[float] = None

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function through the circuit breaker."""
        with self._lock:
            if not self._can_execute():
                self.metrics.rejected_calls += 1
                if self.config.fallback:
                    self.metrics.fallback_calls += 1
                    return self.config.fallback(*args, **kwargs)
                raise CircuitOpenError(
                    f"Circuit '{self.name}' is {self.state.value}. "
                    f"Failures: {len(self._failures)}/{self.config.failure_threshold}")

            if self.state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1

        start = time.time()
        try:
            result = func(*args, **kwargs)
            duration_ms = (time.time() - start) * 1000
            self._on_success(duration_ms)
            return result
        except Exception as e:
            if type(e) in self.config.excluded_exceptions:
                raise
            duration_ms = (time.time() - start) * 1000
            self._on_failure(duration_ms, e)
            raise

    def _can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if self._opened_at and (time.time() - self._opened_at) >= self.config.timeout_seconds:
                self._transition(CircuitState.HALF_OPEN)
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.config.max_half_open_calls
        return False

    def _on_success(self, duration_ms: float):
        with self._lock:
            self.metrics.total_calls += 1
            self.metrics.successful_calls += 1
            self.metrics.last_success_time = time.time()
            self.metrics.record_response(duration_ms)

            if self.state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                self._half_open_calls -= 1
                if self._consecutive_successes >= self.config.success_threshold:
                    self._transition(CircuitState.CLOSED)

    def _on_failure(self, duration_ms: float, exception: Exception):
        with self._lock:
            self.metrics.total_calls += 1
            self.metrics.failed_calls += 1
            self.metrics.last_failure_time = time.time()
            self.metrics.record_response(duration_ms)

            now = time.time()
            self._failures.append(now)
            cutoff = now - self.config.window_seconds
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()

            if self.state == CircuitState.HALF_OPEN:
                self._half_open_calls -= 1
                self._transition(CircuitState.OPEN)
            elif self.state == CircuitState.CLOSED:
                if len(self._failures) >= self.config.failure_threshold:
                    self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState):
        old_state = self.state
        self.state = new_state
        self.metrics.state_transitions.append({
            "from": old_state.value, "to": new_state.value,
            "at": datetime.now().isoformat(), "timestamp": time.time()
        })
        if new_state == CircuitState.OPEN:
            self._opened_at = time.time()
            self._consecutive_successes = 0
            logger.warning("Circuit '%s' OPENED (failures: %d)", self.name, len(self._failures))
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._consecutive_successes = 0
            logger.info("Circuit '%s' HALF-OPEN (testing recovery)", self.name)
        elif new_state == CircuitState.CLOSED:
            self._failures.clear()
            self._consecutive_successes = 0
            self._opened_at = None
            logger.info("Circuit '%s' CLOSED (recovered)", self.name)

    def get_status(self) -> dict:
        return {
            "name": self.name, "state": self.state.value,
            "metrics": {
                "total_calls": self.metrics.total_calls,
                "successful": self.metrics.successful_calls,
                "failed": self.metrics.failed_calls,
                "rejected": self.metrics.rejected_calls,
                "fallback": self.metrics.fallback_calls,
                "avg_response_ms": round(self.metrics.avg_response_ms, 2),
                "recent_failures": len(self._failures),
            },
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "timeout_seconds": self.config.timeout_seconds,
                "window_seconds": self.config.window_seconds,
            },
            "transitions": self.metrics.state_transitions[-10:],
        }

    def force_open(self):
        with self._lock:
            self._transition(CircuitState.OPEN)

    def force_close(self):
        with self._lock:
            self._transition(CircuitState.CLOSED)


class CircuitOpenError(Exception):
    pass


# ---------------------------------------------------------------------------
# iGaming circuit breaker registry
# ---------------------------------------------------------------------------

class GamblingCircuitBreakerRegistry:
    """Manage circuit breakers for all gambling platform dependencies."""

    DEFAULTS = {
        "payment_provider": CircuitBreakerConfig(
            failure_threshold=3, timeout_seconds=60, window_seconds=120,
            max_half_open_calls=1),
        "game_provider": CircuitBreakerConfig(
            failure_threshold=5, timeout_seconds=30, window_seconds=60),
        "kyc_provider": CircuitBreakerConfig(
            failure_threshold=5, timeout_seconds=45, window_seconds=90),
        "odds_feed": CircuitBreakerConfig(
            failure_threshold=3, timeout_seconds=10, window_seconds=30,
            max_half_open_calls=2),
        "internal_service": CircuitBreakerConfig(
            failure_threshold=10, timeout_seconds=15, window_seconds=60),
    }

    def __init__(self):
        self.breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(self, name: str, service_type: str = "internal_service",
                      config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        if name not in self.breakers:
            cfg = config or self.DEFAULTS.get(service_type, CircuitBreakerConfig())
            self.breakers[name] = CircuitBreaker(name, cfg)
        return self.breakers[name]

    def get_all_status(self) -> list[dict]:
        return [cb.get_status() for cb in self.breakers.values()]

    def get_open_circuits(self) -> list[dict]:
        return [cb.get_status() for cb in self.breakers.values()
                if cb.state != CircuitState.CLOSED]


# ---------------------------------------------------------------------------
# Demo simulation
# ---------------------------------------------------------------------------

def demo():
    """Demonstrate circuit breaker with simulated payment provider."""
    import random

    registry = GamblingCircuitBreakerRegistry()

    # Payment provider with fallback
    def payment_fallback(*args, **kwargs):
        return {"status": "queued", "message": "Payment queued for retry", "provider": "fallback"}

    payment_cb = registry.get_or_create("stripe-payments", "payment_provider",
        CircuitBreakerConfig(failure_threshold=3, timeout_seconds=5, window_seconds=30,
                             fallback=payment_fallback))

    game_cb = registry.get_or_create("evolution-live", "game_provider")

    def simulate_payment(amount):
        if random.random() < 0.7:
            raise ConnectionError("Payment provider timeout")
        return {"status": "success", "amount": amount, "tx_id": f"TX-{random.randint(1000,9999)}"}

    def simulate_game_launch(game_id):
        if random.random() < 0.3:
            raise TimeoutError("Game provider slow response")
        return {"status": "launched", "game_id": game_id, "session": f"GS-{random.randint(1000,9999)}"}

    print("=== Circuit Breaker Demo: iGaming Platform ===\n")

    for i in range(20):
        # Payment calls
        try:
            result = payment_cb.call(simulate_payment, round(random.uniform(10, 500), 2))
            print(f"  [{i+1:2d}] Payment: {result['status']:10s} | Circuit: {payment_cb.state.value}")
        except CircuitOpenError as e:
            print(f"  [{i+1:2d}] Payment: REJECTED    | Circuit: {payment_cb.state.value} (fallback used)")
        except ConnectionError:
            print(f"  [{i+1:2d}] Payment: FAILED      | Circuit: {payment_cb.state.value}")

        # Game launch calls
        try:
            result = game_cb.call(simulate_game_launch, f"game-{random.randint(1,100)}")
            print(f"  [{i+1:2d}] Game:    {result['status']:10s} | Circuit: {game_cb.state.value}")
        except CircuitOpenError:
            print(f"  [{i+1:2d}] Game:    REJECTED    | Circuit: {game_cb.state.value}")
        except TimeoutError:
            print(f"  [{i+1:2d}] Game:    TIMEOUT     | Circuit: {game_cb.state.value}")

        time.sleep(0.5)

    print(f"\n=== Final Status ===")
    for status in registry.get_all_status():
        print(json.dumps(status, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(description="iGaming Circuit Breaker")
    parser.add_argument("--demo", action="store_true", help="Run demo simulation")
    parser.add_argument("--status", action="store_true", help="Show all circuit status")
    args = parser.parse_args()

    if args.demo:
        demo()
    else:
        print("Usage: python circuit_breaker.py --demo")
        print("\nCircuit breaker configurations for iGaming services:")
        for stype, cfg in GamblingCircuitBreakerRegistry.DEFAULTS.items():
            print(f"  {stype:25s} failures={cfg.failure_threshold}, timeout={cfg.timeout_seconds}s, window={cfg.window_seconds}s")


if __name__ == "__main__":
    main()
