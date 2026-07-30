# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""provider_failover.py — circuit-breaker failover for notification providers.

Routes each send to a primary provider and falls back to a secondary when the
primary's circuit is open. Companion module for Chapter 33c.

A provider is any callable ``send(message) -> bool``. The breaker opens after
``failure_threshold`` consecutive failures and half-opens after
``reset_timeout`` seconds, at which point a single trial send decides whether
it closes again.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_timeout: float = 30.0
    _failures: int = 0
    _opened_at: float | None = None
    state: CircuitState = CircuitState.CLOSED

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.state is CircuitState.OPEN:
            assert self._opened_at is not None
            if now - self._opened_at >= self.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self.state = CircuitState.CLOSED

    def record_failure(self, now: float | None = None) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold or self.state is CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self._opened_at = time.monotonic() if now is None else now


@dataclass
class FailoverRouter:
    """Primary/secondary router guarded by an independent breaker each."""

    primary: Callable[[Any], bool]
    secondary: Callable[[Any], bool]
    primary_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    secondary_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def send(self, message: Any) -> str:
        """Return the name of the provider that accepted the message.

        Raises RuntimeError if neither provider could deliver.
        """
        for name, provider, breaker in (
            ("primary", self.primary, self.primary_breaker),
            ("secondary", self.secondary, self.secondary_breaker),
        ):
            if not breaker.allow():
                continue
            try:
                if provider(message):
                    breaker.record_success()
                    return name
                breaker.record_failure()
            except Exception:  # provider raised — treat as a failure
                breaker.record_failure()
        raise RuntimeError("all notification providers unavailable")
