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
AWS Glue ETL Job: Bronze to Silver Layer Transformation

This job processes raw data from the Bronze layer, applies data quality
checks, transformations, and writes cleaned data to the Silver layer.

Transformations Applied:
1. Schema validation and type casting
2. Data quality checks (nulls, duplicates, ranges)
3. PII masking for compliance
4. Timestamp normalization (UTC)
5. Currency standardization
6. Partitioning by date

Input: s3://bronze-bucket/events/
Output: s3://silver-bucket/events_cleaned/

Usage:
    AWS Glue Console or via Terraform deployment

Environment Variables:
    - BRONZE_BUCKET: Source bucket name
    - SILVER_BUCKET: Target bucket name
    - GLUE_DATABASE: Glue catalog database name

Dependencies:
    - AWS Glue 4.0 (Spark 3.3, Python 3.10)
    - pyspark
    - awsglue
"""

import sys
from datetime import datetime, timezone
from typing import Any

from awsglue.context import GlueContext  # ty:ignore[unresolved-import]
from awsglue.dynamicframe import DynamicFrame  # ty:ignore[unresolved-import]
from awsglue.job import Job  # ty:ignore[unresolved-import]
from awsglue.transforms import *  # ty:ignore[unresolved-import]
from awsglue.utils import getResolvedOptions  # ty:ignore[unresolved-import]
from pyspark.context import SparkContext  # ty:ignore[unresolved-import]
from pyspark.sql import DataFrame, SparkSession  # ty:ignore[unresolved-import]
from pyspark.sql import functions as F  # ty:ignore[unresolved-import]
from pyspark.sql.types import (  # ty:ignore[unresolved-import]
    DecimalType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Get job parameters
args = getResolvedOptions(
    sys.argv,
    [
        "JOB_NAME",
        "BRONZE_BUCKET",
        "SILVER_BUCKET",
        "GLUE_DATABASE",
        "PROCESSING_DATE",
    ],
)

# Initialize Glue context
sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

# Configuration
BRONZE_BUCKET = args["BRONZE_BUCKET"]
SILVER_BUCKET = args["SILVER_BUCKET"]
GLUE_DATABASE = args["GLUE_DATABASE"]
PROCESSING_DATE = args.get("PROCESSING_DATE", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

# PII fields to mask
PII_FIELDS = ["email", "phone", "ip_address", "card_number", "ssn"]

# Data quality thresholds
QUALITY_THRESHOLDS = {
    "max_null_percentage": 5.0,
    "max_duplicate_percentage": 1.0,
    "min_records": 100,
}


# =============================================================================
# SCHEMA DEFINITIONS
# =============================================================================

EVENTS_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), False),
        StructField("event_type", StringType(), False),
        StructField("entity_type", StringType(), False),
        StructField("entity_id", StringType(), False),
        StructField("timestamp", TimestampType(), False),
        StructField("properties", StringType(), True),
        StructField("metadata", StringType(), True),
        StructField("ingested_at", TimestampType(), True),
    ]
)

TRANSACTIONS_SCHEMA = StructType(
    [
        StructField("transaction_id", StringType(), False),
        StructField("player_id", StringType(), False),
        StructField("transaction_type", StringType(), False),
        StructField("amount", DecimalType(18, 8), False),
        StructField("currency", StringType(), False),
        StructField("timestamp", TimestampType(), False),
        StructField("status", StringType(), False),
        StructField("metadata", StringType(), True),
    ]
)


# =============================================================================
# DATA QUALITY FUNCTIONS
# =============================================================================


def check_data_quality(df: DataFrame, table_name: str) -> dict[str, Any]:
    """
    Run data quality checks on a DataFrame.

    Args:
        df: Input DataFrame
        table_name: Table name for logging

    Returns:
        Dictionary with quality metrics
    """
    total_records = df.count()

    if total_records < QUALITY_THRESHOLDS["min_records"]:
        print(f"WARNING: {table_name} has only {total_records} records")

    # Check for nulls in key columns
    null_counts = {}
    for col in df.columns:
        null_count = df.filter(F.col(col).isNull()).count()
        null_pct = (null_count / total_records * 100) if total_records > 0 else 0
        null_counts[col] = {"count": null_count, "percentage": null_pct}

    # Check for duplicates on primary key
    pk_column = "event_id" if "event_id" in df.columns else "transaction_id"
    duplicate_count = total_records - df.select(pk_column).distinct().count()
    duplicate_pct = (duplicate_count / total_records * 100) if total_records > 0 else 0

    quality_report = {
        "table_name": table_name,
        "total_records": total_records,
        "null_counts": null_counts,
        "duplicate_count": duplicate_count,
        "duplicate_percentage": duplicate_pct,
        "quality_passed": (
            duplicate_pct <= QUALITY_THRESHOLDS["max_duplicate_percentage"]
            and total_records >= QUALITY_THRESHOLDS["min_records"]
        ),
    }

    print(f"Data Quality Report for {table_name}:")
    print(f"  Total Records: {total_records:,}")
    print(f"  Duplicates: {duplicate_count:,} ({duplicate_pct:.2f}%)")
    print(f"  Quality Passed: {quality_report['quality_passed']}")

    return quality_report


def remove_duplicates(df: DataFrame, pk_column: str) -> DataFrame:
    """
    Remove duplicate records, keeping the most recent.

    Args:
        df: Input DataFrame
        pk_column: Primary key column name

    Returns:
        DataFrame with duplicates removed
    """
    # Add row number partitioned by PK, ordered by timestamp desc
    window_spec = (
        F.row_number()
        .over(
            F.Window.partitionBy(pk_column).orderBy(F.col("timestamp").desc())
        )
    )

    df_with_rn = df.withColumn("row_num", window_spec)
    df_deduped = df_with_rn.filter(F.col("row_num") == 1).drop("row_num")

    return df_deduped


# =============================================================================
# TRANSFORMATION FUNCTIONS
# =============================================================================


def mask_pii_fields(df: DataFrame) -> DataFrame:
    """
    Mask PII fields for compliance.

    Args:
        df: Input DataFrame

    Returns:
        DataFrame with PII fields masked
    """
    for field in PII_FIELDS:
        if field in df.columns:
            if field == "email":
                # Mask email: john.doe@example.com -> j***@e***.com
                df = df.withColumn(
                    field,
                    F.concat(
                        F.substring(F.col(field), 1, 1),
                        F.lit("***@"),
                        F.substring(
                            F.split(F.col(field), "@").getItem(1), 1, 1
                        ),
                        F.lit("***."),
                        F.element_at(
                            F.split(F.split(F.col(field), "@").getItem(1), "\\."),
                            -1,
                        ),
                    ),
                )
            elif field == "phone":
                # Mask phone: keep last 4 digits
                df = df.withColumn(
                    field,
                    F.concat(F.lit("***-***-"), F.substring(F.col(field), -4, 4)),
                )
            elif field == "ip_address":
                # Mask IP: 192.168.1.100 -> 192.168.x.x
                df = df.withColumn(
                    field,
                    F.concat(
                        F.concat_ws(
                            ".",
                            F.split(F.col(field), "\\.").getItem(0),
                            F.split(F.col(field), "\\.").getItem(1),
                        ),
                        F.lit(".x.x"),
                    ),
                )
            else:
                # Generic masking for other PII fields
                df = df.withColumn(field, F.lit("***MASKED***"))

    return df


def normalize_timestamps(df: DataFrame) -> DataFrame:
    """
    Normalize all timestamps to UTC.

    Args:
        df: Input DataFrame

    Returns:
        DataFrame with normalized timestamps
    """
    timestamp_columns = [
        col for col in df.columns if "timestamp" in col.lower() or "date" in col.lower()
    ]

    for col in timestamp_columns:
        df = df.withColumn(col, F.to_utc_timestamp(F.col(col), "UTC"))

    return df


def standardize_currency(df: DataFrame) -> DataFrame:
    """
    Standardize currency codes to ISO 4217.

    Args:
        df: Input DataFrame

    Returns:
        DataFrame with standardized currency codes
    """
    if "currency" not in df.columns:
        return df

    # Currency mapping for common variations
    currency_mapping = {
        "USD": "USD",
        "US": "USD",
        "DOLLAR": "USD",
        "EUR": "EUR",
        "EURO": "EUR",
        "GBP": "GBP",
        "POUND": "GBP",
        "BTC": "BTC",
        "BITCOIN": "BTC",
        "ETH": "ETH",
    }

    # Create mapping expression
    mapping_expr = F.create_map(
        [F.lit(x) for pair in currency_mapping.items() for x in pair]
    )

    df = df.withColumn(
        "currency",
        F.coalesce(mapping_expr[F.upper(F.col("currency"))], F.col("currency")),
    )

    return df


def add_derived_columns(df: DataFrame) -> DataFrame:
    """
    Add derived columns for analytics.

    Args:
        df: Input DataFrame

    Returns:
        DataFrame with derived columns
    """
    # Add date partitioning columns
    df = df.withColumn("year", F.year(F.col("timestamp")))
    df = df.withColumn("month", F.month(F.col("timestamp")))
    df = df.withColumn("day", F.dayofmonth(F.col("timestamp")))
    df = df.withColumn("hour", F.hour(F.col("timestamp")))

    # Add day of week
    df = df.withColumn("day_of_week", F.dayofweek(F.col("timestamp")))

    # Add processing metadata
    df = df.withColumn("processed_at", F.current_timestamp())
    df = df.withColumn("processing_date", F.lit(PROCESSING_DATE))

    return df


def validate_ranges(df: DataFrame) -> DataFrame:
    """
    Validate and filter records with out-of-range values.

    Args:
        df: Input DataFrame

    Returns:
        DataFrame with valid records only
    """
    # Filter out future timestamps
    df = df.filter(F.col("timestamp") <= F.current_timestamp())

    # Filter out negative amounts (if amount column exists)
    if "amount" in df.columns:
        df = df.filter(F.col("amount") >= 0)

    return df


# =============================================================================
# MAIN ETL PIPELINE
# =============================================================================


def process_events() -> None:
    """Process events from Bronze to Silver layer."""
    print(f"Processing events for date: {PROCESSING_DATE}")

    # Read from Bronze layer
    bronze_path = f"s3://{BRONZE_BUCKET}/events/year={PROCESSING_DATE[:4]}/month={PROCESSING_DATE[5:7]}/day={PROCESSING_DATE[8:10]}/"

    try:
        df_bronze = spark.read.parquet(bronze_path)
        print(f"Read {df_bronze.count():,} records from Bronze layer")
    except Exception as e:
        print(f"Error reading Bronze data: {e}")
        return

    # Data quality check
    quality_report = check_data_quality(df_bronze, "events_bronze")

    # Apply transformations
    df_transformed = df_bronze

    # 1. Remove duplicates
    df_transformed = remove_duplicates(df_transformed, "event_id")

    # 2. Normalize timestamps
    df_transformed = normalize_timestamps(df_transformed)

    # 3. Mask PII fields
    df_transformed = mask_pii_fields(df_transformed)

    # 4. Validate ranges
    df_transformed = validate_ranges(df_transformed)

    # 5. Add derived columns
    df_transformed = add_derived_columns(df_transformed)

    # Write to Silver layer
    silver_path = f"s3://{SILVER_BUCKET}/events_cleaned/"

    df_transformed.write.mode("append").partitionBy(
        "year", "month", "day"
    ).parquet(silver_path)

    print(f"Wrote {df_transformed.count():,} records to Silver layer")

    # Update Glue catalog
    glue_context.write_dynamic_frame.from_catalog(
        frame=DynamicFrame.fromDF(df_transformed, glue_context, "events_silver"),
        database=GLUE_DATABASE,
        table_name="events_silver",
        transformation_ctx="events_silver_output",
    )


def process_transactions() -> None:
    """Process transactions from Bronze to Silver layer."""
    print(f"Processing transactions for date: {PROCESSING_DATE}")

    # Read from Bronze layer
    bronze_path = f"s3://{BRONZE_BUCKET}/transactions/year={PROCESSING_DATE[:4]}/month={PROCESSING_DATE[5:7]}/day={PROCESSING_DATE[8:10]}/"

    try:
        df_bronze = spark.read.parquet(bronze_path)
        print(f"Read {df_bronze.count():,} records from Bronze layer")
    except Exception as e:
        print(f"Error reading Bronze transactions: {e}")
        return

    # Data quality check
    quality_report = check_data_quality(df_bronze, "transactions_bronze")

    # Apply transformations
    df_transformed = df_bronze

    # 1. Remove duplicates
    df_transformed = remove_duplicates(df_transformed, "transaction_id")

    # 2. Normalize timestamps
    df_transformed = normalize_timestamps(df_transformed)

    # 3. Standardize currency
    df_transformed = standardize_currency(df_transformed)

    # 4. Validate ranges
    df_transformed = validate_ranges(df_transformed)

    # 5. Add derived columns
    df_transformed = add_derived_columns(df_transformed)

    # Write to Silver layer
    silver_path = f"s3://{SILVER_BUCKET}/transactions_cleaned/"

    df_transformed.write.mode("append").partitionBy(
        "year", "month", "day"
    ).parquet(silver_path)

    print(f"Wrote {df_transformed.count():,} records to Silver layer")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Bronze to Silver ETL Job Started")
    print(f"Processing Date: {PROCESSING_DATE}")
    print(f"Bronze Bucket: {BRONZE_BUCKET}")
    print(f"Silver Bucket: {SILVER_BUCKET}")
    print("=" * 60)

    try:
        process_events()
        process_transactions()

        print("=" * 60)
        print("Bronze to Silver ETL Job Completed Successfully")
        print("=" * 60)

    except Exception as e:
        print(f"ETL Job Failed: {e}")
        raise

    finally:
        job.commit()
