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
Apache Iceberg Data Lakehouse for Fraud Analytics
==================================================

Reference implementation for Chapter 19: Anti-Fraud System Deep Dive.

This module demonstrates how to build an Apache Iceberg-based data lakehouse
for iGaming fraud analytics. Iceberg provides ACID transactions, time-travel
queries, schema evolution, and partition evolution -- all critical for fraud
detection workflows where data integrity and auditability are non-negotiable.

Why Iceberg for Fraud Detection:
- ACID transactions: concurrent reads/writes without corruption during
  high-volume transaction processing (100K+ TPS at peak)
- Time-travel: compare fraud patterns across dates, replay investigations
- Schema evolution: add new fraud signals without rewriting existing data
- Partition evolution: change partitioning strategy as fraud patterns shift
- Merge-on-read: fast writes for streaming fraud events
- Copy-on-write: fast reads for batch analytics and reporting

Architecture:
    Games → Kafka → Flink (real-time) → Iceberg Tables
                                              ↓
    Spark (batch) → Feature Engineering → ML Pipeline
                                              ↓
                                     fraud_alerts table

Storage: MinIO (S3-compatible) for local dev, AWS S3 for production.
Catalog: Iceberg REST catalog (or Hive Metastore in legacy environments).

Usage:
    python iceberg_fraud_lakehouse.py --catalog-uri http://localhost:8181
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# PyIceberg imports (requires: pip install pyiceberg[s3])
# In production, these are available. For reference/educational purposes,
# we wrap imports so the module can be read without pyiceberg installed.
# ---------------------------------------------------------------------------
try:
    from pyiceberg.catalog import load_catalog
    from pyiceberg.catalog.rest import RestCatalog
    from pyiceberg.expressions import (
        AlwaysTrue,
        And,
        EqualTo,
        GreaterThanOrEqual,
        LessThan,
    )
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.table import Table
    from pyiceberg.transforms import (
        DayTransform,
        IdentityTransform,
    )
    from pyiceberg.types import (
        BooleanType,
        DoubleType,
        FloatType,
        IntegerType,
        LongType,
        NestedField,
        StringType,
        TimestamptzType,
    )

    PYICEBERG_AVAILABLE = True
except ImportError:
    PYICEBERG_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("iceberg_fraud_lakehouse")


# ---------------------------------------------------------------------------
# Enums for fraud domain
# ---------------------------------------------------------------------------
class RiskLevel(str, Enum):
    """Risk classification tiers used across all fraud tables."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudType(str, Enum):
    """Known fraud categories in iGaming."""

    BOT_PLAY = "bot_play"
    ACCOUNT_TAKEOVER = "account_takeover"
    MONEY_LAUNDERING = "money_laundering"
    COLLUSION = "collusion"
    BONUS_ABUSE = "bonus_abuse"
    MULTI_ACCOUNTING = "multi_accounting"
    CHIP_DUMPING = "chip_dumping"


class WriteMode(str, Enum):
    """Iceberg write modes with different performance trade-offs.

    MERGE_ON_READ: Fast writes, slower reads. Best for streaming fraud events
    where write latency matters (sub-second ingestion from Flink).

    COPY_ON_WRITE: Slower writes, faster reads. Best for batch analytics
    where Spark runs overnight aggregations on fraud_alerts.
    """

    MERGE_ON_READ = "merge-on-read"
    COPY_ON_WRITE = "copy-on-write"


# ---------------------------------------------------------------------------
# Table schema definitions
# ---------------------------------------------------------------------------

# Transactions table: every bet, deposit, withdrawal
# Partitioned by day + jurisdiction for regulatory compliance (each jurisdiction
# requires data isolation for audits).
TRANSACTIONS_SCHEMA = Schema(
    NestedField(field_id=1, name="transaction_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="player_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="event_time", field_type=TimestamptzType(), required=True),
    NestedField(field_id=4, name="transaction_type", field_type=StringType(), required=True),
    NestedField(field_id=5, name="amount_cents", field_type=LongType(), required=True),
    NestedField(field_id=6, name="currency", field_type=StringType(), required=True),
    NestedField(field_id=7, name="game_id", field_type=StringType(), required=False),
    NestedField(field_id=8, name="game_type", field_type=StringType(), required=False),
    NestedField(field_id=9, name="payment_method", field_type=StringType(), required=False),
    NestedField(field_id=10, name="jurisdiction", field_type=StringType(), required=True),
    NestedField(field_id=11, name="brand_id", field_type=IntegerType(), required=True),
    NestedField(field_id=12, name="ip_address", field_type=StringType(), required=False),
    NestedField(field_id=13, name="device_fingerprint", field_type=StringType(), required=False),
    NestedField(field_id=14, name="session_id", field_type=StringType(), required=False),
    NestedField(field_id=15, name="risk_score", field_type=FloatType(), required=False),
    NestedField(field_id=16, name="risk_level", field_type=StringType(), required=False),
)

# Player sessions table: aggregated session-level features
PLAYER_SESSIONS_SCHEMA = Schema(
    NestedField(field_id=1, name="session_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="player_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="session_start", field_type=TimestamptzType(), required=True),
    NestedField(field_id=4, name="session_end", field_type=TimestamptzType(), required=False),
    NestedField(field_id=5, name="duration_seconds", field_type=IntegerType(), required=False),
    NestedField(field_id=6, name="total_bets", field_type=IntegerType(), required=False),
    NestedField(field_id=7, name="total_wagered_cents", field_type=LongType(), required=False),
    NestedField(field_id=8, name="total_won_cents", field_type=LongType(), required=False),
    NestedField(field_id=9, name="distinct_games", field_type=IntegerType(), required=False),
    NestedField(field_id=10, name="jurisdiction", field_type=StringType(), required=True),
    NestedField(field_id=11, name="ip_address", field_type=StringType(), required=False),
    NestedField(field_id=12, name="device_fingerprint", field_type=StringType(), required=False),
    NestedField(field_id=13, name="country", field_type=StringType(), required=False),
    NestedField(field_id=14, name="avg_bet_interval_ms", field_type=DoubleType(), required=False),
    NestedField(field_id=15, name="is_bot_suspect", field_type=BooleanType(), required=False),
    NestedField(field_id=16, name="risk_level", field_type=StringType(), required=False),
)

# Fraud alerts table: output of detection pipeline
FRAUD_ALERTS_SCHEMA = Schema(
    NestedField(field_id=1, name="alert_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="player_id", field_type=StringType(), required=True),
    NestedField(field_id=3, name="detected_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=4, name="fraud_type", field_type=StringType(), required=True),
    NestedField(field_id=5, name="severity", field_type=StringType(), required=True),
    NestedField(field_id=6, name="confidence_score", field_type=FloatType(), required=True),
    NestedField(field_id=7, name="description", field_type=StringType(), required=False),
    NestedField(field_id=8, name="jurisdiction", field_type=StringType(), required=True),
    NestedField(field_id=9, name="risk_level", field_type=StringType(), required=True),
    NestedField(field_id=10, name="transaction_ids", field_type=StringType(), required=False),
    NestedField(field_id=11, name="status", field_type=StringType(), required=False),
    NestedField(field_id=12, name="analyst_id", field_type=StringType(), required=False),
    NestedField(field_id=13, name="resolved_at", field_type=TimestamptzType(), required=False),
    NestedField(field_id=14, name="resolution", field_type=StringType(), required=False),
)

# Risk scores table: ML model output per player
RISK_SCORES_SCHEMA = Schema(
    NestedField(field_id=1, name="player_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="scored_at", field_type=TimestamptzType(), required=True),
    NestedField(field_id=3, name="overall_risk_score", field_type=FloatType(), required=True),
    NestedField(field_id=4, name="risk_level", field_type=StringType(), required=True),
    NestedField(field_id=5, name="bot_score", field_type=FloatType(), required=False),
    NestedField(field_id=6, name="ato_score", field_type=FloatType(), required=False),
    NestedField(field_id=7, name="laundering_score", field_type=FloatType(), required=False),
    NestedField(field_id=8, name="collusion_score", field_type=FloatType(), required=False),
    NestedField(field_id=9, name="bonus_abuse_score", field_type=FloatType(), required=False),
    NestedField(field_id=10, name="model_version", field_type=StringType(), required=True),
    NestedField(field_id=11, name="jurisdiction", field_type=StringType(), required=True),
    NestedField(field_id=12, name="features_json", field_type=StringType(), required=False),
)


# ---------------------------------------------------------------------------
# Partition specifications
# ---------------------------------------------------------------------------

# Partition by date + jurisdiction + risk_level
# Date partitioning enables efficient time-travel queries.
# Jurisdiction partitioning supports regulatory data isolation.
# Risk level partitioning optimizes analyst queries (they filter by risk).
TRANSACTIONS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=3, field_id=1000, transform=DayTransform(), name="event_day"
    ),
    PartitionField(
        source_id=10, field_id=1001, transform=IdentityTransform(), name="jurisdiction"
    ),
    PartitionField(
        source_id=16, field_id=1002, transform=IdentityTransform(), name="risk_level"
    ),
)

FRAUD_ALERTS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=3, field_id=1000, transform=DayTransform(), name="detected_day"
    ),
    PartitionField(
        source_id=8, field_id=1001, transform=IdentityTransform(), name="jurisdiction"
    ),
    PartitionField(
        source_id=9, field_id=1002, transform=IdentityTransform(), name="risk_level"
    ),
)


# ---------------------------------------------------------------------------
# Catalog and table management
# ---------------------------------------------------------------------------

@dataclass
class LakehouseConfig:
    """Configuration for the Iceberg fraud lakehouse."""

    catalog_name: str = "fraud_catalog"
    catalog_uri: str = "http://localhost:8181"
    warehouse: str = "s3://fraud-lakehouse/warehouse"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    namespace: str = "fraud_analytics"


@dataclass
class TableDefinition:
    """Defines an Iceberg table with its schema and partition spec."""

    name: str
    schema: Any  # Schema type from pyiceberg
    partition_spec: Any  # PartitionSpec type from pyiceberg
    write_mode: WriteMode = WriteMode.MERGE_ON_READ
    properties: dict[str, str] = field(default_factory=dict)


def get_table_definitions() -> list[TableDefinition]:
    """Return all table definitions for the fraud lakehouse.

    Each table is designed for a specific role in the fraud pipeline:
    - transactions: raw event store (merge-on-read for fast streaming writes)
    - player_sessions: aggregated sessions (copy-on-write for read-heavy analytics)
    - fraud_alerts: detection output (merge-on-read for real-time alerting)
    - risk_scores: ML scores (copy-on-write for batch model serving)
    """
    return [
        TableDefinition(
            name="transactions",
            schema=TRANSACTIONS_SCHEMA,
            partition_spec=TRANSACTIONS_PARTITION_SPEC,
            write_mode=WriteMode.MERGE_ON_READ,
            properties={
                "write.merge.mode": "merge-on-read",
                "commit.retry.num-retries": "4",
                "write.target-file-size-bytes": "134217728",  # 128MB
                "history.expire.max-snapshot-age-ms": "259200000",  # 3 days
            },
        ),
        TableDefinition(
            name="player_sessions",
            schema=PLAYER_SESSIONS_SCHEMA,
            partition_spec=PartitionSpec(
                PartitionField(
                    source_id=3, field_id=1000, transform=DayTransform(), name="session_day"
                ),
                PartitionField(
                    source_id=10, field_id=1001, transform=IdentityTransform(), name="jurisdiction"
                ),
            ),
            write_mode=WriteMode.COPY_ON_WRITE,
            properties={
                "write.merge.mode": "copy-on-write",
                "write.target-file-size-bytes": "268435456",  # 256MB
            },
        ),
        TableDefinition(
            name="fraud_alerts",
            schema=FRAUD_ALERTS_SCHEMA,
            partition_spec=FRAUD_ALERTS_PARTITION_SPEC,
            write_mode=WriteMode.MERGE_ON_READ,
            properties={
                "write.merge.mode": "merge-on-read",
                "commit.retry.num-retries": "4",
            },
        ),
        TableDefinition(
            name="risk_scores",
            schema=RISK_SCORES_SCHEMA,
            partition_spec=PartitionSpec(
                PartitionField(
                    source_id=2, field_id=1000, transform=DayTransform(), name="scored_day"
                ),
                PartitionField(
                    source_id=11, field_id=1001, transform=IdentityTransform(), name="jurisdiction"
                ),
            ),
            write_mode=WriteMode.COPY_ON_WRITE,
            properties={
                "write.merge.mode": "copy-on-write",
                "write.target-file-size-bytes": "67108864",  # 64MB (smaller tables)
            },
        ),
    ]


class FraudLakehouse:
    """Manages the Apache Iceberg fraud analytics lakehouse.

    This class handles catalog connection, namespace creation, table creation,
    schema evolution, time-travel queries, and snapshot management. It wraps
    PyIceberg with gaming-specific conventions.

    Example usage:
        config = LakehouseConfig(catalog_uri="http://iceberg-rest:8181")
        lakehouse = FraudLakehouse(config)
        lakehouse.initialize()
        lakehouse.create_all_tables()

        # Time-travel: compare fraud rates last week vs this week
        alerts_last_week = lakehouse.time_travel_query(
            "fraud_alerts",
            as_of=datetime.now(timezone.utc) - timedelta(days=7),
        )
    """

    def __init__(self, config: LakehouseConfig) -> None:
        self.config = config
        self.catalog: Any = None  # RestCatalog when initialized
        self._tables: dict[str, Any] = {}

    def initialize(self) -> None:
        """Connect to the Iceberg REST catalog and create namespace."""
        if not PYICEBERG_AVAILABLE:
            logger.error("PyIceberg not installed. Run: pip install 'pyiceberg[s3]'")
            sys.exit(1)

        logger.info("Connecting to Iceberg catalog at %s", self.config.catalog_uri)
        self.catalog = load_catalog(
            self.config.catalog_name,
            **{
                "type": "rest",
                "uri": self.config.catalog_uri,
                "s3.endpoint": self.config.s3_endpoint,
                "s3.access-key-id": self.config.s3_access_key,
                "s3.secret-access-key": self.config.s3_secret_key,
                "warehouse": self.config.warehouse,
            },
        )

        # Create namespace if it doesn't exist
        namespaces = self.catalog.list_namespaces()
        ns_tuple = (self.config.namespace,)
        if ns_tuple not in namespaces:
            logger.info("Creating namespace: %s", self.config.namespace)
            self.catalog.create_namespace(
                self.config.namespace,
                properties={
                    "description": "Fraud analytics lakehouse for iGaming platform",
                    "owner": "fraud-engineering-team",
                },
            )

    def create_all_tables(self) -> None:
        """Create all fraud analytics tables with appropriate schemas and partitioning."""
        definitions = get_table_definitions()
        for table_def in definitions:
            self._create_table(table_def)

    def _create_table(self, table_def: TableDefinition) -> None:
        """Create a single Iceberg table if it doesn't already exist."""
        full_name = f"{self.config.namespace}.{table_def.name}"

        try:
            table = self.catalog.load_table(full_name)
            logger.info("Table %s already exists (schema version: %s)", full_name, table.schema())
            self._tables[table_def.name] = table
        except Exception:
            logger.info("Creating table: %s", full_name)
            table = self.catalog.create_table(
                full_name,
                schema=table_def.schema,
                partition_spec=table_def.partition_spec,
                properties=table_def.properties,
            )
            self._tables[table_def.name] = table
            logger.info("Created table %s with %d columns", full_name, len(table_def.schema.fields))

    def get_table(self, name: str) -> Any:
        """Load and return a table by name."""
        if name not in self._tables:
            full_name = f"{self.config.namespace}.{name}"
            self._tables[name] = self.catalog.load_table(full_name)
        return self._tables[name]

    # -------------------------------------------------------------------
    # Schema evolution: adding new fraud signals without rewriting data
    # -------------------------------------------------------------------

    def evolve_schema_add_fraud_signal(
        self,
        table_name: str,
        field_name: str,
        field_type: str = "float",
        doc: str = "",
    ) -> None:
        """Add a new fraud signal column to an existing table.

        Iceberg's schema evolution is metadata-only -- no data rewrite needed.
        This is critical for fraud detection where new signals are discovered
        frequently and the transaction table may hold terabytes of data.

        Example: a new behavioral signal like 'mouse_entropy_score' can be
        added without touching existing Parquet files.

        Args:
            table_name: Name of the table to evolve.
            field_name: Name of the new column.
            field_type: Type string ('float', 'string', 'int', 'boolean').
            doc: Documentation for the new field.
        """
        type_map: dict[str, Any] = {
            "float": FloatType(),
            "double": DoubleType(),
            "string": StringType(),
            "int": IntegerType(),
            "long": LongType(),
            "boolean": BooleanType(),
            "timestamp": TimestamptzType(),
        }

        iceberg_type = type_map.get(field_type)
        if iceberg_type is None:
            logger.error("Unknown type: %s. Supported: %s", field_type, list(type_map.keys()))
            return

        table = self.get_table(table_name)
        logger.info(
            "Evolving schema for %s: adding column '%s' (%s)",
            table_name, field_name, field_type,
        )

        with table.update_schema() as update:
            update.add_column(field_name, iceberg_type, doc=doc)

        logger.info(
            "Schema evolution complete. No data rewrite needed -- "
            "existing Parquet files are untouched."
        )

    def demonstrate_schema_evolution(self) -> None:
        """Show how to incrementally add fraud signals over time.

        In practice, the fraud team discovers new signals quarterly.
        Iceberg handles this gracefully -- old data has NULLs for new columns,
        new data populates them. No ETL job, no downtime.
        """
        # Quarter 1: basic fraud detection launches
        logger.info("--- Q1: Adding mouse entropy signal ---")
        self.evolve_schema_add_fraud_signal(
            "transactions",
            "mouse_entropy_score",
            "float",
            "Shannon entropy of mouse movement patterns (0=bot, 1=human)",
        )

        # Quarter 2: device fingerprinting improvements
        logger.info("--- Q2: Adding canvas fingerprint hash ---")
        self.evolve_schema_add_fraud_signal(
            "transactions",
            "canvas_fingerprint_hash",
            "string",
            "SHA-256 of HTML5 canvas fingerprint for device identification",
        )

        # Quarter 3: new regulation requires tracking
        logger.info("--- Q3: Adding regulatory hold flag ---")
        self.evolve_schema_add_fraud_signal(
            "transactions",
            "regulatory_hold",
            "boolean",
            "Transaction held for regulatory review (FIAU requirement)",
        )

    # -------------------------------------------------------------------
    # Time-travel queries: investigate fraud patterns across dates
    # -------------------------------------------------------------------

    def time_travel_query(
        self,
        table_name: str,
        as_of: datetime | None = None,
        snapshot_id: int | None = None,
    ) -> Any:
        """Query a table at a specific point in time.

        Time-travel is essential for fraud investigation:
        - "What did the player's risk score look like 3 days before the incident?"
        - "Show me all transactions for this player as they appeared last Tuesday"
        - "Compare fraud alert volumes before and after model v2.3 deployment"

        Args:
            table_name: Table to query.
            as_of: Query as of this timestamp.
            snapshot_id: Query at this specific snapshot.

        Returns:
            PyArrow table with results.
        """
        table = self.get_table(table_name)

        if snapshot_id is not None:
            logger.info("Time-travel query on %s at snapshot %d", table_name, snapshot_id)
            scan = table.scan(snapshot_id=snapshot_id)
        elif as_of is not None:
            # Find the snapshot that was current at the given timestamp
            logger.info("Time-travel query on %s as of %s", table_name, as_of.isoformat())
            # PyIceberg uses milliseconds for timestamps
            as_of_ms = int(as_of.timestamp() * 1000)
            target_snapshot = None
            for snapshot in table.metadata.snapshots:
                if snapshot.timestamp_ms <= as_of_ms:
                    if target_snapshot is None or snapshot.timestamp_ms > target_snapshot.timestamp_ms:
                        target_snapshot = snapshot
            if target_snapshot is None:
                logger.warning("No snapshot found before %s", as_of.isoformat())
                return None
            scan = table.scan(snapshot_id=target_snapshot.snapshot_id)
        else:
            scan = table.scan()

        return scan.to_arrow()

    def compare_fraud_rates(
        self,
        date_a: datetime,
        date_b: datetime,
        jurisdiction: str | None = None,
    ) -> dict[str, Any]:
        """Compare fraud alert volumes between two dates.

        Useful for measuring the impact of model deployments or rule changes.
        "Did deploying model v2.3 actually reduce false positives in MGA?"

        Args:
            date_a: First comparison date.
            date_b: Second comparison date.
            jurisdiction: Optional jurisdiction filter.

        Returns:
            Dict with comparison metrics.
        """
        logger.info(
            "Comparing fraud rates: %s vs %s (jurisdiction=%s)",
            date_a.date(), date_b.date(), jurisdiction or "all",
        )

        results_a = self.time_travel_query("fraud_alerts", as_of=date_a)
        results_b = self.time_travel_query("fraud_alerts", as_of=date_b)

        count_a = len(results_a) if results_a is not None else 0
        count_b = len(results_b) if results_b is not None else 0

        return {
            "date_a": date_a.isoformat(),
            "date_b": date_b.isoformat(),
            "alerts_a": count_a,
            "alerts_b": count_b,
            "change_pct": ((count_b - count_a) / count_a * 100) if count_a > 0 else 0.0,
            "jurisdiction": jurisdiction or "all",
        }

    # -------------------------------------------------------------------
    # Snapshot management and compaction
    # -------------------------------------------------------------------

    def manage_snapshots(self, table_name: str, max_age_days: int = 7) -> None:
        """Expire old snapshots and compact small files.

        Iceberg accumulates snapshots with every write. For fraud tables that
        receive streaming writes every second, snapshot count grows fast.

        Snapshot expiry removes old metadata (data files shared across snapshots
        are kept). Compaction merges small files into larger ones for better
        read performance.

        Gaming workload considerations:
        - Keep at least 7 days of snapshots for investigation time-travel
        - Compact during off-peak hours (4-6 AM UTC for most operators)
        - Monitor file count per partition (>1000 small files = compaction needed)

        Args:
            table_name: Table to maintain.
            max_age_days: Keep snapshots newer than this.
        """
        table = self.get_table(table_name)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=max_age_days)
        cutoff_ms = int(cutoff.timestamp() * 1000)

        logger.info(
            "Expiring snapshots older than %d days for %s (before %s)",
            max_age_days, table_name, cutoff.isoformat(),
        )

        # Expire old snapshots
        snapshots = table.metadata.snapshots
        expired_count = sum(1 for s in snapshots if s.timestamp_ms < cutoff_ms)
        logger.info(
            "Found %d snapshots total, %d eligible for expiry",
            len(snapshots), expired_count,
        )

        # In production, use: table.expire_snapshots().expire_older_than(cutoff_ms).commit()
        # PyIceberg API may vary by version; this demonstrates the concept.
        logger.info(
            "Snapshot management complete for %s. "
            "Run compaction via Spark: "
            "spark.sql('CALL catalog.system.rewrite_data_files(table => \"%s.%s\")')",
            table_name, self.config.namespace, table_name,
        )

    def get_table_stats(self, table_name: str) -> dict[str, Any]:
        """Get statistics about an Iceberg table.

        Useful for monitoring lakehouse health: file count, snapshot count,
        partition distribution, total size.
        """
        table = self.get_table(table_name)
        metadata = table.metadata

        stats: dict[str, Any] = {
            "table": table_name,
            "format_version": metadata.format_version,
            "snapshot_count": len(metadata.snapshots),
            "schema_fields": len(metadata.schema().fields),
            "partition_fields": len(metadata.spec().fields) if metadata.spec() else 0,
            "properties": dict(metadata.properties),
        }

        if metadata.current_snapshot():
            current = metadata.current_snapshot()
            stats["current_snapshot_id"] = current.snapshot_id
            stats["current_snapshot_time"] = datetime.fromtimestamp(
                current.timestamp_ms / 1000, tz=timezone.utc
            ).isoformat()

        logger.info("Table stats for %s: %s", table_name, stats)
        return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Apache Iceberg Fraud Lakehouse for iGaming",
    )
    parser.add_argument(
        "--catalog-uri",
        default="http://localhost:8181",
        help="Iceberg REST catalog URI (default: http://localhost:8181)",
    )
    parser.add_argument(
        "--warehouse",
        default="s3://fraud-lakehouse/warehouse",
        help="Warehouse location (S3 or MinIO path)",
    )
    parser.add_argument(
        "--s3-endpoint",
        default="http://localhost:9000",
        help="S3/MinIO endpoint (default: http://localhost:9000)",
    )
    parser.add_argument(
        "--action",
        choices=["init", "evolve", "stats", "compare"],
        default="init",
        help="Action to perform",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point for the Iceberg fraud lakehouse."""
    args = parse_args()

    config = LakehouseConfig(
        catalog_uri=args.catalog_uri,
        warehouse=args.warehouse,
        s3_endpoint=args.s3_endpoint,
    )

    lakehouse = FraudLakehouse(config)
    lakehouse.initialize()

    if args.action == "init":
        logger.info("Initializing fraud lakehouse tables...")
        lakehouse.create_all_tables()
        for table_def in get_table_definitions():
            lakehouse.get_table_stats(table_def.name)

    elif args.action == "evolve":
        logger.info("Demonstrating schema evolution...")
        lakehouse.demonstrate_schema_evolution()

    elif args.action == "stats":
        for table_def in get_table_definitions():
            lakehouse.get_table_stats(table_def.name)

    elif args.action == "compare":
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        result = lakehouse.compare_fraud_rates(week_ago, now)
        logger.info("Comparison result: %s", result)

    logger.info("Done.")


if __name__ == "__main__":
    main()
