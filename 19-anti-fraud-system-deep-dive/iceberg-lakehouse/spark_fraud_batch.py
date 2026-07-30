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
PySpark Batch Fraud Processing with Apache Iceberg
===================================================

Reference implementation for Chapter 19: Anti-Fraud System Deep Dive.

This module demonstrates batch fraud detection using PySpark reading from
and writing to Apache Iceberg tables. While Flink handles real-time detection
(see flink_fraud_realtime.py), Spark handles the heavy batch workloads:

- Nightly feature engineering across all players
- Batch model scoring with ensemble ML models
- Data quality validation
- Aggregated fraud reporting per jurisdiction
- Historical pattern analysis (backfilling fraud signals)

Architecture:
    Iceberg (transactions) → Spark (feature eng + scoring) → Iceberg (fraud_alerts)
                                      ↓
                               Iceberg (risk_scores)

Performance targets for gaming workloads:
- Process 500M daily transactions in under 2 hours
- Feature engineering: 50+ features per player per day
- Batch scoring: ensemble of 6 models (XGBoost, IsolationForest, LSTM, etc.)
- Jurisdiction-partitioned for regulatory compliance

Usage:
    spark-submit --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0 \\
        spark_fraud_batch.py --date 2026-03-12 --jurisdiction MGA
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# PySpark imports -- wrapped for educational readability
try:
    from pyspark.sql import DataFrame, SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql import types as T
    from pyspark.sql.window import Window

    PYSPARK_AVAILABLE = True
except ImportError:
    PYSPARK_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("spark_fraud_batch")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SparkFraudConfig:
    """Configuration for the Spark fraud batch pipeline."""

    catalog_name: str = "fraud_catalog"
    catalog_uri: str = "http://localhost:8181"
    warehouse: str = "s3a://fraud-lakehouse/warehouse"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    namespace: str = "fraud_analytics"
    # Processing parameters
    processing_date: str = ""  # YYYY-MM-DD
    jurisdiction: str | None = None  # None = all jurisdictions
    model_version: str = "v2.3"
    # Thresholds
    risk_threshold_critical: float = 0.9
    risk_threshold_high: float = 0.7
    risk_threshold_medium: float = 0.4
    # Feature engineering
    velocity_windows: list[int] | None = None  # minutes

    def __post_init__(self) -> None:
        if self.velocity_windows is None:
            self.velocity_windows = [5, 15, 60, 1440]  # 5min, 15min, 1hr, 24hr


# ---------------------------------------------------------------------------
# Spark session with Iceberg catalog
# ---------------------------------------------------------------------------

def create_spark_session(config: SparkFraudConfig) -> Any:
    """Create a SparkSession configured with Iceberg catalog.

    The Iceberg catalog integration lets Spark read/write Iceberg tables
    with full support for schema evolution, partition pruning, and
    time-travel queries.

    For production, replace MinIO with AWS S3 and use Glue catalog
    or Hive Metastore instead of REST catalog.
    """
    if not PYSPARK_AVAILABLE:
        logger.error("PySpark not installed. Run: pip install pyspark")
        sys.exit(1)

    logger.info("Creating SparkSession with Iceberg catalog at %s", config.catalog_uri)

    spark = (
        SparkSession.builder
        .appName(f"FraudBatch-{config.processing_date}")
        .config(
            f"spark.sql.catalog.{config.catalog_name}",
            "org.apache.iceberg.spark.SparkCatalog",
        )
        .config(
            f"spark.sql.catalog.{config.catalog_name}.type",
            "rest",
        )
        .config(
            f"spark.sql.catalog.{config.catalog_name}.uri",
            config.catalog_uri,
        )
        .config(
            f"spark.sql.catalog.{config.catalog_name}.warehouse",
            config.warehouse,
        )
        .config(
            f"spark.sql.catalog.{config.catalog_name}.io-impl",
            "org.apache.iceberg.aws.s3.S3FileIO",
        )
        .config(
            f"spark.sql.catalog.{config.catalog_name}.s3.endpoint",
            config.s3_endpoint,
        )
        # Iceberg extensions for MERGE INTO, time-travel, etc.
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        # Adaptive query execution for dynamic partition pruning
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
        .getOrCreate()
    )

    logger.info("SparkSession created. App ID: %s", spark.sparkContext.applicationId)
    return spark


# ---------------------------------------------------------------------------
# Data loading with partition pruning
# ---------------------------------------------------------------------------

def load_transactions(
    spark: Any,
    config: SparkFraudConfig,
    processing_date: str,
    jurisdiction: str | None = None,
) -> Any:
    """Load transactions from Iceberg with partition pruning.

    Iceberg's partition pruning is critical for performance. Without it,
    Spark would scan the entire transaction history. With date + jurisdiction
    partitioning, we only read the relevant Parquet files.

    For a 500M row/day table, this reduces scan from ~50TB to ~500GB.

    Args:
        spark: Active SparkSession.
        config: Pipeline configuration.
        processing_date: Date to process (YYYY-MM-DD).
        jurisdiction: Optional jurisdiction filter.

    Returns:
        DataFrame with day's transactions.
    """
    table_path = f"{config.catalog_name}.{config.namespace}.transactions"
    logger.info("Loading transactions from %s for date=%s", table_path, processing_date)

    df = spark.table(table_path)

    # Date filter -- Iceberg will prune partitions automatically
    df = df.filter(F.col("event_time").cast("date") == processing_date)

    # Jurisdiction filter for regulatory-partitioned processing
    if jurisdiction:
        df = df.filter(F.col("jurisdiction") == jurisdiction)
        logger.info("Filtered to jurisdiction: %s", jurisdiction)

    count = df.count()
    logger.info("Loaded %d transactions for %s", count, processing_date)
    return df


def load_historical_transactions(
    spark: Any,
    config: SparkFraudConfig,
    player_ids: list[str],
    lookback_days: int = 30,
) -> Any:
    """Load historical transactions for a set of players.

    Used for feature engineering -- computing 30-day velocity, average bet
    sizes, preferred game types, etc. The lookback window is configurable
    per jurisdiction (UKGC requires 90-day monitoring windows).

    Args:
        spark: Active SparkSession.
        config: Pipeline configuration.
        player_ids: List of player IDs to load history for.
        lookback_days: Number of days of history.

    Returns:
        DataFrame with historical transactions.
    """
    table_path = f"{config.catalog_name}.{config.namespace}.transactions"
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    df = spark.table(table_path)
    df = df.filter(
        (F.col("event_time").cast("date") >= start_date)
        & (F.col("player_id").isin(player_ids))
    )

    logger.info(
        "Loaded historical transactions for %d players (lookback=%d days)",
        len(player_ids), lookback_days,
    )
    return df


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def compute_velocity_features(df: Any, windows_minutes: list[int]) -> Any:
    """Compute transaction velocity features across multiple time windows.

    Velocity is the most reliable fraud signal in gambling. A legitimate
    player might place 5-20 bets per hour. A bot places 500+. Money
    laundering often shows rapid deposit-withdraw cycles.

    Features per window:
    - tx_count: number of transactions
    - total_amount: sum of amounts
    - distinct_games: number of different games played
    - avg_amount: average transaction amount
    - max_amount: largest single transaction
    - amount_stddev: standard deviation (low stddev = automated behavior)

    Args:
        df: Transactions DataFrame.
        windows_minutes: List of window sizes in minutes.

    Returns:
        DataFrame with velocity features per player.
    """
    logger.info("Computing velocity features for windows: %s minutes", windows_minutes)

    result_df = df.select("player_id").distinct()

    for window_min in windows_minutes:
        window_seconds = window_min * 60
        suffix = f"_{window_min}m"

        # Window spec: per player, ordered by time, range-based
        w = (
            Window.partitionBy("player_id")
            .orderBy(F.col("event_time").cast("long"))
            .rangeBetween(-window_seconds, 0)
        )

        velocity_df = df.select(
            "player_id",
            F.count("transaction_id").over(w).alias(f"tx_count{suffix}"),
            F.sum("amount_cents").over(w).alias(f"total_amount{suffix}"),
            F.avg("amount_cents").over(w).alias(f"avg_amount{suffix}"),
            F.max("amount_cents").over(w).alias(f"max_amount{suffix}"),
            F.stddev("amount_cents").over(w).alias(f"amount_stddev{suffix}"),
            F.countDistinct("game_id").over(w).alias(f"distinct_games{suffix}"),
        ).dropDuplicates(["player_id"])

        result_df = result_df.join(velocity_df, on="player_id", how="left")

    return result_df


def compute_amount_pattern_features(df: Any) -> Any:
    """Compute amount-based features that detect anomalous betting patterns.

    Fraud signals from amount patterns:
    - Round amounts: bots often bet exact amounts (1000, 5000)
    - Structuring: amounts just below reporting thresholds (9999 vs 10000)
    - Escalation: rapidly increasing bet sizes (chasing losses or laundering)
    - Even distribution: money laundering uses very consistent amounts

    Returns:
        DataFrame with amount pattern features per player.
    """
    logger.info("Computing amount pattern features")

    # Amount distribution features per player
    amount_features = df.groupBy("player_id").agg(
        F.count("transaction_id").alias("total_tx_count"),
        F.sum("amount_cents").alias("total_wagered"),
        F.avg("amount_cents").alias("avg_bet_amount"),
        F.stddev("amount_cents").alias("bet_amount_stddev"),
        F.min("amount_cents").alias("min_bet_amount"),
        F.max("amount_cents").alias("max_bet_amount"),
        # Round amount detection: count of transactions with round amounts
        F.sum(
            F.when(F.col("amount_cents") % 100 == 0, 1).otherwise(0)
        ).alias("round_amount_count"),
        # Structuring detection: amounts in 90-100% of common thresholds
        F.sum(
            F.when(
                (F.col("amount_cents") >= 900000) & (F.col("amount_cents") < 1000000),
                1,
            ).otherwise(0)
        ).alias("near_threshold_count"),
        # Distinct payment methods
        F.countDistinct("payment_method").alias("distinct_payment_methods"),
        # Game diversity
        F.countDistinct("game_id").alias("distinct_games_played"),
        F.countDistinct("game_type").alias("distinct_game_types"),
    )

    # Derived features
    amount_features = amount_features.withColumn(
        "round_amount_ratio",
        F.col("round_amount_count") / F.col("total_tx_count"),
    ).withColumn(
        "bet_coefficient_of_variation",
        F.when(
            F.col("avg_bet_amount") > 0,
            F.col("bet_amount_stddev") / F.col("avg_bet_amount"),
        ).otherwise(0.0),
    )

    return amount_features


def compute_geo_anomaly_features(df: Any) -> Any:
    """Compute geographic anomaly features.

    Key fraud signals:
    - Multiple countries in short time (impossible travel)
    - IP address changes within a session
    - Jurisdiction mismatch (registered in UK, playing from Russia)
    - VPN/proxy usage patterns (same IP across many players)

    Returns:
        DataFrame with geo features per player.
    """
    logger.info("Computing geographic anomaly features")

    # Per-player geographic features
    geo_features = df.groupBy("player_id").agg(
        F.countDistinct("ip_address").alias("distinct_ips"),
        F.countDistinct("device_fingerprint").alias("distinct_devices"),
        # Session-level IP changes (signals account sharing or ATO)
        F.countDistinct(
            F.concat("session_id", F.lit(":"), "ip_address")
        ).alias("session_ip_combinations"),
        F.countDistinct("session_id").alias("total_sessions"),
    )

    # IP sharing: same IP used by multiple players (collusion/multi-accounting signal)
    ip_sharing = df.groupBy("ip_address").agg(
        F.countDistinct("player_id").alias("players_per_ip"),
    )

    # Join back to get max players sharing an IP with this player
    player_ips = df.select("player_id", "ip_address").distinct()
    player_ip_sharing = player_ips.join(ip_sharing, on="ip_address").groupBy("player_id").agg(
        F.max("players_per_ip").alias("max_ip_sharing_count"),
        F.avg("players_per_ip").alias("avg_ip_sharing_count"),
    )

    geo_features = geo_features.join(player_ip_sharing, on="player_id", how="left")

    # Derived: IP changes per session (high = suspicious)
    geo_features = geo_features.withColumn(
        "ip_changes_per_session",
        F.when(
            F.col("total_sessions") > 0,
            F.col("session_ip_combinations") / F.col("total_sessions"),
        ).otherwise(0.0),
    )

    return geo_features


def engineer_all_features(
    spark: Any,
    transactions_df: Any,
    config: SparkFraudConfig,
) -> Any:
    """Run the complete feature engineering pipeline.

    Combines velocity, amount, and geo features into a single feature
    vector per player. This is the input to batch model scoring.

    Args:
        spark: Active SparkSession.
        transactions_df: Day's transactions.
        config: Pipeline configuration.

    Returns:
        DataFrame with all features per player.
    """
    logger.info("Running complete feature engineering pipeline")

    assert config.velocity_windows is not None
    velocity = compute_velocity_features(transactions_df, config.velocity_windows)
    amounts = compute_amount_pattern_features(transactions_df)
    geo = compute_geo_anomaly_features(transactions_df)

    # Join all feature sets
    features = velocity.join(amounts, on="player_id", how="outer")
    features = features.join(geo, on="player_id", how="outer")

    # Fill nulls with 0 for numeric columns
    numeric_cols = [
        f.name for f in features.schema.fields if str(f.dataType) in ("DoubleType()", "LongType()", "IntegerType()")
    ]
    features = features.fillna(0, subset=numeric_cols)

    feature_count = len(features.columns) - 1  # minus player_id
    player_count = features.count()
    logger.info(
        "Feature engineering complete: %d features for %d players",
        feature_count, player_count,
    )

    return features


# ---------------------------------------------------------------------------
# Batch scoring
# ---------------------------------------------------------------------------

def batch_score_players(
    features_df: Any,
    config: SparkFraudConfig,
) -> Any:
    """Score all players using pre-trained ensemble models.

    In production, models are loaded from MLflow or a model registry.
    This reference implementation uses threshold-based scoring to
    demonstrate the pipeline structure.

    The ensemble combines:
    1. XGBoost (supervised) -- trained on labeled fraud cases
    2. Isolation Forest (unsupervised) -- detects anomalies without labels
    3. LSTM (temporal) -- catches sequence-based patterns
    4. Random Forest (supervised) -- interpretable baseline
    5. Autoencoder (unsupervised) -- reconstruction error as anomaly score
    6. Graph Neural Network -- network-based collusion detection

    Each model produces a score [0, 1]. The ensemble averages them with
    learned weights (XGBoost typically gets highest weight ~0.3).

    Args:
        features_df: DataFrame with features per player.
        config: Pipeline configuration.

    Returns:
        DataFrame with risk scores per player.
    """
    logger.info("Batch scoring with model version: %s", config.model_version)

    # In production, load trained models here:
    # xgb_model = mlflow.sklearn.load_model(f"models:/fraud_xgboost/{config.model_version}")
    # iso_model = mlflow.sklearn.load_model(f"models:/fraud_isolation_forest/{config.model_version}")

    # Reference implementation: rule-based scoring that mimics model output
    # This demonstrates the scoring pipeline structure without requiring
    # actual trained models.
    scored_df = features_df.withColumn(
        "bot_score",
        F.when(F.col("tx_count_5m") > 50, 0.95)
        .when(F.col("tx_count_5m") > 20, 0.7)
        .when(F.col("bet_coefficient_of_variation") < 0.05, 0.6)
        .otherwise(0.1),
    ).withColumn(
        "ato_score",
        F.when(F.col("distinct_ips") > 10, 0.8)
        .when(F.col("ip_changes_per_session") > 3, 0.7)
        .when(F.col("distinct_devices") > 5, 0.6)
        .otherwise(0.1),
    ).withColumn(
        "laundering_score",
        F.when(F.col("near_threshold_count") > 5, 0.85)
        .when(F.col("round_amount_ratio") > 0.8, 0.7)
        .when(
            (F.col("total_wagered") > 10000000) & (F.col("bet_coefficient_of_variation") < 0.1),
            0.75,
        )
        .otherwise(0.1),
    ).withColumn(
        "collusion_score",
        F.when(F.col("max_ip_sharing_count") > 5, 0.8)
        .when(F.col("avg_ip_sharing_count") > 3, 0.6)
        .otherwise(0.1),
    ).withColumn(
        "bonus_abuse_score",
        # Simplified: in production, this checks bonus-to-wagering ratios
        F.when(
            (F.col("distinct_payment_methods") > 5) & (F.col("total_tx_count") < 20),
            0.7,
        ).otherwise(0.1),
    )

    # Ensemble: weighted average of individual model scores
    # Weights learned via validation set optimization
    scored_df = scored_df.withColumn(
        "overall_risk_score",
        (
            F.col("bot_score") * 0.25
            + F.col("ato_score") * 0.20
            + F.col("laundering_score") * 0.25
            + F.col("collusion_score") * 0.15
            + F.col("bonus_abuse_score") * 0.15
        ),
    )

    # Classify risk level
    scored_df = scored_df.withColumn(
        "risk_level",
        F.when(F.col("overall_risk_score") >= config.risk_threshold_critical, "critical")
        .when(F.col("overall_risk_score") >= config.risk_threshold_high, "high")
        .when(F.col("overall_risk_score") >= config.risk_threshold_medium, "medium")
        .otherwise("low"),
    )

    # Add metadata
    now_utc = datetime.now(timezone.utc).isoformat()
    scored_df = scored_df.withColumn("scored_at", F.lit(now_utc).cast("timestamp"))
    scored_df = scored_df.withColumn("model_version", F.lit(config.model_version))

    # Log distribution
    risk_dist = scored_df.groupBy("risk_level").count().collect()
    for row in risk_dist:
        logger.info("Risk level %s: %d players", row["risk_level"], row["count"])

    return scored_df


# ---------------------------------------------------------------------------
# Write results back to Iceberg
# ---------------------------------------------------------------------------

def write_risk_scores(
    spark: Any,
    scored_df: Any,
    config: SparkFraudConfig,
) -> None:
    """Write risk scores to the Iceberg risk_scores table.

    Uses MERGE INTO for upsert semantics -- if a player already has a
    score for today, update it rather than creating duplicates.

    Args:
        spark: Active SparkSession.
        scored_df: DataFrame with scored players.
        config: Pipeline configuration.
    """
    table_path = f"{config.catalog_name}.{config.namespace}.risk_scores"
    logger.info("Writing risk scores to %s", table_path)

    # Select only the columns that match the risk_scores schema
    output_df = scored_df.select(
        "player_id",
        "scored_at",
        "overall_risk_score",
        "risk_level",
        "bot_score",
        "ato_score",
        "laundering_score",
        "collusion_score",
        "bonus_abuse_score",
        "model_version",
    )

    # Add jurisdiction (from the config or default)
    if config.jurisdiction:
        output_df = output_df.withColumn("jurisdiction", F.lit(config.jurisdiction))
    else:
        output_df = output_df.withColumn("jurisdiction", F.lit("ALL"))

    # Write using Iceberg's overwrite-by-filter for idempotent processing
    output_df.writeTo(table_path).overwritePartitions()

    count = output_df.count()
    logger.info("Wrote %d risk scores to %s", count, table_path)


def write_fraud_alerts(
    spark: Any,
    scored_df: Any,
    config: SparkFraudConfig,
) -> None:
    """Generate and write fraud alerts for high-risk players.

    Only players above the medium threshold generate alerts. Each alert
    includes the dominant fraud type and supporting evidence.

    Args:
        spark: Active SparkSession.
        scored_df: DataFrame with scored players.
        config: Pipeline configuration.
    """
    table_path = f"{config.catalog_name}.{config.namespace}.fraud_alerts"

    # Filter to alertable players
    alertable = scored_df.filter(
        F.col("overall_risk_score") >= config.risk_threshold_medium
    )

    # Determine dominant fraud type per player
    alertable = alertable.withColumn(
        "fraud_type",
        F.when(
            F.greatest("bot_score", "ato_score", "laundering_score", "collusion_score", "bonus_abuse_score")
            == F.col("bot_score"),
            "bot_play",
        )
        .when(
            F.greatest("bot_score", "ato_score", "laundering_score", "collusion_score", "bonus_abuse_score")
            == F.col("ato_score"),
            "account_takeover",
        )
        .when(
            F.greatest("bot_score", "ato_score", "laundering_score", "collusion_score", "bonus_abuse_score")
            == F.col("laundering_score"),
            "money_laundering",
        )
        .when(
            F.greatest("bot_score", "ato_score", "laundering_score", "collusion_score", "bonus_abuse_score")
            == F.col("collusion_score"),
            "collusion",
        )
        .otherwise("bonus_abuse"),
    )

    # Build alert records
    now_utc = datetime.now(timezone.utc).isoformat()
    alerts_df = alertable.select(
        F.concat(F.lit("ALERT-"), F.col("player_id"), F.lit("-"), F.lit(config.processing_date)).alias("alert_id"),
        "player_id",
        F.lit(now_utc).cast("timestamp").alias("detected_at"),
        "fraud_type",
        F.col("risk_level").alias("severity"),
        F.col("overall_risk_score").alias("confidence_score"),
        F.concat(
            F.lit("Batch detection: "),
            F.col("fraud_type"),
            F.lit(" (score="),
            F.round("overall_risk_score", 3).cast("string"),
            F.lit(")"),
        ).alias("description"),
        F.lit(config.jurisdiction or "ALL").alias("jurisdiction"),
        "risk_level",
        F.lit(None).cast("string").alias("transaction_ids"),
        F.lit("detected").alias("status"),
        F.lit(None).cast("string").alias("analyst_id"),
        F.lit(None).cast("timestamp").alias("resolved_at"),
        F.lit(None).cast("string").alias("resolution"),
    )

    alerts_df.writeTo(table_path).append()

    count = alerts_df.count()
    logger.info("Generated %d fraud alerts to %s", count, table_path)


# ---------------------------------------------------------------------------
# Data quality checks
# ---------------------------------------------------------------------------

def run_data_quality_checks(df: Any, stage: str) -> dict[str, Any]:
    """Run data quality checks and return metrics.

    Quality checks are essential in fraud pipelines -- corrupt data leads
    to false positives that erode analyst trust, or false negatives that
    let fraud through.

    Args:
        df: DataFrame to check.
        stage: Pipeline stage name for logging.

    Returns:
        Dict of quality metrics.
    """
    logger.info("Running data quality checks for stage: %s", stage)

    total_rows = df.count()
    null_counts: dict[str, int] = {}
    for col_name in df.columns:
        null_count = df.filter(F.col(col_name).isNull()).count()
        if null_count > 0:
            null_counts[col_name] = null_count

    # Check for duplicate transaction IDs
    duplicate_count = 0
    if "transaction_id" in df.columns:
        distinct_count = df.select("transaction_id").distinct().count()
        duplicate_count = total_rows - distinct_count

    metrics = {
        "stage": stage,
        "total_rows": total_rows,
        "null_columns": null_counts,
        "duplicate_count": duplicate_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Log warnings for quality issues
    if duplicate_count > 0:
        logger.warning(
            "[%s] Found %d duplicate transaction IDs", stage, duplicate_count
        )

    high_null_cols = {k: v for k, v in null_counts.items() if v > total_rows * 0.1}
    if high_null_cols:
        logger.warning(
            "[%s] Columns with >10%% nulls: %s", stage, high_null_cols
        )

    logger.info("[%s] Quality check passed: %d rows, %d duplicates", stage, total_rows, duplicate_count)
    return metrics


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

def collect_pipeline_metrics(
    config: SparkFraudConfig,
    quality_metrics: list[dict[str, Any]],
    risk_distribution: dict[str, int],
    processing_time_seconds: float,
) -> dict[str, Any]:
    """Collect and log pipeline execution metrics.

    These metrics feed into Prometheus/Grafana for pipeline monitoring.
    Key SLIs:
    - Processing time (SLO: <2 hours for 500M transactions)
    - Alert volume (sudden spikes indicate model issues)
    - Risk distribution (drift from expected distribution = model degradation)

    Args:
        config: Pipeline configuration.
        quality_metrics: Quality check results from each stage.
        risk_distribution: Count of players per risk level.
        processing_time_seconds: Total pipeline execution time.

    Returns:
        Dict of pipeline metrics.
    """
    metrics = {
        "pipeline": "spark_fraud_batch",
        "processing_date": config.processing_date,
        "jurisdiction": config.jurisdiction or "ALL",
        "model_version": config.model_version,
        "processing_time_seconds": processing_time_seconds,
        "risk_distribution": risk_distribution,
        "quality_checks": quality_metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("Pipeline metrics: %s", json.dumps(metrics, indent=2))

    # In production, push to Prometheus pushgateway:
    # from prometheus_client import CollectorRegistry, push_to_gateway, Gauge
    # registry = CollectorRegistry()
    # g = Gauge('fraud_batch_processing_time', 'Batch processing duration',
    #           registry=registry)
    # g.set(processing_time_seconds)
    # push_to_gateway('prometheus-pushgw:9091', job='fraud_batch', registry=registry)

    return metrics


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_batch_pipeline(config: SparkFraudConfig) -> dict[str, Any]:
    """Execute the complete batch fraud detection pipeline.

    Steps:
    1. Load transactions from Iceberg (partition-pruned)
    2. Data quality check on input
    3. Feature engineering (velocity + amount + geo)
    4. Data quality check on features
    5. Batch scoring with ensemble models
    6. Write risk scores to Iceberg
    7. Generate and write fraud alerts to Iceberg
    8. Collect and report metrics

    Args:
        config: Pipeline configuration.

    Returns:
        Pipeline execution metrics.
    """
    import time

    start_time = time.monotonic()
    quality_metrics: list[dict[str, Any]] = []

    logger.info(
        "Starting batch fraud pipeline for date=%s, jurisdiction=%s",
        config.processing_date, config.jurisdiction or "ALL",
    )

    # Step 1: Create Spark session
    spark = create_spark_session(config)

    try:
        # Step 2: Load transactions
        transactions_df = load_transactions(
            spark, config, config.processing_date, config.jurisdiction
        )

        # Step 3: Input quality check
        input_qc = run_data_quality_checks(transactions_df, "input")
        quality_metrics.append(input_qc)

        if input_qc["total_rows"] == 0:
            logger.warning("No transactions found for %s. Exiting.", config.processing_date)
            return {"status": "skipped", "reason": "no_data"}

        # Step 4: Feature engineering
        features_df = engineer_all_features(spark, transactions_df, config)

        # Step 5: Feature quality check
        feature_qc = run_data_quality_checks(features_df, "features")
        quality_metrics.append(feature_qc)

        # Step 6: Batch scoring
        scored_df = batch_score_players(features_df, config)

        # Step 7: Write results to Iceberg
        write_risk_scores(spark, scored_df, config)
        write_fraud_alerts(spark, scored_df, config)

        # Step 8: Collect metrics
        risk_dist_rows = scored_df.groupBy("risk_level").count().collect()
        risk_distribution = {row["risk_level"]: row["count"] for row in risk_dist_rows}

        elapsed = time.monotonic() - start_time
        metrics = collect_pipeline_metrics(
            config, quality_metrics, risk_distribution, elapsed
        )

        logger.info("Batch fraud pipeline completed in %.1f seconds", elapsed)
        return metrics

    finally:
        spark.stop()
        logger.info("SparkSession stopped.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="PySpark Batch Fraud Detection with Iceberg",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Processing date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--jurisdiction",
        default=None,
        help="Jurisdiction filter (e.g., MGA, UKGC, SGA). Default: all.",
    )
    parser.add_argument(
        "--catalog-uri",
        default="http://localhost:8181",
        help="Iceberg REST catalog URI",
    )
    parser.add_argument(
        "--model-version",
        default="v2.3",
        help="ML model version to use for scoring",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    config = SparkFraudConfig(
        processing_date=args.date,
        jurisdiction=args.jurisdiction,
        catalog_uri=args.catalog_uri,
        model_version=args.model_version,
    )

    run_batch_pipeline(config)


if __name__ == "__main__":
    main()
