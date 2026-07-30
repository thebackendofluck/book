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
Predictive Auto-Scaler for Sporting Events
===========================================
Integrates with Kubernetes HPA and cloud auto-scaling groups to
pre-scale infrastructure based on sporting event schedules.

Monitors event calendars, correlates with historical traffic patterns,
and triggers scaling actions ahead of predicted demand spikes.

Usage:
    python predictive_autoscaler.py --config autoscaler_config.yaml
    python predictive_autoscaler.py --dry-run --event "2026-06-21T15:00:00Z"
    python predictive_autoscaler.py --mode schedule --lookahead-hours 24

Requirements:
    pip install boto3 kubernetes pyyaml requests
"""

import json
import math
import time
import logging
import argparse
import datetime
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("predictive-autoscaler")


class ScalingPhase(Enum):
    """Phases of the scaling lifecycle for an event."""
    IDLE = "idle"
    PRE_SCALE = "pre_scale"
    PEAK = "peak"
    SUSTAIN = "sustain"
    COOL_DOWN = "cool_down"
    TEARDOWN = "teardown"


@dataclass
class SportingEvent:
    """Represents a sporting event with scaling metadata."""
    event_id: str
    name: str
    sport: str
    competition: str
    kickoff_utc: datetime.datetime
    estimated_duration_min: int
    tier: int  # 1=highest (World Cup Final), 4=lowest
    markets_count: int
    expected_peak_multiplier: float
    regions: List[str] = field(default_factory=list)


@dataclass
class ScalingTarget:
    """A Kubernetes deployment or ASG to scale."""
    name: str
    namespace: str
    resource_type: str  # "deployment", "statefulset", "asg"
    current_replicas: int
    min_replicas: int
    max_replicas: int
    target_replicas: int = 0
    cpu_per_replica: float = 0.0  # vCPUs
    memory_per_replica_gb: float = 0.0
    scale_up_time_sec: int = 60
    region: str = "us-east-1"


@dataclass
class ScalingAction:
    """A planned or executed scaling action."""
    target: str
    action: str  # "scale_up", "scale_down", "pre_warm"
    from_replicas: int
    to_replicas: int
    scheduled_time: datetime.datetime
    executed: bool = False
    execution_time: Optional[datetime.datetime] = None
    dry_run: bool = False


# Scaling profiles per event tier
TIER_PROFILES = {
    1: {
        "pre_scale_minutes": 120,
        "peak_multiplier": 12.0,
        "cool_down_phases": [
            {"after_min": 0, "scale_pct": 100},
            {"after_min": 30, "scale_pct": 70},
            {"after_min": 60, "scale_pct": 40},
            {"after_min": 120, "scale_pct": 10},
        ],
        "services": {
            "api-gateway": {"base_rps_per_pod": 1500, "peak_multiplier": 12.0},
            "betting-engine": {"base_rps_per_pod": 300, "peak_multiplier": 15.0},
            "odds-service": {"base_rps_per_pod": 800, "peak_multiplier": 10.0},
            "user-service": {"base_rps_per_pod": 2000, "peak_multiplier": 8.0},
            "websocket-gateway": {"base_conns_per_pod": 8000, "peak_multiplier": 12.0},
            "settlement-engine": {"base_rps_per_pod": 500, "peak_multiplier": 14.0},
            "cache-proxy": {"base_rps_per_pod": 5000, "peak_multiplier": 10.0},
        },
    },
    2: {
        "pre_scale_minutes": 60,
        "peak_multiplier": 6.0,
        "cool_down_phases": [
            {"after_min": 0, "scale_pct": 100},
            {"after_min": 30, "scale_pct": 50},
            {"after_min": 60, "scale_pct": 15},
        ],
        "services": {
            "api-gateway": {"base_rps_per_pod": 1500, "peak_multiplier": 6.0},
            "betting-engine": {"base_rps_per_pod": 300, "peak_multiplier": 8.0},
            "odds-service": {"base_rps_per_pod": 800, "peak_multiplier": 5.0},
            "user-service": {"base_rps_per_pod": 2000, "peak_multiplier": 4.0},
            "websocket-gateway": {"base_conns_per_pod": 8000, "peak_multiplier": 6.0},
            "settlement-engine": {"base_rps_per_pod": 500, "peak_multiplier": 7.0},
            "cache-proxy": {"base_rps_per_pod": 5000, "peak_multiplier": 5.0},
        },
    },
    3: {
        "pre_scale_minutes": 30,
        "peak_multiplier": 3.0,
        "cool_down_phases": [
            {"after_min": 0, "scale_pct": 100},
            {"after_min": 20, "scale_pct": 30},
        ],
        "services": {
            "api-gateway": {"base_rps_per_pod": 1500, "peak_multiplier": 3.0},
            "betting-engine": {"base_rps_per_pod": 300, "peak_multiplier": 4.0},
            "odds-service": {"base_rps_per_pod": 800, "peak_multiplier": 2.5},
            "websocket-gateway": {"base_conns_per_pod": 8000, "peak_multiplier": 3.0},
        },
    },
    4: {
        "pre_scale_minutes": 15,
        "peak_multiplier": 1.5,
        "cool_down_phases": [
            {"after_min": 0, "scale_pct": 100},
            {"after_min": 15, "scale_pct": 0},
        ],
        "services": {
            "api-gateway": {"base_rps_per_pod": 1500, "peak_multiplier": 1.5},
            "betting-engine": {"base_rps_per_pod": 300, "peak_multiplier": 2.0},
        },
    },
}


class EventCalendarClient:
    """
    Fetches upcoming sporting events from the platform's event calendar.
    In production, this connects to the odds feed provider API or internal
    event management system.
    """

    def get_upcoming_events(self, lookahead_hours: int = 24) -> List[SportingEvent]:
        """Retrieve events scheduled within the lookahead window."""
        now = datetime.datetime.utcnow()  # ty:ignore[deprecated]
        # Simulated event calendar - replace with API calls in production
        events = [
            SportingEvent(
                event_id="wc2026-final",
                name="FIFA World Cup 2026 Final",
                sport="football",
                competition="world_cup",
                kickoff_utc=now + datetime.timedelta(hours=4),
                estimated_duration_min=150,
                tier=1,
                markets_count=450,
                expected_peak_multiplier=12.5,
                regions=["us-east-1", "eu-west-1", "sa-east-1"],
            ),
            SportingEvent(
                event_id="pl-matchday-38",
                name="Premier League Matchday 38 (10 simultaneous)",
                sport="football",
                competition="premier_league",
                kickoff_utc=now + datetime.timedelta(hours=8),
                estimated_duration_min=120,
                tier=2,
                markets_count=1200,
                expected_peak_multiplier=6.0,
                regions=["eu-west-1", "eu-west-2"],
            ),
            SportingEvent(
                event_id="nba-game-7",
                name="NBA Finals Game 7",
                sport="basketball",
                competition="nba",
                kickoff_utc=now + datetime.timedelta(hours=12),
                estimated_duration_min=180,
                tier=2,
                markets_count=300,
                expected_peak_multiplier=5.5,
                regions=["us-east-1", "us-west-2"],
            ),
        ]

        cutoff = now + datetime.timedelta(hours=lookahead_hours)
        return [e for e in events if e.kickoff_utc <= cutoff]


class KubernetesScaler:
    """Manages Kubernetes HPA and deployment scaling."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run

    def get_current_replicas(self, deployment: str, namespace: str) -> int:
        """Get current replica count for a deployment."""
        if self.dry_run:
            # Simulated current state
            defaults = {
                "api-gateway": 4,
                "betting-engine": 3,
                "odds-service": 3,
                "user-service": 2,
                "websocket-gateway": 3,
                "settlement-engine": 2,
                "cache-proxy": 2,
            }
            return defaults.get(deployment, 2)

        try:
            result = subprocess.run(
                ["kubectl", "get", "deployment", deployment,
                 "-n", namespace, "-o", "jsonpath={.spec.replicas}"],
                capture_output=True, text=True, check=True,
            )
            return int(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get replicas for {deployment}: {e}")
            return 0

    def scale_deployment(
        self, deployment: str, namespace: str, replicas: int,
    ) -> bool:
        """Scale a Kubernetes deployment to target replicas."""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Scaling {namespace}/{deployment} to {replicas} replicas"
        )

        if self.dry_run:
            return True

        try:
            subprocess.run(
                ["kubectl", "scale", "deployment", deployment,
                 "-n", namespace, f"--replicas={replicas}"],
                capture_output=True, text=True, check=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to scale {deployment}: {e}")
            return False

    def set_hpa_min(
        self, deployment: str, namespace: str, min_replicas: int,
    ) -> bool:
        """Update HPA minimum replicas to prevent scale-down during events."""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Setting HPA min for {namespace}/{deployment} to {min_replicas}"
        )

        if self.dry_run:
            return True

        try:
            subprocess.run(
                ["kubectl", "patch", "hpa", deployment,
                 "-n", namespace, "--type=merge",
                 "-p", json.dumps({"spec": {"minReplicas": min_replicas}})],
                capture_output=True, text=True, check=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to patch HPA for {deployment}: {e}")
            return False


class AWSAutoScaler:
    """Manages AWS Auto Scaling Groups for non-Kubernetes resources."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run

    def update_asg(
        self, asg_name: str, min_size: int, desired: int, max_size: int,
    ) -> bool:
        """Update ASG capacity settings."""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Updating ASG {asg_name}: min={min_size}, desired={desired}, max={max_size}"
        )

        if self.dry_run:
            return True

        try:
            import boto3  # ty:ignore[unresolved-import]
            client = boto3.client("autoscaling")
            client.update_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                MinSize=min_size,
                DesiredCapacity=desired,
                MaxSize=max_size,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update ASG {asg_name}: {e}")
            return False

    def add_read_replica(self, cluster_id: str, replica_id: str, region: str) -> bool:
        """Add an RDS read replica for the event."""
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Adding read replica {replica_id} to cluster {cluster_id} in {region}"
        )

        if self.dry_run:
            return True

        try:
            import boto3  # ty:ignore[unresolved-import]
            rds = boto3.client("rds", region_name=region)
            rds.create_db_instance_read_replica(
                DBInstanceIdentifier=replica_id,
                SourceDBInstanceIdentifier=cluster_id,
                DBInstanceClass="db.r5.2xlarge",
                AvailabilityZone=f"{region}a",
            )
            return True
        except Exception as e:
            logger.error(f"Failed to create replica: {e}")
            return False


class PredictiveAutoscaler:
    """Main auto-scaler orchestrator."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.calendar = EventCalendarClient()
        self.k8s_scaler = KubernetesScaler(dry_run=dry_run)
        self.aws_scaler = AWSAutoScaler(dry_run=dry_run)
        self.actions: List[ScalingAction] = []
        self.namespace = "betting-platform"

    def plan_scaling(self, event: SportingEvent) -> List[ScalingAction]:
        """Create a scaling plan for a sporting event."""
        profile = TIER_PROFILES.get(event.tier, TIER_PROFILES[4])
        actions = []
        pre_scale_time = event.kickoff_utc - datetime.timedelta(
            minutes=profile["pre_scale_minutes"]  # ty:ignore[invalid-argument-type]
        )

        logger.info(f"Planning scaling for: {event.name} (Tier {event.tier})")
        logger.info(f"  Kickoff: {event.kickoff_utc.isoformat()}")
        logger.info(f"  Pre-scale at: {pre_scale_time.isoformat()}")

        safety_margin = 1.3

        for svc_name, svc_config in profile["services"].items():  # ty:ignore[unresolved-attribute]
            current = self.k8s_scaler.get_current_replicas(svc_name, self.namespace)
            peak_mult = svc_config["peak_multiplier"]
            target = math.ceil(current * peak_mult * safety_margin)

            # Scale-up action
            actions.append(ScalingAction(
                target=f"{self.namespace}/{svc_name}",
                action="scale_up",
                from_replicas=current,
                to_replicas=target,
                scheduled_time=pre_scale_time,
                dry_run=self.dry_run,
            ))

            # Cool-down actions (phased scale-down after event)
            event_end = event.kickoff_utc + datetime.timedelta(
                minutes=event.estimated_duration_min
            )
            for phase in profile["cool_down_phases"]:  # ty:ignore[not-iterable]
                phase_time = event_end + datetime.timedelta(minutes=phase["after_min"])  # ty:ignore[invalid-argument-type]
                scale_pct = phase["scale_pct"] / 100.0  # ty:ignore[invalid-argument-type]
                phase_replicas = max(current, math.ceil(target * scale_pct))

                if phase["after_min"] > 0:  # ty:ignore[invalid-argument-type]
                    actions.append(ScalingAction(
                        target=f"{self.namespace}/{svc_name}",
                        action="scale_down",
                        from_replicas=target,
                        to_replicas=phase_replicas,
                        scheduled_time=phase_time,
                        dry_run=self.dry_run,
                    ))

        # Database read replicas for tier 1-2 events
        if event.tier <= 2:
            for region in event.regions:
                actions.append(ScalingAction(
                    target=f"rds-replica-{region}-{event.event_id}",
                    action="pre_warm",
                    from_replicas=0,
                    to_replicas=1,
                    scheduled_time=pre_scale_time - datetime.timedelta(minutes=30),
                    dry_run=self.dry_run,
                ))

        return actions

    def execute_due_actions(self, actions: List[ScalingAction]) -> int:
        """Execute any scaling actions that are due now."""
        now = datetime.datetime.utcnow()  # ty:ignore[deprecated]
        executed = 0

        for action in actions:
            if action.executed:
                continue
            if action.scheduled_time > now:
                continue

            logger.info(
                f"Executing: {action.action} {action.target} "
                f"({action.from_replicas} -> {action.to_replicas})"
            )

            target_parts = action.target.split("/")
            if len(target_parts) == 2:
                namespace, deployment = target_parts
                success = self.k8s_scaler.scale_deployment(
                    deployment, namespace, action.to_replicas
                )
                if success and action.action == "scale_up":
                    self.k8s_scaler.set_hpa_min(
                        deployment, namespace, action.to_replicas
                    )
            elif action.target.startswith("rds-replica"):
                success = self.aws_scaler.add_read_replica(
                    "betting-primary", action.target, "us-east-1"
                )
            else:
                logger.warning(f"Unknown target format: {action.target}")
                success = False

            action.executed = success
            action.execution_time = now
            if success:
                executed += 1

        return executed

    def run_schedule_mode(self, lookahead_hours: int = 24):
        """
        Continuously monitor event calendar and execute scaling actions.
        This is the main loop for production deployment.
        """
        logger.info(f"Starting predictive autoscaler (lookahead={lookahead_hours}h)")
        all_actions = []
        planned_events = set()

        while True:
            events = self.calendar.get_upcoming_events(lookahead_hours)

            for event in events:
                if event.event_id not in planned_events:
                    new_actions = self.plan_scaling(event)
                    all_actions.extend(new_actions)
                    planned_events.add(event.event_id)
                    logger.info(
                        f"Planned {len(new_actions)} actions for {event.name}"
                    )

            executed = self.execute_due_actions(all_actions)
            if executed > 0:
                logger.info(f"Executed {executed} scaling actions")

            pending = sum(1 for a in all_actions if not a.executed)
            logger.info(f"Pending actions: {pending}")

            if self.dry_run:
                break  # Single pass in dry-run mode

            time.sleep(60)  # Check every minute

    def print_plan(self, actions: List[ScalingAction]):
        """Print a formatted scaling plan."""
        print("\n" + "=" * 90)
        print("  PREDICTIVE SCALING PLAN")
        print("=" * 90)
        print(f"\n  {'Time (UTC)':<25} {'Action':<12} {'Target':<35} {'Replicas':<15}")
        print(f"  {'-'*25} {'-'*12} {'-'*35} {'-'*15}")

        sorted_actions = sorted(actions, key=lambda a: a.scheduled_time)
        for a in sorted_actions:
            time_str = a.scheduled_time.strftime("%Y-%m-%d %H:%M:%S")
            replica_str = f"{a.from_replicas} -> {a.to_replicas}"
            print(f"  {time_str:<25} {a.action:<12} {a.target:<35} {replica_str:<15}")

        total_scale_up = sum(
            a.to_replicas - a.from_replicas
            for a in actions if a.action == "scale_up"
        )
        print(f"\n  Total additional pods to provision: {total_scale_up}")
        print("=" * 90)


def main():
    parser = argparse.ArgumentParser(
        description="Predictive Auto-Scaler for Sporting Events"
    )
    parser.add_argument(
        "--mode", choices=["plan", "schedule", "execute"],
        default="plan", help="Operation mode",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Dry run mode (no actual scaling)",
    )
    parser.add_argument(
        "--no-dry-run", action="store_true",
        help="Disable dry run (actually execute scaling)",
    )
    parser.add_argument(
        "--lookahead-hours", type=int, default=24,
        help="Hours to look ahead for events (default: 24)",
    )
    parser.add_argument(
        "--event", type=str, default=None,
        help="Specific event datetime to plan for (ISO format)",
    )
    parser.add_argument(
        "--namespace", type=str, default="betting-platform",
        help="Kubernetes namespace",
    )
    args = parser.parse_args()

    dry_run = not args.no_dry_run
    scaler = PredictiveAutoscaler(dry_run=dry_run)
    scaler.namespace = args.namespace

    if args.mode == "plan":
        events = scaler.calendar.get_upcoming_events(args.lookahead_hours)
        if not events:
            logger.info("No upcoming events found in lookahead window")
            return

        for event in events:
            actions = scaler.plan_scaling(event)
            scaler.print_plan(actions)
            scaler.actions.extend(actions)

    elif args.mode == "schedule":
        scaler.run_schedule_mode(args.lookahead_hours)

    elif args.mode == "execute":
        events = scaler.calendar.get_upcoming_events(args.lookahead_hours)
        for event in events:
            actions = scaler.plan_scaling(event)
            scaler.print_plan(actions)
            executed = scaler.execute_due_actions(actions)
            logger.info(f"Executed {executed} actions for {event.name}")


if __name__ == "__main__":
    main()
