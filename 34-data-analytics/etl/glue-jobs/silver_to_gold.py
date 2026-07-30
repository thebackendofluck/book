# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AWS Glue ETL Job: Silver to Gold Layer Transformation

This job aggregates cleaned data from the Silver layer and creates
analytics-ready datasets in the Gold layer for business intelligence.

Aggregations Created:
1. Player daily summaries
2. Game performance metrics
3. Revenue aggregations
4. Cohort analysis tables
5. Risk scoring datasets

Input: s3://silver-bucket/
Output: s3://gold-bucket/

Usage:
    AWS Glue Console or via Terraform deployment

Environment Variables:
    - SILVER_BUCKET: Source bucket name
    - GOLD_BUCKET: Target bucket name
    - GLUE_DATABASE: Glue catalog database name
"""

import sys
from datetime import datetime, timezone

from awsglue.context import GlueContext  # ty:ignore[unresolved-import]
from awsglue.dynamicframe import DynamicFrame  # ty:ignore[unresolved-import]
from awsglue.job import Job  # ty:ignore[unresolved-import]
from awsglue.utils import getResolvedOptions  # ty:ignore[unresolved-import]
from pyspark.context import SparkContext  # ty:ignore[unresolved-import]
from pyspark.sql import DataFrame, SparkSession  # ty:ignore[unresolved-import]
from pyspark.sql import functions as F  # ty:ignore[unresolved-import]
from pyspark.sql.window import Window  # ty:ignore[unresolved-import]


# =============================================================================
# CONFIGURATION
# =============================================================================

args = getResolvedOptions(
    sys.argv,
    [
        "JOB_NAME",
        "SILVER_BUCKET",
        "GOLD_BUCKET",
        "GLUE_DATABASE",
        "PROCESSING_DATE",
    ],
)

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

SILVER_BUCKET = args["SILVER_BUCKET"]
GOLD_BUCKET = args["GOLD_BUCKET"]
GLUE_DATABASE = args["GLUE_DATABASE"]
PROCESSING_DATE = args.get("PROCESSING_DATE", datetime.now(timezone.utc).strftime("%Y-%m-%d"))


# =============================================================================
# AGGREGATION FUNCTIONS
# =============================================================================


def create_player_daily_summary(df_events: DataFrame) -> DataFrame:
    """
    Create daily player summary metrics.

    Metrics:
    - Total bets, wins, deposits, withdrawals
    - Session count and duration
    - Game preferences
    - Risk indicators
    """
    # Filter player events
    df_player = df_events.filter(F.col("entity_type") == "player")

    # Parse properties JSON
    df_player = df_player.withColumn(
        "amount", F.get_json_object(F.col("properties"), "$.amount").cast("decimal(18,8)")
    ).withColumn(
        "game_id", F.get_json_object(F.col("properties"), "$.game_id")
    )

    # Aggregate by player and date
    df_summary = df_player.groupBy(
        F.col("entity_id").alias("player_id"),
        F.col("year"),
        F.col("month"),
        F.col("day"),
    ).agg(
        # Activity counts
        F.count("*").alias("total_events"),
        F.countDistinct("event_id").alias("unique_events"),

        # Financial metrics
        F.sum(F.when(F.col("event_type") == "bet_placed", F.col("amount")).otherwise(0)).alias("total_bets"),
        F.sum(F.when(F.col("event_type") == "win", F.col("amount")).otherwise(0)).alias("total_wins"),
        F.sum(F.when(F.col("event_type") == "deposit", F.col("amount")).otherwise(0)).alias("total_deposits"),
        F.sum(F.when(F.col("event_type") == "withdrawal", F.col("amount")).otherwise(0)).alias("total_withdrawals"),

        # Counts by type
        F.count(F.when(F.col("event_type") == "bet_placed", 1)).alias("bet_count"),
        F.count(F.when(F.col("event_type") == "win", 1)).alias("win_count"),
        F.count(F.when(F.col("event_type") == "login", 1)).alias("login_count"),

        # Game preferences
        F.countDistinct("game_id").alias("unique_games_played"),
        F.collect_set("game_id").alias("games_played"),

        # Time analysis
        F.min("timestamp").alias("first_activity"),
        F.max("timestamp").alias("last_activity"),
    )

    # Calculate derived metrics
    df_summary = df_summary.withColumn(
        "win_rate",
        F.when(F.col("total_bets") > 0, F.col("total_wins") / F.col("total_bets")).otherwise(0)
    ).withColumn(
        "ggr",  # Gross Gaming Revenue
        F.col("total_bets") - F.col("total_wins")
    ).withColumn(
        "net_deposits",
        F.col("total_deposits") - F.col("total_withdrawals")
    ).withColumn(
        "session_duration_minutes",
        (F.unix_timestamp("last_activity") - F.unix_timestamp("first_activity")) / 60
    )

    # Add processing timestamp
    df_summary = df_summary.withColumn("processed_at", F.current_timestamp())

    return df_summary


def create_game_performance_metrics(df_events: DataFrame) -> DataFrame:
    """
    Create game performance metrics.

    Metrics:
    - Total bets, wins, rounds
    - RTP (Return to Player)
    - Popularity ranking
    - Player retention
    """
    # Filter game events
    df_games = df_events.filter(
        F.col("event_type").isin(["bet_placed", "win", "game_round"])
    )

    # Parse properties
    df_games = df_games.withColumn(
        "game_id", F.get_json_object(F.col("properties"), "$.game_id")
    ).withColumn(
        "amount", F.get_json_object(F.col("properties"), "$.amount").cast("decimal(18,8)")
    ).filter(F.col("game_id").isNotNull())

    # Aggregate by game and date
    df_metrics = df_games.groupBy(
        F.col("game_id"),
        F.col("year"),
        F.col("month"),
        F.col("day"),
    ).agg(
        # Volume metrics
        F.count("*").alias("total_rounds"),
        F.countDistinct("entity_id").alias("unique_players"),

        # Financial metrics
        F.sum(F.when(F.col("event_type") == "bet_placed", F.col("amount")).otherwise(0)).alias("total_bets"),
        F.sum(F.when(F.col("event_type") == "win", F.col("amount")).otherwise(0)).alias("total_wins"),

        # Average metrics
        F.avg(F.when(F.col("event_type") == "bet_placed", F.col("amount"))).alias("avg_bet_size"),
        F.max(F.when(F.col("event_type") == "bet_placed", F.col("amount"))).alias("max_bet_size"),
    )

    # Calculate RTP
    df_metrics = df_metrics.withColumn(
        "rtp_percentage",
        F.when(F.col("total_bets") > 0, (F.col("total_wins") / F.col("total_bets")) * 100).otherwise(0)
    ).withColumn(
        "ggr",
        F.col("total_bets") - F.col("total_wins")
    ).withColumn(
        "house_edge_percentage",
        100 - F.col("rtp_percentage")
    )

    # Add popularity rank within date
    window_spec = Window.partitionBy("year", "month", "day").orderBy(F.col("total_bets").desc())
    df_metrics = df_metrics.withColumn("popularity_rank", F.row_number().over(window_spec))

    df_metrics = df_metrics.withColumn("processed_at", F.current_timestamp())

    return df_metrics


def create_revenue_aggregations(df_events: DataFrame, df_transactions: DataFrame) -> DataFrame:
    """
    Create revenue aggregation tables.

    Metrics:
    - Daily GGR, NGR
    - Revenue by jurisdiction
    - Revenue by currency
    - First-time depositor metrics
    """
    # Combine events and transactions for comprehensive view
    df_financial = df_events.filter(
        F.col("event_type").isin(["deposit", "withdrawal", "bet_placed", "win"])
    )

    # Parse properties
    df_financial = df_financial.withColumn(
        "amount", F.get_json_object(F.col("properties"), "$.amount").cast("decimal(18,8)")
    ).withColumn(
        "currency", F.coalesce(
            F.get_json_object(F.col("properties"), "$.currency"),
            F.lit("USD")
        )
    ).withColumn(
        "jurisdiction", F.coalesce(
            F.get_json_object(F.col("metadata"), "$.jurisdiction"),
            F.lit("UNKNOWN")
        )
    )

    # Aggregate by date, currency, jurisdiction
    df_revenue = df_financial.groupBy(
        F.col("year"),
        F.col("month"),
        F.col("day"),
        F.col("currency"),
        F.col("jurisdiction"),
    ).agg(
        # Volume
        F.countDistinct("entity_id").alias("unique_players"),
        F.count("*").alias("total_transactions"),

        # Financial
        F.sum(F.when(F.col("event_type") == "deposit", F.col("amount")).otherwise(0)).alias("total_deposits"),
        F.sum(F.when(F.col("event_type") == "withdrawal", F.col("amount")).otherwise(0)).alias("total_withdrawals"),
        F.sum(F.when(F.col("event_type") == "bet_placed", F.col("amount")).otherwise(0)).alias("total_bets"),
        F.sum(F.when(F.col("event_type") == "win", F.col("amount")).otherwise(0)).alias("total_wins"),
    )

    # Calculate revenue metrics
    df_revenue = df_revenue.withColumn(
        "ggr",
        F.col("total_bets") - F.col("total_wins")
    ).withColumn(
        "ngr",  # Assuming 20% of GGR goes to bonuses/taxes
        F.col("ggr") * 0.8
    ).withColumn(
        "net_deposits",
        F.col("total_deposits") - F.col("total_withdrawals")
    ).withColumn(
        "hold_percentage",
        F.when(F.col("total_deposits") > 0, F.col("ggr") / F.col("total_deposits") * 100).otherwise(0)
    )

    df_revenue = df_revenue.withColumn("processed_at", F.current_timestamp())

    return df_revenue


def create_cohort_analysis(df_events: DataFrame) -> DataFrame:
    """
    Create cohort analysis tables.

    Metrics:
    - Registration cohorts
    - Retention by cohort
    - LTV by cohort
    """
    # Get registration events
    df_registrations = df_events.filter(
        F.col("event_type") == "registration"
    ).select(
        F.col("entity_id").alias("player_id"),
        F.col("timestamp").alias("registration_date"),
        F.date_format(F.col("timestamp"), "yyyy-MM").alias("cohort_month"),
    )

    # Get all activity
    df_activity = df_events.filter(
        F.col("entity_type") == "player"
    ).select(
        F.col("entity_id").alias("player_id"),
        F.col("timestamp").alias("activity_date"),
        F.col("event_type"),
        F.get_json_object(F.col("properties"), "$.amount").cast("decimal(18,8)").alias("amount"),
    )

    # Join to get cohort for each activity
    df_cohort = df_activity.join(
        df_registrations,
        on="player_id",
        how="left"
    )

    # Calculate months since registration
    df_cohort = df_cohort.withColumn(
        "months_since_registration",
        F.months_between(F.col("activity_date"), F.col("registration_date")).cast("int")
    )

    # Aggregate by cohort and period
    df_cohort_metrics = df_cohort.groupBy(
        "cohort_month",
        "months_since_registration",
    ).agg(
        F.countDistinct("player_id").alias("active_players"),
        F.sum(F.when(F.col("event_type") == "bet_placed", F.col("amount")).otherwise(0)).alias("total_bets"),
        F.sum(F.when(F.col("event_type") == "deposit", F.col("amount")).otherwise(0)).alias("total_deposits"),
    )

    # Calculate retention rate
    cohort_size_window = Window.partitionBy("cohort_month")
    df_cohort_metrics = df_cohort_metrics.withColumn(
        "cohort_size",
        F.first(F.when(F.col("months_since_registration") == 0, F.col("active_players"))).over(cohort_size_window)
    ).withColumn(
        "retention_rate",
        F.when(F.col("cohort_size") > 0, F.col("active_players") / F.col("cohort_size") * 100).otherwise(0)
    )

    df_cohort_metrics = df_cohort_metrics.withColumn("processed_at", F.current_timestamp())

    return df_cohort_metrics


def create_risk_scoring_dataset(df_events: DataFrame) -> DataFrame:
    """
    Create risk scoring dataset for ML models.

    Features:
    - Betting patterns
    - Deposit/withdrawal behavior
    - Session characteristics
    - Anomaly indicators
    """
    # Filter player events
    df_player = df_events.filter(F.col("entity_type") == "player")

    # Parse properties
    df_player = df_player.withColumn(
        "amount", F.get_json_object(F.col("properties"), "$.amount").cast("decimal(18,8)")
    )

    # Aggregate features by player
    df_risk = df_player.groupBy(
        F.col("entity_id").alias("player_id"),
    ).agg(
        # Volume features
        F.count("*").alias("total_events"),
        F.countDistinct(F.date_format(F.col("timestamp"), "yyyy-MM-dd")).alias("active_days"),

        # Financial features
        F.sum(F.when(F.col("event_type") == "bet_placed", F.col("amount")).otherwise(0)).alias("total_bets"),
        F.sum(F.when(F.col("event_type") == "deposit", F.col("amount")).otherwise(0)).alias("total_deposits"),
        F.sum(F.when(F.col("event_type") == "withdrawal", F.col("amount")).otherwise(0)).alias("total_withdrawals"),

        # Betting patterns
        F.avg(F.when(F.col("event_type") == "bet_placed", F.col("amount"))).alias("avg_bet"),
        F.max(F.when(F.col("event_type") == "bet_placed", F.col("amount"))).alias("max_bet"),
        F.stddev(F.when(F.col("event_type") == "bet_placed", F.col("amount"))).alias("bet_stddev"),

        # Deposit patterns
        F.count(F.when(F.col("event_type") == "deposit", 1)).alias("deposit_count"),
        F.avg(F.when(F.col("event_type") == "deposit", F.col("amount"))).alias("avg_deposit"),

        # Win rate
        F.sum(F.when(F.col("event_type") == "win", F.col("amount")).otherwise(0)).alias("total_wins"),

        # Time features
        F.min("timestamp").alias("first_activity"),
        F.max("timestamp").alias("last_activity"),
    )

    # Calculate derived risk features
    df_risk = df_risk.withColumn(
        "win_rate",
        F.when(F.col("total_bets") > 0, F.col("total_wins") / F.col("total_bets")).otherwise(0)
    ).withColumn(
        "deposit_frequency",
        F.when(F.col("active_days") > 0, F.col("deposit_count") / F.col("active_days")).otherwise(0)
    ).withColumn(
        "max_bet_to_deposit_ratio",
        F.when(F.col("total_deposits") > 0, F.col("max_bet") / F.col("total_deposits")).otherwise(0)
    ).withColumn(
        "withdrawal_to_deposit_ratio",
        F.when(F.col("total_deposits") > 0, F.col("total_withdrawals") / F.col("total_deposits")).otherwise(0)
    )

    # Risk indicators
    df_risk = df_risk.withColumn(
        "risk_unusual_win_rate",
        F.when(F.col("win_rate") > 0.6, 1).otherwise(0)
    ).withColumn(
        "risk_high_deposit_frequency",
        F.when(F.col("deposit_frequency") > 2, 1).otherwise(0)
    ).withColumn(
        "risk_large_bets",
        F.when(F.col("max_bet_to_deposit_ratio") > 0.5, 1).otherwise(0)
    ).withColumn(
        "risk_score",
        F.col("risk_unusual_win_rate") * 30 +
        F.col("risk_high_deposit_frequency") * 20 +
        F.col("risk_large_bets") * 25
    ).withColumn(
        "risk_level",
        F.when(F.col("risk_score") >= 70, "CRITICAL")
        .when(F.col("risk_score") >= 50, "HIGH")
        .when(F.col("risk_score") >= 30, "MEDIUM")
        .otherwise("LOW")
    )

    df_risk = df_risk.withColumn("processed_at", F.current_timestamp())

    return df_risk


# =============================================================================
# MAIN ETL PIPELINE
# =============================================================================


def run_gold_aggregations() -> None:
    """Run all Gold layer aggregations."""
    print(f"Processing Gold aggregations for date: {PROCESSING_DATE}")

    # Read Silver layer data
    events_path = f"s3://{SILVER_BUCKET}/events_cleaned/"
    transactions_path = f"s3://{SILVER_BUCKET}/transactions_cleaned/"

    try:
        df_events = spark.read.parquet(events_path)
        print(f"Read {df_events.count():,} events from Silver layer")

        df_transactions = spark.read.parquet(transactions_path)
        print(f"Read {df_transactions.count():,} transactions from Silver layer")
    except Exception as e:
        print(f"Error reading Silver data: {e}")
        raise

    # Create aggregations
    aggregations = [
        ("player_daily_summary", create_player_daily_summary(df_events)),
        ("game_performance", create_game_performance_metrics(df_events)),
        ("revenue_daily", create_revenue_aggregations(df_events, df_transactions)),
        ("cohort_analysis", create_cohort_analysis(df_events)),
        ("risk_scoring", create_risk_scoring_dataset(df_events)),
    ]

    # Write each aggregation to Gold layer
    for table_name, df in aggregations:
        gold_path = f"s3://{GOLD_BUCKET}/{table_name}/"

        # Determine partitioning
        if "year" in df.columns:
            partition_cols = ["year", "month"]
        else:
            partition_cols = []

        if partition_cols:
            df.write.mode("overwrite").partitionBy(*partition_cols).parquet(gold_path)
        else:
            df.write.mode("overwrite").parquet(gold_path)

        print(f"Wrote {table_name} to Gold layer: {df.count():,} records")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Silver to Gold ETL Job Started")
    print(f"Processing Date: {PROCESSING_DATE}")
    print(f"Silver Bucket: {SILVER_BUCKET}")
    print(f"Gold Bucket: {GOLD_BUCKET}")
    print("=" * 60)

    try:
        run_gold_aggregations()

        print("=" * 60)
        print("Silver to Gold ETL Job Completed Successfully")
        print("=" * 60)

    except Exception as e:
        print(f"ETL Job Failed: {e}")
        raise

    finally:
        job.commit()
