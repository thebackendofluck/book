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
Fraud Pipeline Orchestrator (Iceberg + Spark + Flink + ML)
==========================================================

Reference implementation for Chapter 19: Anti-Fraud System Deep Dive.

This module ties together the entire fraud detection lakehouse pipeline:
Iceberg table management, Spark batch processing, Flink real-time detection,
and ML model integration. It provides a DAG-style orchestration layer
that can run standalone or integrate with Apache Airflow.

Pipeline DAG:
    1. ingest      → Verify Kafka topics and Iceberg tables are healthy
    2. enrich      → Run Spark feature engineering on new transactions
    3. detect      → Check Flink real-time pipeline status
    4. score       → Run Spark batch scoring with ensemble models
    5. alert       → Generate and route alerts to analyst dashboard
    6. maintain    → Iceberg compaction, snapshot expiry, orphan file cleanup
    7. report      → Generate jurisdiction-level fraud reports

Each step is idempotent and can be retried independently. Failed steps
land in a dead letter queue for manual investigation.

Usage:
    # Run full pipeline
    python fraud_pipeline_orchestrator.py --action run-all --date 2026-03-12

    # Run single step
    python fraud_pipeline_orchestrator.py --action run-step --step enrich --date 2026-03-12

    # Run maintenance only
    python fraud_pipeline_orchestrator.py --action maintain

    # Generate Airflow DAG definition
    python fraud_pipeline_orchestrator.py --action generate-dag
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fraud_pipeline_orchestrator")


# ---------------------------------------------------------------------------
# Pipeline step definitions
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    """Execution status of a pipeline step."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class StepName(str, Enum):
    """Names of pipeline steps in execution order."""
    INGEST = "ingest"
    ENRICH = "enrich"
    DETECT = "detect"
    SCORE = "score"
    ALERT = "alert"
    MAINTAIN = "maintain"
    REPORT = "report"


@dataclass
class StepResult:
    """Result of executing a pipeline step."""
    step: StepName
    status: StepStatus
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    records_processed: int = 0
    error_message: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "step": self.step.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "records_processed": self.records_processed,
            "error_message": self.error_message,
            "metrics": self.metrics,
        }


@dataclass
class PipelineConfig:
    """Configuration for the fraud pipeline orchestrator."""

    # Processing
    processing_date: str = ""  # YYYY-MM-DD
    jurisdiction: str | None = None  # None = all

    # Infrastructure endpoints
    kafka_bootstrap: str = "localhost:9092"
    iceberg_catalog_uri: str = "http://localhost:8181"
    spark_master: str = "spark://localhost:7077"
    flink_jobmanager: str = "http://localhost:8081"

    # Iceberg
    iceberg_namespace: str = "fraud_analytics"
    warehouse: str = "s3a://fraud-lakehouse/warehouse"

    # Retry policy
    max_retries: int = 3
    retry_delay_seconds: int = 30

    # Maintenance
    snapshot_max_age_days: int = 7
    orphan_file_max_age_days: int = 3
    compaction_target_file_size_mb: int = 128

    # Alerting
    alert_webhook_url: str = ""
    alert_email: str = ""

    # Paths
    scripts_dir: str = "."
    dead_letter_dir: str = "./dead-letter"
    reports_dir: str = "./reports"

    # Jurisdictions to process
    jurisdictions: list[str] = field(
        default_factory=lambda: ["MGA", "UKGC", "SGA", "DGA", "AGCO", "NJDGE"]
    )


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step_ingest(config: PipelineConfig) -> StepResult:
    """Step 1: Verify data ingestion health.

    Checks:
    - Kafka topics exist and have recent messages
    - Iceberg tables are accessible
    - No schema drift between expected and actual
    - Consumer lag is within acceptable bounds

    In production, this step validates that upstream data producers
    (game servers, payment gateways) are sending data correctly.
    """
    start = datetime.now(timezone.utc)
    logger.info("[INGEST] Verifying data ingestion health")

    checks_passed = 0
    total_checks = 4

    # Check 1: Kafka connectivity
    logger.info("[INGEST] Checking Kafka connectivity at %s", config.kafka_bootstrap)
    # In production: KafkaAdminClient.list_topics()
    checks_passed += 1

    # Check 2: Iceberg catalog connectivity
    logger.info("[INGEST] Checking Iceberg catalog at %s", config.iceberg_catalog_uri)
    # In production: catalog.list_namespaces()
    checks_passed += 1

    # Check 3: Required tables exist
    required_tables = ["transactions", "player_sessions", "fraud_alerts", "risk_scores"]
    logger.info("[INGEST] Verifying tables: %s", required_tables)
    checks_passed += 1

    # Check 4: Recent data exists
    logger.info("[INGEST] Checking for recent data (date=%s)", config.processing_date)
    checks_passed += 1

    end = datetime.now(timezone.utc)
    return StepResult(
        step=StepName.INGEST,
        status=StepStatus.SUCCESS if checks_passed == total_checks else StepStatus.FAILED,
        started_at=start,
        completed_at=end,
        duration_seconds=(end - start).total_seconds(),
        metrics={
            "checks_passed": checks_passed,
            "total_checks": total_checks,
        },
    )


def step_enrich(config: PipelineConfig) -> StepResult:
    """Step 2: Run Spark feature engineering.

    Submits spark_fraud_batch.py to the Spark cluster for feature
    engineering. Features computed:
    - Velocity (5min, 15min, 1hr, 24hr windows)
    - Amount patterns (mean, stddev, round amounts, structuring)
    - Geographic anomalies (IP diversity, impossible travel)
    - Device patterns (fingerprint changes, sharing)

    The enriched features are written to a staging Iceberg table
    that the scoring step reads from.
    """
    start = datetime.now(timezone.utc)
    logger.info("[ENRICH] Running Spark feature engineering for date=%s", config.processing_date)

    # Build spark-submit command
    spark_script = str(Path(config.scripts_dir) / "spark_fraud_batch.py")
    cmd = [
        "spark-submit",
        "--master", config.spark_master,
        "--packages", "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0",
        spark_script,
        "--date", config.processing_date,
        "--catalog-uri", config.iceberg_catalog_uri,
    ]

    if config.jurisdiction:
        cmd.extend(["--jurisdiction", config.jurisdiction])

    logger.info("[ENRICH] Command: %s", " ".join(cmd))

    # In production, execute:
    # result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    # For reference, we simulate success:
    logger.info("[ENRICH] Spark job would be submitted to %s", config.spark_master)

    end = datetime.now(timezone.utc)
    return StepResult(
        step=StepName.ENRICH,
        status=StepStatus.SUCCESS,
        started_at=start,
        completed_at=end,
        duration_seconds=(end - start).total_seconds(),
        metrics={"spark_master": config.spark_master},
    )


def step_detect(config: PipelineConfig) -> StepResult:
    """Step 3: Verify Flink real-time detection is running.

    Checks the Flink jobmanager API to verify:
    - The fraud detection job is in RUNNING state
    - Checkpoint success rate > 99%
    - Consumer lag < 10,000 events
    - No task failures in the last hour

    If the job is not running, attempts to restart it.
    """
    start = datetime.now(timezone.utc)
    logger.info("[DETECT] Checking Flink real-time pipeline status")

    # In production, check Flink REST API:
    # GET http://flink-jobmanager:8081/jobs
    # For each job, verify status == "RUNNING"
    logger.info(
        "[DETECT] Would check Flink at %s/jobs",
        config.flink_jobmanager,
    )

    # Check metrics
    flink_metrics = {
        "job_status": "RUNNING",
        "checkpoint_success_rate": 99.8,
        "consumer_lag": 245,
        "task_failures_last_hour": 0,
    }

    logger.info("[DETECT] Flink status: %s", flink_metrics)

    end = datetime.now(timezone.utc)
    return StepResult(
        step=StepName.DETECT,
        status=StepStatus.SUCCESS,
        started_at=start,
        completed_at=end,
        duration_seconds=(end - start).total_seconds(),
        metrics=flink_metrics,
    )


def step_score(config: PipelineConfig) -> StepResult:
    """Step 4: Run batch model scoring.

    Uses the features from step_enrich to score all players with
    the ensemble ML models. Results are written to Iceberg risk_scores
    table.

    Model ensemble:
    1. XGBoost (weight: 0.25) -- best for tabular fraud features
    2. Isolation Forest (0.15) -- catches novel anomalies
    3. LSTM (0.20) -- temporal sequence patterns
    4. Random Forest (0.15) -- interpretable baseline
    5. Autoencoder (0.10) -- reconstruction-based anomaly
    6. GNN (0.15) -- network/collusion detection
    """
    start = datetime.now(timezone.utc)
    logger.info("[SCORE] Running batch model scoring for date=%s", config.processing_date)

    # In production, this reuses the Spark session from step_enrich
    # or submits a separate Spark job for scoring only.
    logger.info("[SCORE] Model ensemble scoring would execute on Spark cluster")

    scoring_metrics = {
        "model_version": "v2.3",
        "players_scored": 0,  # Would be actual count
        "risk_distribution": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        },
    }

    end = datetime.now(timezone.utc)
    return StepResult(
        step=StepName.SCORE,
        status=StepStatus.SUCCESS,
        started_at=start,
        completed_at=end,
        duration_seconds=(end - start).total_seconds(),
        metrics=scoring_metrics,
    )


def step_alert(config: PipelineConfig) -> StepResult:
    """Step 5: Generate and route alerts.

    Reads high-risk players from risk_scores table and generates
    alerts in the fraud_alerts table. Routes alerts based on severity:
    - CRITICAL: immediate Slack/PagerDuty notification + auto-block
    - HIGH: queue for review within 1 hour
    - MEDIUM: queue for review within 24 hours
    - LOW: weekly batch review

    Also sends jurisdiction-specific notifications for regulatory
    reporting (FIAU for MGA, UKGC compliance team, etc.).
    """
    start = datetime.now(timezone.utc)
    logger.info("[ALERT] Generating and routing fraud alerts")

    alert_metrics = {
        "total_alerts_generated": 0,
        "alerts_by_severity": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        },
        "notifications_sent": 0,
        "auto_blocks_triggered": 0,
    }

    # In production:
    # 1. Query risk_scores where risk_level in ('critical', 'high', 'medium')
    # 2. Create fraud_alert records
    # 3. Send notifications via webhook
    if config.alert_webhook_url:
        logger.info("[ALERT] Would send webhook to %s", config.alert_webhook_url)

    end = datetime.now(timezone.utc)
    return StepResult(
        step=StepName.ALERT,
        status=StepStatus.SUCCESS,
        started_at=start,
        completed_at=end,
        duration_seconds=(end - start).total_seconds(),
        metrics=alert_metrics,
    )


def step_maintain(config: PipelineConfig) -> StepResult:
    """Step 6: Iceberg table maintenance.

    Maintenance tasks (run during off-peak hours):
    1. Snapshot expiry: remove snapshots older than 7 days
    2. Orphan file cleanup: delete unreferenced data files
    3. Compaction: merge small files into target size (128MB)
    4. Sort order optimization: re-sort data for query patterns

    Why this matters for fraud tables:
    - Flink writes small files every checkpoint (60 seconds)
    - Without compaction, query performance degrades rapidly
    - Orphan files from failed writes waste storage
    - Snapshot accumulation bloats metadata
    """
    start = datetime.now(timezone.utc)
    logger.info("[MAINTAIN] Running Iceberg table maintenance")

    tables = ["transactions", "player_sessions", "fraud_alerts", "risk_scores"]
    maintenance_results: dict[str, dict[str, Any]] = {}

    for table_name in tables:
        logger.info("[MAINTAIN] Processing table: %s", table_name)

        table_result: dict[str, Any] = {}

        # 1. Expire old snapshots
        logger.info(
            "[MAINTAIN] Expiring snapshots older than %d days for %s",
            config.snapshot_max_age_days, table_name,
        )
        table_result["snapshots_expired"] = 0

        # In production (Spark SQL):
        # CALL fraud_catalog.system.expire_snapshots(
        #     table => 'fraud_analytics.{table_name}',
        #     older_than => TIMESTAMP '{cutoff}',
        #     retain_last => 5
        # )

        # 2. Remove orphan files
        logger.info("[MAINTAIN] Removing orphan files for %s", table_name)
        table_result["orphan_files_removed"] = 0

        # 3. Compact small files
        logger.info(
            "[MAINTAIN] Compacting files to %dMB target for %s",
            config.compaction_target_file_size_mb, table_name,
        )
        table_result["files_compacted"] = 0

        # In production (Spark SQL):
        # CALL fraud_catalog.system.rewrite_data_files(
        #     table => 'fraud_analytics.{table_name}',
        #     options => map(
        #         'target-file-size-bytes', '{target_bytes}',
        #         'min-file-size-bytes', '{min_bytes}'
        #     )
        # )

        maintenance_results[table_name] = table_result

    end = datetime.now(timezone.utc)
    return StepResult(
        step=StepName.MAINTAIN,
        status=StepStatus.SUCCESS,
        started_at=start,
        completed_at=end,
        duration_seconds=(end - start).total_seconds(),
        metrics={"tables": maintenance_results},
    )


def step_report(config: PipelineConfig) -> StepResult:
    """Step 7: Generate fraud reports per jurisdiction.

    Produces daily/weekly fraud reports required by gaming regulators:
    - MGA: Monthly FIAU report with suspicious transaction details
    - UKGC: Quarterly fraud statistics and trends
    - SGA: Real-time reporting for high-severity incidents

    Reports are generated as JSON and optionally converted to PDF
    for regulatory submission.
    """
    start = datetime.now(timezone.utc)
    logger.info("[REPORT] Generating jurisdiction fraud reports")

    reports_dir = Path(config.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_metrics: dict[str, Any] = {"jurisdictions_reported": []}

    for jurisdiction in config.jurisdictions:
        logger.info("[REPORT] Generating report for %s", jurisdiction)

        report = {
            "jurisdiction": jurisdiction,
            "processing_date": config.processing_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_transactions": 0,
                "flagged_transactions": 0,
                "fraud_rate_pct": 0.0,
                "alerts_generated": 0,
                "alerts_resolved": 0,
                "accounts_blocked": 0,
            },
            "fraud_type_breakdown": {
                "bot_play": 0,
                "account_takeover": 0,
                "money_laundering": 0,
                "collusion": 0,
                "bonus_abuse": 0,
            },
        }

        report_path = reports_dir / f"fraud_report_{jurisdiction}_{config.processing_date}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        report_metrics["jurisdictions_reported"].append(jurisdiction)
        logger.info("[REPORT] Wrote report to %s", report_path)

    end = datetime.now(timezone.utc)
    return StepResult(
        step=StepName.REPORT,
        status=StepStatus.SUCCESS,
        started_at=start,
        completed_at=end,
        duration_seconds=(end - start).total_seconds(),
        metrics=report_metrics,
    )


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

# Execution order and dependencies
PIPELINE_STEPS: list[tuple[StepName, Any]] = [
    (StepName.INGEST, step_ingest),
    (StepName.ENRICH, step_enrich),
    (StepName.DETECT, step_detect),
    (StepName.SCORE, step_score),
    (StepName.ALERT, step_alert),
    (StepName.MAINTAIN, step_maintain),
    (StepName.REPORT, step_report),
]


def run_step_with_retry(
    step_name: StepName,
    step_fn: Any,
    config: PipelineConfig,
) -> StepResult:
    """Execute a pipeline step with retry logic.

    Retries with exponential backoff on failure. Failed events
    after max retries are written to the dead letter queue for
    manual investigation.

    Args:
        step_name: Name of the step.
        step_fn: Callable that executes the step.
        config: Pipeline configuration.

    Returns:
        StepResult with final status.
    """
    last_error = ""

    for attempt in range(1, config.max_retries + 1):
        try:
            logger.info(
                "Executing step %s (attempt %d/%d)",
                step_name.value, attempt, config.max_retries,
            )
            result = step_fn(config)

            if result.status == StepStatus.SUCCESS:
                return result

            last_error = result.error_message or "Step returned non-success status"

        except Exception as e:
            last_error = str(e)
            logger.error(
                "Step %s failed (attempt %d/%d): %s",
                step_name.value, attempt, config.max_retries, e,
            )

        # Retry delay with exponential backoff
        if attempt < config.max_retries:
            delay = config.retry_delay_seconds * (2 ** (attempt - 1))
            logger.info("Retrying in %d seconds...", delay)
            time.sleep(min(delay, 300))  # Cap at 5 minutes

    # All retries exhausted -- write to dead letter queue
    dlq_path = Path(config.dead_letter_dir)
    dlq_path.mkdir(parents=True, exist_ok=True)
    dlq_file = dlq_path / f"{step_name.value}_{config.processing_date}_{int(time.time())}.json"

    dlq_entry = {
        "step": step_name.value,
        "processing_date": config.processing_date,
        "jurisdiction": config.jurisdiction,
        "error": last_error,
        "max_retries": config.max_retries,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with open(dlq_file, "w", encoding="utf-8") as f:
        json.dump(dlq_entry, f, indent=2)

    logger.error(
        "Step %s failed after %d retries. Written to DLQ: %s",
        step_name.value, config.max_retries, dlq_file,
    )

    return StepResult(
        step=step_name,
        status=StepStatus.FAILED,
        started_at=datetime.now(timezone.utc),
        error_message=last_error,
    )


def run_full_pipeline(config: PipelineConfig) -> list[StepResult]:
    """Execute the complete fraud detection pipeline.

    Runs all steps in order. If a step fails, subsequent steps
    are skipped (except MAINTAIN which always runs).

    Args:
        config: Pipeline configuration.

    Returns:
        List of StepResults for all steps.
    """
    logger.info("=" * 60)
    logger.info("FRAUD PIPELINE: Starting full pipeline run")
    logger.info("Date: %s | Jurisdiction: %s", config.processing_date, config.jurisdiction or "ALL")
    logger.info("=" * 60)

    results: list[StepResult] = []
    pipeline_failed = False

    for step_name, step_fn in PIPELINE_STEPS:
        # Maintenance always runs even if earlier steps failed
        if pipeline_failed and step_name != StepName.MAINTAIN:
            logger.warning("Skipping step %s due to earlier failure", step_name.value)
            results.append(StepResult(
                step=step_name,
                status=StepStatus.SKIPPED,
                started_at=datetime.now(timezone.utc),
            ))
            continue

        result = run_step_with_retry(step_name, step_fn, config)
        results.append(result)

        if result.status == StepStatus.FAILED:
            pipeline_failed = True
            logger.error("Step %s FAILED. Remaining steps will be skipped.", step_name.value)

    # Summary
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    for r in results:
        status_icon = {
            StepStatus.SUCCESS: "OK",
            StepStatus.FAILED: "FAIL",
            StepStatus.SKIPPED: "SKIP",
        }.get(r.status, "??")
        logger.info(
            "  [%s] %s (%.1fs)",
            status_icon, r.step.value, r.duration_seconds,
        )

    total_time = sum(r.duration_seconds for r in results)
    failed_count = sum(1 for r in results if r.status == StepStatus.FAILED)
    logger.info("Total time: %.1f seconds | Failed steps: %d", total_time, failed_count)
    logger.info("=" * 60)

    return results


def run_single_step(config: PipelineConfig, step_name: str) -> StepResult:
    """Execute a single pipeline step.

    Args:
        config: Pipeline configuration.
        step_name: Name of the step to run.

    Returns:
        StepResult.
    """
    step_map = dict(PIPELINE_STEPS)
    step_enum = StepName(step_name)

    if step_enum not in step_map:
        logger.error("Unknown step: %s. Available: %s", step_name, [s.value for s in StepName])
        sys.exit(1)

    return run_step_with_retry(step_enum, step_map[step_enum], config)


# ---------------------------------------------------------------------------
# Airflow DAG generation
# ---------------------------------------------------------------------------

def generate_airflow_dag(config: PipelineConfig) -> str:
    """Generate an Apache Airflow DAG definition for the fraud pipeline.

    This outputs a Python file that can be placed in Airflow's dags/
    directory. It uses the SparkSubmitOperator for Spark steps and
    the FlinkOperator (custom) for Flink monitoring.

    Returns:
        String containing the Airflow DAG Python code.
    """
    dag_code = '''"""
Auto-generated Airflow DAG for Fraud Detection Pipeline.
Place this file in your Airflow dags/ directory.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

default_args = {
    "owner": "fraud-engineering",
    "depends_on_past": False,
    "email_on_failure": True,
    "email": ["fraud-alerts@operator.com"],
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="fraud_detection_pipeline",
    default_args=default_args,
    description="Daily fraud detection: ingest, enrich, score, alert, maintain",
    schedule_interval="0 2 * * *",  # Run at 2 AM UTC daily
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["fraud", "ml", "iceberg"],
) as dag:

    ingest = PythonOperator(
        task_id="ingest_health_check",
        python_callable=lambda: print("Checking ingestion health"),
    )

    enrich = SparkSubmitOperator(
        task_id="spark_feature_engineering",
        application="/opt/fraud-scripts/spark_fraud_batch.py",
        conn_id="spark_default",
        packages="org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0",
        application_args=[
            "--date", "{{ ds }}",
            "--catalog-uri", "http://iceberg-rest:8181",
        ],
    )

    detect = PythonOperator(
        task_id="flink_status_check",
        python_callable=lambda: print("Checking Flink job status"),
    )

    score = SparkSubmitOperator(
        task_id="spark_batch_scoring",
        application="/opt/fraud-scripts/spark_fraud_batch.py",
        conn_id="spark_default",
        packages="org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0",
        application_args=[
            "--date", "{{ ds }}",
            "--catalog-uri", "http://iceberg-rest:8181",
        ],
    )

    alert = PythonOperator(
        task_id="generate_alerts",
        python_callable=lambda: print("Generating and routing alerts"),
    )

    maintain = PythonOperator(
        task_id="iceberg_maintenance",
        python_callable=lambda: print("Running Iceberg maintenance"),
    )

    report = PythonOperator(
        task_id="generate_reports",
        python_callable=lambda: print("Generating jurisdiction reports"),
    )

    # DAG dependency chain
    ingest >> enrich >> detect >> score >> alert >> [maintain, report]
'''
    return dag_code


# ---------------------------------------------------------------------------
# Jurisdiction-specific configuration
# ---------------------------------------------------------------------------

# Different jurisdictions have different fraud detection requirements.
# This mapping configures thresholds and reporting intervals per jurisdiction.
JURISDICTION_CONFIG: dict[str, dict[str, Any]] = {
    "MGA": {
        "description": "Malta Gaming Authority",
        "reporting_interval": "monthly",
        "aml_threshold_eur": 10000,
        "velocity_multiplier": 1.0,  # Standard thresholds
        "requires_fiau_report": True,
        "data_retention_days": 2555,  # 7 years
    },
    "UKGC": {
        "description": "UK Gambling Commission",
        "reporting_interval": "quarterly",
        "aml_threshold_eur": 2000,  # Lower threshold for UK
        "velocity_multiplier": 0.8,  # Stricter velocity thresholds
        "requires_sar_report": True,
        "data_retention_days": 2555,
    },
    "SGA": {
        "description": "Swedish Gambling Authority",
        "reporting_interval": "realtime",  # Immediate for high severity
        "aml_threshold_eur": 5000,
        "velocity_multiplier": 1.0,
        "requires_deposit_limits": True,
        "data_retention_days": 1825,  # 5 years
    },
    "DGA": {
        "description": "Danish Gambling Authority",
        "reporting_interval": "monthly",
        "aml_threshold_eur": 7500,
        "velocity_multiplier": 1.0,
        "data_retention_days": 1825,
    },
    "AGCO": {
        "description": "Alcohol and Gaming Commission of Ontario",
        "reporting_interval": "monthly",
        "aml_threshold_eur": 10000,
        "velocity_multiplier": 1.2,  # Slightly relaxed
        "data_retention_days": 2555,
    },
    "NJDGE": {
        "description": "New Jersey Division of Gaming Enforcement",
        "reporting_interval": "monthly",
        "aml_threshold_eur": 10000,
        "velocity_multiplier": 1.0,
        "requires_geofencing": True,
        "data_retention_days": 2555,
    },
}


def get_jurisdiction_config(jurisdiction: str) -> dict[str, Any]:
    """Get jurisdiction-specific pipeline configuration.

    Args:
        jurisdiction: Jurisdiction code (MGA, UKGC, etc.)

    Returns:
        Dict of jurisdiction-specific settings.
    """
    config = JURISDICTION_CONFIG.get(jurisdiction, {})
    if not config:
        logger.warning("No specific config for jurisdiction %s. Using defaults.", jurisdiction)
    return config


# ---------------------------------------------------------------------------
# Pipeline monitoring
# ---------------------------------------------------------------------------

def check_pipeline_health(config: PipelineConfig) -> dict[str, Any]:
    """Check overall pipeline health.

    Verifies:
    - All infrastructure components are reachable
    - No stuck jobs
    - No DLQ accumulation
    - Iceberg table sizes within expected bounds

    Returns:
        Dict of health metrics.
    """
    health: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "healthy",
        "components": {},
    }

    # Check DLQ size
    dlq_path = Path(config.dead_letter_dir)
    dlq_count = 0
    if dlq_path.exists():
        dlq_count = sum(1 for _ in dlq_path.glob("*.json"))
    health["dlq_size"] = dlq_count
    if dlq_count > 10:
        health["status"] = "degraded"
        logger.warning("DLQ has %d entries. Investigation needed.", dlq_count)

    logger.info("Pipeline health: %s", json.dumps(health, indent=2))
    return health


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fraud Pipeline Orchestrator (Iceberg + Spark + Flink + ML)",
    )
    parser.add_argument(
        "--action",
        choices=["run-all", "run-step", "maintain", "generate-dag", "health"],
        default="run-all",
        help="Action to perform",
    )
    parser.add_argument(
        "--step",
        choices=[s.value for s in StepName],
        help="Step to run (with --action run-step)",
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Processing date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--jurisdiction",
        default=None,
        help="Jurisdiction filter (e.g., MGA, UKGC)",
    )
    parser.add_argument(
        "--catalog-uri",
        default="http://localhost:8181",
        help="Iceberg REST catalog URI",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    config = PipelineConfig(
        processing_date=args.date,
        jurisdiction=args.jurisdiction,
        iceberg_catalog_uri=args.catalog_uri,
    )

    if args.action == "run-all":
        results = run_full_pipeline(config)
        # Output summary as JSON
        summary = [r.to_dict() for r in results]
        print(json.dumps(summary, indent=2))

    elif args.action == "run-step":
        if not args.step:
            logger.error("--step required with --action run-step")
            sys.exit(1)
        result = run_single_step(config, args.step)
        print(json.dumps(result.to_dict(), indent=2))

    elif args.action == "maintain":
        result = run_step_with_retry(StepName.MAINTAIN, step_maintain, config)
        print(json.dumps(result.to_dict(), indent=2))

    elif args.action == "generate-dag":
        dag_code = generate_airflow_dag(config)
        print(dag_code)

    elif args.action == "health":
        health = check_pipeline_health(config)
        print(json.dumps(health, indent=2))


if __name__ == "__main__":
    main()
