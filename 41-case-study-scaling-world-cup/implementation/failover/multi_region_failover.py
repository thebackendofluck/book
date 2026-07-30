#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 41, Case Study.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Multi-Region Failover Orchestrator
====================================
Manages active-active and active-passive multi-region failover for
a betting platform during major sporting events.

Supports automated health checks, DNS failover (Route 53), database
promotion, and traffic shifting between regions.

Usage:
    python multi_region_failover.py --check-health
    python multi_region_failover.py --failover --from us-east-1 --to eu-west-1
    python multi_region_failover.py --failback --to us-east-1
    python multi_region_failover.py --monitor --interval 10

Requirements:
    pip install boto3 requests
"""

import json
import time
import logging
import argparse
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("multi-region-failover")


class RegionStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    FAILOVER_IN_PROGRESS = "failover_in_progress"
    STANDBY = "standby"


class FailoverMode(Enum):
    ACTIVE_ACTIVE = "active_active"      # Both regions serve traffic
    ACTIVE_PASSIVE = "active_passive"    # One primary, one standby
    ACTIVE_WARM = "active_warm"          # Primary serves, standby pre-warmed


@dataclass
class RegionConfig:
    """Configuration for a single region."""
    region_id: str
    display_name: str
    route53_hosted_zone: str
    dns_record: str
    health_check_endpoints: List[str]
    db_cluster_id: str
    db_role: str  # "primary" or "replica"
    redis_cluster: str
    k8s_context: str
    asg_prefix: str
    weight: int = 100  # DNS weight (0-100)
    status: RegionStatus = RegionStatus.HEALTHY


@dataclass
class HealthCheckResult:
    """Result of a health check for a region."""
    region_id: str
    timestamp: str
    overall_status: RegionStatus
    checks: Dict[str, Dict] = field(default_factory=dict)
    latency_ms: float = 0.0
    error_rate: float = 0.0
    active_connections: int = 0


@dataclass
class FailoverEvent:
    """Record of a failover event."""
    event_id: str
    timestamp: str
    from_region: str
    to_region: str
    reason: str
    duration_seconds: float
    steps_completed: List[str] = field(default_factory=list)
    status: str = "in_progress"  # in_progress, completed, failed, rolled_back


# Region configurations
REGIONS: Dict[str, RegionConfig] = {
    "us-east-1": RegionConfig(
        region_id="us-east-1",
        display_name="US East (Virginia)",
        route53_hosted_zone="Z1234567890ABC",
        dns_record="api.betting-platform.example.com",
        health_check_endpoints=[
            "https://api-use1.betting-platform.example.com/health",
            "https://api-use1.betting-platform.example.com/health/deep",
            "https://api-use1.betting-platform.example.com/health/db",
            "https://api-use1.betting-platform.example.com/health/cache",
            "https://api-use1.betting-platform.example.com/health/queue",
        ],
        db_cluster_id="betting-db-us-east-1",
        db_role="primary",
        redis_cluster="betting-redis-use1",
        k8s_context="arn:aws:eks:us-east-1:123456789:cluster/betting-prod-use1",
        asg_prefix="betting-prod-use1",
        weight=70,
    ),
    "eu-west-1": RegionConfig(
        region_id="eu-west-1",
        display_name="EU West (Ireland)",
        route53_hosted_zone="Z1234567890ABC",
        dns_record="api.betting-platform.example.com",
        health_check_endpoints=[
            "https://api-euw1.betting-platform.example.com/health",
            "https://api-euw1.betting-platform.example.com/health/deep",
            "https://api-euw1.betting-platform.example.com/health/db",
            "https://api-euw1.betting-platform.example.com/health/cache",
            "https://api-euw1.betting-platform.example.com/health/queue",
        ],
        db_cluster_id="betting-db-eu-west-1",
        db_role="replica",
        redis_cluster="betting-redis-euw1",
        k8s_context="arn:aws:eks:eu-west-1:123456789:cluster/betting-prod-euw1",
        asg_prefix="betting-prod-euw1",
        weight=30,
    ),
    "sa-east-1": RegionConfig(
        region_id="sa-east-1",
        display_name="South America (Sao Paulo)",
        route53_hosted_zone="Z1234567890ABC",
        dns_record="api.betting-platform.example.com",
        health_check_endpoints=[
            "https://api-sae1.betting-platform.example.com/health",
            "https://api-sae1.betting-platform.example.com/health/deep",
        ],
        db_cluster_id="betting-db-sa-east-1",
        db_role="replica",
        redis_cluster="betting-redis-sae1",
        k8s_context="arn:aws:eks:sa-east-1:123456789:cluster/betting-prod-sae1",
        asg_prefix="betting-prod-sae1",
        weight=0,
        status=RegionStatus.STANDBY,
    ),
}

# Failover thresholds
THRESHOLDS = {
    "error_rate_degraded": 0.05,    # 5% error rate -> degraded
    "error_rate_unhealthy": 0.15,   # 15% error rate -> unhealthy
    "latency_degraded_ms": 500,     # 500ms avg -> degraded
    "latency_unhealthy_ms": 2000,   # 2s avg -> unhealthy
    "health_check_failures": 3,     # 3 consecutive failures -> failover
    "failover_cooldown_sec": 300,   # 5 min between failovers
}


class HealthChecker:
    """Performs health checks against region endpoints."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.failure_counts: Dict[str, int] = {}

    def check_region(self, region: RegionConfig) -> HealthCheckResult:
        """Run all health checks for a region."""
        result = HealthCheckResult(
            region_id=region.region_id,
            timestamp=datetime.datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            overall_status=RegionStatus.HEALTHY,
        )

        if self.dry_run:
            # Simulate health check results
            result.checks = self._simulate_health_checks(region)
        else:
            result.checks = self._run_health_checks(region)

        # Evaluate overall status
        failed_checks = sum(
            1 for c in result.checks.values() if c["status"] != "ok"
        )
        total_checks = len(result.checks)

        if failed_checks == 0:
            result.overall_status = RegionStatus.HEALTHY
            self.failure_counts[region.region_id] = 0
        elif failed_checks <= total_checks * 0.3:
            result.overall_status = RegionStatus.DEGRADED
        else:
            result.overall_status = RegionStatus.UNHEALTHY
            self.failure_counts.setdefault(region.region_id, 0)
            self.failure_counts[region.region_id] += 1

        # Calculate aggregate metrics
        latencies = [
            c.get("latency_ms", 0) for c in result.checks.values()
            if c.get("latency_ms")
        ]
        result.latency_ms = round(sum(latencies) / len(latencies), 1) if latencies else 0

        return result

    def _simulate_health_checks(self, region: RegionConfig) -> Dict:
        """Simulate health check results for dry-run mode."""
        import random
        checks = {}
        for endpoint in region.health_check_endpoints:
            check_name = endpoint.split("/")[-1]
            latency = random.uniform(10, 150)
            checks[check_name] = {
                "endpoint": endpoint,
                "status": "ok",
                "latency_ms": round(latency, 1),
                "details": "simulated check",
            }
        return checks

    def _run_health_checks(self, region: RegionConfig) -> Dict:
        """Run actual HTTP health checks."""
        import requests
        checks = {}

        for endpoint in region.health_check_endpoints:
            check_name = endpoint.split("/")[-1]
            try:
                start = time.time()
                resp = requests.get(endpoint, timeout=5)
                latency_ms = (time.time() - start) * 1000

                checks[check_name] = {
                    "endpoint": endpoint,
                    "status": "ok" if resp.status_code == 200 else "error",
                    "http_code": resp.status_code,
                    "latency_ms": round(latency_ms, 1),
                    "details": resp.json() if resp.status_code == 200 else resp.text[:200],
                }
            except requests.RequestException as e:
                checks[check_name] = {
                    "endpoint": endpoint,
                    "status": "error",
                    "latency_ms": 0,
                    "details": str(e),
                }

        return checks

    def should_failover(self, region_id: str) -> bool:
        """Determine if a region should be failed over based on consecutive failures."""
        count = self.failure_counts.get(region_id, 0)
        return count >= THRESHOLDS["health_check_failures"]


class FailoverOrchestrator:
    """Orchestrates multi-region failover operations."""

    def __init__(self, dry_run: bool = True, mode: FailoverMode = FailoverMode.ACTIVE_ACTIVE):
        self.dry_run = dry_run
        self.mode = mode
        self.health_checker = HealthChecker(dry_run=dry_run)
        self.last_failover_time: Optional[datetime.datetime] = None
        self.events: List[FailoverEvent] = []

    def check_all_regions(self) -> Dict[str, HealthCheckResult]:
        """Check health of all configured regions."""
        results = {}
        for region_id, config in REGIONS.items():
            if config.status == RegionStatus.STANDBY and self.mode != FailoverMode.ACTIVE_ACTIVE:
                logger.info(f"Skipping standby region: {region_id}")
                continue
            results[region_id] = self.health_checker.check_region(config)
            logger.info(
                f"Region {region_id}: {results[region_id].overall_status.value} "
                f"(latency: {results[region_id].latency_ms}ms)"
            )
        return results

    def execute_failover(
        self, from_region: str, to_region: str, reason: str = "manual",
    ) -> FailoverEvent:
        """Execute a failover from one region to another."""
        event = FailoverEvent(
            event_id=f"fo-{int(time.time())}",
            timestamp=datetime.datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            from_region=from_region,
            to_region=to_region,
            reason=reason,
            duration_seconds=0,
        )
        start_time = time.time()

        # Check cooldown
        if self.last_failover_time:
            elapsed = (datetime.datetime.utcnow() - self.last_failover_time).total_seconds()  # ty:ignore[deprecated]
            if elapsed < THRESHOLDS["failover_cooldown_sec"]:
                logger.warning(
                    f"Failover cooldown active ({elapsed:.0f}s < "
                    f"{THRESHOLDS['failover_cooldown_sec']}s). Skipping."
                )
                event.status = "blocked_cooldown"
                return event

        logger.info(f"{'[DRY RUN] ' if self.dry_run else ''}"
                     f"INITIATING FAILOVER: {from_region} -> {to_region}")

        try:
            # Step 1: Verify target region health
            logger.info("Step 1: Verifying target region health...")
            target_config = REGIONS[to_region]
            target_health = self.health_checker.check_region(target_config)
            if target_health.overall_status == RegionStatus.UNHEALTHY:
                raise RuntimeError(f"Target region {to_region} is unhealthy, aborting failover")
            event.steps_completed.append("target_health_verified")

            # Step 2: Shift DNS traffic
            logger.info("Step 2: Shifting DNS traffic...")
            self._update_dns_weights(from_region, 0, to_region, 100)
            event.steps_completed.append("dns_shifted")

            # Step 3: Promote database replica if needed
            logger.info("Step 3: Checking database promotion...")
            source_config = REGIONS[from_region]
            if source_config.db_role == "primary" and target_config.db_role == "replica":
                self._promote_database(to_region)
                event.steps_completed.append("database_promoted")
            else:
                event.steps_completed.append("database_already_primary")

            # Step 4: Scale up target region
            logger.info("Step 4: Scaling up target region...")
            self._scale_region(to_region, scale_factor=2.0)
            event.steps_completed.append("target_scaled_up")

            # Step 5: Drain connections from source
            logger.info("Step 5: Draining connections from source region...")
            self._drain_connections(from_region)
            event.steps_completed.append("source_drained")

            # Step 6: Update region status
            REGIONS[from_region].status = RegionStatus.UNHEALTHY
            REGIONS[to_region].status = RegionStatus.HEALTHY
            REGIONS[to_region].weight = 100
            REGIONS[from_region].weight = 0
            event.steps_completed.append("status_updated")

            event.status = "completed"
            self.last_failover_time = datetime.datetime.utcnow()  # ty:ignore[deprecated]

        except Exception as e:
            logger.error(f"Failover failed: {e}")
            event.status = "failed"
            # Attempt rollback
            logger.info("Attempting rollback...")
            self._update_dns_weights(
                from_region, REGIONS[from_region].weight,
                to_region, REGIONS[to_region].weight,
            )
            event.steps_completed.append(f"rollback_attempted: {e}")

        event.duration_seconds = round(time.time() - start_time, 2)
        self.events.append(event)

        logger.info(
            f"Failover {'completed' if event.status == 'completed' else 'FAILED'} "
            f"in {event.duration_seconds}s. Steps: {event.steps_completed}"
        )

        return event

    def _update_dns_weights(
        self, region_a: str, weight_a: int, region_b: str, weight_b: int,
    ):
        """Update Route 53 weighted routing weights."""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"DNS weights: {region_a}={weight_a}, {region_b}={weight_b}"
        )

        if self.dry_run:
            return

        try:
            import boto3  # ty:ignore[unresolved-import]
            r53 = boto3.client("route53")
            hosted_zone = REGIONS[region_a].route53_hosted_zone

            changes = []
            for region_id, weight in [(region_a, weight_a), (region_b, weight_b)]:
                config = REGIONS[region_id]
                changes.append({
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": config.dns_record,
                        "Type": "A",
                        "SetIdentifier": region_id,
                        "Weight": weight,
                        "AliasTarget": {
                            "HostedZoneId": hosted_zone,
                            "DNSName": f"alb-{region_id}.betting-platform.example.com",
                            "EvaluateTargetHealth": True,
                        },
                    },
                })

            r53.change_resource_record_sets(
                HostedZoneId=hosted_zone,
                ChangeBatch={"Changes": changes},
            )
        except Exception as e:
            logger.error(f"DNS update failed: {e}")
            raise

    def _promote_database(self, region: str):
        """Promote a read replica to primary in the target region."""
        config = REGIONS[region]
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Promoting DB {config.db_cluster_id} to primary"
        )

        if self.dry_run:
            return

        try:
            import boto3  # ty:ignore[unresolved-import]
            rds = boto3.client("rds", region_name=region)

            # For Aurora Global Database
            rds.failover_global_cluster(
                GlobalClusterIdentifier="betting-global-cluster",
                TargetDbClusterIdentifier=config.db_cluster_id,
            )

            # Wait for promotion
            waiter = rds.get_waiter("db_cluster_available")
            waiter.wait(DBClusterIdentifier=config.db_cluster_id)

            config.db_role = "primary"
        except Exception as e:
            logger.error(f"Database promotion failed: {e}")
            raise

    def _scale_region(self, region: str, scale_factor: float = 2.0):
        """Scale up all services in a region."""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Scaling region {region} by {scale_factor}x"
        )
        # In production, this would use kubectl or ASG APIs

    def _drain_connections(self, region: str):
        """Gracefully drain connections from a region."""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Draining connections from {region} (30s grace period)"
        )
        if not self.dry_run:
            time.sleep(30)

    def execute_failback(self, to_region: str) -> FailoverEvent:
        """Failback to the original primary region after recovery."""
        # Find current primary
        current_primary = None
        for region_id, config in REGIONS.items():
            if config.weight > 0 and config.status == RegionStatus.HEALTHY:
                current_primary = region_id
                break

        if not current_primary:
            logger.error("No healthy primary region found for failback")
            return FailoverEvent(
                event_id=f"fb-{int(time.time())}",
                timestamp=datetime.datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
                from_region="unknown",
                to_region=to_region,
                reason="failback",
                duration_seconds=0,
                status="failed",
            )

        logger.info(f"Failing back from {current_primary} to {to_region}")

        # Gradual traffic shift for failback (safer than instant)
        event = FailoverEvent(
            event_id=f"fb-{int(time.time())}",
            timestamp=datetime.datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            from_region=current_primary,
            to_region=to_region,
            reason="failback",
            duration_seconds=0,
        )
        start_time = time.time()

        # Phase 1: 10% traffic to recovered region
        logger.info("Failback Phase 1: Sending 10% traffic to recovered region")
        self._update_dns_weights(current_primary, 90, to_region, 10)
        event.steps_completed.append("phase1_10pct")

        if not self.dry_run:
            time.sleep(120)  # Monitor for 2 minutes

        # Phase 2: 50/50 split
        logger.info("Failback Phase 2: 50/50 traffic split")
        self._update_dns_weights(current_primary, 50, to_region, 50)
        event.steps_completed.append("phase2_50pct")

        if not self.dry_run:
            time.sleep(120)

        # Phase 3: Restore original weights
        original_weight = 70  # Original primary weight
        logger.info(f"Failback Phase 3: Restoring {to_region} as primary ({original_weight}%)")
        self._update_dns_weights(current_primary, 100 - original_weight, to_region, original_weight)
        event.steps_completed.append("phase3_restored")

        REGIONS[to_region].status = RegionStatus.HEALTHY
        REGIONS[to_region].weight = original_weight
        REGIONS[current_primary].weight = 100 - original_weight

        event.duration_seconds = round(time.time() - start_time, 2)
        event.status = "completed"
        self.events.append(event)

        return event

    def monitor_loop(self, interval_sec: int = 30):
        """Continuous monitoring loop with automatic failover."""
        logger.info(f"Starting failover monitor (interval={interval_sec}s)")

        while True:
            results = self.check_all_regions()

            for region_id, result in results.items():
                if result.overall_status == RegionStatus.UNHEALTHY:
                    if self.health_checker.should_failover(region_id):
                        # Find best failover target
                        target = self._find_failover_target(region_id, results)
                        if target:
                            logger.warning(
                                f"AUTO-FAILOVER triggered: {region_id} -> {target}"
                            )
                            self.execute_failover(
                                region_id, target,
                                reason=f"auto: {THRESHOLDS['health_check_failures']} "
                                       f"consecutive health check failures",
                            )
                        else:
                            logger.error("No healthy failover target available!")

                elif result.overall_status == RegionStatus.DEGRADED:
                    logger.warning(f"Region {region_id} is DEGRADED - monitoring closely")

            if self.dry_run:
                break

            time.sleep(interval_sec)

    def _find_failover_target(
        self, failing_region: str, results: Dict[str, HealthCheckResult],
    ) -> Optional[str]:
        """Find the best healthy region to failover to."""
        candidates = []
        for region_id, result in results.items():
            if region_id == failing_region:
                continue
            if result.overall_status in (RegionStatus.HEALTHY, RegionStatus.DEGRADED):
                candidates.append((region_id, result.latency_ms))

        if not candidates:
            return None

        # Pick the healthiest (lowest latency) candidate
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    def print_status(self, results: Dict[str, HealthCheckResult]):
        """Print a formatted status report."""
        print("\n" + "=" * 80)
        print("  MULTI-REGION FAILOVER STATUS")
        print("=" * 80)

        for region_id, result in results.items():
            config = REGIONS[region_id]
            status_icon = {
                RegionStatus.HEALTHY: "OK",
                RegionStatus.DEGRADED: "WARN",
                RegionStatus.UNHEALTHY: "CRIT",
                RegionStatus.STANDBY: "STBY",
            }.get(result.overall_status, "??")

            print(f"\n  [{status_icon}] {config.display_name} ({region_id})")
            print(f"       Status:   {result.overall_status.value}")
            print(f"       Weight:   {config.weight}%")
            print(f"       DB Role:  {config.db_role}")
            print(f"       Latency:  {result.latency_ms}ms")
            print(f"       Checks:")
            for check_name, check_data in result.checks.items():
                check_status = check_data["status"].upper()
                check_latency = check_data.get("latency_ms", 0)
                print(f"         - {check_name}: {check_status} ({check_latency}ms)")

        if self.events:
            print(f"\n  RECENT FAILOVER EVENTS:")
            for e in self.events[-5:]:
                print(f"    {e.timestamp} | {e.from_region} -> {e.to_region} | "
                      f"{e.status} ({e.duration_seconds}s)")

        print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Region Failover Orchestrator"
    )
    parser.add_argument("--check-health", action="store_true", help="Check all regions")
    parser.add_argument("--failover", action="store_true", help="Execute failover")
    parser.add_argument("--failback", action="store_true", help="Execute failback")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring")
    parser.add_argument("--from", dest="from_region", type=str, help="Source region")
    parser.add_argument("--to", dest="to_region", type=str, help="Target region")
    parser.add_argument("--interval", type=int, default=30, help="Monitor interval (sec)")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", action="store_true")
    args = parser.parse_args()

    dry_run = not args.no_dry_run
    orchestrator = FailoverOrchestrator(dry_run=dry_run)

    if args.check_health:
        results = orchestrator.check_all_regions()
        orchestrator.print_status(results)

    elif args.failover:
        if not args.from_region or not args.to_region:
            logger.error("--from and --to are required for failover")
            return
        event = orchestrator.execute_failover(args.from_region, args.to_region, reason="manual")
        results = orchestrator.check_all_regions()
        orchestrator.print_status(results)

    elif args.failback:
        if not args.to_region:
            logger.error("--to is required for failback")
            return
        event = orchestrator.execute_failback(args.to_region)
        results = orchestrator.check_all_regions()
        orchestrator.print_status(results)

    elif args.monitor:
        orchestrator.monitor_loop(interval_sec=args.interval)

    else:
        # Default: check health and print status
        results = orchestrator.check_all_regions()
        orchestrator.print_status(results)


if __name__ == "__main__":
    main()
