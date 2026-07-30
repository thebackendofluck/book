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
Dead Letter Queue (DLQ) Handler for Fraud Detection Pipeline
=============================================================

Handles events rejected during schema validation, enrichment, or model scoring.
Implements retry logic with exponential backoff and poison pill isolation.

DLQ Strategy:
  - Events failing schema validation -> fraud.dlq.schema-validation
  - Events failing enrichment       -> fraud.dlq.enrichment-failures
  - Events failing model scoring    -> fraud.dlq.scoring-failures
  - Events failing response exec    -> fraud.dlq.response-failures

Retry Policy:
  - Max retries: 3
  - Backoff: exponential (30s, 120s, 480s)
  - After max retries: move to poison pill topic for manual review
  - All DLQ events carry original event + error context for debugging

Why DLQ matters in fraud detection:
  - A dropped event could mean a fraudulent transaction goes unscored
  - Regulatory audits require proof that ALL transactions were evaluated
  - Pattern detection needs complete event streams (gaps create blind spots)
"""

import json
import logging
import time
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
from confluent_kafka import Consumer, Producer, KafkaError, KafkaException  # ty:ignore[unresolved-import]
from confluent_kafka.admin import AdminClient  # ty:ignore[unresolved-import]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fraud.dlq")


# =============================================================================
# Configuration
# =============================================================================

class DLQTopic(Enum):
    """DLQ topics mapped to failure stages in the fraud pipeline."""
    SCHEMA_VALIDATION = "fraud.dlq.schema-validation"
    ENRICHMENT = "fraud.dlq.enrichment-failures"
    SCORING = "fraud.dlq.scoring-failures"
    RESPONSE = "fraud.dlq.response-failures"
    POISON_PILL = "fraud.dlq.poison-pills"  # Events that exhausted retries


@dataclass
class DLQConfig:
    """Configuration for the DLQ handler."""
    bootstrap_servers: str = "localhost:9092"
    consumer_group: str = "fraud-dlq-handler"
    max_retries: int = 3
    base_backoff_seconds: float = 30.0
    backoff_multiplier: float = 4.0
    max_backoff_seconds: float = 600.0
    batch_size: int = 100
    poll_timeout_seconds: float = 1.0
    # Alerts when DLQ depth exceeds threshold (indicates systemic issues)
    alert_threshold_depth: int = 1000
    alert_threshold_rate_per_min: int = 50


@dataclass
class DLQEvent:
    """Wraps a failed event with error context and retry metadata."""
    original_topic: str
    original_partition: int
    original_offset: int
    original_key: Optional[str]
    original_value: str
    original_timestamp: int
    error_stage: str          # Which pipeline stage failed
    error_type: str           # Exception class name
    error_message: str        # Human-readable error description
    error_stacktrace: str     # Full stacktrace for debugging
    retry_count: int = 0
    first_failure_time: str = ""
    last_failure_time: str = ""
    dlq_event_id: str = ""
    next_retry_time: Optional[str] = None

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.first_failure_time:
            self.first_failure_time = now
        self.last_failure_time = now
        if not self.dlq_event_id:
            # Deterministic ID based on original event for deduplication
            raw = f"{self.original_topic}:{self.original_partition}:{self.original_offset}"
            self.dlq_event_id = hashlib.sha256(raw.encode()).hexdigest()[:16]


# =============================================================================
# DLQ Producer - Sends failed events to appropriate DLQ topic
# =============================================================================

class DLQProducer:
    """Sends failed events to the appropriate Dead Letter Queue topic."""

    def __init__(self, config: DLQConfig):
        self.config = config
        self.producer = Producer({
            "bootstrap.servers": config.bootstrap_servers,
            "acks": "all",  # Ensure DLQ events are durably written
            "retries": 5,
            "retry.backoff.ms": 1000,
            "enable.idempotence": True,  # Exactly-once for DLQ writes
            "compression.type": "gzip",
        })
        self._dlq_counts: dict[str, int] = {}
        logger.info("DLQ Producer initialized (bootstrap=%s)", config.bootstrap_servers)

    def send_to_dlq(
        self,
        dlq_topic: DLQTopic,
        original_topic: str,
        original_partition: int,
        original_offset: int,
        original_key: Optional[str],
        original_value: bytes,
        original_timestamp: int,
        error_stage: str,
        error: Exception,
        retry_count: int = 0,
    ) -> None:
        """
        Send a failed event to the appropriate DLQ topic.

        Args:
            dlq_topic: Target DLQ topic based on failure stage
            original_topic: Source topic where the event originated
            original_partition: Source partition
            original_offset: Source offset
            original_key: Original message key
            original_value: Original message value (bytes)
            original_timestamp: Original event timestamp
            error_stage: Pipeline stage where failure occurred
            error: The exception that caused the failure
            retry_count: How many times this event has been retried
        """
        import traceback

        dlq_event = DLQEvent(
            original_topic=original_topic,
            original_partition=original_partition,
            original_offset=original_offset,
            original_key=original_key,
            original_value=original_value.decode("utf-8", errors="replace"),
            original_timestamp=original_timestamp,
            error_stage=error_stage,
            error_type=type(error).__name__,
            error_message=str(error),
            error_stacktrace=traceback.format_exc(),
            retry_count=retry_count,
        )

        # Calculate next retry time if retries remain
        if retry_count < self.config.max_retries:
            backoff = min(
                self.config.base_backoff_seconds * (self.config.backoff_multiplier ** retry_count),
                self.config.max_backoff_seconds,
            )
            next_retry = datetime.now(timezone.utc).timestamp() + backoff
            dlq_event.next_retry_time = datetime.fromtimestamp(
                next_retry, tz=timezone.utc
            ).isoformat()

        # Serialize and send
        dlq_value = json.dumps(asdict(dlq_event), ensure_ascii=False).encode("utf-8")

        self.producer.produce(
            topic=dlq_topic.value,
            key=dlq_event.dlq_event_id.encode("utf-8"),
            value=dlq_value,
            callback=self._delivery_callback,
            headers={
                "error_stage": error_stage.encode("utf-8"),
                "error_type": type(error).__name__.encode("utf-8"),
                "retry_count": str(retry_count).encode("utf-8"),
                "original_topic": original_topic.encode("utf-8"),
            },
        )
        self.producer.flush(timeout=5)

        # Track DLQ counts for alerting
        topic_key = dlq_topic.value
        self._dlq_counts[topic_key] = self._dlq_counts.get(topic_key, 0) + 1

        logger.warning(
            "Event sent to DLQ: topic=%s stage=%s error=%s retry=%d/%d event_id=%s",
            dlq_topic.value,
            error_stage,
            type(error).__name__,
            retry_count,
            self.config.max_retries,
            dlq_event.dlq_event_id,
        )

    def _delivery_callback(self, err, msg):
        """Callback for DLQ message delivery confirmation."""
        if err:
            logger.error("DLQ delivery FAILED: %s (topic=%s)", err, msg.topic())
        else:
            logger.debug(
                "DLQ delivery confirmed: topic=%s partition=%d offset=%d",
                msg.topic(), msg.partition(), msg.offset(),
            )

    def get_counts(self) -> dict[str, int]:
        """Return DLQ event counts per topic (for monitoring)."""
        return dict(self._dlq_counts)


# =============================================================================
# DLQ Consumer - Processes DLQ events with retry logic
# =============================================================================

class DLQRetryProcessor:
    """
    Consumes events from DLQ topics and retries them with exponential backoff.

    Processing flow:
    1. Consume batch of DLQ events
    2. Check if next_retry_time has passed
    3. If ready: re-publish to original topic for reprocessing
    4. If max retries exceeded: move to poison pill topic
    5. If not ready: skip (will be consumed again later)
    """

    def __init__(self, config: DLQConfig):
        self.config = config
        self.consumer = Consumer({
            "bootstrap.servers": config.bootstrap_servers,
            "group.id": config.consumer_group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # Manual commit after successful processing
            "max.poll.interval.ms": 300000,
        })
        self.producer = Producer({
            "bootstrap.servers": config.bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
        })
        self.dlq_producer = DLQProducer(config)

        # Subscribe to all DLQ topics except poison pills
        dlq_topics = [t.value for t in DLQTopic if t != DLQTopic.POISON_PILL]
        self.consumer.subscribe(dlq_topics)
        logger.info("DLQ Retry Processor subscribed to: %s", dlq_topics)

        # Metrics
        self._retried = 0
        self._poisoned = 0
        self._skipped = 0

    def process_batch(self) -> dict[str, int]:
        """
        Process a batch of DLQ events.

        Returns dict with counts: retried, poisoned, skipped.
        """
        messages = self.consumer.consume(
            num_messages=self.config.batch_size,
            timeout=self.config.poll_timeout_seconds,
        )

        if not messages:
            return {"retried": 0, "poisoned": 0, "skipped": 0}

        batch_retried = 0
        batch_poisoned = 0
        batch_skipped = 0

        for msg in messages:
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("DLQ consumer error: %s", msg.error())
                continue

            try:
                dlq_event = json.loads(msg.value().decode("utf-8"))
                retry_count = dlq_event.get("retry_count", 0)
                next_retry_time = dlq_event.get("next_retry_time")

                # Check if max retries exceeded -> poison pill
                if retry_count >= self.config.max_retries:
                    self._move_to_poison_pill(dlq_event)
                    batch_poisoned += 1
                    continue

                # Check if it's time to retry
                if next_retry_time:
                    retry_dt = datetime.fromisoformat(next_retry_time)
                    if datetime.now(timezone.utc) < retry_dt:
                        # Not ready for retry yet, skip
                        batch_skipped += 1
                        continue

                # Re-publish to original topic with incremented retry count
                self._retry_event(dlq_event)
                batch_retried += 1

            except Exception as e:
                logger.error("Error processing DLQ event: %s", e, exc_info=True)

        # Commit offsets after processing batch
        self.consumer.commit(asynchronous=False)

        self._retried += batch_retried
        self._poisoned += batch_poisoned
        self._skipped += batch_skipped

        if batch_retried > 0 or batch_poisoned > 0:
            logger.info(
                "DLQ batch processed: retried=%d poisoned=%d skipped=%d (total: r=%d p=%d)",
                batch_retried, batch_poisoned, batch_skipped,
                self._retried, self._poisoned,
            )

        return {
            "retried": batch_retried,
            "poisoned": batch_poisoned,
            "skipped": batch_skipped,
        }

    def _retry_event(self, dlq_event: dict) -> None:
        """Re-publish event to its original topic for reprocessing."""
        original_value = dlq_event["original_value"].encode("utf-8")
        original_key = dlq_event.get("original_key")

        self.producer.produce(
            topic=dlq_event["original_topic"],
            key=original_key.encode("utf-8") if original_key else None,
            value=original_value,
            headers={
                "dlq_retry_count": str(dlq_event["retry_count"] + 1).encode("utf-8"),
                "dlq_event_id": dlq_event["dlq_event_id"].encode("utf-8"),
                "dlq_original_error": dlq_event["error_type"].encode("utf-8"),
            },
        )
        self.producer.flush(timeout=5)

        logger.info(
            "Retrying event: original_topic=%s retry=%d/%d event_id=%s",
            dlq_event["original_topic"],
            dlq_event["retry_count"] + 1,
            self.config.max_retries,
            dlq_event["dlq_event_id"],
        )

    def _move_to_poison_pill(self, dlq_event: dict) -> None:
        """Move event to poison pill topic after exhausting retries."""
        poison_value = json.dumps({
            **dlq_event,
            "poisoned_at": datetime.now(timezone.utc).isoformat(),
            "reason": f"Exhausted {self.config.max_retries} retries",
        }).encode("utf-8")

        self.producer.produce(
            topic=DLQTopic.POISON_PILL.value,
            key=dlq_event["dlq_event_id"].encode("utf-8"),
            value=poison_value,
            headers={
                "original_topic": dlq_event["original_topic"].encode("utf-8"),
                "error_type": dlq_event["error_type"].encode("utf-8"),
                "total_retries": str(dlq_event["retry_count"]).encode("utf-8"),
            },
        )
        self.producer.flush(timeout=5)

        logger.error(
            "POISON PILL: Event exhausted retries: topic=%s error=%s event_id=%s. "
            "Manual investigation required.",
            dlq_event["original_topic"],
            dlq_event["error_type"],
            dlq_event["dlq_event_id"],
        )

    def run(self) -> None:
        """Run the DLQ retry processor continuously."""
        logger.info("Starting DLQ Retry Processor...")
        try:
            while True:
                result = self.process_batch()
                # If nothing to process, back off to avoid busy-waiting
                if result["retried"] == 0 and result["poisoned"] == 0:
                    time.sleep(5)
        except KeyboardInterrupt:
            logger.info("DLQ Retry Processor shutting down...")
        finally:
            self.consumer.close()
            logger.info(
                "DLQ Retry Processor stopped. Total: retried=%d poisoned=%d skipped=%d",
                self._retried, self._poisoned, self._skipped,
            )


# =============================================================================
# DLQ Monitor - Tracks DLQ health metrics
# =============================================================================

class DLQMonitor:
    """
    Monitors DLQ topic depths and rates.
    Publishes metrics for Prometheus/Grafana integration.
    """

    def __init__(self, config: DLQConfig):
        self.config = config
        self.admin = AdminClient({"bootstrap.servers": config.bootstrap_servers})

    def get_topic_depths(self) -> dict[str, int]:
        """Get message count (depth) for each DLQ topic."""
        depths = {}
        consumer = Consumer({
            "bootstrap.servers": self.config.bootstrap_servers,
            "group.id": f"{self.config.consumer_group}-monitor",
            "auto.offset.reset": "earliest",
        })

        for dlq_topic in DLQTopic:
            try:
                # Get partition metadata
                metadata = self.admin.list_topics(topic=dlq_topic.value, timeout=10)
                if dlq_topic.value not in metadata.topics:
                    depths[dlq_topic.value] = 0
                    continue

                partitions = metadata.topics[dlq_topic.value].partitions
                total_depth = 0

                for pid in partitions:
                    from confluent_kafka import TopicPartition  # ty:ignore[unresolved-import]
                    tp = TopicPartition(dlq_topic.value, pid)
                    # Get high and low watermarks
                    low, high = consumer.get_watermark_offsets(tp, timeout=5)
                    total_depth += (high - low)

                depths[dlq_topic.value] = total_depth
            except Exception as e:
                logger.error("Error getting depth for %s: %s", dlq_topic.value, e)
                depths[dlq_topic.value] = -1

        consumer.close()
        return depths

    def check_alerts(self) -> list[dict]:
        """Check if DLQ depths exceed alert thresholds."""
        depths = self.get_topic_depths()
        alerts = []

        for topic, depth in depths.items():
            if depth > self.config.alert_threshold_depth:
                alerts.append({
                    "severity": "CRITICAL" if depth > self.config.alert_threshold_depth * 5 else "HIGH",
                    "topic": topic,
                    "depth": depth,
                    "threshold": self.config.alert_threshold_depth,
                    "message": (
                        f"DLQ topic {topic} has {depth} pending events "
                        f"(threshold: {self.config.alert_threshold_depth}). "
                        f"This indicates a systemic failure in the fraud pipeline. "
                        f"Investigate immediately - unscored transactions are a compliance risk."
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        return alerts


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Run the DLQ handler with retry processing and monitoring."""
    import argparse

    parser = argparse.ArgumentParser(description="Fraud Detection DLQ Handler")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--mode", choices=["retry", "monitor", "both"], default="both")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    config = DLQConfig(
        bootstrap_servers=args.bootstrap_servers,
        max_retries=args.max_retries,
    )

    if args.mode in ("monitor", "both"):
        monitor = DLQMonitor(config)
        depths = monitor.get_topic_depths()
        logger.info("DLQ Topic Depths: %s", json.dumps(depths, indent=2))

        alerts = monitor.check_alerts()
        for alert in alerts:
            logger.warning("DLQ ALERT: %s", json.dumps(alert))

    if args.mode in ("retry", "both"):
        processor = DLQRetryProcessor(config)
        processor.run()


if __name__ == "__main__":
    main()
