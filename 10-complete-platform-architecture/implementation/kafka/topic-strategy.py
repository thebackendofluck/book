#!/usr/bin/env python3
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
Chapter 42 - Complete Platform Architecture
Kafka Topic Partitioning Strategy Generator for iGambling Platform

Generates Kafka topic configurations per bounded context with:
- Partition counts based on expected throughput per domain
- Replication factors based on data criticality
- Retention policies aligned with regulatory requirements
- Key strategies for optimal partition distribution

Usage:
    python topic-strategy.py --output topics.json
    python topic-strategy.py --apply --bootstrap-servers kafka:9092
    python topic-strategy.py --dry-run
"""

import argparse
import json
import subprocess
from typing import Any
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class DataCriticality(Enum):
    CRITICAL = "critical"       # Financial transactions, RNG results
    HIGH = "high"               # Player actions, compliance events
    MEDIUM = "medium"           # Session events, game state
    LOW = "low"                 # Analytics, logs


class PartitionKeyStrategy(Enum):
    PLAYER_ID = "player_id"             # Even distribution by player
    GAME_SESSION_ID = "game_session_id" # Ordering per game session
    TRANSACTION_ID = "transaction_id"   # Ordering per transaction
    PROVIDER_ID = "provider_id"         # Per game provider
    JURISDICTION = "jurisdiction"       # Per regulatory jurisdiction
    TIMESTAMP = "timestamp"            # Time-based (analytics)
    COMPOSITE = "composite"            # Multiple key fields


@dataclass
class TopicConfig:
    name: str
    domain: str
    partitions: int
    replication_factor: int
    retention_ms: int
    retention_bytes: int
    cleanup_policy: str
    min_insync_replicas: int
    key_strategy: PartitionKeyStrategy
    key_fields: list = field(default_factory=list)
    criticality: DataCriticality = DataCriticality.MEDIUM
    compression_type: str = "lz4"
    max_message_bytes: int = 1048576  # 1MB
    segment_bytes: int = 1073741824   # 1GB
    description: str = ""
    headers: dict = field(default_factory=dict)

    def to_kafka_config(self) -> dict:
        return {
            "topic": self.name,
            "partitions": self.partitions,
            "replication_factor": self.replication_factor,
            "config": {
                "retention.ms": str(self.retention_ms),
                "retention.bytes": str(self.retention_bytes),
                "cleanup.policy": self.cleanup_policy,
                "min.insync.replicas": str(self.min_insync_replicas),
                "compression.type": self.compression_type,
                "max.message.bytes": str(self.max_message_bytes),
                "segment.bytes": str(self.segment_bytes),
                "message.timestamp.type": "CreateTime",
            }
        }


# ──────────────────────────────────────────────────────────────
# Domain-specific topic definitions
# ──────────────────────────────────────────────────────────────

SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000
NINETY_DAYS_MS = 90 * 24 * 60 * 60 * 1000
ONE_YEAR_MS = 365 * 24 * 60 * 60 * 1000
FIVE_YEARS_MS = 5 * 365 * 24 * 60 * 60 * 1000
UNLIMITED = -1


def generate_payment_topics() -> list:
    """Payment Processing bounded context - highest criticality."""
    return [
        TopicConfig(
            name="payment.deposits.initiated",
            domain="payment-processing",
            partitions=24,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            compression_type="lz4",
            description="Deposit initiation events - partitioned by player for ordering"
        ),
        TopicConfig(
            name="payment.deposits.completed",
            domain="payment-processing",
            partitions=24,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.TRANSACTION_ID,
            key_fields=["transaction_id"],
            criticality=DataCriticality.CRITICAL,
            description="Deposit completion confirmations from PSPs"
        ),
        TopicConfig(
            name="payment.withdrawals.requested",
            domain="payment-processing",
            partitions=12,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            description="Withdrawal requests - lower throughput than deposits"
        ),
        TopicConfig(
            name="payment.withdrawals.processed",
            domain="payment-processing",
            partitions=12,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.TRANSACTION_ID,
            key_fields=["transaction_id"],
            criticality=DataCriticality.CRITICAL,
            description="Processed withdrawal confirmations"
        ),
        TopicConfig(
            name="payment.psp.callbacks",
            domain="payment-processing",
            partitions=16,
            replication_factor=3,
            retention_ms=NINETY_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.TRANSACTION_ID,
            key_fields=["psp_reference"],
            criticality=DataCriticality.CRITICAL,
            description="PSP webhook callbacks - high volume during peak"
        ),
        TopicConfig(
            name="payment.wallet.balance-changes",
            domain="payment-processing",
            partitions=32,
            replication_factor=3,
            retention_ms=ONE_YEAR_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="compact",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            description="Wallet balance change log - compacted for current state"
        ),
        TopicConfig(
            name="payment.reconciliation.events",
            domain="payment-processing",
            partitions=8,
            replication_factor=3,
            retention_ms=ONE_YEAR_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.TIMESTAMP,
            key_fields=["reconciliation_batch_id"],
            criticality=DataCriticality.HIGH,
            description="Daily/hourly reconciliation batch results"
        ),
    ]


def generate_game_engine_topics() -> list:
    """Game Engine bounded context - high throughput, medium retention."""
    return [
        TopicConfig(
            name="games.rounds.started",
            domain="game-engine",
            partitions=48,
            replication_factor=3,
            retention_ms=THIRTY_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.GAME_SESSION_ID,
            key_fields=["session_id"],
            criticality=DataCriticality.HIGH,
            description="Game round start - highest throughput topic, 48 partitions"
        ),
        TopicConfig(
            name="games.rounds.completed",
            domain="game-engine",
            partitions=48,
            replication_factor=3,
            retention_ms=NINETY_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.GAME_SESSION_ID,
            key_fields=["session_id"],
            criticality=DataCriticality.HIGH,
            description="Game round results with outcome and payout"
        ),
        TopicConfig(
            name="games.rng.results",
            domain="game-engine",
            partitions=24,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.GAME_SESSION_ID,
            key_fields=["round_id"],
            criticality=DataCriticality.CRITICAL,
            description="RNG results - regulatory retention requirement (5+ years)"
        ),
        TopicConfig(
            name="games.bets.placed",
            domain="game-engine",
            partitions=48,
            replication_factor=3,
            retention_ms=ONE_YEAR_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            description="Bet placement events - partitioned by player for ordering"
        ),
        TopicConfig(
            name="games.settlements",
            domain="game-engine",
            partitions=32,
            replication_factor=3,
            retention_ms=ONE_YEAR_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            description="Game settlement / payout events"
        ),
        TopicConfig(
            name="games.provider.events",
            domain="game-engine",
            partitions=16,
            replication_factor=3,
            retention_ms=THIRTY_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PROVIDER_ID,
            key_fields=["provider_id"],
            criticality=DataCriticality.MEDIUM,
            description="Third-party game provider integration events"
        ),
    ]


def generate_player_topics() -> list:
    """Player Management bounded context."""
    return [
        TopicConfig(
            name="players.registration",
            domain="player-management",
            partitions=8,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.HIGH,
            description="Player registration events"
        ),
        TopicConfig(
            name="players.kyc.verification",
            domain="player-management",
            partitions=8,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            description="KYC verification results - regulatory retention"
        ),
        TopicConfig(
            name="players.sessions",
            domain="player-management",
            partitions=32,
            replication_factor=3,
            retention_ms=NINETY_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.MEDIUM,
            description="Player session lifecycle (login/logout/timeout)"
        ),
        TopicConfig(
            name="players.responsible-gaming",
            domain="player-management",
            partitions=8,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            description="Self-exclusion, deposit limits, cooling-off events"
        ),
        TopicConfig(
            name="players.profile.changes",
            domain="player-management",
            partitions=8,
            replication_factor=3,
            retention_ms=ONE_YEAR_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="compact",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.HIGH,
            description="Profile updates - compacted for latest state"
        ),
    ]


def generate_compliance_topics() -> list:
    """Compliance bounded context - long retention, regulatory."""
    return [
        TopicConfig(
            name="compliance.aml.alerts",
            domain="compliance",
            partitions=8,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.CRITICAL,
            description="AML suspicious activity alerts"
        ),
        TopicConfig(
            name="compliance.regulatory.reports",
            domain="compliance",
            partitions=4,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.JURISDICTION,
            key_fields=["jurisdiction"],
            criticality=DataCriticality.CRITICAL,
            description="Generated regulatory reports per jurisdiction"
        ),
        TopicConfig(
            name="compliance.audit.trail",
            domain="compliance",
            partitions=16,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.COMPOSITE,
            key_fields=["source_service", "event_type"],
            criticality=DataCriticality.CRITICAL,
            description="Cross-domain audit trail - all compliance-relevant events"
        ),
        TopicConfig(
            name="compliance.sar.filings",
            domain="compliance",
            partitions=4,
            replication_factor=3,
            retention_ms=FIVE_YEARS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id", "filing_id"],
            criticality=DataCriticality.CRITICAL,
            description="Suspicious Activity Report filings"
        ),
    ]


def generate_analytics_topics() -> list:
    """Analytics bounded context - high volume, shorter retention."""
    return [
        TopicConfig(
            name="analytics.player.activity",
            domain="analytics",
            partitions=32,
            replication_factor=2,
            retention_ms=THIRTY_DAYS_MS,
            retention_bytes=107374182400,  # 100GB
            cleanup_policy="delete",
            min_insync_replicas=1,
            key_strategy=PartitionKeyStrategy.PLAYER_ID,
            key_fields=["player_id"],
            criticality=DataCriticality.LOW,
            compression_type="zstd",
            description="Aggregated player activity for BI"
        ),
        TopicConfig(
            name="analytics.revenue.realtime",
            domain="analytics",
            partitions=16,
            replication_factor=2,
            retention_ms=SEVEN_DAYS_MS,
            retention_bytes=53687091200,  # 50GB
            cleanup_policy="delete",
            min_insync_replicas=1,
            key_strategy=PartitionKeyStrategy.JURISDICTION,
            key_fields=["jurisdiction", "game_type"],
            criticality=DataCriticality.MEDIUM,
            compression_type="zstd",
            description="Real-time revenue metrics per jurisdiction/game type"
        ),
        TopicConfig(
            name="analytics.platform.metrics",
            domain="analytics",
            partitions=8,
            replication_factor=2,
            retention_ms=SEVEN_DAYS_MS,
            retention_bytes=21474836480,  # 20GB
            cleanup_policy="delete",
            min_insync_replicas=1,
            key_strategy=PartitionKeyStrategy.TIMESTAMP,
            key_fields=["metric_name"],
            criticality=DataCriticality.LOW,
            compression_type="zstd",
            description="Platform-level operational metrics"
        ),
    ]


def generate_platform_topics() -> list:
    """Cross-cutting platform topics (DLQ, saga, CDC)."""
    return [
        TopicConfig(
            name="platform.dlq",
            domain="platform",
            partitions=8,
            replication_factor=3,
            retention_ms=THIRTY_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.COMPOSITE,
            key_fields=["original_topic", "error_type"],
            criticality=DataCriticality.HIGH,
            description="Dead letter queue for failed message processing"
        ),
        TopicConfig(
            name="platform.saga.commands",
            domain="platform",
            partitions=16,
            replication_factor=3,
            retention_ms=SEVEN_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.COMPOSITE,
            key_fields=["saga_id"],
            criticality=DataCriticality.CRITICAL,
            description="Saga orchestrator commands for distributed transactions"
        ),
        TopicConfig(
            name="platform.saga.replies",
            domain="platform",
            partitions=16,
            replication_factor=3,
            retention_ms=SEVEN_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.COMPOSITE,
            key_fields=["saga_id"],
            criticality=DataCriticality.CRITICAL,
            description="Saga participant replies"
        ),
        TopicConfig(
            name="platform.cdc.outbox",
            domain="platform",
            partitions=24,
            replication_factor=3,
            retention_ms=SEVEN_DAYS_MS,
            retention_bytes=UNLIMITED,
            cleanup_policy="delete",
            min_insync_replicas=2,
            key_strategy=PartitionKeyStrategy.COMPOSITE,
            key_fields=["aggregate_type", "aggregate_id"],
            criticality=DataCriticality.HIGH,
            description="Change Data Capture outbox events (Debezium)"
        ),
    ]


def generate_all_topics() -> list:
    """Generate complete topic strategy for the platform."""
    topics = []
    topics.extend(generate_payment_topics())
    topics.extend(generate_game_engine_topics())
    topics.extend(generate_player_topics())
    topics.extend(generate_compliance_topics())
    topics.extend(generate_analytics_topics())
    topics.extend(generate_platform_topics())
    return topics


def print_strategy_summary(topics: list):
    """Print a human-readable summary of the topic strategy."""
    domains = {}
    for t in topics:
        domains.setdefault(t.domain, []).append(t)

    total_partitions = sum(t.partitions for t in topics)

    print("=" * 80)
    print("iGambling Platform - Kafka Topic Partitioning Strategy")
    print("=" * 80)
    print(f"\nTotal topics: {len(topics)}")
    print(f"Total partitions: {total_partitions}")
    print(f"Recommended broker count: {max(3, total_partitions // 100)} minimum")
    print()

    for domain, domain_topics in sorted(domains.items()):
        domain_partitions = sum(t.partitions for t in domain_topics)
        print(f"\n{'─' * 60}")
        print(f"Domain: {domain} ({len(domain_topics)} topics, {domain_partitions} partitions)")
        print(f"{'─' * 60}")
        for t in domain_topics:
            retention_days = t.retention_ms // (24 * 60 * 60 * 1000)
            print(f"  {t.name}")
            print(f"    Partitions: {t.partitions} | RF: {t.replication_factor} | "
                  f"ISR: {t.min_insync_replicas}")
            print(f"    Key: {t.key_strategy.value} ({', '.join(t.key_fields)})")
            print(f"    Retention: {retention_days} days | Criticality: {t.criticality.value}")
            print(f"    Cleanup: {t.cleanup_policy} | Compression: {t.compression_type}")
            print(f"    {t.description}")
            print()


def generate_kafka_commands(topics: list, bootstrap_servers: str) -> list:
    """Generate kafka-topics.sh commands for topic creation."""
    commands = []
    for t in topics:
        config = t.to_kafka_config()
        config_flags = " ".join(
            f"--config {k}={v}" for k, v in config["config"].items()
        )
        cmd = (
            f"kafka-topics.sh --create "
            f"--bootstrap-server {bootstrap_servers} "
            f"--topic {config['topic']} "
            f"--partitions {config['partitions']} "
            f"--replication-factor {config['replication_factor']} "
            f"{config_flags}"
        )
        commands.append(cmd)
    return commands


def export_json(topics: list, output_file: str):
    """Export topic strategy to JSON file."""
    data: dict[str, Any] = {
        "platform": "acme-casino",
        "version": "1.0.0",
        "total_topics": len(topics),
        "total_partitions": sum(t.partitions for t in topics),
        "domains": {},
        "topics": []
    }

    for t in topics:
        topic_dict = {
            "name": t.name,
            "domain": t.domain,
            "partitions": t.partitions,
            "replication_factor": t.replication_factor,
            "retention_ms": t.retention_ms,
            "cleanup_policy": t.cleanup_policy,
            "min_insync_replicas": t.min_insync_replicas,
            "key_strategy": t.key_strategy.value,
            "key_fields": t.key_fields,
            "criticality": t.criticality.value,
            "compression_type": t.compression_type,
            "description": t.description,
            "kafka_config": t.to_kafka_config()["config"]
        }
        data["topics"].append(topic_dict)
        data["domains"].setdefault(t.domain, {
            "topic_count": 0,
            "partition_count": 0,
        })
        data["domains"][t.domain]["topic_count"] += 1
        data["domains"][t.domain]["partition_count"] += t.partitions

    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Topic strategy exported to {output_file}")


def apply_topics(topics: list, bootstrap_servers: str, dry_run: bool = False):
    """Apply topic configurations to Kafka cluster."""
    commands = generate_kafka_commands(topics, bootstrap_servers)

    for cmd in commands:
        if dry_run:
            print(f"[DRY RUN] {cmd}")
        else:
            print(f"Executing: {cmd}")
            try:
                result = subprocess.run(
                    cmd.split(), capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    print(f"  OK: {result.stdout.strip()}")
                else:
                    print(f"  ERROR: {result.stderr.strip()}")
            except FileNotFoundError:
                print("  ERROR: kafka-topics.sh not found. "
                      "Ensure Kafka binaries are in PATH.")
                sys.exit(1)
            except subprocess.TimeoutExpired:
                print("  ERROR: Command timed out")


def main():
    parser = argparse.ArgumentParser(
        description="Kafka Topic Partitioning Strategy for iGambling Platform"
    )
    parser.add_argument("--output", "-o", help="Export strategy to JSON file")
    parser.add_argument("--apply", action="store_true",
                        help="Apply topics to Kafka cluster")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing")
    parser.add_argument("--bootstrap-servers", default="kafka:9092",
                        help="Kafka bootstrap servers (default: kafka:9092)")
    parser.add_argument("--domain", choices=[
        "payment-processing", "game-engine", "player-management",
        "compliance", "analytics", "platform", "all"
    ], default="all", help="Generate topics for specific domain")
    parser.add_argument("--summary", action="store_true",
                        help="Print strategy summary")

    args = parser.parse_args()

    domain_generators = {
        "payment-processing": generate_payment_topics,
        "game-engine": generate_game_engine_topics,
        "player-management": generate_player_topics,
        "compliance": generate_compliance_topics,
        "analytics": generate_analytics_topics,
        "platform": generate_platform_topics,
    }

    if args.domain == "all":
        topics = generate_all_topics()
    else:
        topics = domain_generators[args.domain]()

    if args.summary or (not args.output and not args.apply and not args.dry_run):
        print_strategy_summary(topics)

    if args.output:
        export_json(topics, args.output)

    if args.apply or args.dry_run:
        apply_topics(topics, args.bootstrap_servers, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
