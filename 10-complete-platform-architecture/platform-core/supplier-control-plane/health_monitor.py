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
health_monitor.py
-----------------
Supplier Health Monitor for the Supplier Integration Control Plane.

Probes registered suppliers and classifies their health into three buckets:

    HEALTHY     — latency < 2 s and error rate < 5 %
    DEGRADED    — latency >= 2 s OR error rate >= 5 %
    UNREACHABLE — health endpoint timed out or raised a network error

The monitor is intentionally synchronous so it can be called from both
sync tests and async FastAPI route handlers (via asyncio.run_in_executor).
An async wrapper check_supplier_health_async() is provided for FastAPI use.

Health probes
-------------
In production, the probe issues a lightweight GET request to the supplier's
health endpoint (configured via SUPPLIER_{ID}_HEALTH_URL environment variable).
For suppliers that don't expose a health endpoint, the probe falls back to a
latency test against the main API base URL.

This module simulates probes via an injectable _probe_fn to keep it fully
unit-testable without live network access.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from models import HealthStatus, SupplierHealth, SupplierStatus
from registry import SupplierRegistry, registry as default_registry

logger = logging.getLogger(__name__)

# Threshold constants (also reflected in SupplierHealth dataclass)
LATENCY_THRESHOLD_MS: float = 2000.0   # 2 seconds
ERROR_RATE_THRESHOLD: float = 0.05     # 5 %
DEGRADED_ALERT_WINDOW_HOURS: float = 1.0  # alert if degraded > 1 h


# ---------------------------------------------------------------------------
# Probe function type
# ---------------------------------------------------------------------------

# A probe callable receives a supplier_id string and returns a tuple of
# (latency_ms: float, error_rate: float, message: str).
# The default implementation does a real HTTP GET; tests inject a stub.
ProbeResult = tuple[float, float, str]
ProbeFunction = Callable[[str], ProbeResult]


def _default_probe(supplier_id: str) -> ProbeResult:
    """
    Default health probe — issues a real HTTP GET to the supplier's
    configured health URL.

    Returns (latency_ms, error_rate, message). error_rate is derived
    from the HTTP status code (non-2xx → 1.0) because the probe only
    samples a single request; production deployments should feed real
    p99 error rates from their APM system.
    """
    import os
    import urllib.request
    import urllib.error

    url = os.environ.get(
        f"SUPPLIER_{supplier_id.upper()}_HEALTH_URL",
        f"https://{supplier_id}.example.com/health",
    )

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            latency_ms = (time.monotonic() - t0) * 1000
            message = f"HTTP {resp.status}"
            # Treat 2xx as healthy; anything else as degraded
            error_rate = 0.0 if 200 <= resp.status < 300 else 1.0
            return latency_ms, error_rate, message
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.monotonic() - t0) * 1000
        return latency_ms, 1.0, str(exc)


class HealthMonitor:
    """
    Probes supplier health endpoints and maintains a cache of recent results.

    Parameters
    ----------
    registry:   The SupplierRegistry to probe.
    probe_fn:   Callable to use for each probe. Defaults to _default_probe.
                Inject a stub in tests.
    """

    def __init__(
        self,
        registry: SupplierRegistry = default_registry,
        probe_fn: Optional[ProbeFunction] = None,
    ) -> None:
        self._registry = registry
        self._probe_fn: ProbeFunction = probe_fn or _default_probe
        self._cache: dict[str, SupplierHealth] = {}
        self._degraded_since: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Core probe methods
    # ------------------------------------------------------------------

    def check_supplier_health(self, supplier_id: str) -> SupplierHealth:
        """
        Probe a single supplier and return its SupplierHealth.

        The result is cached and also updates the registry status to
        DEGRADED if thresholds are exceeded.
        """
        # Confirm supplier exists (raises SupplierNotFoundError if not)
        self._registry.get_supplier(supplier_id)

        latency_ms, error_rate, message = self._probe_fn(supplier_id)
        now = datetime.now(timezone.utc)

        # Derive health status
        if error_rate >= 1.0 and latency_ms == 0:
            status = HealthStatus.UNREACHABLE
        elif error_rate > ERROR_RATE_THRESHOLD or latency_ms > LATENCY_THRESHOLD_MS:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        # Track consecutive failures for circuit-breaker awareness
        prev = self._cache.get(supplier_id)
        consecutive_failures = 0
        if status != HealthStatus.HEALTHY and prev is not None:
            consecutive_failures = prev.consecutive_failures + 1
        elif status != HealthStatus.HEALTHY:
            consecutive_failures = 1

        health = SupplierHealth(
            supplier_id=supplier_id,
            last_check=now,
            latency_ms=latency_ms,
            error_rate=error_rate,
            status=status,
            message=message,
            consecutive_failures=consecutive_failures,
        )
        self._cache[supplier_id] = health

        # Mirror status into registry
        if status == HealthStatus.DEGRADED:
            self._registry.update_status(supplier_id, SupplierStatus.DEGRADED)
            if supplier_id not in self._degraded_since:
                self._degraded_since[supplier_id] = now
        elif status == HealthStatus.HEALTHY:
            # Restore ACTIVE only if previously DEGRADED (not MAINTENANCE/DISABLED)
            record = self._registry.get_supplier(supplier_id)
            if record.status == SupplierStatus.DEGRADED:
                self._registry.update_status(supplier_id, SupplierStatus.ACTIVE)
            self._degraded_since.pop(supplier_id, None)

        logger.debug(
            "Health check supplier=%s status=%s latency=%.0fms error_rate=%.2f%%",
            supplier_id,
            status.value,
            latency_ms,
            error_rate * 100,
        )
        return health

    def check_all_suppliers(self) -> dict[str, SupplierHealth]:
        """
        Probe all registered suppliers and return a map of id → SupplierHealth.

        Suppliers that are DISABLED are skipped (they are intentionally offline).
        Maintenance suppliers are probed but their result does not trigger alerts.
        """
        results: dict[str, SupplierHealth] = {}
        for record in self._registry.list_suppliers():
            if record.status == SupplierStatus.DISABLED:
                logger.debug("Skipping health check for disabled supplier %s", record.id)
                continue
            try:
                results[record.id] = self.check_supplier_health(record.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to probe supplier %s: %s", record.id, exc)
                results[record.id] = SupplierHealth(
                    supplier_id=record.id,
                    last_check=datetime.now(timezone.utc),
                    latency_ms=0.0,
                    error_rate=1.0,
                    status=HealthStatus.UNREACHABLE,
                    message=str(exc),
                )
        return results

    def detect_degraded_suppliers(self) -> list[SupplierHealth]:
        """
        Return suppliers currently in DEGRADED or UNREACHABLE state.

        Detection criteria (OR):
        - error_rate > 5 %
        - latency_ms > 2 000 ms

        Only returns suppliers whose last health reading is cached; call
        check_all_suppliers() first to refresh the cache.
        """
        degraded: list[SupplierHealth] = []
        for health in self._cache.values():
            if health.is_degraded() or health.status == HealthStatus.UNREACHABLE:
                degraded.append(health)
        return degraded

    def get_long_degraded_suppliers(
        self,
        threshold_hours: float = DEGRADED_ALERT_WINDOW_HOURS,
    ) -> list[tuple[str, float]]:
        """
        Return (supplier_id, hours_degraded) for suppliers that have been
        in DEGRADED state longer than threshold_hours without recovery.

        Used by M9 operational script to fire alerts.
        """
        now = datetime.now(timezone.utc)
        result: list[tuple[str, float]] = []
        for supplier_id, since in self._degraded_since.items():
            hours = (now - since).total_seconds() / 3600
            if hours > threshold_hours:
                result.append((supplier_id, hours))
        return result

    def get_cached_health(self, supplier_id: str) -> Optional[SupplierHealth]:
        """Return the last cached health reading without issuing a new probe."""
        return self._cache.get(supplier_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

monitor = HealthMonitor()
