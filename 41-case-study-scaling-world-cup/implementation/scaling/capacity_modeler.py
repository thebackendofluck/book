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
Event-Driven Capacity Modeler for Sporting Events
==================================================
Analyzes historical traffic data and predicts capacity requirements
for major sporting events (World Cup, Champions League, Super Bowl).

Uses time-series decomposition and event-correlation to forecast
infrastructure needs across compute, database, cache, and network layers.

Usage:
    python capacity_modeler.py --event "world_cup_2026_final" --baseline-days 90
    python capacity_modeler.py --event "champions_league_semi" --output report.json
"""

import json
import math
import random
import argparse
import datetime
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from enum import Enum


class EventTier(Enum):
    """Event classification by expected traffic impact."""
    TIER_1 = "tier_1"  # World Cup Final, Super Bowl (10-15x baseline)
    TIER_2 = "tier_2"  # World Cup Group, Champions League Final (5-8x)
    TIER_3 = "tier_3"  # League matches, regular tournaments (2-4x)
    TIER_4 = "tier_4"  # Minor events, friendlies (1.2-2x)


@dataclass
class TrafficPattern:
    """Represents a traffic pattern for a time window."""
    timestamp: str
    requests_per_second: float
    concurrent_users: int
    bet_submissions_per_second: float
    avg_response_time_ms: float
    error_rate: float
    cpu_utilization: float
    memory_utilization: float
    db_connections: int
    cache_hit_rate: float


@dataclass
class CapacityRequirement:
    """Calculated capacity requirement for a resource layer."""
    resource: str
    current_capacity: float
    predicted_peak: float
    recommended_capacity: float
    safety_margin: float
    scaling_lead_time_minutes: int
    cost_per_unit_hour: float
    units_needed: int
    estimated_cost_event: float


@dataclass
class EventCapacityPlan:
    """Complete capacity plan for a sporting event."""
    event_name: str
    event_tier: str
    event_date: str
    baseline_rps: float
    predicted_peak_rps: float
    peak_multiplier: float
    pre_scale_minutes: int
    requirements: List[CapacityRequirement] = field(default_factory=list)
    total_estimated_cost: float = 0.0
    risk_assessment: Dict[str, str] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


# Historical multipliers based on real-world sporting event patterns
EVENT_MULTIPLIERS = {
    "world_cup_final": {"tier": EventTier.TIER_1, "peak_mult": 12.5, "pre_scale_min": 120,
                        "ramp_pattern": "exponential", "duration_hours": 4},
    "world_cup_semi": {"tier": EventTier.TIER_1, "peak_mult": 9.0, "pre_scale_min": 90,
                       "ramp_pattern": "exponential", "duration_hours": 3.5},
    "world_cup_group": {"tier": EventTier.TIER_2, "peak_mult": 6.0, "pre_scale_min": 60,
                        "ramp_pattern": "stepped", "duration_hours": 3},
    "champions_league_final": {"tier": EventTier.TIER_2, "peak_mult": 7.5, "pre_scale_min": 90,
                               "ramp_pattern": "exponential", "duration_hours": 3.5},
    "champions_league_semi": {"tier": EventTier.TIER_2, "peak_mult": 5.5, "pre_scale_min": 60,
                              "ramp_pattern": "stepped", "duration_hours": 3},
    "super_bowl": {"tier": EventTier.TIER_1, "peak_mult": 15.0, "pre_scale_min": 180,
                   "ramp_pattern": "exponential", "duration_hours": 5},
    "grand_slam_final": {"tier": EventTier.TIER_3, "peak_mult": 3.5, "pre_scale_min": 45,
                         "ramp_pattern": "linear", "duration_hours": 4},
    "premier_league": {"tier": EventTier.TIER_3, "peak_mult": 3.0, "pre_scale_min": 30,
                       "ramp_pattern": "stepped", "duration_hours": 2.5},
    "default": {"tier": EventTier.TIER_4, "peak_mult": 2.0, "pre_scale_min": 30,
                "ramp_pattern": "linear", "duration_hours": 2},
}

# Infrastructure cost estimates (USD/hour)
RESOURCE_COSTS = {
    "api_server": {"type": "c5.2xlarge", "cost_hr": 0.34, "rps_capacity": 2000,
                   "scale_time_min": 5},
    "websocket_server": {"type": "c5.xlarge", "cost_hr": 0.17, "connections": 10000,
                         "scale_time_min": 5},
    "betting_engine": {"type": "c5.4xlarge", "cost_hr": 0.68, "bets_per_sec": 500,
                       "scale_time_min": 8},
    "odds_calculator": {"type": "r5.2xlarge", "cost_hr": 0.504, "calcs_per_sec": 1000,
                        "scale_time_min": 5},
    "db_primary": {"type": "db.r5.4xlarge", "cost_hr": 2.28, "connections": 5000,
                   "scale_time_min": 30},
    "db_read_replica": {"type": "db.r5.2xlarge", "cost_hr": 1.14, "connections": 3000,
                        "scale_time_min": 20},
    "redis_cache": {"type": "cache.r5.xlarge", "cost_hr": 0.468, "ops_per_sec": 100000,
                    "scale_time_min": 10},
    "message_queue": {"type": "mq.m5.large", "cost_hr": 0.30, "msgs_per_sec": 50000,
                      "scale_time_min": 5},
    "cdn_bandwidth": {"type": "cloudfront", "cost_hr": 0.085, "gbps": 10,
                      "scale_time_min": 0},
}


def generate_baseline_traffic(days: int = 90) -> List[TrafficPattern]:
    """
    Generate realistic baseline traffic data.
    In production, this reads from monitoring systems (Prometheus, Datadog, etc.).
    """
    patterns = []
    base_rps = 1500  # Baseline requests per second
    now = datetime.datetime.now()

    for day in range(days):
        for hour in range(24):
            ts = now - datetime.timedelta(days=days - day, hours=24 - hour)

            # Daily traffic pattern: peak at 19:00-22:00 local time
            hour_multiplier = _daily_traffic_curve(hour)

            # Weekend uplift (Sat/Sun get 1.4-1.8x)
            day_of_week = ts.weekday()
            weekend_mult = 1.6 if day_of_week >= 5 else 1.0
            if day_of_week == 4:  # Friday evening
                weekend_mult = 1.3

            # Seasonal variation
            seasonal_mult = 1.0 + 0.2 * math.sin(2 * math.pi * day / 365)

            rps = base_rps * hour_multiplier * weekend_mult * seasonal_mult
            noise = random.gauss(1.0, 0.05)
            rps *= noise

            patterns.append(TrafficPattern(
                timestamp=ts.isoformat(),
                requests_per_second=round(rps, 1),
                concurrent_users=int(rps * 2.5),
                bet_submissions_per_second=round(rps * 0.08, 1),
                avg_response_time_ms=round(25 + (rps / base_rps) * 15, 1),
                error_rate=round(max(0.001, 0.002 * (rps / base_rps) ** 2), 4),
                cpu_utilization=round(min(0.95, 0.15 + (rps / base_rps) * 0.25), 2),
                memory_utilization=round(min(0.90, 0.30 + (rps / base_rps) * 0.15), 2),
                db_connections=int(200 + rps * 0.3),
                cache_hit_rate=round(max(0.85, 0.95 - (rps / base_rps) * 0.03), 3),
            ))

    return patterns


def _daily_traffic_curve(hour: int) -> float:
    """Realistic daily traffic curve for a gambling platform."""
    curves = {
        0: 0.3, 1: 0.2, 2: 0.15, 3: 0.1, 4: 0.08, 5: 0.1,
        6: 0.15, 7: 0.25, 8: 0.4, 9: 0.55, 10: 0.65, 11: 0.7,
        12: 0.75, 13: 0.8, 14: 0.85, 15: 0.9, 16: 0.95, 17: 1.0,
        18: 1.1, 19: 1.3, 20: 1.4, 21: 1.35, 22: 1.1, 23: 0.7,
    }
    return curves.get(hour, 0.5)


def analyze_baseline(patterns: List[TrafficPattern]) -> Dict:
    """Extract baseline statistics from historical traffic data."""
    rps_values = [p.requests_per_second for p in patterns]
    bet_values = [p.bet_submissions_per_second for p in patterns]

    return {
        "avg_rps": round(sum(rps_values) / len(rps_values), 1),
        "p50_rps": round(sorted(rps_values)[len(rps_values) // 2], 1),
        "p95_rps": round(sorted(rps_values)[int(len(rps_values) * 0.95)], 1),
        "p99_rps": round(sorted(rps_values)[int(len(rps_values) * 0.99)], 1),
        "max_rps": round(max(rps_values), 1),
        "avg_bets_per_sec": round(sum(bet_values) / len(bet_values), 1),
        "max_bets_per_sec": round(max(bet_values), 1),
        "avg_concurrent_users": int(sum(p.concurrent_users for p in patterns) / len(patterns)),
        "max_concurrent_users": max(p.concurrent_users for p in patterns),
        "avg_response_time_ms": round(
            sum(p.avg_response_time_ms for p in patterns) / len(patterns), 1),
        "avg_cpu_util": round(sum(p.cpu_utilization for p in patterns) / len(patterns), 2),
        "avg_cache_hit_rate": round(
            sum(p.cache_hit_rate for p in patterns) / len(patterns), 3),
    }


def predict_event_traffic(
    baseline: Dict,
    event_key: str,
) -> Tuple[float, float, Dict]:
    """
    Predict peak traffic for a specific event type.
    Returns (predicted_peak_rps, peak_bets_per_sec, event_params).
    """
    event_params = EVENT_MULTIPLIERS.get(event_key, EVENT_MULTIPLIERS["default"])
    peak_mult = event_params["peak_mult"]

    # Use P95 as the baseline reference (not average) for safety
    base_rps = baseline["p95_rps"]
    predicted_peak_rps = base_rps * peak_mult

    # Betting rate spikes harder than general traffic during live events
    bet_spike_factor = peak_mult * 1.3  # Bets spike 30% more than general traffic  # ty:ignore[unsupported-operator]
    peak_bets_per_sec = baseline["avg_bets_per_sec"] * bet_spike_factor

    return predicted_peak_rps, peak_bets_per_sec, event_params


def calculate_requirements(
    predicted_peak_rps: float,
    peak_bets_per_sec: float,
    event_params: Dict,
    safety_margin: float = 1.3,
) -> List[CapacityRequirement]:
    """Calculate infrastructure requirements for each resource layer."""
    requirements = []
    duration_hours = event_params["duration_hours"]

    # API Servers
    api = RESOURCE_COSTS["api_server"]
    api_units = math.ceil(predicted_peak_rps / api["rps_capacity"] * safety_margin)  # ty:ignore[unsupported-operator]
    requirements.append(CapacityRequirement(
        resource="API Servers (c5.2xlarge)",
        current_capacity=api["rps_capacity"],  # ty:ignore[invalid-argument-type]
        predicted_peak=predicted_peak_rps,
        recommended_capacity=api_units * api["rps_capacity"],
        safety_margin=safety_margin,
        scaling_lead_time_minutes=api["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=api["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=api_units,
        estimated_cost_event=round(api_units * api["cost_hr"] * duration_hours, 2),
    ))

    # WebSocket Servers
    ws = RESOURCE_COSTS["websocket_server"]
    concurrent_connections = int(predicted_peak_rps * 2.5)
    ws_units = math.ceil(concurrent_connections / ws["connections"] * safety_margin)  # ty:ignore[unsupported-operator]
    requirements.append(CapacityRequirement(
        resource="WebSocket Servers (c5.xlarge)",
        current_capacity=ws["connections"],  # ty:ignore[invalid-argument-type]
        predicted_peak=concurrent_connections,
        recommended_capacity=ws_units * ws["connections"],
        safety_margin=safety_margin,
        scaling_lead_time_minutes=ws["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=ws["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=ws_units,
        estimated_cost_event=round(ws_units * ws["cost_hr"] * duration_hours, 2),
    ))

    # Betting Engine
    be = RESOURCE_COSTS["betting_engine"]
    be_units = math.ceil(peak_bets_per_sec / be["bets_per_sec"] * safety_margin)  # ty:ignore[unsupported-operator]
    requirements.append(CapacityRequirement(
        resource="Betting Engine (c5.4xlarge)",
        current_capacity=be["bets_per_sec"],  # ty:ignore[invalid-argument-type]
        predicted_peak=peak_bets_per_sec,
        recommended_capacity=be_units * be["bets_per_sec"],
        safety_margin=safety_margin,
        scaling_lead_time_minutes=be["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=be["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=be_units,
        estimated_cost_event=round(be_units * be["cost_hr"] * duration_hours, 2),
    ))

    # Odds Calculator
    oc = RESOURCE_COSTS["odds_calculator"]
    odds_calcs = peak_bets_per_sec * 5  # Each bet triggers ~5 odds calculations
    oc_units = math.ceil(odds_calcs / oc["calcs_per_sec"] * safety_margin)  # ty:ignore[unsupported-operator]
    requirements.append(CapacityRequirement(
        resource="Odds Calculator (r5.2xlarge)",
        current_capacity=oc["calcs_per_sec"],  # ty:ignore[invalid-argument-type]
        predicted_peak=odds_calcs,
        recommended_capacity=oc_units * oc["calcs_per_sec"],
        safety_margin=safety_margin,
        scaling_lead_time_minutes=oc["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=oc["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=oc_units,
        estimated_cost_event=round(oc_units * oc["cost_hr"] * duration_hours, 2),
    ))

    # Database - Primary (vertical scaling, plan ahead)
    db_p = RESOURCE_COSTS["db_primary"]
    db_connections = int(predicted_peak_rps * 0.3)
    db_p_units = math.ceil(db_connections / db_p["connections"] * safety_margin)  # ty:ignore[unsupported-operator]
    requirements.append(CapacityRequirement(
        resource="Database Primary (db.r5.4xlarge)",
        current_capacity=db_p["connections"],  # ty:ignore[invalid-argument-type]
        predicted_peak=db_connections,
        recommended_capacity=db_p_units * db_p["connections"],
        safety_margin=safety_margin,
        scaling_lead_time_minutes=db_p["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=db_p["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=db_p_units,
        estimated_cost_event=round(db_p_units * db_p["cost_hr"] * duration_hours, 2),
    ))

    # Database - Read Replicas
    db_r = RESOURCE_COSTS["db_read_replica"]
    read_connections = int(predicted_peak_rps * 0.7)  # 70% reads
    db_r_units = math.ceil(read_connections / db_r["connections"] * safety_margin)  # ty:ignore[unsupported-operator]
    requirements.append(CapacityRequirement(
        resource="Database Read Replicas (db.r5.2xlarge)",
        current_capacity=db_r["connections"],  # ty:ignore[invalid-argument-type]
        predicted_peak=read_connections,
        recommended_capacity=db_r_units * db_r["connections"],
        safety_margin=safety_margin,
        scaling_lead_time_minutes=db_r["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=db_r["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=db_r_units,
        estimated_cost_event=round(db_r_units * db_r["cost_hr"] * duration_hours, 2),
    ))

    # Redis Cache
    rc = RESOURCE_COSTS["redis_cache"]
    cache_ops = int(predicted_peak_rps * 8)  # ~8 cache ops per request
    rc_units = math.ceil(cache_ops / rc["ops_per_sec"] * safety_margin)  # ty:ignore[unsupported-operator]
    rc_units = max(rc_units, 3)  # Minimum 3-node cluster
    requirements.append(CapacityRequirement(
        resource="Redis Cache Cluster (cache.r5.xlarge)",
        current_capacity=rc["ops_per_sec"],  # ty:ignore[invalid-argument-type]
        predicted_peak=cache_ops,
        recommended_capacity=rc_units * rc["ops_per_sec"],  # ty:ignore[invalid-argument-type]
        safety_margin=safety_margin,
        scaling_lead_time_minutes=rc["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=rc["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=rc_units,
        estimated_cost_event=round(rc_units * rc["cost_hr"] * duration_hours, 2),
    ))

    # Message Queue
    mq = RESOURCE_COSTS["message_queue"]
    msg_rate = int(predicted_peak_rps * 3)  # ~3 messages per request
    mq_units = math.ceil(msg_rate / mq["msgs_per_sec"] * safety_margin)  # ty:ignore[unsupported-operator]
    requirements.append(CapacityRequirement(
        resource="Message Queue (mq.m5.large)",
        current_capacity=mq["msgs_per_sec"],  # ty:ignore[invalid-argument-type]
        predicted_peak=msg_rate,
        recommended_capacity=mq_units * mq["msgs_per_sec"],
        safety_margin=safety_margin,
        scaling_lead_time_minutes=mq["scale_time_min"],  # ty:ignore[invalid-argument-type]
        cost_per_unit_hour=mq["cost_hr"],  # ty:ignore[invalid-argument-type]
        units_needed=mq_units,
        estimated_cost_event=round(mq_units * mq["cost_hr"] * duration_hours, 2),
    ))

    return requirements


def assess_risks(
    requirements: List[CapacityRequirement],
    event_params: Dict,
) -> Dict[str, str]:
    """Assess risks for the capacity plan."""
    risks = {}

    max_scale_time = max(r.scaling_lead_time_minutes for r in requirements)
    if max_scale_time > event_params["pre_scale_min"]:
        risks["scaling_window"] = (
            f"CRITICAL: Longest scaling lead time ({max_scale_time}min) exceeds "
            f"pre-scale window ({event_params['pre_scale_min']}min). "
            f"Database must be pre-scaled manually."
        )

    total_cost = sum(r.estimated_cost_event for r in requirements)
    if total_cost > 5000:
        risks["cost_overrun"] = (
            f"WARNING: Estimated event cost ${total_cost:.2f} is significant. "
            f"Consider reserved capacity for predictable events."
        )

    for r in requirements:
        if r.predicted_peak > r.recommended_capacity * 0.85:
            risks[f"capacity_{r.resource}"] = (
                f"WARNING: {r.resource} will operate at >85% capacity even with "
                f"safety margin. Consider additional headroom."
            )

    db_reqs = [r for r in requirements if "Database Primary" in r.resource]
    for db in db_reqs:
        if db.scaling_lead_time_minutes > 15:
            risks["database_scaling"] = (
                "HIGH: Database vertical scaling requires 30+ minutes. "
                "Must be pre-scaled 2 hours before event. "
                "Consider Aurora Serverless for automatic scaling."
            )

    if event_params["tier"] in (EventTier.TIER_1, "tier_1"):
        risks["provider_feeds"] = (
            "HIGH: Odds feed providers may rate-limit during peak. "
            "Negotiate burst capacity with Betradar/LSports 2 weeks before event."
        )

    return risks


def generate_recommendations(
    event_params: Dict,
    requirements: List[CapacityRequirement],
    risks: Dict[str, str],
) -> List[str]:
    """Generate actionable recommendations."""
    recs = []

    pre_scale = event_params["pre_scale_min"]
    recs.append(
        f"Pre-scale all auto-scaling groups {pre_scale} minutes before kickoff. "
        f"Set minimum instances to calculated peaks."
    )

    recs.append(
        "Enable database read replicas in all regions 2 hours before event. "
        "Route all read queries (odds display, account balances) to replicas."
    )

    recs.append(
        "Pre-warm CDN edge caches with static assets, event pages, and odds snapshots "
        "30 minutes before the event. See cdn_prewarmer.sh."
    )

    recs.append(
        "Activate queue-based bet processing to absorb microsecond spikes. "
        "Bets enter SQS/RabbitMQ and are processed asynchronously with "
        "confirmation via WebSocket push."
    )

    recs.append(
        "Deploy circuit breakers on all external provider integrations "
        "(odds feeds, payment gateways, KYC). Use cached fallback values "
        "for odds if feed latency exceeds 500ms."
    )

    recs.append(
        "Set up war room 60 minutes before event with dedicated screens for: "
        "RPS/latency, error rates, bet processing queue depth, provider feed health, "
        "and infrastructure costs. See war_room_dashboard.json."
    )

    recs.append(
        "Run load test at predicted peak + 30% safety margin 48 hours before event. "
        "Validate auto-scaling triggers, database connection pooling, and cache eviction. "
        "See world_cup_load_test.js."
    )

    recs.append(
        "Prepare rollback runbook: if error rate exceeds 5%, activate degraded mode "
        "(disable live betting, serve cached odds, queue all bets). "
        "If >10%, activate maintenance page and drain queues."
    )

    if "provider_feeds" in risks:
        recs.append(
            "Contact Betradar/LSports account managers to negotiate 10x burst rate limits "
            "for event duration. Confirm in writing at least 14 days before event."
        )

    recs.append(
        "Schedule post-event teardown 2 hours after final whistle. "
        "Scale down in 3 phases: 50% immediately, 25% after 1 hour, remainder after 2 hours. "
        "See post_event_teardown.sh."
    )

    return recs


def build_capacity_plan(
    event_key: str,
    baseline_days: int = 90,
    safety_margin: float = 1.3,
) -> EventCapacityPlan:
    """Build a complete capacity plan for a sporting event."""
    print(f"[1/5] Generating {baseline_days}-day baseline traffic data...")
    patterns = generate_baseline_traffic(baseline_days)

    print("[2/5] Analyzing baseline traffic patterns...")
    baseline = analyze_baseline(patterns)
    print(f"       Baseline: avg={baseline['avg_rps']} RPS, "
          f"P95={baseline['p95_rps']} RPS, max={baseline['max_rps']} RPS")

    print(f"[3/5] Predicting event traffic for '{event_key}'...")
    peak_rps, peak_bets, event_params = predict_event_traffic(baseline, event_key)
    print(f"       Predicted peak: {peak_rps:.0f} RPS, {peak_bets:.0f} bets/sec")

    print("[4/5] Calculating infrastructure requirements...")
    requirements = calculate_requirements(peak_rps, peak_bets, event_params, safety_margin)
    total_cost = sum(r.estimated_cost_event for r in requirements)

    print("[5/5] Assessing risks and generating recommendations...")
    risks = assess_risks(requirements, event_params)
    recommendations = generate_recommendations(event_params, requirements, risks)

    plan = EventCapacityPlan(
        event_name=event_key,
        event_tier=str(event_params["tier"].value if isinstance(event_params["tier"], EventTier)
                       else event_params["tier"]),
        event_date=datetime.datetime.now().isoformat(),
        baseline_rps=baseline["p95_rps"],
        predicted_peak_rps=round(peak_rps, 1),
        peak_multiplier=event_params["peak_mult"],
        pre_scale_minutes=event_params["pre_scale_min"],
        requirements=requirements,
        total_estimated_cost=round(total_cost, 2),
        risk_assessment=risks,
        recommendations=recommendations,
    )

    return plan


def print_plan(plan: EventCapacityPlan) -> None:
    """Print a formatted capacity plan report."""
    print("\n" + "=" * 80)
    print(f"  CAPACITY PLAN: {plan.event_name.upper().replace('_', ' ')}")
    print("=" * 80)

    print(f"\n  Event Tier:         {plan.event_tier}")
    print(f"  Baseline RPS (P95): {plan.baseline_rps}")
    print(f"  Predicted Peak RPS: {plan.predicted_peak_rps}")
    print(f"  Peak Multiplier:    {plan.peak_multiplier}x")
    print(f"  Pre-Scale Window:   {plan.pre_scale_minutes} minutes before event")
    print(f"  Total Est. Cost:    ${plan.total_estimated_cost:,.2f}")

    print(f"\n{'  INFRASTRUCTURE REQUIREMENTS':}")
    print(f"  {'Resource':<40} {'Peak':>10} {'Capacity':>10} {'Units':>6} {'Cost':>10}")
    print(f"  {'-'*40} {'-'*10} {'-'*10} {'-'*6} {'-'*10}")
    for r in plan.requirements:
        print(f"  {r.resource:<40} {r.predicted_peak:>10.0f} "
              f"{r.recommended_capacity:>10.0f} {r.units_needed:>6} "
              f"${r.estimated_cost_event:>8.2f}")

    if plan.risk_assessment:
        print(f"\n  RISK ASSESSMENT:")
        for risk_key, risk_desc in plan.risk_assessment.items():
            print(f"  - {risk_desc}")

    print(f"\n  RECOMMENDATIONS:")
    for i, rec in enumerate(plan.recommendations, 1):
        print(f"  {i}. {rec}")

    print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Event-Driven Capacity Modeler for Sporting Events"
    )
    parser.add_argument(
        "--event", type=str, default="world_cup_final",
        choices=list(EVENT_MULTIPLIERS.keys()),
        help="Event type to model capacity for",
    )
    parser.add_argument(
        "--baseline-days", type=int, default=90,
        help="Number of days of baseline data to analyze (default: 90)",
    )
    parser.add_argument(
        "--safety-margin", type=float, default=1.3,
        help="Safety margin multiplier (default: 1.3 = 30%% headroom)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output file for JSON report (optional)",
    )
    args = parser.parse_args()

    plan = build_capacity_plan(args.event, args.baseline_days, args.safety_margin)
    print_plan(plan)

    if args.output:
        report = asdict(plan)
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON report saved to: {args.output}")


if __name__ == "__main__":
    main()
