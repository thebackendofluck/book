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
degradation_controller.py
-------------------------
Degradation Controller for the Supplier Integration Control Plane.

Handles supplier outages through a layered degradation strategy:

1. **Circuit Breaker** — Per-supplier circuit breaker that opens after
   consecutive failures, preventing cascading failures from overwhelming
   the platform when a supplier is down.

2. **Graceful Degradation** — When a supplier enters a degraded or
   unreachable state, the controller:
   - Hides affected games from the lobby
   - Shows maintenance banners to active players
   - Redirects to fallback suppliers when available

3. **Auto-Recovery** — Background health checks detect when a supplier
   recovers. The circuit breaker transitions from OPEN -> HALF_OPEN
   (allowing probe traffic) -> CLOSED (fully restored).

Circuit Breaker States
----------------------
    CLOSED    — Normal operation, all requests pass through
    OPEN      — Supplier is down, all requests fail-fast
    HALF_OPEN — Recovery probe: allow a single request through to test

Transition rules:
    CLOSED    → OPEN:      consecutive failures >= threshold
    OPEN      → HALF_OPEN: recovery_timeout has elapsed
    HALF_OPEN → CLOSED:    probe request succeeds
    HALF_OPEN → OPEN:      probe request fails

Usage:
    controller = DegradationController(registry=registry, monitor=monitor)
    # Check if a supplier is available before routing
    if controller.is_available("evolution"):
        route_to_supplier("evolution", request)
    else:
        route_to_fallback("evolution", request)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

from models import HealthStatus, SupplierStatus
from registry import SupplierRegistry, registry as default_registry
from health_monitor import HealthMonitor, monitor as default_monitor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitBreakerConfig:
    """Configuration for a per-supplier circuit breaker."""
    failure_threshold: int = 5
    recovery_timeout_seconds: int = 30
    half_open_max_requests: int = 1
    success_threshold: int = 1  # one successful probe closes from half-open


@dataclass
class CircuitBreaker:
    """
    Per-supplier circuit breaker implementation.

    Tracks consecutive failures and transitions between CLOSED, OPEN,
    and HALF_OPEN states.
    """
    supplier_id: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_state_change: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_trips: int = 0  # how many times it has opened

    def record_success(self) -> None:
        """Record a successful request through this supplier."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self._transition(CircuitState.CLOSED)
                self.failure_count = 0
                self.success_count = 0
                logger.info(
                    "Circuit breaker CLOSED for supplier %s (recovered)",
                    self.supplier_id,
                )
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed request through this supplier."""
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN)
            self.success_count = 0
            logger.warning(
                "Circuit breaker re-OPENED for supplier %s (probe failed)",
                self.supplier_id,
            )
        elif (
            self.state == CircuitState.CLOSED
            and self.failure_count >= self.config.failure_threshold
        ):
            self._transition(CircuitState.OPEN)
            self.total_trips += 1
            logger.warning(
                "Circuit breaker OPENED for supplier %s after %d failures (trip #%d)",
                self.supplier_id,
                self.failure_count,
                self.total_trips,
            )

    def should_allow_request(self) -> bool:
        """
        Determine if a request should be allowed through.

        CLOSED:    always allow
        OPEN:      block unless recovery timeout has elapsed (transition to HALF_OPEN)
        HALF_OPEN: allow up to half_open_max_requests
        """
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = (datetime.now(timezone.utc) - self.last_state_change).total_seconds()
            if elapsed >= self.config.recovery_timeout_seconds:
                self._transition(CircuitState.HALF_OPEN)
                self.success_count = 0
                logger.info(
                    "Circuit breaker HALF_OPEN for supplier %s (probing)",
                    self.supplier_id,
                )
                return True
            return False

        # HALF_OPEN: allow limited traffic
        return self.success_count < self.config.half_open_max_requests

    def _transition(self, new_state: CircuitState) -> None:
        self.state = new_state
        self.last_state_change = datetime.now(timezone.utc)

    def reset(self) -> None:
        """Reset the circuit breaker to CLOSED state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.last_state_change = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier_id": self.supplier_id,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "total_trips": self.total_trips,
            "last_failure_time": (
                self.last_failure_time.isoformat() if self.last_failure_time else None
            ),
            "last_state_change": self.last_state_change.isoformat(),
        }


# ---------------------------------------------------------------------------
# Degradation Actions
# ---------------------------------------------------------------------------


@dataclass
class DegradationAction:
    """Describes an action taken during degradation."""
    supplier_id: str
    action_type: str  # "hide_games", "show_maintenance", "redirect_fallback"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Degradation Controller
# ---------------------------------------------------------------------------


class DegradationController:
    """
    Orchestrates graceful degradation when suppliers experience issues.

    Responsibilities:
    - Manage per-supplier circuit breakers
    - Determine availability based on circuit state + registry status
    - Track fallback mappings between suppliers
    - Execute degradation actions (hide games, show banners)
    - Monitor recovery and restore normal operation

    Parameters
    ----------
    registry:       SupplierRegistry for supplier lookups.
    monitor:        HealthMonitor for health state.
    default_config: Default circuit breaker configuration.
    """

    def __init__(
        self,
        registry: SupplierRegistry = default_registry,
        monitor: HealthMonitor = default_monitor,
        default_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        self._registry = registry
        self._monitor = monitor
        self._default_config = default_config or CircuitBreakerConfig()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._fallback_map: dict[str, str] = {}  # supplier -> fallback_supplier
        self._degradation_log: list[DegradationAction] = []
        self._hidden_suppliers: set[str] = set()

    # ------------------------------------------------------------------
    # Circuit breaker management
    # ------------------------------------------------------------------

    def get_breaker(self, supplier_id: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a supplier."""
        if supplier_id not in self._breakers:
            self._breakers[supplier_id] = CircuitBreaker(
                supplier_id=supplier_id,
                config=CircuitBreakerConfig(
                    failure_threshold=self._default_config.failure_threshold,
                    recovery_timeout_seconds=self._default_config.recovery_timeout_seconds,
                    half_open_max_requests=self._default_config.half_open_max_requests,
                    success_threshold=self._default_config.success_threshold,
                ),
            )
        return self._breakers[supplier_id]

    def set_breaker_config(
        self,
        supplier_id: str,
        config: CircuitBreakerConfig,
    ) -> None:
        """Override the circuit breaker config for a specific supplier."""
        breaker = self.get_breaker(supplier_id)
        breaker.config = config

    def record_success(self, supplier_id: str) -> None:
        """Record a successful interaction with a supplier."""
        breaker = self.get_breaker(supplier_id)
        was_open = breaker.state != CircuitState.CLOSED
        breaker.record_success()

        # If the breaker just closed, restore the supplier
        if was_open and breaker.state == CircuitState.CLOSED:
            self._restore_supplier(supplier_id)

    def record_failure(self, supplier_id: str) -> None:
        """Record a failed interaction with a supplier."""
        breaker = self.get_breaker(supplier_id)
        was_closed = breaker.state == CircuitState.CLOSED
        breaker.record_failure()

        # If the breaker just opened, degrade the supplier
        if was_closed and breaker.state == CircuitState.OPEN:
            self._degrade_supplier(supplier_id)

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def is_available(self, supplier_id: str) -> bool:
        """
        Check if a supplier is available for traffic.

        Considers both registry status and circuit breaker state.
        """
        try:
            record = self._registry.get_supplier(supplier_id)
        except KeyError:
            return False

        if record.status in (SupplierStatus.DISABLED, SupplierStatus.MAINTENANCE):
            return False

        breaker = self.get_breaker(supplier_id)
        return breaker.should_allow_request()

    def get_available_supplier(self, supplier_id: str) -> Optional[str]:
        """
        Return the best available supplier: the primary if available,
        otherwise the configured fallback.
        """
        if self.is_available(supplier_id):
            return supplier_id

        fallback = self._fallback_map.get(supplier_id)
        if fallback and self.is_available(fallback):
            logger.info(
                "Routing to fallback supplier: %s -> %s",
                supplier_id, fallback,
            )
            return fallback

        return None

    # ------------------------------------------------------------------
    # Fallback management
    # ------------------------------------------------------------------

    def set_fallback(self, supplier_id: str, fallback_id: str) -> None:
        """Configure a fallback supplier for degradation scenarios."""
        self._fallback_map[supplier_id] = fallback_id

    def get_fallback(self, supplier_id: str) -> Optional[str]:
        return self._fallback_map.get(supplier_id)

    def remove_fallback(self, supplier_id: str) -> None:
        self._fallback_map.pop(supplier_id, None)

    # ------------------------------------------------------------------
    # Degradation and recovery
    # ------------------------------------------------------------------

    def _degrade_supplier(self, supplier_id: str) -> None:
        """Execute degradation actions for a supplier."""
        self._hidden_suppliers.add(supplier_id)

        action = DegradationAction(
            supplier_id=supplier_id,
            action_type="hide_games",
            details={"reason": "circuit_breaker_open"},
        )
        self._degradation_log.append(action)

        try:
            record = self._registry.get_supplier(supplier_id)
            if record.status == SupplierStatus.ACTIVE:
                self._registry.update_status(supplier_id, SupplierStatus.DEGRADED)
        except KeyError:
            pass

        logger.warning(
            "Supplier %s degraded: games hidden, maintenance banner shown",
            supplier_id,
        )

    def _restore_supplier(self, supplier_id: str) -> None:
        """Restore a supplier after recovery."""
        self._hidden_suppliers.discard(supplier_id)

        action = DegradationAction(
            supplier_id=supplier_id,
            action_type="restore",
            details={"reason": "circuit_breaker_closed"},
        )
        self._degradation_log.append(action)

        try:
            record = self._registry.get_supplier(supplier_id)
            if record.status == SupplierStatus.DEGRADED:
                self._registry.update_status(supplier_id, SupplierStatus.ACTIVE)
        except KeyError:
            pass

        logger.info("Supplier %s restored to active service", supplier_id)

    def is_hidden(self, supplier_id: str) -> bool:
        """Check if a supplier's games are currently hidden."""
        return supplier_id in self._hidden_suppliers

    # ------------------------------------------------------------------
    # Health-driven evaluation
    # ------------------------------------------------------------------

    def evaluate_all_suppliers(self) -> dict[str, dict[str, Any]]:
        """
        Run health checks and evaluate circuit breaker state for all suppliers.

        Returns a status summary per supplier.
        """
        health_results = self._monitor.check_all_suppliers()
        summary: dict[str, dict[str, Any]] = {}

        for supplier_id, health in health_results.items():
            breaker = self.get_breaker(supplier_id)

            if health.status == HealthStatus.HEALTHY:
                breaker.record_success()
            else:
                breaker.record_failure()

            summary[supplier_id] = {
                "health_status": health.status.value,
                "circuit_state": breaker.state.value,
                "available": self.is_available(supplier_id),
                "hidden": self.is_hidden(supplier_id),
                "fallback": self._fallback_map.get(supplier_id),
            }

        return summary

    # ------------------------------------------------------------------
    # Status and metrics
    # ------------------------------------------------------------------

    def get_all_breaker_states(self) -> dict[str, dict[str, Any]]:
        """Return current circuit breaker state for all tracked suppliers."""
        return {
            sid: breaker.to_dict()
            for sid, breaker in self._breakers.items()
        }

    def get_degradation_log(
        self,
        supplier_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[DegradationAction]:
        """Return recent degradation actions, optionally filtered by supplier."""
        log = self._degradation_log
        if supplier_id:
            log = [a for a in log if a.supplier_id == supplier_id]
        return log[-limit:]

    def get_hidden_suppliers(self) -> set[str]:
        return set(self._hidden_suppliers)

    def reset_breaker(self, supplier_id: str) -> None:
        """Manually reset a circuit breaker (e.g., after manual verification)."""
        breaker = self.get_breaker(supplier_id)
        breaker.reset()
        self._restore_supplier(supplier_id)
        logger.info("Circuit breaker manually reset for supplier %s", supplier_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

degradation_controller = DegradationController()
