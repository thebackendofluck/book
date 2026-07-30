# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
health_monitor.py – Service health aggregation for the internal-alerts service.

Performs periodic health checks against:
  - The Postgres database (alerts store)
  - The Kafka broker (consumer connectivity)
  - The mailer micro-service (outbound email)
  - Configurable third-party endpoints (e.g. Slack webhook liveness)

Exposes a HealthStatus aggregate used by the /health HTTP endpoint and
by the outbox service to pause dispatching when dependencies are unhealthy.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_HEALTH_QUERY = "SELECT 1"
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
MAILER_BASE_URI = os.getenv("MAILER_BASE_URI", "http://mailer-service:8080")
MAILER_HEALTH_PATH = os.getenv("MAILER_HEALTH_PATH", "/health")
HTTP_TIMEOUT = float(os.getenv("HEALTH_HTTP_TIMEOUT", "3"))


# ---------------------------------------------------------------------------
# Health check result
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    healthy: bool
    latency_ms: float = 0.0
    message: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class HealthStatus:
    """Aggregate health across all monitored dependencies."""
    overall: bool
    checks: List[CheckResult]
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "ok" if self.overall else "degraded",
            "checked_at": self.checked_at.isoformat(),
            "checks": [
                {
                    "name": c.name,
                    "healthy": c.healthy,
                    "latency_ms": round(c.latency_ms, 2),
                    "message": c.message,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Individual health checks
# ---------------------------------------------------------------------------


class HealthCheck(ABC):
    """Abstract base class for a single health check."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def check(self) -> CheckResult: ...


class DatabaseHealthCheck(HealthCheck):
    """
    Verifies database connectivity by executing a lightweight SELECT.
    Accepts a callable that executes the query (allows injecting a real
    DB connection or a mock in tests).
    """

    def __init__(self, execute_fn) -> None:
        self._execute = execute_fn

    @property
    def name(self) -> str:
        return "database"

    def check(self) -> CheckResult:
        start = time.monotonic()
        try:
            self._execute(DB_HEALTH_QUERY)
            latency_ms = (time.monotonic() - start) * 1000
            return CheckResult(name=self.name, healthy=True, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            logger.warning("Database health check failed: %s", exc)
            return CheckResult(name=self.name, healthy=False, latency_ms=latency_ms, message=str(exc))


class KafkaHealthCheck(HealthCheck):
    """
    Verifies Kafka connectivity by attempting a metadata fetch.
    Uses confluent-kafka if available, otherwise falls back to kafka-python.
    """

    def __init__(self, bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS) -> None:
        self.bootstrap_servers = bootstrap_servers

    @property
    def name(self) -> str:
        return "kafka"

    def check(self) -> CheckResult:
        start = time.monotonic()
        try:
            self._ping_kafka()
            latency_ms = (time.monotonic() - start) * 1000
            return CheckResult(name=self.name, healthy=True, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            logger.warning("Kafka health check failed: %s", exc)
            return CheckResult(name=self.name, healthy=False, latency_ms=latency_ms, message=str(exc))

    def _ping_kafka(self) -> None:
        """Attempt a cluster metadata request to verify broker reachability."""
        try:
            from confluent_kafka.admin import AdminClient

            admin = AdminClient({"bootstrap.servers": self.bootstrap_servers})
            meta = admin.list_topics(timeout=2)
            if not meta:
                raise RuntimeError("Empty metadata response from Kafka")
        except ImportError:
            from kafka import KafkaAdminClient  # type: ignore
            from kafka.errors import KafkaError  # type: ignore

            client = KafkaAdminClient(
                bootstrap_servers=self.bootstrap_servers.split(","),
                request_timeout_ms=2000,
            )
            client.list_topics()
            client.close()


class HttpHealthCheck(HealthCheck):
    """
    Checks an HTTP service by issuing a GET request to its /health endpoint.
    """

    def __init__(
        self,
        check_name: str,
        url: str,
        timeout: float = HTTP_TIMEOUT,
        expected_status: int = 200,
    ) -> None:
        self._name = check_name
        self.url = url
        self.timeout = timeout
        self.expected_status = expected_status

    @property
    def name(self) -> str:
        return self._name

    def check(self) -> CheckResult:
        start = time.monotonic()
        try:
            resp = httpx.get(self.url, timeout=self.timeout)
            latency_ms = (time.monotonic() - start) * 1000
            healthy = resp.status_code == self.expected_status
            message = None if healthy else f"status {resp.status_code}"
            return CheckResult(name=self.name, healthy=healthy, latency_ms=latency_ms, message=message)
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            logger.warning("%s health check failed: %s", self.name, exc)
            return CheckResult(name=self.name, healthy=False, latency_ms=latency_ms, message=str(exc))


class MailerHealthCheck(HttpHealthCheck):
    """Convenience wrapper for the mailer service health endpoint."""

    def __init__(
        self,
        base_uri: str = MAILER_BASE_URI,
        path: str = MAILER_HEALTH_PATH,
    ) -> None:
        super().__init__(
            check_name="mailer",
            url=f"{base_uri.rstrip('/')}{path}",
        )


# ---------------------------------------------------------------------------
# Health monitor
# ---------------------------------------------------------------------------


class HealthMonitor:
    """
    Aggregates results from all registered health checks.

    Usage
    -----
    monitor = HealthMonitor()
    monitor.register(DatabaseHealthCheck(db.execute))
    monitor.register(KafkaHealthCheck())
    monitor.register(MailerHealthCheck())

    status = monitor.run_checks()
    """

    def __init__(self) -> None:
        self._checks: List[HealthCheck] = []

    def register(self, check: HealthCheck) -> "HealthMonitor":
        self._checks.append(check)
        return self

    def run_checks(self) -> HealthStatus:
        """Execute all checks and aggregate into a HealthStatus."""
        results: List[CheckResult] = []
        for check in self._checks:
            try:
                result = check.check()
                results.append(result)
            except Exception as exc:
                logger.exception("Unexpected error in health check %s", check.name)
                results.append(CheckResult(name=check.name, healthy=False, message=str(exc)))

        overall = all(r.healthy for r in results)
        return HealthStatus(overall=overall, checks=results)

    def is_healthy(self) -> bool:
        """Quick boolean health gate (used by outbox dispatcher before sending)."""
        return all(c.check().healthy for c in self._checks)


# ---------------------------------------------------------------------------
# Default monitor factory
# ---------------------------------------------------------------------------


def build_default_monitor(db_execute_fn=None) -> HealthMonitor:
    """
    Construct a HealthMonitor with the standard set of checks.

    Parameters
    ----------
    db_execute_fn:
        A callable that accepts a SQL string and executes it.  Pass None to
        skip the database check (useful in tests or when DB is not yet available).
    """
    monitor = HealthMonitor()
    if db_execute_fn is not None:
        monitor.register(DatabaseHealthCheck(db_execute_fn))
    monitor.register(KafkaHealthCheck())
    monitor.register(MailerHealthCheck())
    return monitor
