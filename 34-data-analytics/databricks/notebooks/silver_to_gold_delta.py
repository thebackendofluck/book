# Companion code for "The Backend of Luck" - Chapter 34, Data and Analytics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Databricks notebook source
# MAGIC %md
# MAGIC # Silver to Gold ETL - Delta Lake Aggregations
# MAGIC
# MAGIC Creates analytics-ready aggregations from Silver layer data.
# MAGIC
# MAGIC ## Gold Tables Created:
# MAGIC - `player_daily_metrics` - Daily player KPIs
# MAGIC - `game_performance` - Game-level analytics
# MAGIC - `revenue_summary` - Revenue by date/currency/jurisdiction
# MAGIC - `player_lifetime_value` - LTV calculations
# MAGIC - `risk_indicators` - Risk scoring features
# MAGIC
# MAGIC ## Features:
# MAGIC - Incremental aggregation with merge
# MAGIC - Materialized views pattern
# MAGIC - Pre-computed ML features
# MAGIC - Regulatory reporting tables

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration

# COMMAND ----------

from pyspark.sql import SparkSession, DataFrame, Window  # ty:ignore[unresolved-import]
from pyspark.sql import functions as F  # ty:ignore[unresolved-import]
from pyspark.sql.types import DecimalType, IntegerType  # ty:ignore[unresolved-import]
from delta.tables import DeltaTable  # ty:ignore[unresolved-import]
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import json

# Widget parameters
dbutils.widgets.text("source_catalog", "igaming_catalog")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("source_database", "silver")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("target_database", "gold")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("target_bucket", "s3://igaming-datalake-gold")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("processing_date", "")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("lookback_days", "1")  # ty:ignore[unresolved-reference]

# Get parameters
SOURCE_CATALOG = dbutils.widgets.get("source_catalog")  # ty:ignore[unresolved-reference]
SOURCE_DATABASE = dbutils.widgets.get("source_database")  # ty:ignore[unresolved-reference]
TARGET_DATABASE = dbutils.widgets.get("target_database")  # ty:ignore[unresolved-reference]
TARGET_BUCKET = dbutils.widgets.get("target_bucket")  # ty:ignore[unresolved-reference]
PROCESSING_DATE = dbutils.widgets.get("processing_date") or datetime.now().strftime("%Y-%m-%d")  # ty:ignore[unresolved-reference]
LOOKBACK_DAYS = int(dbutils.widgets.get("lookback_days"))  # ty:ignore[unresolved-reference]

FULL_SOURCE = f"{SOURCE_CATALOG}.{SOURCE_DATABASE}"
FULL_TARGET = f"{SOURCE_CATALOG}.{TARGET_DATABASE}"

print(f"Configuration:")
print(f"  Source: {FULL_SOURCE}")
print(f"  Target: {FULL_TARGET}")
print(f"  Processing Date: {PROCESSING_DATE}")
print(f"  Lookback Days: {LOOKBACK_DAYS}")

# Ensure target database exists
spark.sql(f"CREATE DATABASE IF NOT EXISTS {FULL_TARGET}")  # ty:ignore[unresolved-reference]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Aggregation Pipelines

# COMMAND ----------

class GoldAggregationPipeline:
    """
    Pipeline for creating Gold layer aggregations.

    Implements incremental materialized views pattern
    with Delta Lake for efficient updates.
    """

    def __init__(self, spark: SparkSession):
        self.spark = spark
        self.processing_date = datetime.strptime(PROCESSING_DATE, "%Y-%m-%d")
        self.start_date = self.processing_date - timedelta(days=LOOKBACK_DAYS)

    def _get_date_filter(self) -> str:
        """Get partition filter for incremental processing."""
        return f"""
            year >= {self.start_date.year}
            AND month >= {self.start_date.month}
            AND day >= {self.start_date.day}
        """

    def _write_gold_table(
        self,
        df: DataFrame,
        table_name: str,
        merge_keys: List[str],
        partition_cols: List[str] = ["year", "month"]
    ) -> int:
        """Write aggregation to Gold layer using MERGE."""
        table_path = f"{TARGET_BUCKET}/{table_name}"
        full_table_name = f"{FULL_TARGET}.{table_name}"

        # Add audit columns
        df = df.withColumn("_aggregated_at", F.current_timestamp())

        table_exists = DeltaTable.isDeltaTable(self.spark, table_path)

        if not table_exists:
            print(f"Creating new Gold table: {full_table_name}")
            df.write.format("delta") \
                .mode("overwrite") \
                .partitionBy(*partition_cols) \
                .option("path", table_path) \
                .saveAsTable(full_table_name)
            return df.count()
        else:
            print(f"Merging into Gold table: {full_table_name}")
            delta_table = DeltaTable.forPath(self.spark, table_path)

            merge_condition = " AND ".join([
                f"target.{key} = source.{key}" for key in merge_keys
            ])

            delta_table.alias("target").merge(
                df.alias("source"),
                merge_condition
            ).whenMatchedUpdateAll(
            ).whenNotMatchedInsertAll(
            ).execute()

            return df.count()

    def aggregate_player_daily_metrics(self) -> int:
        """
        Create daily player metrics aggregation.

        Metrics:
        - Total bets, wins, deposits, withdrawals
        - Session count and duration
        - Favorite game
        - GGR (Gross Gaming Revenue)
        - Win rate
        """
        print("\n" + "=" * 60)
        print("Aggregating: Player Daily Metrics")
        print("=" * 60)

        # Game rounds aggregation
        game_stats = self.spark.sql(f"""
            SELECT
                player_id,
                DATE(started_at) as metric_date,
                YEAR(started_at) as year,
                MONTH(started_at) as month,
                COUNT(*) as total_rounds,
                SUM(bet_amount) as total_bets,
                SUM(win_amount) as total_wins,
                SUM(bet_amount) - SUM(win_amount) as ggr,
                AVG(rtp) as avg_rtp,
                COUNT(DISTINCT game_id) as unique_games,
                FIRST(game_id) as favorite_game,
                MIN(started_at) as first_round_time,
                MAX(ended_at) as last_round_time
            FROM {FULL_SOURCE}.game_rounds
            WHERE {self._get_date_filter()}
            GROUP BY player_id, DATE(started_at), YEAR(started_at), MONTH(started_at)
        """)

        # Transactions aggregation
        tx_stats = self.spark.sql(f"""
            SELECT
                player_id,
                DATE(created_at) as metric_date,
                SUM(CASE WHEN transaction_type = 'deposit' THEN amount ELSE 0 END) as deposits,
                SUM(CASE WHEN transaction_type = 'withdrawal' THEN amount ELSE 0 END) as withdrawals,
                COUNT(CASE WHEN transaction_type = 'deposit' THEN 1 END) as deposit_count,
                COUNT(CASE WHEN transaction_type = 'withdrawal' THEN 1 END) as withdrawal_count
            FROM {FULL_SOURCE}.transactions
            WHERE status = 'completed'
            AND {self._get_date_filter()}
            GROUP BY player_id, DATE(created_at)
        """)

        # Session stats from player events
        session_stats = self.spark.sql(f"""
            SELECT
                player_id,
                DATE(timestamp) as metric_date,
                COUNT(DISTINCT session_id) as session_count,
                COUNT(*) as event_count,
                MAX(timestamp) - MIN(timestamp) as session_duration
            FROM {FULL_SOURCE}.player_events
            WHERE {self._get_date_filter()}
            GROUP BY player_id, DATE(timestamp)
        """)

        # Join all metrics
        df = game_stats.alias("g").join(
            tx_stats.alias("t"),
            (F.col("g.player_id") == F.col("t.player_id")) &
            (F.col("g.metric_date") == F.col("t.metric_date")),
            "left"
        ).join(
            session_stats.alias("s"),
            (F.col("g.player_id") == F.col("s.player_id")) &
            (F.col("g.metric_date") == F.col("s.metric_date")),
            "left"
        ).select(
            F.col("g.player_id"),
            F.col("g.metric_date"),
            F.col("g.year"),
            F.col("g.month"),
            F.col("g.total_rounds"),
            F.col("g.total_bets"),
            F.col("g.total_wins"),
            F.col("g.ggr"),
            F.col("g.avg_rtp"),
            F.col("g.unique_games"),
            F.col("g.favorite_game"),
            F.coalesce(F.col("t.deposits"), F.lit(0)).alias("deposits"),
            F.coalesce(F.col("t.withdrawals"), F.lit(0)).alias("withdrawals"),
            F.coalesce(F.col("t.deposit_count"), F.lit(0)).alias("deposit_count"),
            F.coalesce(F.col("t.withdrawal_count"), F.lit(0)).alias("withdrawal_count"),
            F.coalesce(F.col("s.session_count"), F.lit(0)).alias("session_count"),
            F.coalesce(F.col("s.event_count"), F.lit(0)).alias("event_count"),
            # Derived metrics
            (F.col("g.total_wins") / F.col("g.total_bets")).alias("win_rate"),
            (F.col("g.total_bets") / F.col("g.total_rounds")).alias("avg_bet_size")
        )

        records = self._write_gold_table(
            df,
            "player_daily_metrics",
            ["player_id", "metric_date"]
        )

        print(f"  Aggregated {records} player-day combinations")
        return records

    def aggregate_game_performance(self) -> int:
        """
        Create game performance aggregation.

        Metrics per game:
        - Total rounds, bets, wins
        - RTP (actual vs theoretical)
        - Unique players
        - Popularity rank
        - Revenue contribution
        """
        print("\n" + "=" * 60)
        print("Aggregating: Game Performance")
        print("=" * 60)

        df = self.spark.sql(f"""
            WITH daily_stats AS (
                SELECT
                    game_id,
                    game_type,
                    DATE(started_at) as metric_date,
                    YEAR(started_at) as year,
                    MONTH(started_at) as month,
                    COUNT(*) as rounds,
                    SUM(bet_amount) as bets,
                    SUM(win_amount) as wins,
                    SUM(bet_amount) - SUM(win_amount) as ggr,
                    COUNT(DISTINCT player_id) as unique_players,
                    AVG(bet_amount) as avg_bet,
                    PERCENTILE(bet_amount, 0.5) as median_bet,
                    MAX(win_amount) as max_win
                FROM {FULL_SOURCE}.game_rounds
                WHERE {self._get_date_filter()}
                GROUP BY game_id, game_type, DATE(started_at), YEAR(started_at), MONTH(started_at)
            )
            SELECT
                game_id,
                game_type,
                metric_date,
                year,
                month,
                rounds,
                bets,
                wins,
                ggr,
                unique_players,
                avg_bet,
                median_bet,
                max_win,
                CASE WHEN bets > 0 THEN wins / bets ELSE 0 END as actual_rtp,
                RANK() OVER (PARTITION BY metric_date ORDER BY rounds DESC) as popularity_rank,
                RANK() OVER (PARTITION BY metric_date ORDER BY ggr DESC) as revenue_rank
            FROM daily_stats
        """)

        records = self._write_gold_table(
            df,
            "game_performance",
            ["game_id", "metric_date"]
        )

        print(f"  Aggregated {records} game-day combinations")
        return records

    def aggregate_revenue_summary(self) -> int:
        """
        Create revenue summary for reporting.

        Aggregated by:
        - Date
        - Currency
        - Game type
        - Transaction type
        """
        print("\n" + "=" * 60)
        print("Aggregating: Revenue Summary")
        print("=" * 60)

        # Gaming revenue from game rounds
        gaming_revenue = self.spark.sql(f"""
            SELECT
                DATE(started_at) as report_date,
                YEAR(started_at) as year,
                MONTH(started_at) as month,
                currency,
                game_type,
                'gaming' as revenue_type,
                SUM(bet_amount) as total_bets,
                SUM(win_amount) as total_payouts,
                SUM(bet_amount) - SUM(win_amount) as ggr,
                COUNT(*) as transaction_count,
                COUNT(DISTINCT player_id) as unique_players
            FROM {FULL_SOURCE}.game_rounds
            WHERE {self._get_date_filter()}
            GROUP BY DATE(started_at), YEAR(started_at), MONTH(started_at), currency, game_type
        """)

        # Deposit/withdrawal revenue
        payment_revenue = self.spark.sql(f"""
            SELECT
                DATE(created_at) as report_date,
                YEAR(created_at) as year,
                MONTH(created_at) as month,
                currency,
                'payment' as game_type,
                transaction_type as revenue_type,
                SUM(amount) as total_bets,
                0 as total_payouts,
                SUM(amount) as ggr,
                COUNT(*) as transaction_count,
                COUNT(DISTINCT player_id) as unique_players
            FROM {FULL_SOURCE}.transactions
            WHERE status = 'completed'
            AND {self._get_date_filter()}
            GROUP BY DATE(created_at), YEAR(created_at), MONTH(created_at), currency, transaction_type
        """)

        df = gaming_revenue.union(payment_revenue)

        records = self._write_gold_table(
            df,
            "revenue_summary",
            ["report_date", "currency", "game_type", "revenue_type"]
        )

        print(f"  Aggregated {records} revenue records")
        return records

    def aggregate_player_lifetime_value(self) -> int:
        """
        Calculate Player Lifetime Value (LTV).

        Features:
        - Total historical GGR
        - Days since registration
        - Average daily value
        - Predicted future value (simple model)
        - Player segment
        """
        print("\n" + "=" * 60)
        print("Aggregating: Player Lifetime Value")
        print("=" * 60)

        df = self.spark.sql(f"""
            WITH player_history AS (
                SELECT
                    player_id,
                    MIN(started_at) as first_activity,
                    MAX(started_at) as last_activity,
                    DATEDIFF(MAX(started_at), MIN(started_at)) + 1 as active_days,
                    SUM(bet_amount) as lifetime_bets,
                    SUM(win_amount) as lifetime_wins,
                    SUM(bet_amount) - SUM(win_amount) as lifetime_ggr,
                    COUNT(*) as lifetime_rounds,
                    COUNT(DISTINCT game_id) as games_played,
                    AVG(bet_amount) as avg_bet_size
                FROM {FULL_SOURCE}.game_rounds
                GROUP BY player_id
            ),
            player_deposits AS (
                SELECT
                    player_id,
                    SUM(CASE WHEN transaction_type = 'deposit' THEN amount ELSE 0 END) as lifetime_deposits,
                    SUM(CASE WHEN transaction_type = 'withdrawal' THEN amount ELSE 0 END) as lifetime_withdrawals
                FROM {FULL_SOURCE}.transactions
                WHERE status = 'completed'
                GROUP BY player_id
            )
            SELECT
                h.player_id,
                DATE('{PROCESSING_DATE}') as calculated_date,
                YEAR('{PROCESSING_DATE}') as year,
                MONTH('{PROCESSING_DATE}') as month,
                h.first_activity,
                h.last_activity,
                h.active_days,
                DATEDIFF('{PROCESSING_DATE}', h.last_activity) as days_since_last_activity,
                h.lifetime_bets,
                h.lifetime_wins,
                h.lifetime_ggr,
                h.lifetime_rounds,
                h.games_played,
                h.avg_bet_size,
                COALESCE(d.lifetime_deposits, 0) as lifetime_deposits,
                COALESCE(d.lifetime_withdrawals, 0) as lifetime_withdrawals,
                -- Derived metrics
                h.lifetime_ggr / h.active_days as daily_value,
                h.lifetime_ggr / h.lifetime_rounds as ggr_per_round,
                -- Simple LTV prediction (30-day projected value)
                (h.lifetime_ggr / h.active_days) * 30 as projected_30d_value,
                -- Player segmentation
                CASE
                    WHEN h.lifetime_ggr > 10000 THEN 'VIP'
                    WHEN h.lifetime_ggr > 1000 THEN 'HIGH'
                    WHEN h.lifetime_ggr > 100 THEN 'MEDIUM'
                    ELSE 'LOW'
                END as player_segment,
                -- Churn risk (simple rule)
                CASE
                    WHEN DATEDIFF('{PROCESSING_DATE}', h.last_activity) > 30 THEN 'HIGH'
                    WHEN DATEDIFF('{PROCESSING_DATE}', h.last_activity) > 14 THEN 'MEDIUM'
                    ELSE 'LOW'
                END as churn_risk
            FROM player_history h
            LEFT JOIN player_deposits d ON h.player_id = d.player_id
        """)

        records = self._write_gold_table(
            df,
            "player_lifetime_value",
            ["player_id", "calculated_date"]
        )

        print(f"  Aggregated {records} player LTV records")
        return records

    def aggregate_risk_indicators(self) -> int:
        """
        Calculate risk scoring features for ML models.

        Features:
        - Betting velocity
        - Loss chasing patterns
        - Session length anomalies
        - Deposit frequency
        - Self-exclusion risk score
        """
        print("\n" + "=" * 60)
        print("Aggregating: Risk Indicators")
        print("=" * 60)

        df = self.spark.sql(f"""
            WITH player_patterns AS (
                SELECT
                    player_id,
                    DATE(started_at) as activity_date,
                    YEAR(started_at) as year,
                    MONTH(started_at) as month,
                    -- Betting velocity
                    COUNT(*) as rounds_per_day,
                    SUM(bet_amount) as daily_wagered,
                    -- Loss patterns
                    SUM(CASE WHEN bet_amount > win_amount THEN 1 ELSE 0 END) as losing_rounds,
                    SUM(CASE WHEN bet_amount > win_amount THEN bet_amount - win_amount ELSE 0 END) as daily_losses,
                    -- Bet escalation (loss chasing indicator)
                    MAX(bet_amount) / NULLIF(AVG(bet_amount), 0) as max_bet_ratio,
                    -- Session patterns
                    COUNT(DISTINCT HOUR(started_at)) as active_hours,
                    -- Late night gambling (risk indicator)
                    SUM(CASE WHEN HOUR(started_at) BETWEEN 0 AND 5 THEN 1 ELSE 0 END) as late_night_rounds
                FROM {FULL_SOURCE}.game_rounds
                WHERE {self._get_date_filter()}
                GROUP BY player_id, DATE(started_at), YEAR(started_at), MONTH(started_at)
            ),
            deposit_patterns AS (
                SELECT
                    player_id,
                    DATE(created_at) as activity_date,
                    COUNT(*) as deposit_count,
                    SUM(amount) as deposit_amount,
                    MAX(amount) as max_deposit
                FROM {FULL_SOURCE}.transactions
                WHERE transaction_type = 'deposit'
                AND status = 'completed'
                AND {self._get_date_filter()}
                GROUP BY player_id, DATE(created_at)
            )
            SELECT
                p.player_id,
                p.activity_date,
                p.year,
                p.month,
                p.rounds_per_day,
                p.daily_wagered,
                p.losing_rounds,
                p.daily_losses,
                p.max_bet_ratio,
                p.active_hours,
                p.late_night_rounds,
                COALESCE(d.deposit_count, 0) as deposit_count,
                COALESCE(d.deposit_amount, 0) as deposit_amount,
                -- Risk score calculation (weighted factors)
                (
                    -- High velocity (weight: 20%)
                    CASE WHEN p.rounds_per_day > 100 THEN 20 ELSE p.rounds_per_day / 5 END +
                    -- High losses (weight: 30%)
                    CASE WHEN p.daily_losses > 1000 THEN 30 ELSE p.daily_losses / 33.33 END +
                    -- Loss chasing (weight: 25%)
                    CASE WHEN p.max_bet_ratio > 5 THEN 25 ELSE p.max_bet_ratio * 5 END +
                    -- Late night gambling (weight: 15%)
                    CASE WHEN p.late_night_rounds > 10 THEN 15 ELSE p.late_night_rounds * 1.5 END +
                    -- Frequent deposits (weight: 10%)
                    CASE WHEN COALESCE(d.deposit_count, 0) > 3 THEN 10 ELSE COALESCE(d.deposit_count, 0) * 3.33 END
                ) as risk_score,
                -- Risk level classification
                CASE
                    WHEN (
                        CASE WHEN p.rounds_per_day > 100 THEN 20 ELSE p.rounds_per_day / 5 END +
                        CASE WHEN p.daily_losses > 1000 THEN 30 ELSE p.daily_losses / 33.33 END +
                        CASE WHEN p.max_bet_ratio > 5 THEN 25 ELSE p.max_bet_ratio * 5 END +
                        CASE WHEN p.late_night_rounds > 10 THEN 15 ELSE p.late_night_rounds * 1.5 END +
                        CASE WHEN COALESCE(d.deposit_count, 0) > 3 THEN 10 ELSE COALESCE(d.deposit_count, 0) * 3.33 END
                    ) > 70 THEN 'CRITICAL'
                    WHEN (
                        CASE WHEN p.rounds_per_day > 100 THEN 20 ELSE p.rounds_per_day / 5 END +
                        CASE WHEN p.daily_losses > 1000 THEN 30 ELSE p.daily_losses / 33.33 END +
                        CASE WHEN p.max_bet_ratio > 5 THEN 25 ELSE p.max_bet_ratio * 5 END +
                        CASE WHEN p.late_night_rounds > 10 THEN 15 ELSE p.late_night_rounds * 1.5 END +
                        CASE WHEN COALESCE(d.deposit_count, 0) > 3 THEN 10 ELSE COALESCE(d.deposit_count, 0) * 3.33 END
                    ) > 50 THEN 'HIGH'
                    WHEN (
                        CASE WHEN p.rounds_per_day > 100 THEN 20 ELSE p.rounds_per_day / 5 END +
                        CASE WHEN p.daily_losses > 1000 THEN 30 ELSE p.daily_losses / 33.33 END +
                        CASE WHEN p.max_bet_ratio > 5 THEN 25 ELSE p.max_bet_ratio * 5 END +
                        CASE WHEN p.late_night_rounds > 10 THEN 15 ELSE p.late_night_rounds * 1.5 END +
                        CASE WHEN COALESCE(d.deposit_count, 0) > 3 THEN 10 ELSE COALESCE(d.deposit_count, 0) * 3.33 END
                    ) > 30 THEN 'MEDIUM'
                    ELSE 'LOW'
                END as risk_level
            FROM player_patterns p
            LEFT JOIN deposit_patterns d
                ON p.player_id = d.player_id
                AND p.activity_date = d.activity_date
        """)

        records = self._write_gold_table(
            df,
            "risk_indicators",
            ["player_id", "activity_date"]
        )

        print(f"  Aggregated {records} risk indicator records")
        return records

# COMMAND ----------

# MAGIC %md
# MAGIC ## Main Execution

# COMMAND ----------

# Initialize pipeline
pipeline = GoldAggregationPipeline(spark)  # ty:ignore[unresolved-reference]

# Process all aggregations
results = {}

aggregations = [
    ("player_daily_metrics", pipeline.aggregate_player_daily_metrics),
    ("game_performance", pipeline.aggregate_game_performance),
    ("revenue_summary", pipeline.aggregate_revenue_summary),
    ("player_lifetime_value", pipeline.aggregate_player_lifetime_value),
    ("risk_indicators", pipeline.aggregate_risk_indicators),
]

for name, func in aggregations:
    try:
        results[name] = func()
    except Exception as e:
        print(f"Error in {name}: {str(e)}")
        results[name] = -1

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

print("\n" + "=" * 60)
print("Gold Aggregation Summary")
print("=" * 60)
print(f"Processing Date: {PROCESSING_DATE}")
print(f"Lookback Days: {LOOKBACK_DAYS}")
print("-" * 60)

total_records = 0
for table, count in results.items():
    status = "SUCCESS" if count >= 0 else "FAILED"
    print(f"  {table}: {count:,} records [{status}]")
    if count > 0:
        total_records += count

print("-" * 60)
print(f"  TOTAL: {total_records:,} records aggregated")
print("=" * 60)

# Return results
dbutils.notebook.exit(json.dumps(results))  # ty:ignore[unresolved-reference]
