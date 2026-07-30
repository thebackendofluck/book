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
platform.retry — Retry Decorator with Exponential Backoff
==========================================================

Transient failures are inevitable in distributed systems: network timeouts,
temporary DNS failures, database connection pool exhaustion, and supplier
API rate limits all cause operations to fail and succeed on a subsequent
attempt.

This module provides a ``@with_retry`` decorator that automatically retries
a function on configurable exception types with:

* **Exponential backoff** — waiting time doubles after each failure.
* **Full jitter** — random component added to backoff to prevent
  thundering-herd problems when many workers retry simultaneously.
* **Max-attempts cap** — prevents infinite loops.
* **Selective exception handling** — only retry on the exception types
  listed in ``retryable_on``; re-raise immediately for others.
* **Async support** — works with both regular and ``async def`` functions.

Exponential backoff with jitter
---------------------------------
After the *n*-th failure, the sleep duration is sampled uniformly from
``[0, base_delay * (2 ** n)]``.  This is "Full Jitter" as described in
the AWS Architecture Blog post on retries (Cohn et al., 2015).

Example::

    @with_retry(RetryConfig(max_attempts=3, base_delay=0.5))
    def call_supplier_api() -> dict:
        ...  # May raise requests.Timeout

    # Async variant
    @with_retry(RetryConfig(max_attempts=5, base_delay=1.0, max_delay=30.0))
    async def fetch_balance(player_id: str) -> WalletSnapshot:
        ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryConfig:
    """Configuration for the retry decorator.

    Attributes
    ----------
    max_attempts:
        Total number of attempts (including the first).  A value of 1 means
        no retries — the function is called once and any exception propagates.
    base_delay:
        Minimum backoff duration in seconds after the first failure.
    max_delay:
        Upper cap on the backoff duration in seconds.  Prevents the delay
        from growing unboundedly on long retry sequences.
    retryable_on:
        Tuple of exception types that should trigger a retry.  Defaults to
        ``(Exception,)`` which retries on any exception.  Narrow this to
        specific transient errors (e.g. ``(TimeoutError, ConnectionError)``)
        to avoid retrying on deterministic failures.
    on_retry:
        Optional callback invoked before each retry with the exception
        instance and attempt number.  Useful for logging or metrics.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    retryable_on: tuple[type[Exception], ...] = field(default=(Exception,))
    on_retry: Callable[[Exception, int], None] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.base_delay < 0:
            raise ValueError(f"base_delay must be non-negative, got {self.base_delay}")
        if self.max_delay < self.base_delay:
            raise ValueError(
                f"max_delay ({self.max_delay}) must be >= base_delay ({self.base_delay})"
            )


# ---------------------------------------------------------------------------
# Backoff calculation
# ---------------------------------------------------------------------------


def _jittered_delay(attempt: int, base: float, ceiling: float) -> float:
    """Compute a full-jitter exponential backoff delay.

    Parameters
    ----------
    attempt:
        Zero-based failure count (0 = after first failure).
    base:
        Minimum delay in seconds.
    ceiling:
        Maximum delay cap in seconds.

    Returns
    -------
    float
        Seconds to sleep before the next attempt.
    """
    exponential = min(ceiling, base * (2 ** attempt))
    return random.uniform(0, exponential)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def with_retry(config: RetryConfig | None = None) -> Callable[[F], F]:
    """Return a decorator that wraps a function with retry logic.

    The decorator supports both synchronous and asynchronous functions.
    For async functions it uses ``asyncio.sleep``; for sync functions it
    uses ``time.sleep``.

    Parameters
    ----------
    config:
        Retry configuration.  Defaults to :class:`RetryConfig` with
        ``max_attempts=3`` and ``base_delay=0.5``.

    Returns
    -------
    Callable
        A decorator that wraps the target function.

    Examples
    --------
    ::

        @with_retry()
        def fetch() -> dict:
            return requests.get("https://example.com/api", timeout=10).json()

        @with_retry(RetryConfig(max_attempts=5, base_delay=1.0))
        async def get_balance(player_id: str) -> WalletSnapshot:
            return await wallet_client.get_balance(player_id)
    """
    cfg = config or RetryConfig()

    def decorator(func: F) -> F:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception = Exception("retry exhausted")
                for attempt in range(cfg.max_attempts):
                    try:
                        return await func(*args, **kwargs)
                    except cfg.retryable_on as exc:
                        last_exc = exc
                        if attempt == cfg.max_attempts - 1:
                            break
                        delay = _jittered_delay(attempt, cfg.base_delay, cfg.max_delay)
                        if cfg.on_retry is not None:
                            cfg.on_retry(exc, attempt + 1)
                        logger.warning(
                            {
                                "event": "retry",
                                "function": func.__qualname__,
                                "attempt": attempt + 1,
                                "max_attempts": cfg.max_attempts,
                                "delay_seconds": round(delay, 3),
                                "error": str(exc),
                            }
                        )
                        await asyncio.sleep(delay)
                raise last_exc

            return async_wrapper  # type: ignore[return-value]

        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exc: Exception = Exception("retry exhausted")
                for attempt in range(cfg.max_attempts):
                    try:
                        return func(*args, **kwargs)
                    except cfg.retryable_on as exc:
                        last_exc = exc
                        if attempt == cfg.max_attempts - 1:
                            break
                        delay = _jittered_delay(attempt, cfg.base_delay, cfg.max_delay)
                        if cfg.on_retry is not None:
                            cfg.on_retry(exc, attempt + 1)
                        logger.warning(
                            {
                                "event": "retry",
                                "function": func.__qualname__,
                                "attempt": attempt + 1,
                                "max_attempts": cfg.max_attempts,
                                "delay_seconds": round(delay, 3),
                                "error": str(exc),
                            }
                        )
                        time.sleep(delay)
                raise last_exc

            return sync_wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["RetryConfig", "with_retry"]
