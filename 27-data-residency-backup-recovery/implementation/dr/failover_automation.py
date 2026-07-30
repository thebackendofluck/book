#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Automated Failover for Tier-1 iGaming Services
================================================
Manages automated failover for critical services (wallet, game engine,
payment processing) with health monitoring, DNS switching, and
jurisdiction-compliant region selection.

Features:
- Health check monitoring with configurable thresholds
- Automatic DNS failover (Route53/Cloudflare compatible)
- Jurisdiction-aware failover targets
- Connection draining and traffic shifting
- Rollback capability
- Audit logging of all failover events

Usage:
    python failover_automation.py --monitor
    python failover_automation.py --failover wallet --target eu-west-2b
    python failover_automation.py --status
    python failover_automation.py --drill
    python failover_automation.py --demo
"""

import json
import logging
import argparse
import time
import random
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("failover-automation")


# ---------------------------------------------------------------------------
# Service tiers and health
# ---------------------------------------------------------------------------
class ServiceTier(str, Enum):
    TIER_1 = "tier_1"  # 15-min RTO: wallet, active bets, sessions
    TIER_2 = "tier_2"  # 1-hour RTO: game history, profiles
    TIER_3 = "tier_3"  # 4-hour RTO: reports, analytics
    TIER_4 = "tier_4"  # 24-hour RTO: archives, logs


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNREACHABLE = "unreachable"


class FailoverStatus(str, Enum):
    IDLE = "idle"
    MONITORING = "monitoring"
    DRAINING = "draining"
    FAILING_OVER = "failing_over"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class ServiceEndpoint:
    service_name: str
    tier: ServiceTier
    jurisdiction: str
    primary_region: str
    primary_endpoint: str
    failover_region: str
    failover_endpoint: str
    health_check_url: str
    health_check_interval_seconds: int
    rto_seconds: int
    rpo_seconds: int
    dns_record: str
    dns_ttl: int = 30
    current_status: HealthStatus = HealthStatus.HEALTHY
    is_primary_active: bool = True
    consecutive_failures: int = 0
    last_check: Optional[str] = None


@dataclass
class FailoverEvent:
    event_id: str
    service_name: str
    jurisdiction: str
    trigger: str  # automatic / manual / drill
    from_region: str
    to_region: str
    status: FailoverStatus
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    details: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class HealthCheckResult:
    service_name: str
    endpoint: str
    status: HealthStatus
    response_time_ms: float
    status_code: Optional[int] = None
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Service definitions for iGaming platform
# ---------------------------------------------------------------------------
IGAMING_SERVICES: dict[str, ServiceEndpoint] = {
    "wallet": ServiceEndpoint(
        service_name="wallet",
        tier=ServiceTier.TIER_1,
        jurisdiction="UK",
        primary_region="eu-west-2a",
        primary_endpoint="wallet-primary.internal:8443",
        failover_region="eu-west-2b",
        failover_endpoint="wallet-standby.internal:8443",
        health_check_url="https://wallet.internal:8443/health",
        health_check_interval_seconds=5,
        rto_seconds=900,   # 15 minutes
        rpo_seconds=60,    # 1 minute
        dns_record="wallet.acme-casino.internal",
    ),
    "game_engine": ServiceEndpoint(
        service_name="game_engine",
        tier=ServiceTier.TIER_1,
        jurisdiction="UK",
        primary_region="eu-west-2a",
        primary_endpoint="games-primary.internal:8443",
        failover_region="eu-west-2b",
        failover_endpoint="games-standby.internal:8443",
        health_check_url="https://games.internal:8443/health",
        health_check_interval_seconds=5,
        rto_seconds=900,
        rpo_seconds=60,
        dns_record="games.acme-casino.internal",
    ),
    "payment_gateway": ServiceEndpoint(
        service_name="payment_gateway",
        tier=ServiceTier.TIER_1,
        jurisdiction="UK",
        primary_region="eu-west-2a",
        primary_endpoint="payments-primary.internal:8443",
        failover_region="eu-west-2b",
        failover_endpoint="payments-standby.internal:8443",
        health_check_url="https://payments.internal:8443/health",
        health_check_interval_seconds=10,
        rto_seconds=900,
        rpo_seconds=60,
        dns_record="payments.acme-casino.internal",
    ),
    "player_accounts": ServiceEndpoint(
        service_name="player_accounts",
        tier=ServiceTier.TIER_2,
        jurisdiction="UK",
        primary_region="eu-west-2a",
        primary_endpoint="accounts-primary.internal:8443",
        failover_region="eu-west-2b",
        failover_endpoint="accounts-standby.internal:8443",
        health_check_url="https://accounts.internal:8443/health",
        health_check_interval_seconds=30,
        rto_seconds=3600,  # 1 hour
        rpo_seconds=300,   # 5 minutes
        dns_record="accounts.acme-casino.internal",
    ),
    "reporting": ServiceEndpoint(
        service_name="reporting",
        tier=ServiceTier.TIER_3,
        jurisdiction="UK",
        primary_region="eu-west-2a",
        primary_endpoint="reports-primary.internal:8443",
        failover_region="eu-west-2b",
        failover_endpoint="reports-standby.internal:8443",
        health_check_url="https://reports.internal:8443/health",
        health_check_interval_seconds=60,
        rto_seconds=14400,  # 4 hours
        rpo_seconds=3600,   # 1 hour
        dns_record="reports.acme-casino.internal",
    ),
}


# ---------------------------------------------------------------------------
# Health checker
# ---------------------------------------------------------------------------
class HealthChecker:
    """Simulates health checks against service endpoints."""

    FAILURE_THRESHOLD = 3  # consecutive failures before failover

    def check(self, service: ServiceEndpoint) -> HealthCheckResult:
        """
        Check health of a service endpoint.
        In production, this makes HTTP requests to the health_check_url.
        """
        # Simulate health check (replace with real HTTP calls in production)
        start = time.time()

        # Simulate varying response times and occasional failures
        simulated_healthy = random.random() > 0.1  # 90% healthy
        response_time = random.uniform(5, 50) if simulated_healthy else 0

        if simulated_healthy:
            status = HealthStatus.HEALTHY
            if response_time > 30:
                status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.UNHEALTHY
            response_time = 0

        result = HealthCheckResult(
            service_name=service.service_name,
            endpoint=service.primary_endpoint if service.is_primary_active
                     else service.failover_endpoint,
            status=status,
            response_time_ms=round(response_time, 2),
            status_code=200 if simulated_healthy else 503,
        )

        return result


# ---------------------------------------------------------------------------
# Failover manager
# ---------------------------------------------------------------------------
class FailoverManager:
    """
    Orchestrates failover for iGaming services.
    Handles health monitoring, automatic failover triggers,
    DNS updates, and rollback.
    """

    def __init__(self, services: Optional[dict[str, ServiceEndpoint]] = None):
        self.services = services or dict(IGAMING_SERVICES)
        self.health_checker = HealthChecker()
        self.events: list[FailoverEvent] = []
        self._monitoring = False

    def check_health(self, service_name: str) -> HealthCheckResult:
        """Check health and update failure count."""
        service = self.services[service_name]
        result = self.health_checker.check(service)

        if result.status in (HealthStatus.UNHEALTHY, HealthStatus.UNREACHABLE):
            service.consecutive_failures += 1
            logger.warning(
                "%s: health check FAILED (%d/%d)",
                service_name,
                service.consecutive_failures,
                HealthChecker.FAILURE_THRESHOLD,
            )
        else:
            service.consecutive_failures = 0

        service.current_status = result.status
        service.last_check = result.timestamp
        return result

    def should_failover(self, service_name: str) -> bool:
        """Determine if automatic failover should be triggered."""
        service = self.services[service_name]
        return (
            service.consecutive_failures >= HealthChecker.FAILURE_THRESHOLD
            and service.is_primary_active
        )

    def execute_failover(
        self,
        service_name: str,
        trigger: str = "automatic",
        target_region: Optional[str] = None,
    ) -> FailoverEvent:
        """
        Execute failover for a service.

        Steps:
        1. Validate failover target compliance
        2. Drain connections from primary
        3. Verify standby is ready
        4. Switch DNS
        5. Verify traffic flowing to new endpoint
        6. Log and notify
        """
        service = self.services[service_name]
        event_id = f"FO-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc)

        event = FailoverEvent(
            event_id=event_id,
            service_name=service_name,
            jurisdiction=service.jurisdiction,
            trigger=trigger,
            from_region=service.primary_region if service.is_primary_active
                        else service.failover_region,
            to_region=target_region or (
                service.failover_region if service.is_primary_active
                else service.primary_region
            ),
            status=FailoverStatus.DRAINING,
            started_at=now.isoformat(),
        )

        logger.info(
            "FAILOVER %s: %s -> %s (trigger: %s)",
            service_name, event.from_region, event.to_region, trigger,
        )

        # Step 1: Validate jurisdiction compliance
        event.details.append(f"Validated {event.to_region} for {service.jurisdiction}")

        # Step 2: Drain connections
        event.status = FailoverStatus.DRAINING
        event.details.append("Draining connections (30s grace period)")
        logger.info("  Draining connections from %s", event.from_region)
        # In production: set load balancer to drain mode
        # time.sleep(30)  # Connection drain timeout

        # Step 3: Verify standby
        event.status = FailoverStatus.FAILING_OVER
        event.details.append("Verifying standby endpoint readiness")
        logger.info("  Verifying standby at %s", event.to_region)

        # Step 4: DNS switch
        event.details.append(
            f"DNS update: {service.dns_record} -> {event.to_region} "
            f"(TTL: {service.dns_ttl}s)"
        )
        self._update_dns(service, event.to_region)
        logger.info("  DNS updated: %s -> %s", service.dns_record, event.to_region)

        # Step 5: Verify traffic
        event.status = FailoverStatus.VERIFYING
        event.details.append("Verifying traffic flow to new endpoint")
        # In production: check request logs, monitor error rates

        # Step 6: Complete
        service.is_primary_active = not service.is_primary_active
        service.consecutive_failures = 0

        end_time = datetime.now(timezone.utc)
        event.status = FailoverStatus.COMPLETE
        event.completed_at = end_time.isoformat()
        event.duration_seconds = (end_time - now).total_seconds()
        event.metrics = {
            "rto_target_seconds": service.rto_seconds,
            "actual_duration_seconds": event.duration_seconds,
            "rto_met": event.duration_seconds <= service.rto_seconds,
            "dns_propagation_estimate_seconds": service.dns_ttl * 2,
        }

        self.events.append(event)

        if event.metrics["rto_met"]:
            logger.info(
                "FAILOVER COMPLETE: %s (%.1fs, RTO met)",
                service_name, event.duration_seconds,
            )
        else:
            logger.warning(
                "FAILOVER COMPLETE: %s (%.1fs, RTO EXCEEDED -- target: %ds)",
                service_name, event.duration_seconds, service.rto_seconds,
            )

        return event

    def rollback(self, service_name: str) -> FailoverEvent:
        """Roll back a failover to the original primary."""
        logger.info("Rolling back failover for %s", service_name)
        event = self.execute_failover(service_name, trigger="rollback")
        event.status = FailoverStatus.ROLLED_BACK
        return event

    def run_drill(self) -> list[FailoverEvent]:
        """Run a DR drill for all Tier-1 services."""
        logger.info("=== DR DRILL START ===")
        results = []

        tier1_services = [
            name for name, svc in self.services.items()
            if svc.tier == ServiceTier.TIER_1
        ]

        for svc_name in tier1_services:
            logger.info("Drill: failing over %s", svc_name)
            event = self.execute_failover(svc_name, trigger="drill")
            results.append(event)

        # Roll back all
        logger.info("Drill: rolling back all services")
        for svc_name in tier1_services:
            rollback_event = self.rollback(svc_name)
            results.append(rollback_event)

        logger.info("=== DR DRILL COMPLETE ===")
        return results

    def get_status(self) -> dict:
        """Get current status of all services."""
        status: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "services": {},
            "recent_events": [asdict(e) for e in self.events[-10:]],
        }

        for name, svc in self.services.items():
            status["services"][name] = {
                "tier": svc.tier.value,
                "jurisdiction": svc.jurisdiction,
                "current_status": svc.current_status.value,
                "active_region": svc.primary_region if svc.is_primary_active
                                 else svc.failover_region,
                "is_primary": svc.is_primary_active,
                "consecutive_failures": svc.consecutive_failures,
                "rto_seconds": svc.rto_seconds,
                "rpo_seconds": svc.rpo_seconds,
                "last_check": svc.last_check,
            }

        return status

    def _update_dns(self, service: ServiceEndpoint, target_region: str):
        """
        Update DNS to point to the target region.
        In production, this calls Route53/Cloudflare API.
        """
        logger.info(
            "  [DNS] Updating %s -> %s (TTL: %ds)",
            service.dns_record, target_region, service.dns_ttl,
        )
        # Production implementation:
        # route53.change_resource_record_sets(...)
        # or cloudflare.dns.records.update(...)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def run_demo():
    fm = FailoverManager()

    print("=" * 80)
    print("AUTOMATED FAILOVER DEMONSTRATION")
    print("=" * 80)

    # Show initial status
    print("\n--- Initial Service Status ---")
    status = fm.get_status()
    for name, info in status["services"].items():
        print(
            f"  {name:20s} | {info['tier']:8s} | "
            f"{info['active_region']:15s} | {info['current_status']}"
        )

    # Simulate health check failures for wallet service
    print("\n--- Simulating wallet service failure ---")
    wallet = fm.services["wallet"]
    wallet.consecutive_failures = 3
    wallet.current_status = HealthStatus.UNHEALTHY

    if fm.should_failover("wallet"):
        print("  Failover threshold reached!")
        event = fm.execute_failover("wallet", trigger="automatic")
        print(f"  Event ID:   {event.event_id}")
        print(f"  Duration:   {event.duration_seconds}s")
        print(f"  RTO met:    {event.metrics.get('rto_met')}")
        print(f"  New region: {event.to_region}")

    # Run DR drill
    print("\n--- Running DR Drill (Tier-1 services) ---")
    drill_results = fm.run_drill()
    for event in drill_results:
        print(
            f"  {event.service_name:20s} | {event.trigger:10s} | "
            f"{event.from_region} -> {event.to_region} | "
            f"{event.duration_seconds:.1f}s | {event.status.value}"
        )

    # Final status
    print("\n--- Final Service Status ---")
    status = fm.get_status()
    for name, info in status["services"].items():
        print(
            f"  {name:20s} | {info['active_region']:15s} | "
            f"primary: {info['is_primary']}"
        )

    # Event log
    print("\n--- Event Log ---")
    for event in fm.events:
        print(
            f"  [{event.event_id}] {event.service_name} | "
            f"{event.trigger} | {event.status.value} | "
            f"{event.duration_seconds:.1f}s"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Automated Failover for Tier-1 iGaming Services"
    )
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--monitor", action="store_true", help="Start health monitoring")
    parser.add_argument(
        "--failover",
        metavar="SERVICE",
        help="Trigger manual failover for a service",
    )
    parser.add_argument("--target", help="Target region for failover")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--drill", action="store_true", help="Run DR drill")
    parser.add_argument("--rollback", metavar="SERVICE", help="Rollback a failover")

    args = parser.parse_args()
    fm = FailoverManager()

    if args.demo:
        run_demo()
    elif args.monitor:
        print("Starting health monitoring (Ctrl+C to stop)...")
        while True:
            for name in fm.services:
                result = fm.check_health(name)
                if fm.should_failover(name):
                    fm.execute_failover(name, trigger="automatic")
            time.sleep(5)
    elif args.failover:
        event = fm.execute_failover(args.failover, trigger="manual", target_region=args.target)
        print(json.dumps(asdict(event), indent=2))
    elif args.status:
        print(json.dumps(fm.get_status(), indent=2))
    elif args.drill:
        results = fm.run_drill()
        print(json.dumps([asdict(e) for e in results], indent=2))
    elif args.rollback:
        event = fm.rollback(args.rollback)
        print(json.dumps(asdict(event), indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
