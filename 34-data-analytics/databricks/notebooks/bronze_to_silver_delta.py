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
# MAGIC # Bronze to Silver ETL - Delta Lake
# MAGIC
# MAGIC Transforms raw data from Bronze layer to cleaned, validated Silver layer using Delta Lake.
# MAGIC
# MAGIC ## Features:
# MAGIC - Schema enforcement and evolution
# MAGIC - ACID transactions with Delta Lake
# MAGIC - Change Data Capture (CDC) with MERGE
# MAGIC - Data quality checks with expectations
# MAGIC - PII masking for GDPR compliance
# MAGIC - Automatic optimization (Z-ORDER, OPTIMIZE)
# MAGIC
# MAGIC ## Schedule:
# MAGIC - Runs every hour via Databricks Workflow
# MAGIC - Processes incremental data using watermarks

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration

# COMMAND ----------

from pyspark.sql import SparkSession, DataFrame  # ty:ignore[unresolved-import]
from pyspark.sql import functions as F  # ty:ignore[unresolved-import]
from pyspark.sql.types import (  # ty:ignore[unresolved-import]
    StructType, StructField, StringType, TimestampType,
    DecimalType, IntegerType, BooleanType, MapType
)
from delta.tables import DeltaTable  # ty:ignore[unresolved-import]
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import hashlib
import json

# Widget parameters (set via Databricks Workflows)
dbutils.widgets.text("source_bucket", "s3://igaming-datalake-bronze")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("target_bucket", "s3://igaming-datalake-silver")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("catalog", "igaming_catalog")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("database", "silver")  # ty:ignore[unresolved-reference]
dbutils.widgets.text("processing_date", "")  # ty:ignore[unresolved-reference]
dbutils.widgets.dropdown("run_mode", "incremental", ["incremental", "full"])  # ty:ignore[unresolved-reference]

# Get widget values
SOURCE_BUCKET = dbutils.widgets.get("source_bucket")  # ty:ignore[unresolved-reference]
TARGET_BUCKET = dbutils.widgets.get("target_bucket")  # ty:ignore[unresolved-reference]
CATALOG = dbutils.widgets.get("catalog")  # ty:ignore[unresolved-reference]
DATABASE = dbutils.widgets.get("database")  # ty:ignore[unresolved-reference]
PROCESSING_DATE = dbutils.widgets.get("processing_date") or datetime.now().strftime("%Y-%m-%d")  # ty:ignore[unresolved-reference]
RUN_MODE = dbutils.widgets.get("run_mode")  # ty:ignore[unresolved-reference]

print(f"Configuration:")
print(f"  Source: {SOURCE_BUCKET}")
print(f"  Target: {TARGET_BUCKET}")
print(f"  Catalog: {CATALOG}.{DATABASE}")
print(f"  Processing Date: {PROCESSING_DATE}")
print(f"  Run Mode: {RUN_MODE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Schema Definitions

# COMMAND ----------

# Player events schema
PLAYER_EVENTS_SCHEMA = StructType([
    StructField("event_id", StringType(), False),
    StructField("event_type", StringType(), False),
    StructField("player_id", StringType(), False),
    StructField("session_id", StringType(), True),
    StructField("timestamp", TimestampType(), False),
    StructField("ip_address", StringType(), True),
    StructField("device_type", StringType(), True),
    StructField("country_code", StringType(), True),
    StructField("properties", MapType(StringType(), StringType()), True),
])

# Transactions schema
TRANSACTIONS_SCHEMA = StructType([
    StructField("transaction_id", StringType(), False),
    StructField("player_id", StringType(), False),
    StructField("transaction_type", StringType(), False),
    StructField("amount", DecimalType(18, 2), False),
    StructField("currency", StringType(), False),
    StructField("status", StringType(), False),
    StructField("created_at", TimestampType(), False),
    StructField("completed_at", TimestampType(), True),
    StructField("payment_method", StringType(), True),
    StructField("metadata", MapType(StringType(), StringType()), True),
])

# Game rounds schema
GAME_ROUNDS_SCHEMA = StructType([
    StructField("round_id", StringType(), False),
    StructField("game_id", StringType(), False),
    StructField("player_id", StringType(), False),
    StructField("bet_amount", DecimalType(18, 2), False),
    StructField("win_amount", DecimalType(18, 2), False),
    StructField("currency", StringType(), False),
    StructField("started_at", TimestampType(), False),
    StructField("ended_at", TimestampType(), True),
    StructField("game_type", StringType(), True),
    StructField("jackpot_contribution", DecimalType(18, 2), True),
])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Utility Functions

# COMMAND ----------

def mask_pii(df: DataFrame, columns_config: Dict[str, str]) -> DataFrame:
    """
    Mask PII columns based on configuration.

    Args:
        df: Input DataFrame
        columns_config: Dict mapping column names to masking type
            - 'email': j***@e***.com
            - 'phone': ***-***-1234
            - 'ip': 192.168.***.***
            - 'hash': SHA256 hash

    Returns:
        DataFrame with masked PII columns
    """
    result_df = df

    for column, mask_type in columns_config.items():
        if column not in df.columns:
            continue

        if mask_type == 'email':
            result_df = result_df.withColumn(
                column,
                F.when(
                    F.col(column).isNotNull(),
                    F.concat(
                        F.substring(F.col(column), 1, 1),
                        F.lit("***@"),
                        F.substring(F.split(F.col(column), "@")[1], 1, 1),
                        F.lit("***.com")
                    )
                ).otherwise(F.col(column))
            )
        elif mask_type == 'phone':
            result_df = result_df.withColumn(
                column,
                F.when(
                    F.col(column).isNotNull(),
                    F.concat(
                        F.lit("***-***-"),
                        F.substring(F.col(column), -4, 4)
                    )
                ).otherwise(F.col(column))
            )
        elif mask_type == 'ip':
            result_df = result_df.withColumn(
                column,
                F.when(
                    F.col(column).isNotNull(),
                    F.concat(
                        F.split(F.col(column), "\\.")[0],
                        F.lit("."),
                        F.split(F.col(column), "\\.")[1],
                        F.lit(".***.***")
                    )
                ).otherwise(F.col(column))
            )
        elif mask_type == 'hash':
            result_df = result_df.withColumn(
                column,
                F.when(
                    F.col(column).isNotNull(),
                    F.sha2(F.col(column), 256)
                ).otherwise(F.col(column))
            )

    return result_df


def add_audit_columns(df: DataFrame) -> DataFrame:
    """Add standard audit columns for tracking."""
    return df.withColumn(
        "_ingested_at", F.current_timestamp()
    ).withColumn(
        "_source_file", F.input_file_name()
    ).withColumn(
        "_processing_date", F.lit(PROCESSING_DATE)
    )


def add_partitioning_columns(df: DataFrame, timestamp_col: str) -> DataFrame:
    """Add partitioning columns from timestamp."""
    return df.withColumn(
        "year", F.year(F.col(timestamp_col))
    ).withColumn(
        "month", F.month(F.col(timestamp_col))
    ).withColumn(
        "day", F.dayofmonth(F.col(timestamp_col))
    )


def validate_data_quality(
    df: DataFrame,
    table_name: str,
    checks: List[Dict[str, Any]]
) -> DataFrame:
    """
    Apply data quality checks and log violations.

    Args:
        df: Input DataFrame
        table_name: Name of the table for logging
        checks: List of quality check configurations
            - column: Column name to check
            - check_type: 'not_null', 'positive', 'range', 'regex', 'unique'
            - params: Additional parameters for the check

    Returns:
        DataFrame with quality check results
    """
    quality_results = []

    for check in checks:
        column = check['column']
        check_type = check['check_type']
        params = check.get('params', {})

        if check_type == 'not_null':
            null_count = df.filter(F.col(column).isNull()).count()
            quality_results.append({
                'table': table_name,
                'column': column,
                'check': 'not_null',
                'violations': null_count,
                'status': 'PASS' if null_count == 0 else 'WARN'
            })

        elif check_type == 'positive':
            negative_count = df.filter(F.col(column) < 0).count()
            quality_results.append({
                'table': table_name,
                'column': column,
                'check': 'positive',
                'violations': negative_count,
                'status': 'PASS' if negative_count == 0 else 'FAIL'
            })

        elif check_type == 'range':
            min_val, max_val = params.get('min', 0), params.get('max', float('inf'))
            out_of_range = df.filter(
                (F.col(column) < min_val) | (F.col(column) > max_val)
            ).count()
            quality_results.append({
                'table': table_name,
                'column': column,
                'check': f'range({min_val},{max_val})',
                'violations': out_of_range,
                'status': 'PASS' if out_of_range == 0 else 'FAIL'
            })

    # Log quality results
    for result in quality_results:
        print(f"  Quality Check: {result['table']}.{result['column']} "
              f"[{result['check']}] = {result['status']} "
              f"({result['violations']} violations)")

    return df

# COMMAND ----------

# MAGIC %md
# MAGIC ## ETL Pipeline Classes

# COMMAND ----------

class BronzeToSilverPipeline:
    """
    ETL pipeline for Bronze to Silver transformation using Delta Lake.

    Features:
    - Incremental processing with watermarks
    - MERGE for upserts (CDC)
    - Schema enforcement
    - PII masking
    - Data quality validation
    """

    def __init__(self, spark: SparkSession, catalog: str, database: str):
        self.spark = spark
        self.catalog = catalog
        self.database = database
        self.full_database = f"{catalog}.{database}"

        # Ensure database exists
        spark.sql(f"CREATE DATABASE IF NOT EXISTS {self.full_database}")

    def read_bronze(
        self,
        table_name: str,
        schema: StructType,
        partition_filter: Optional[str] = None
    ) -> DataFrame:
        """
        Read data from Bronze layer with optional partition filter.

        Args:
            table_name: Name of the bronze table
            schema: Expected schema
            partition_filter: Optional partition filter expression

        Returns:
            DataFrame with bronze data
        """
        path = f"{SOURCE_BUCKET}/{table_name}"

        if RUN_MODE == "incremental" and partition_filter:
            # Read only new partitions
            df = self.spark.read.schema(schema).parquet(path).filter(partition_filter)
        else:
            # Full read
            df = self.spark.read.schema(schema).parquet(path)

        print(f"Read {df.count()} records from {path}")
        return df

    def write_silver(
        self,
        df: DataFrame,
        table_name: str,
        merge_keys: List[str],
        partition_cols: List[str] = ["year", "month", "day"]
    ) -> int:
        """
        Write data to Silver layer using Delta Lake MERGE.

        Args:
            df: DataFrame to write
            table_name: Target table name
            merge_keys: Columns to use for MERGE condition
            partition_cols: Columns to partition by

        Returns:
            Number of records processed
        """
        table_path = f"{TARGET_BUCKET}/{table_name}"
        full_table_name = f"{self.full_database}.{table_name}"

        # Check if table exists
        table_exists = DeltaTable.isDeltaTable(self.spark, table_path)

        if not table_exists:
            # First write - create table
            print(f"Creating new Delta table: {full_table_name}")
            df.write.format("delta") \
                .mode("overwrite") \
                .partitionBy(*partition_cols) \
                .option("path", table_path) \
                .saveAsTable(full_table_name)

            records_written = df.count()
        else:
            # Incremental write - MERGE
            print(f"Merging into existing Delta table: {full_table_name}")
            delta_table = DeltaTable.forPath(self.spark, table_path)

            # Build merge condition
            merge_condition = " AND ".join([
                f"target.{key} = source.{key}" for key in merge_keys
            ])

            # Perform MERGE
            merge_result = delta_table.alias("target").merge(
                df.alias("source"),
                merge_condition
            ).whenMatchedUpdateAll(
            ).whenNotMatchedInsertAll(
            ).execute()

            # Get metrics
            history = delta_table.history(1).collect()[0]
            records_written = history.operationMetrics.get("numTargetRowsInserted", 0)
            records_updated = history.operationMetrics.get("numTargetRowsUpdated", 0)

            print(f"  Inserted: {records_written}, Updated: {records_updated}")
            records_written = int(records_written) + int(records_updated)

        return records_written

    def optimize_table(self, table_name: str, z_order_cols: Optional[List[str]] = None):
        """
        Optimize Delta table with compaction and optional Z-ORDER.

        Args:
            table_name: Table to optimize
            z_order_cols: Columns to Z-ORDER by for faster queries
        """
        full_table_name = f"{self.full_database}.{table_name}"

        # Run OPTIMIZE
        if z_order_cols:
            z_order_clause = ", ".join(z_order_cols)
            self.spark.sql(f"OPTIMIZE {full_table_name} ZORDER BY ({z_order_clause})")
            print(f"Optimized {full_table_name} with Z-ORDER on {z_order_cols}")
        else:
            self.spark.sql(f"OPTIMIZE {full_table_name}")
            print(f"Optimized {full_table_name}")

        # Vacuum old files (retain 7 days by default)
        self.spark.sql(f"VACUUM {full_table_name} RETAIN 168 HOURS")

    def process_player_events(self) -> int:
        """Process player events from Bronze to Silver."""
        print("\n" + "=" * 60)
        print("Processing: Player Events")
        print("=" * 60)

        # Read bronze data
        partition_filter = f"year = {PROCESSING_DATE[:4]} AND month = {int(PROCESSING_DATE[5:7])}"
        df = self.read_bronze("player_events", PLAYER_EVENTS_SCHEMA, partition_filter)

        # Data quality checks
        df = validate_data_quality(df, "player_events", [
            {'column': 'event_id', 'check_type': 'not_null'},
            {'column': 'player_id', 'check_type': 'not_null'},
            {'column': 'timestamp', 'check_type': 'not_null'},
        ])

        # Transform
        df = df.dropDuplicates(["event_id"])
        df = mask_pii(df, {"ip_address": "ip"})
        df = add_audit_columns(df)
        df = add_partitioning_columns(df, "timestamp")

        # Normalize timestamp to UTC
        df = df.withColumn("timestamp", F.to_utc_timestamp(F.col("timestamp"), "UTC"))

        # Write to Silver
        records = self.write_silver(df, "player_events", ["event_id"])

        # Optimize if significant data
        if records > 10000:
            self.optimize_table("player_events", ["player_id", "event_type"])

        return records

    def process_transactions(self) -> int:
        """Process transactions from Bronze to Silver."""
        print("\n" + "=" * 60)
        print("Processing: Transactions")
        print("=" * 60)

        # Read bronze data
        partition_filter = f"year = {PROCESSING_DATE[:4]} AND month = {int(PROCESSING_DATE[5:7])}"
        df = self.read_bronze("transactions", TRANSACTIONS_SCHEMA, partition_filter)

        # Data quality checks
        df = validate_data_quality(df, "transactions", [
            {'column': 'transaction_id', 'check_type': 'not_null'},
            {'column': 'player_id', 'check_type': 'not_null'},
            {'column': 'amount', 'check_type': 'positive'},
            {'column': 'amount', 'check_type': 'range', 'params': {'min': 0.01, 'max': 1000000}},
        ])

        # Filter invalid transactions
        df = df.filter(F.col("amount") > 0)
        df = df.filter(F.col("status").isin(["completed", "pending", "failed", "cancelled"]))

        # Transform
        df = df.dropDuplicates(["transaction_id"])
        df = add_audit_columns(df)
        df = add_partitioning_columns(df, "created_at")

        # Add derived columns
        df = df.withColumn(
            "processing_time_seconds",
            F.when(
                F.col("completed_at").isNotNull(),
                F.unix_timestamp("completed_at") - F.unix_timestamp("created_at")
            ).otherwise(F.lit(None))
        )

        # Write to Silver
        records = self.write_silver(df, "transactions", ["transaction_id"])

        # Optimize
        if records > 10000:
            self.optimize_table("transactions", ["player_id", "transaction_type"])

        return records

    def process_game_rounds(self) -> int:
        """Process game rounds from Bronze to Silver."""
        print("\n" + "=" * 60)
        print("Processing: Game Rounds")
        print("=" * 60)

        # Read bronze data
        partition_filter = f"year = {PROCESSING_DATE[:4]} AND month = {int(PROCESSING_DATE[5:7])}"
        df = self.read_bronze("game_rounds", GAME_ROUNDS_SCHEMA, partition_filter)

        # Data quality checks
        df = validate_data_quality(df, "game_rounds", [
            {'column': 'round_id', 'check_type': 'not_null'},
            {'column': 'game_id', 'check_type': 'not_null'},
            {'column': 'player_id', 'check_type': 'not_null'},
            {'column': 'bet_amount', 'check_type': 'positive'},
        ])

        # Filter invalid rounds
        df = df.filter(F.col("bet_amount") >= 0)
        df = df.filter(F.col("win_amount") >= 0)

        # Transform
        df = df.dropDuplicates(["round_id"])
        df = add_audit_columns(df)
        df = add_partitioning_columns(df, "started_at")

        # Add derived columns
        df = df.withColumn(
            "net_result",
            F.col("win_amount") - F.col("bet_amount")
        ).withColumn(
            "rtp",
            F.when(
                F.col("bet_amount") > 0,
                F.col("win_amount") / F.col("bet_amount")
            ).otherwise(F.lit(0))
        ).withColumn(
            "round_duration_seconds",
            F.when(
                F.col("ended_at").isNotNull(),
                F.unix_timestamp("ended_at") - F.unix_timestamp("started_at")
            ).otherwise(F.lit(None))
        )

        # Write to Silver
        records = self.write_silver(df, "game_rounds", ["round_id"])

        # Optimize
        if records > 10000:
            self.optimize_table("game_rounds", ["game_id", "player_id"])

        return records

# COMMAND ----------

# MAGIC %md
# MAGIC ## Main Execution

# COMMAND ----------

# Initialize pipeline
pipeline = BronzeToSilverPipeline(spark, CATALOG, DATABASE)  # ty:ignore[unresolved-reference]

# Process all tables
results = {
    "player_events": 0,
    "transactions": 0,
    "game_rounds": 0,
}

try:
    results["player_events"] = pipeline.process_player_events()
except Exception as e:
    print(f"Error processing player_events: {e}")
    results["player_events"] = -1

try:
    results["transactions"] = pipeline.process_transactions()
except Exception as e:
    print(f"Error processing transactions: {e}")
    results["transactions"] = -1

try:
    results["game_rounds"] = pipeline.process_game_rounds()
except Exception as e:
    print(f"Error processing game_rounds: {e}")
    results["game_rounds"] = -1

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

print("\n" + "=" * 60)
print("ETL Pipeline Summary")
print("=" * 60)
print(f"Processing Date: {PROCESSING_DATE}")
print(f"Run Mode: {RUN_MODE}")
print("-" * 60)
for table, count in results.items():
    status = "SUCCESS" if count >= 0 else "FAILED"
    print(f"  {table}: {count} records [{status}]")
print("=" * 60)

# Return results for workflow monitoring
dbutils.notebook.exit(json.dumps(results))  # ty:ignore[unresolved-reference]
