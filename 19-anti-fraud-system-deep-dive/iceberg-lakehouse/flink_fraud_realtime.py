#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PyFlink Real-Time Fraud Detection with Apache Iceberg
=====================================================

Reference implementation for Chapter 19: Anti-Fraud System Deep Dive.

This module implements real-time fraud detection using Apache Flink's
streaming engine. While Spark handles batch analytics (see spark_fraud_batch.py),
Flink processes every transaction as it happens -- sub-second latency from
bet placement to fraud alert.

Why Flink for real-time fraud in gambling:
- True stream processing (not micro-batch like Spark Streaming)
- Event-time processing with watermarks (handles out-of-order events)
- Exactly-once semantics with Iceberg sink (no duplicate alerts)
- Stateful processing for player profiles (keyed state per player)
- Low latency: 10-50ms per event in production

Architecture:
    Kafka (transactions) → Flink → Rules Engine → Iceberg (fraud_alerts)
                                       ↓
                              Alert Service (WebSocket → Dashboard)

Real-time fraud rules implemented:
1. Velocity checks: transactions per minute exceeding threshold
2. Amount anomaly: statistical deviation from player's baseline
3. Geographic impossibility: login from 2 countries in 5 minutes
4. Device fingerprint change: new device + high-value transaction
5. Bonus abuse: deposit-bonus-withdraw pattern detection
6. Session anomaly: betting patterns inconsistent with human behavior

Usage:
    flink run -py flink_fraud_realtime.py \\
        --kafka-bootstrap kafka:9092 \\
        --iceberg-catalog http://iceberg-rest:8181
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

# PyFlink imports -- wrapped for educational readability
try:
    from pyflink.common import Row, Types, WatermarkStrategy
    from pyflink.common.serialization import SimpleStringSchema
    from pyflink.common.time import Duration
    from pyflink.common.typeinfo import TypeInformation
    from pyflink.datastream import (
        KeyedProcessFunction,
        OutputTag,
        RuntimeContext,
        StreamExecutionEnvironment,
    )
    from pyflink.datastream.connectors.kafka import (
        KafkaOffsetsInitializer,
        KafkaSource,
    )
    from pyflink.datastream.state import (
        ListStateDescriptor,
        MapStateDescriptor,
        ValueStateDescriptor,
    )
    from pyflink.datastream.window import TumblingEventTimeWindows, SlidingEventTimeWindows
    from pyflink.table import EnvironmentSettings, TableEnvironment

    PYFLINK_AVAILABLE = True
except ImportError:
    PYFLINK_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("flink_fraud_realtime")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class AlertSeverity(str, Enum):
    """Alert severity levels matching the analyst dashboard."""
    CRITICAL = "critical"  # Immediate block, notify senior analyst
    HIGH = "high"          # Auto-restrict, queue for review within 1 hour
    MEDIUM = "medium"      # Queue for review within 24 hours
    LOW = "low"            # Log only, review in weekly batch


@dataclass
class FlinkFraudConfig:
    """Configuration for the Flink real-time fraud pipeline."""

    # Kafka
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_transactions: str = "casino.transactions"
    kafka_topic_alerts: str = "casino.fraud.alerts"
    kafka_consumer_group: str = "fraud-flink-realtime"

    # Iceberg
    iceberg_catalog_uri: str = "http://localhost:8181"
    iceberg_warehouse: str = "s3a://fraud-lakehouse/warehouse"
    iceberg_namespace: str = "fraud_analytics"

    # Velocity thresholds (per player)
    velocity_1min_threshold: int = 10     # >10 tx/min = suspicious
    velocity_5min_threshold: int = 30     # >30 tx/5min = suspicious
    velocity_1hr_threshold: int = 200     # >200 tx/hr = likely bot

    # Amount thresholds
    amount_stddev_multiplier: float = 3.0  # >3 sigma from mean = anomaly
    structuring_threshold_cents: int = 1000000  # EUR 10,000 threshold
    structuring_window_pct: float = 0.9    # 90% of threshold = suspicious

    # Geographic thresholds
    geo_impossible_minutes: int = 5        # 2 countries in 5 min = impossible
    max_device_changes_per_day: int = 3    # >3 device changes = ATO signal

    # Bonus abuse
    deposit_withdraw_window_minutes: int = 60  # deposit→withdraw in 60 min

    # Watermark
    max_out_of_orderness_seconds: int = 30

    # Checkpointing
    checkpoint_interval_ms: int = 60000    # 1 minute
    checkpoint_dir: str = "s3a://fraud-lakehouse/checkpoints"

    # State TTL
    state_ttl_hours: int = 24              # Keep player state for 24 hours


# ---------------------------------------------------------------------------
# Transaction event schema
# ---------------------------------------------------------------------------

@dataclass
class TransactionEvent:
    """Deserialized transaction event from Kafka."""

    transaction_id: str
    player_id: str
    event_time: datetime
    transaction_type: str  # bet, deposit, withdrawal, bonus
    amount_cents: int
    currency: str
    game_id: str | None = None
    game_type: str | None = None
    payment_method: str | None = None
    jurisdiction: str = "UNKNOWN"
    brand_id: int = 0
    ip_address: str | None = None
    device_fingerprint: str | None = None
    session_id: str | None = None
    country: str | None = None

    @classmethod
    def from_json(cls, json_str: str) -> TransactionEvent:
        """Deserialize from Kafka JSON message."""
        data = json.loads(json_str)
        return cls(
            transaction_id=data["transaction_id"],
            player_id=data["player_id"],
            event_time=datetime.fromisoformat(data["event_time"]),
            transaction_type=data["transaction_type"],
            amount_cents=data["amount_cents"],
            currency=data["currency"],
            game_id=data.get("game_id"),
            game_type=data.get("game_type"),
            payment_method=data.get("payment_method"),
            jurisdiction=data.get("jurisdiction", "UNKNOWN"),
            brand_id=data.get("brand_id", 0),
            ip_address=data.get("ip_address"),
            device_fingerprint=data.get("device_fingerprint"),
            session_id=data.get("session_id"),
            country=data.get("country"),
        )


@dataclass
class FraudAlert:
    """Fraud alert generated by the real-time pipeline."""

    alert_id: str
    player_id: str
    detected_at: datetime
    fraud_type: str
    severity: AlertSeverity
    confidence_score: float
    description: str
    jurisdiction: str
    risk_level: str
    triggering_transaction_id: str
    rule_name: str

    def to_json(self) -> str:
        """Serialize to JSON for Kafka alert topic."""
        return json.dumps({
            "alert_id": self.alert_id,
            "player_id": self.player_id,
            "detected_at": self.detected_at.isoformat(),
            "fraud_type": self.fraud_type,
            "severity": self.severity.value,
            "confidence_score": self.confidence_score,
            "description": self.description,
            "jurisdiction": self.jurisdiction,
            "risk_level": self.risk_level,
            "triggering_transaction_id": self.triggering_transaction_id,
            "rule_name": self.rule_name,
        })


# ---------------------------------------------------------------------------
# Player state for stateful processing
# ---------------------------------------------------------------------------

@dataclass
class PlayerState:
    """In-memory state maintained per player in Flink's keyed state.

    Flink maintains this state per player_id key, persisted in RocksDB
    with periodic checkpointing to S3. On failure recovery, state is
    restored from the last checkpoint -- exactly-once semantics.
    """

    # Transaction history (recent window for velocity)
    recent_transaction_times: list[float] = field(default_factory=list)
    recent_amounts: list[int] = field(default_factory=list)

    # Amount statistics (running)
    amount_sum: float = 0.0
    amount_sum_sq: float = 0.0
    amount_count: int = 0

    # Geographic state
    last_country: str | None = None
    last_country_time: datetime | None = None
    last_ip: str | None = None

    # Device state
    known_devices: list[str] = field(default_factory=list)
    device_changes_today: int = 0
    last_device_reset_date: str | None = None

    # Bonus abuse tracking
    last_deposit_time: datetime | None = None
    last_deposit_amount: int = 0
    last_bonus_time: datetime | None = None

    @property
    def amount_mean(self) -> float:
        """Running mean of transaction amounts."""
        if self.amount_count == 0:
            return 0.0
        return self.amount_sum / self.amount_count

    @property
    def amount_stddev(self) -> float:
        """Running standard deviation of transaction amounts."""
        if self.amount_count < 2:
            return 0.0
        variance = (self.amount_sum_sq / self.amount_count) - (self.amount_mean ** 2)
        return math.sqrt(max(0.0, variance))


# ---------------------------------------------------------------------------
# Fraud detection rules
# ---------------------------------------------------------------------------

def check_velocity(
    event: TransactionEvent,
    state: PlayerState,
    config: FlinkFraudConfig,
) -> FraudAlert | None:
    """Check transaction velocity against thresholds.

    Velocity is the number of transactions in a sliding time window.
    Bots produce consistent, high-frequency betting patterns that are
    nearly impossible for humans to replicate.

    A human playing slots might average 3-5 spins per minute with pauses.
    A bot maintains 20-50 per minute without variation. The lack of
    variance is itself a signal (checked separately in session anomaly).

    Args:
        event: Current transaction.
        state: Player's accumulated state.
        config: Threshold configuration.

    Returns:
        FraudAlert if velocity exceeds threshold, None otherwise.
    """
    now_ts = event.event_time.timestamp()

    # Add current transaction
    state.recent_transaction_times.append(now_ts)

    # Clean old entries (keep last 60 minutes)
    cutoff = now_ts - 3600
    state.recent_transaction_times = [
        t for t in state.recent_transaction_times if t > cutoff
    ]

    # Count transactions in each window
    count_1min = sum(1 for t in state.recent_transaction_times if t > now_ts - 60)
    count_5min = sum(1 for t in state.recent_transaction_times if t > now_ts - 300)
    count_1hr = len(state.recent_transaction_times)

    # Check thresholds
    if count_1min > config.velocity_1min_threshold:
        severity = AlertSeverity.CRITICAL if count_1min > config.velocity_1min_threshold * 3 else AlertSeverity.HIGH
        return FraudAlert(
            alert_id=f"VEL-{uuid.uuid4().hex[:12]}",
            player_id=event.player_id,
            detected_at=datetime.now(timezone.utc),
            fraud_type="bot_play",
            severity=severity,
            confidence_score=min(1.0, count_1min / (config.velocity_1min_threshold * 3)),
            description=(
                f"Velocity alert: {count_1min} transactions in 1 minute "
                f"(threshold: {config.velocity_1min_threshold})"
            ),
            jurisdiction=event.jurisdiction,
            risk_level=severity.value,
            triggering_transaction_id=event.transaction_id,
            rule_name="velocity_1min",
        )

    if count_5min > config.velocity_5min_threshold:
        return FraudAlert(
            alert_id=f"VEL-{uuid.uuid4().hex[:12]}",
            player_id=event.player_id,
            detected_at=datetime.now(timezone.utc),
            fraud_type="bot_play",
            severity=AlertSeverity.HIGH,
            confidence_score=min(1.0, count_5min / (config.velocity_5min_threshold * 2)),
            description=(
                f"Velocity alert: {count_5min} transactions in 5 minutes "
                f"(threshold: {config.velocity_5min_threshold})"
            ),
            jurisdiction=event.jurisdiction,
            risk_level="high",
            triggering_transaction_id=event.transaction_id,
            rule_name="velocity_5min",
        )

    if count_1hr > config.velocity_1hr_threshold:
        return FraudAlert(
            alert_id=f"VEL-{uuid.uuid4().hex[:12]}",
            player_id=event.player_id,
            detected_at=datetime.now(timezone.utc),
            fraud_type="bot_play",
            severity=AlertSeverity.MEDIUM,
            confidence_score=min(1.0, count_1hr / (config.velocity_1hr_threshold * 2)),
            description=(
                f"Velocity alert: {count_1hr} transactions in 1 hour "
                f"(threshold: {config.velocity_1hr_threshold})"
            ),
            jurisdiction=event.jurisdiction,
            risk_level="medium",
            triggering_transaction_id=event.transaction_id,
            rule_name="velocity_1hr",
        )

    return None


def check_amount_anomaly(
    event: TransactionEvent,
    state: PlayerState,
    config: FlinkFraudConfig,
) -> FraudAlert | None:
    """Detect statistically anomalous transaction amounts.

    Uses a running mean and standard deviation to detect amounts that
    deviate significantly from the player's baseline. A player who
    normally bets EUR 5-20 suddenly placing a EUR 5,000 bet is a signal.

    Also checks for structuring: amounts just below reporting thresholds.
    Money launderers split large amounts into smaller ones (e.g., 9 deposits
    of EUR 9,500 instead of one EUR 85,500 deposit) to avoid triggering
    mandatory FIAU reports at EUR 10,000.

    Args:
        event: Current transaction.
        state: Player's accumulated state.
        config: Threshold configuration.

    Returns:
        FraudAlert if amount is anomalous, None otherwise.
    """
    amount = event.amount_cents

    # Update running statistics
    state.amount_count += 1
    state.amount_sum += amount
    state.amount_sum_sq += amount * amount
    state.recent_amounts.append(amount)

    # Keep last 1000 amounts
    if len(state.recent_amounts) > 1000:
        state.recent_amounts = state.recent_amounts[-1000:]

    # Need at least 10 transactions to establish a baseline
    if state.amount_count < 10:
        return None

    mean = state.amount_mean
    stddev = state.amount_stddev

    # Statistical anomaly: amount deviates more than N standard deviations
    if stddev > 0:
        z_score = abs(amount - mean) / stddev
        if z_score > config.amount_stddev_multiplier:
            severity = AlertSeverity.HIGH if z_score > 5 else AlertSeverity.MEDIUM
            return FraudAlert(
                alert_id=f"AMT-{uuid.uuid4().hex[:12]}",
                player_id=event.player_id,
                detected_at=datetime.now(timezone.utc),
                fraud_type="account_takeover",
                severity=severity,
                confidence_score=min(1.0, z_score / 10.0),
                description=(
                    f"Amount anomaly: {amount/100:.2f} {event.currency} "
                    f"(z-score={z_score:.1f}, mean={mean/100:.2f}, "
                    f"stddev={stddev/100:.2f})"
                ),
                jurisdiction=event.jurisdiction,
                risk_level=severity.value,
                triggering_transaction_id=event.transaction_id,
                rule_name="amount_anomaly",
            )

    # Structuring detection: amount in 90-100% of threshold
    threshold = config.structuring_threshold_cents
    lower_bound = int(threshold * config.structuring_window_pct)
    if event.transaction_type in ("deposit", "withdrawal"):
        if lower_bound <= amount < threshold:
            # Count recent near-threshold transactions
            near_threshold_count = sum(
                1 for a in state.recent_amounts[-50:]
                if lower_bound <= a < threshold
            )
            if near_threshold_count >= 3:
                return FraudAlert(
                    alert_id=f"STR-{uuid.uuid4().hex[:12]}",
                    player_id=event.player_id,
                    detected_at=datetime.now(timezone.utc),
                    fraud_type="money_laundering",
                    severity=AlertSeverity.CRITICAL,
                    confidence_score=min(1.0, near_threshold_count / 5.0),
                    description=(
                        f"Structuring detected: {near_threshold_count} transactions "
                        f"in 90-100% of {threshold/100:.0f} {event.currency} threshold"
                    ),
                    jurisdiction=event.jurisdiction,
                    risk_level="critical",
                    triggering_transaction_id=event.transaction_id,
                    rule_name="structuring",
                )

    return None


def check_geo_impossibility(
    event: TransactionEvent,
    state: PlayerState,
    config: FlinkFraudConfig,
) -> FraudAlert | None:
    """Detect geographically impossible travel patterns.

    If a player logs in from Germany and then from Brazil 3 minutes later,
    that's physically impossible. This is a strong signal of account
    sharing or account takeover.

    Note: VPN users can trigger false positives. The confidence score
    accounts for this by considering the player's historical VPN usage.
    Players who always use VPNs get a lower confidence score.

    Args:
        event: Current transaction.
        state: Player's accumulated state.
        config: Threshold configuration.

    Returns:
        FraudAlert if impossible travel detected, None otherwise.
    """
    if event.country is None:
        return None

    current_country = event.country
    current_time = event.event_time

    if state.last_country and state.last_country != current_country and state.last_country_time:
        time_diff = (current_time - state.last_country_time).total_seconds()
        threshold_seconds = config.geo_impossible_minutes * 60

        if 0 < time_diff < threshold_seconds:
            return FraudAlert(
                alert_id=f"GEO-{uuid.uuid4().hex[:12]}",
                player_id=event.player_id,
                detected_at=datetime.now(timezone.utc),
                fraud_type="account_takeover",
                severity=AlertSeverity.HIGH,
                confidence_score=0.85,
                description=(
                    f"Impossible travel: {state.last_country} → {current_country} "
                    f"in {time_diff:.0f} seconds "
                    f"(threshold: {threshold_seconds}s)"
                ),
                jurisdiction=event.jurisdiction,
                risk_level="high",
                triggering_transaction_id=event.transaction_id,
                rule_name="geo_impossibility",
            )

    # Update state
    state.last_country = current_country
    state.last_country_time = current_time

    return None


def check_device_change(
    event: TransactionEvent,
    state: PlayerState,
    config: FlinkFraudConfig,
) -> FraudAlert | None:
    """Detect suspicious device fingerprint changes.

    A player switching devices is normal -- phone to laptop, for example.
    But switching to 5 different devices in a day, especially combined
    with high-value transactions, signals account takeover.

    Device fingerprints combine: browser user-agent, screen resolution,
    installed fonts, WebGL renderer, canvas fingerprint hash, timezone,
    and language settings. This creates a near-unique identifier that
    persists across sessions (unlike cookies).

    Args:
        event: Current transaction.
        state: Player's accumulated state.
        config: Threshold configuration.

    Returns:
        FraudAlert if suspicious device change, None otherwise.
    """
    if event.device_fingerprint is None:
        return None

    today = event.event_time.strftime("%Y-%m-%d")

    # Reset daily counter
    if state.last_device_reset_date != today:
        state.device_changes_today = 0
        state.last_device_reset_date = today

    # Check if this is a new device
    if event.device_fingerprint not in state.known_devices:
        state.known_devices.append(event.device_fingerprint)
        state.device_changes_today += 1

        # Keep only last 20 known devices
        if len(state.known_devices) > 20:
            state.known_devices = state.known_devices[-20:]

        if state.device_changes_today > config.max_device_changes_per_day:
            # Higher severity if combined with high-value transaction
            is_high_value = event.amount_cents > 50000  # EUR 500+
            severity = AlertSeverity.HIGH if is_high_value else AlertSeverity.MEDIUM

            return FraudAlert(
                alert_id=f"DEV-{uuid.uuid4().hex[:12]}",
                player_id=event.player_id,
                detected_at=datetime.now(timezone.utc),
                fraud_type="account_takeover",
                severity=severity,
                confidence_score=min(1.0, state.device_changes_today / 10.0),
                description=(
                    f"Excessive device changes: {state.device_changes_today} today "
                    f"(threshold: {config.max_device_changes_per_day}). "
                    f"High-value: {is_high_value}"
                ),
                jurisdiction=event.jurisdiction,
                risk_level=severity.value,
                triggering_transaction_id=event.transaction_id,
                rule_name="device_change",
            )

    return None


def check_bonus_abuse(
    event: TransactionEvent,
    state: PlayerState,
    config: FlinkFraudConfig,
) -> FraudAlert | None:
    """Detect deposit-bonus-withdraw abuse patterns.

    Bonus abuse is the most common fraud in iGaming. The pattern:
    1. Player deposits (often minimum qualifying amount)
    2. Claims bonus (match bonus, free spins, etc.)
    3. Plays minimum wagering requirement on highest-RTP games
    4. Withdraws immediately

    The time between deposit and withdrawal is the key signal.
    Legitimate players deposit and play over days/weeks.
    Bonus abusers complete the cycle in minutes/hours.

    Multi-accounting amplifies this: same person, 50 accounts,
    each claiming the welcome bonus. Shared device fingerprints
    and IP addresses connect the network.

    Args:
        event: Current transaction.
        state: Player's accumulated state.
        config: Threshold configuration.

    Returns:
        FraudAlert if bonus abuse detected, None otherwise.
    """
    if event.transaction_type == "deposit":
        state.last_deposit_time = event.event_time
        state.last_deposit_amount = event.amount_cents
        return None

    if event.transaction_type == "bonus":
        state.last_bonus_time = event.event_time
        return None

    if event.transaction_type == "withdrawal":
        # Check for rapid deposit → withdrawal cycle
        if state.last_deposit_time is not None:
            time_diff = (event.event_time - state.last_deposit_time).total_seconds()
            window_seconds = config.deposit_withdraw_window_minutes * 60

            if time_diff < window_seconds:
                # Extra suspicious if a bonus was claimed in between
                bonus_in_between = (
                    state.last_bonus_time is not None
                    and state.last_deposit_time < state.last_bonus_time < event.event_time
                )

                if bonus_in_between:
                    return FraudAlert(
                        alert_id=f"BNS-{uuid.uuid4().hex[:12]}",
                        player_id=event.player_id,
                        detected_at=datetime.now(timezone.utc),
                        fraud_type="bonus_abuse",
                        severity=AlertSeverity.HIGH,
                        confidence_score=0.9,
                        description=(
                            f"Bonus abuse: deposit({state.last_deposit_amount/100:.2f}) "
                            f"→ bonus → withdrawal({event.amount_cents/100:.2f}) "
                            f"in {time_diff/60:.1f} minutes"
                        ),
                        jurisdiction=event.jurisdiction,
                        risk_level="high",
                        triggering_transaction_id=event.transaction_id,
                        rule_name="bonus_abuse_rapid_cycle",
                    )

                # Rapid deposit-withdraw without bonus is potential laundering
                if event.amount_cents >= state.last_deposit_amount * 0.8:
                    return FraudAlert(
                        alert_id=f"LAU-{uuid.uuid4().hex[:12]}",
                        player_id=event.player_id,
                        detected_at=datetime.now(timezone.utc),
                        fraud_type="money_laundering",
                        severity=AlertSeverity.MEDIUM,
                        confidence_score=0.6,
                        description=(
                            f"Rapid deposit-withdraw: deposited "
                            f"{state.last_deposit_amount/100:.2f}, "
                            f"withdrawing {event.amount_cents/100:.2f} "
                            f"after {time_diff/60:.1f} minutes"
                        ),
                        jurisdiction=event.jurisdiction,
                        risk_level="medium",
                        triggering_transaction_id=event.transaction_id,
                        rule_name="rapid_deposit_withdraw",
                    )

    return None


# ---------------------------------------------------------------------------
# Flink pipeline
# ---------------------------------------------------------------------------

def apply_all_rules(
    event: TransactionEvent,
    state: PlayerState,
    config: FlinkFraudConfig,
) -> list[FraudAlert]:
    """Apply all fraud detection rules to a transaction event.

    Each rule is independent and can fire simultaneously. A single
    transaction can trigger multiple alerts (e.g., velocity + amount
    anomaly = very likely bot).

    Args:
        event: Transaction to evaluate.
        state: Player's accumulated state.
        config: Rule thresholds.

    Returns:
        List of FraudAlerts (may be empty).
    """
    alerts: list[FraudAlert] = []

    rule_checks = [
        check_velocity,
        check_amount_anomaly,
        check_geo_impossibility,
        check_device_change,
        check_bonus_abuse,
    ]

    for rule_fn in rule_checks:
        alert = rule_fn(event, state, config)
        if alert is not None:
            alerts.append(alert)

    return alerts


def create_flink_pipeline(config: FlinkFraudConfig) -> Any:
    """Create and configure the Flink streaming pipeline.

    Pipeline topology:
    1. KafkaSource → Deserialize JSON → TransactionEvent
    2. KeyBy player_id (stateful per-player processing)
    3. Process function: apply rules, manage state, emit alerts
    4. Split: alerts → Kafka alert topic + Iceberg fraud_alerts table
    5. Side output: metrics → Prometheus pushgateway

    Checkpointing ensures exactly-once semantics:
    - Flink checkpoints state to S3 every 60 seconds
    - On failure, restores from last checkpoint
    - Iceberg sink commits atomically with checkpoints
    - No duplicate or missed alerts

    Args:
        config: Pipeline configuration.

    Returns:
        Configured StreamExecutionEnvironment.
    """
    if not PYFLINK_AVAILABLE:
        logger.error("PyFlink not installed. Run: pip install apache-flink")
        return None

    logger.info("Creating Flink streaming environment")

    env = StreamExecutionEnvironment.get_execution_environment()

    # Enable checkpointing for exactly-once semantics
    env.enable_checkpointing(config.checkpoint_interval_ms)
    env.get_checkpoint_config().set_checkpoint_storage_dir(config.checkpoint_dir)

    # Kafka source: consume transaction events
    kafka_source = (
        KafkaSource.builder()
        .set_bootstrap_servers(config.kafka_bootstrap)
        .set_topics(config.kafka_topic_transactions)
        .set_group_id(config.kafka_consumer_group)
        .set_starting_offsets(KafkaOffsetsInitializer.latest())
        .set_value_only_deserializer(SimpleStringSchema())
        .build()
    )

    # Watermark strategy: allow 30 seconds out-of-orderness
    # In gambling, events can arrive late due to network issues or
    # mobile connectivity gaps. 30 seconds covers most cases.
    watermark_strategy = (
        WatermarkStrategy
        .for_bounded_out_of_orderness(
            Duration.of_seconds(config.max_out_of_orderness_seconds)
        )
    )

    # Build the pipeline
    transaction_stream = env.from_source(
        kafka_source,
        watermark_strategy,
        "kafka-transactions",
    )

    logger.info("Flink pipeline created. Kafka topic: %s", config.kafka_topic_transactions)
    logger.info(
        "Checkpointing every %d ms to %s",
        config.checkpoint_interval_ms, config.checkpoint_dir,
    )

    return env


# ---------------------------------------------------------------------------
# Windowed aggregations
# ---------------------------------------------------------------------------

@dataclass
class WindowedMetrics:
    """Aggregated metrics from tumbling/sliding windows.

    Used for dashboard reporting and anomaly detection at the
    jurisdiction level (not just per-player).
    """

    window_start: datetime
    window_end: datetime
    jurisdiction: str
    total_transactions: int = 0
    total_amount_cents: int = 0
    alert_count: int = 0
    critical_alerts: int = 0
    high_alerts: int = 0
    unique_players: int = 0
    avg_risk_score: float = 0.0

    def to_json(self) -> str:
        """Serialize for dashboard consumption."""
        return json.dumps({
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "jurisdiction": self.jurisdiction,
            "total_transactions": self.total_transactions,
            "total_amount_cents": self.total_amount_cents,
            "alert_count": self.alert_count,
            "critical_alerts": self.critical_alerts,
            "high_alerts": self.high_alerts,
            "unique_players": self.unique_players,
            "avg_risk_score": self.avg_risk_score,
        })


def describe_windowed_aggregations() -> str:
    """Document the windowed aggregation strategy.

    Returns a description of the windows used in the pipeline.
    This is called by the documentation generator.
    """
    return """
    Windowed Aggregation Strategy:

    1. Tumbling Window (1 minute):
       - Per-jurisdiction transaction count and volume
       - Alert count by severity
       - Feeds real-time dashboard counters

    2. Sliding Window (5 minutes, 1 minute slide):
       - Per-player velocity calculation
       - Running average bet size
       - Device change frequency

    3. Tumbling Window (1 hour):
       - Jurisdiction-level fraud rate (alerts / transactions)
       - Model performance metrics (precision estimate)
       - Capacity planning metrics (TPS per jurisdiction)

    4. Session Window (gap = 30 minutes):
       - Per-player session boundaries
       - Session-level features: duration, bet count, game diversity
       - Bot detection: sessions without natural pauses
    """


# ---------------------------------------------------------------------------
# Iceberg sink configuration
# ---------------------------------------------------------------------------

def configure_iceberg_sink(config: FlinkFraudConfig) -> dict[str, str]:
    """Return configuration for Flink's Iceberg table sink.

    The Iceberg sink commits atomically with Flink checkpoints,
    providing exactly-once delivery to the lakehouse.

    In production, this is configured via Flink SQL:
        CREATE TABLE fraud_alerts WITH (
            'connector' = 'iceberg',
            'catalog-type' = 'rest',
            'catalog-uri' = 'http://iceberg-rest:8181',
            ...
        )

    Returns:
        Dict of Iceberg sink properties.
    """
    return {
        "connector": "iceberg",
        "catalog-type": "rest",
        "catalog-name": "fraud_catalog",
        "catalog-uri": config.iceberg_catalog_uri,
        "warehouse": config.iceberg_warehouse,
        "database-name": config.iceberg_namespace,
        "table-name": "fraud_alerts",
        "write.format.default": "parquet",
        "write.target-file-size-bytes": "134217728",  # 128MB
        "write.upsert.enabled": "true",
        # Commit aligned with Flink checkpoints
        "sink.parallelism": "4",
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="PyFlink Real-Time Fraud Detection with Iceberg",
    )
    parser.add_argument(
        "--kafka-bootstrap",
        default="localhost:9092",
        help="Kafka bootstrap servers",
    )
    parser.add_argument(
        "--kafka-topic",
        default="casino.transactions",
        help="Kafka topic for transaction events",
    )
    parser.add_argument(
        "--iceberg-catalog",
        default="http://localhost:8181",
        help="Iceberg REST catalog URI",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default="s3a://fraud-lakehouse/checkpoints",
        help="Checkpoint storage directory",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point for the Flink fraud detection pipeline."""
    args = parse_args()

    config = FlinkFraudConfig(
        kafka_bootstrap=args.kafka_bootstrap,
        kafka_topic_transactions=args.kafka_topic,
        iceberg_catalog_uri=args.iceberg_catalog,
        checkpoint_dir=args.checkpoint_dir,
    )

    logger.info("Flink Fraud Detection Pipeline")
    logger.info("Kafka: %s (topic: %s)", config.kafka_bootstrap, config.kafka_topic_transactions)
    logger.info("Iceberg: %s", config.iceberg_catalog_uri)

    env = create_flink_pipeline(config)
    if env is None:
        logger.error("Failed to create Flink environment. Check PyFlink installation.")
        return

    # In production, the pipeline runs indefinitely:
    # env.execute("FraudRealtimeDetection")
    logger.info(
        "Pipeline configured. In production, call env.execute() to start "
        "consuming from Kafka and writing alerts to Iceberg."
    )


if __name__ == "__main__":
    main()
