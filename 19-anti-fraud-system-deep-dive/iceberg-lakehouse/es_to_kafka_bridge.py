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
Elasticsearch → Kafka Bridge for Iceberg Lakehouse
===================================================

Reference implementation for Chapter 19: Anti-Fraud System Deep Dive.

Connects the existing Level-1 pipeline (Elasticsearch fraud events on ops-host)
to the Level-2 Iceberg lakehouse by forwarding indexed casino events into the
Kafka topic ``fraud.raw.events``.

This is the integration glue between the two pipeline levels:

    Level 1 (existing):
        Casino (203.0.113.1) → Fraud API (:8180) → Elasticsearch

    Level 2 (new):
        Elasticsearch → [this script] → Kafka (fraud.raw.events)
                                              ↓
                                    Flink (real-time scoring)
                                              ↓
                                    Iceberg (bronze/silver/gold)

Design:
- Runs as a periodic job (cron or loop with --interval)
- Tracks the last processed event timestamp in a local state file
- Uses Elasticsearch scroll for bulk reads
- Produces events to Kafka with exactly-once semantics (idempotent producer)
- Transforms ES document shape to the TransactionEvent schema expected by Flink

Usage:
    # Run once (for cron):
    python es_to_kafka_bridge.py --once

    # Run continuously every 60 seconds:
    python es_to_kafka_bridge.py --interval 60

    # Backfill from a specific date:
    python es_to_kafka_bridge.py --backfill-from 2026-03-01 --once

    # On ops-host (cron every 60s):
    * * * * * /usr/bin/python3 /opt/fraud-scripts/es_to_kafka_bridge.py --once \
        --es-url http://localhost:9200 \
        --kafka-bootstrap localhost:9092 \
        >> /var/log/fraud-bridge.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Optional dependency imports
# ---------------------------------------------------------------------------
try:
    from elasticsearch import Elasticsearch

    ES_AVAILABLE = True
except ImportError:
    ES_AVAILABLE = False

try:
    from kafka import KafkaProducer
    from kafka.errors import KafkaError

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("es_to_kafka_bridge")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class BridgeConfig:
    """Configuration for the Elasticsearch → Kafka bridge."""

    # Elasticsearch
    es_url: str = "http://localhost:9200"
    es_index_pattern: str = "casino-events-*"
    es_scroll_size: int = 500
    es_scroll_ttl: str = "2m"

    # Kafka
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic: str = "fraud.raw.events"

    # State tracking
    state_file: str = "/tmp/es_kafka_bridge_state.json"

    # Processing
    interval_seconds: int = 60
    backfill_from: str | None = None    # ISO date string

    # ES credentials (optional)
    es_user: str | None = None
    es_password: str | None = None


# ---------------------------------------------------------------------------
# State management: track last processed timestamp
# ---------------------------------------------------------------------------


def load_state(state_file: str) -> dict[str, Any]:
    """Load bridge state from file (last processed timestamp per index)."""
    path = Path(state_file)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"last_processed_at": None}


def save_state(state_file: str, state: dict[str, Any]) -> None:
    """Persist bridge state to file."""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Elasticsearch document → Kafka message transformation
# ---------------------------------------------------------------------------


def transform_es_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Transform an Elasticsearch casino-event document to TransactionEvent schema.

    The casino-events-* index stores events from the fraud API ingestion service.
    This function normalises the field names to match the TransactionEvent dataclass
    in flink_fraud_realtime.py and the Iceberg transactions table schema.

    Args:
        doc: Raw Elasticsearch _source document.

    Returns:
        Dict ready for JSON serialisation to Kafka.
    """
    source = doc.get("_source", doc)

    # Normalise event time: accept multiple field names from the casino API.
    # casino-events-* uses 'created_at'; other sources may use 'event_time' or '@timestamp'.
    event_time = (
        source.get("created_at")
        or source.get("event_time")
        or source.get("timestamp")
        or source.get("@timestamp")
        or datetime.now(timezone.utc).isoformat()
    )

    # Ensure event_time is a proper ISO string with timezone
    if isinstance(event_time, (int, float)):
        event_time = datetime.fromtimestamp(event_time / 1000, tz=timezone.utc).isoformat()

    # Transaction type normalisation (casino events use different naming)
    raw_type = source.get("event_type") or source.get("transaction_type") or "bet"
    type_map = {
        "spin": "bet",
        "game_round": "bet",
        "wager": "bet",
        "fund": "deposit",
        "withdraw": "withdrawal",
        "cashout": "withdrawal",
        "free_spin": "bonus",
        "promo": "bonus",
    }
    transaction_type = type_map.get(raw_type.lower(), raw_type.lower())

    # Amount: Elasticsearch stores in cents or as float EUR
    amount_raw = source.get("amount_cents") or source.get("amount") or 0
    if isinstance(amount_raw, float) and amount_raw < 10000:
        # Likely EUR, convert to cents
        amount_cents = int(amount_raw * 100)
    else:
        amount_cents = int(amount_raw)

    return {
        "transaction_id": (
            source.get("transaction_id")
            or source.get("reference_id")   # casino-events-* field
            or source.get("event_id")
            or source.get("_id")
            or doc.get("_id", "unknown")
        ),
        "player_id": (
            source.get("player_id")
            or source.get("user_id")
            or source.get("account_id")
            or "unknown"
        ),
        "event_time": event_time,
        "transaction_type": transaction_type,
        "amount_cents": max(0, amount_cents),
        "currency": source.get("currency", "EUR"),
        "game_id": source.get("game_id") or source.get("game"),
        "game_type": source.get("game_type") or source.get("game_category"),
        "payment_method": source.get("payment_method"),
        "jurisdiction": source.get("jurisdiction", "MGA"),
        "brand_id": int(source.get("brand_id", 1)),
        "ip_address": source.get("ip_address") or source.get("source_ip") or source.get("ip"),
        "device_fingerprint": source.get("device_fingerprint") or source.get("fingerprint"),
        "session_id": source.get("session_id"),
        "country": source.get("country"),
        # Bridge metadata (not in Flink schema but useful for debugging)
        "_bridge_indexed_at": source.get("@timestamp") or source.get("indexed_at"),
        "_bridge_es_index": doc.get("_index"),
        "_bridge_es_id": doc.get("_id"),
    }


# ---------------------------------------------------------------------------
# Elasticsearch reader
# ---------------------------------------------------------------------------


def scroll_new_events(
    es: Any,
    config: BridgeConfig,
    since: str | None,
) -> Iterator[dict[str, Any]]:
    """Scroll through casino events newer than ``since``.

    Uses Elasticsearch point-in-time scroll to avoid missing events that
    arrive during a long scroll operation.

    Args:
        es: Elasticsearch client.
        config: Bridge configuration.
        since: ISO timestamp. Only events after this time are returned.
               If None, returns all events (use only for backfill).

    Yields:
        Raw Elasticsearch hit dicts (with _id, _index, _source).
    """
    # Detect the timestamp field name: casino-events-* uses 'created_at',
    # some other indices may use '@timestamp'. Try both.
    ts_field = "@timestamp"
    try:
        mapping = es.indices.get_mapping(index=config.es_index_pattern)
        for idx_name, idx_data in mapping.items():
            props = idx_data.get("mappings", {}).get("properties", {})
            if "created_at" in props:
                ts_field = "created_at"
                break
            if "@timestamp" in props:
                ts_field = "@timestamp"
                break
    except Exception:
        pass

    logger.info("Using timestamp field: %s", ts_field)

    if since:
        query: dict[str, Any] = {
            "query": {
                "range": {
                    ts_field: {
                        "gt": since,
                    }
                }
            },
            "sort": [{ts_field: {"order": "asc"}}],
        }
    else:
        query = {
            "query": {"match_all": {}},
            "sort": [{"_doc": {"order": "asc"}}],   # Natural order when no timestamp
        }

    logger.info(
        "Scrolling %s (since=%s, size=%d)",
        config.es_index_pattern, since or "beginning", config.es_scroll_size,
    )

    try:
        response = es.search(
            index=config.es_index_pattern,
            body=query,
            scroll=config.es_scroll_ttl,
            size=config.es_scroll_size,
        )
    except Exception as e:
        logger.error("ES search failed: %s", e)
        return

    scroll_id = response.get("_scroll_id")
    hits = response.get("hits", {}).get("hits", [])
    total = response.get("hits", {}).get("total", {})
    total_count = total.get("value", 0) if isinstance(total, dict) else total

    logger.info("Found %d events to process", total_count)

    while hits:
        for hit in hits:
            yield hit

        if not scroll_id:
            break

        try:
            response = es.scroll(scroll_id=scroll_id, scroll=config.es_scroll_ttl)
            scroll_id = response.get("_scroll_id")
            hits = response.get("hits", {}).get("hits", [])
        except Exception as e:
            logger.error("ES scroll failed: %s", e)
            break

    # Clean up scroll context
    if scroll_id:
        try:
            es.clear_scroll(scroll_id=scroll_id)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Kafka producer
# ---------------------------------------------------------------------------


def create_kafka_producer(config: BridgeConfig) -> Any:
    """Create an idempotent Kafka producer.

    Idempotent mode (enable_idempotence=True) ensures that retries on
    transient network errors do not create duplicate messages.
    """
    if not KAFKA_AVAILABLE:
        logger.error("kafka-python not installed. Run: pip install kafka-python")
        sys.exit(1)

    logger.info("Connecting to Kafka at %s", config.kafka_bootstrap)

    producer = KafkaProducer(
        bootstrap_servers=config.kafka_bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        # Idempotent producer: exactly-once delivery
        enable_idempotence=True,
        acks="all",
        retries=5,
        max_in_flight_requests_per_connection=1,
        # Batching for throughput
        batch_size=16384,
        linger_ms=50,
        compression_type="gzip",
    )

    return producer


def ensure_topic_exists(config: BridgeConfig) -> None:
    """Create the Kafka topic if it does not exist.

    Kafka's auto-create is enabled on the fraud-detection stack but we
    create explicitly with the right partition count for Flink parallelism.
    """
    try:
        from kafka.admin import KafkaAdminClient, NewTopic

        admin = KafkaAdminClient(bootstrap_servers=config.kafka_bootstrap)
        existing = set(admin.list_topics())

        if config.kafka_topic not in existing:
            logger.info("Creating Kafka topic: %s", config.kafka_topic)
            admin.create_topics(
                [NewTopic(name=config.kafka_topic, num_partitions=4, replication_factor=1)],
                validate_only=False,
            )
            logger.info("Topic %s created", config.kafka_topic)
        else:
            logger.info("Topic %s already exists", config.kafka_topic)

        admin.close()
    except Exception as e:
        logger.warning("Could not create topic (auto-create may handle it): %s", e)


# ---------------------------------------------------------------------------
# Main bridge logic
# ---------------------------------------------------------------------------


def run_bridge_once(config: BridgeConfig) -> int:
    """Execute one bridge run: ES → Kafka.

    Reads events from Elasticsearch that are newer than the last run,
    transforms them, and produces to Kafka.

    Returns:
        Number of events forwarded.
    """
    if not ES_AVAILABLE:
        logger.error("elasticsearch-py not installed. Run: pip install elasticsearch>=8")
        sys.exit(1)

    # Load state
    state = load_state(config.state_file)
    since = state.get("last_processed_at")

    # Backfill override
    if config.backfill_from and not since:
        since = config.backfill_from
        logger.info("Backfill mode: starting from %s", since)

    # If no state and no backfill, start from 24 hours ago
    if since is None:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        logger.info("No prior state. Processing last 24 hours (since=%s)", since)

    # Connect to Elasticsearch
    es_kwargs: dict[str, Any] = {"hosts": [config.es_url]}
    if config.es_user and config.es_password:
        es_kwargs["http_auth"] = (config.es_user, config.es_password)

    try:
        es = Elasticsearch(**es_kwargs)
        if not es.ping():
            logger.error("Elasticsearch at %s is not reachable", config.es_url)
            return 0
    except Exception as e:
        logger.error("Cannot connect to Elasticsearch: %s", e)
        return 0

    # Connect to Kafka
    ensure_topic_exists(config)
    producer = create_kafka_producer(config)

    count = 0
    last_event_time: str | None = None
    errors = 0

    try:
        for hit in scroll_new_events(es, config, since):
            try:
                event = transform_es_doc(hit)
                player_id = event.get("player_id", "unknown")

                producer.send(
                    topic=config.kafka_topic,
                    key=player_id,   # Key by player_id for Flink keyed state
                    value=event,
                )

                count += 1

                # Track the latest event timestamp for state (prefer source timestamp)
                evt_time = event.get("event_time") or event.get("_bridge_indexed_at") or event.get("created_at")
                if evt_time:
                    last_event_time = evt_time

                if count % 500 == 0:
                    producer.flush()
                    logger.info("Forwarded %d events so far...", count)

            except Exception as e:
                errors += 1
                logger.warning("Failed to process hit %s: %s", hit.get("_id"), e)
                if errors > 100:
                    logger.error("Too many errors (%d). Aborting run.", errors)
                    break

        # Flush remaining messages
        producer.flush()

    finally:
        producer.close()

    # Update state with the latest processed timestamp
    if last_event_time:
        state["last_processed_at"] = last_event_time
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        state["last_run_count"] = count
        save_state(config.state_file, state)

    logger.info(
        "Bridge run complete: %d events forwarded to %s (%d errors)",
        count, config.kafka_topic, errors,
    )
    return count


def run_bridge_loop(config: BridgeConfig) -> None:
    """Run the bridge continuously at the configured interval."""
    logger.info(
        "Starting continuous bridge loop (interval=%ds)", config.interval_seconds
    )
    while True:
        run_start = time.monotonic()
        try:
            run_bridge_once(config)
        except Exception as e:
            logger.error("Bridge run failed: %s", e)

        elapsed = time.monotonic() - run_start
        sleep_time = max(0, config.interval_seconds - elapsed)
        logger.info("Next run in %.1f seconds", sleep_time)
        time.sleep(sleep_time)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Forward Elasticsearch casino events to Kafka for Iceberg lakehouse",
    )
    parser.add_argument(
        "--es-url",
        default=os.environ.get("ES_URL", "http://localhost:9200"),
        help="Elasticsearch URL (default: http://localhost:9200)",
    )
    parser.add_argument(
        "--es-index",
        default="casino-events-*",
        help="Elasticsearch index pattern (default: casino-events-*)",
    )
    parser.add_argument(
        "--kafka-bootstrap",
        default=os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"),
        help="Kafka bootstrap servers (default: localhost:9092)",
    )
    parser.add_argument(
        "--kafka-topic",
        default="fraud.raw.events",
        help="Kafka topic to produce to (default: fraud.raw.events)",
    )
    parser.add_argument(
        "--state-file",
        default="/tmp/es_kafka_bridge_state.json",
        help="Path to state tracking file",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (suitable for cron)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds when not using --once (default: 60)",
    )
    parser.add_argument(
        "--backfill-from",
        default=None,
        help="Backfill from this ISO date if no prior state (e.g. 2026-03-01)",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    config = BridgeConfig(
        es_url=args.es_url,
        es_index_pattern=args.es_index,
        kafka_bootstrap=args.kafka_bootstrap,
        kafka_topic=args.kafka_topic,
        state_file=args.state_file,
        interval_seconds=args.interval,
        backfill_from=args.backfill_from,
    )

    if args.once:
        run_bridge_once(config)
    else:
        run_bridge_loop(config)


if __name__ == "__main__":
    main()
