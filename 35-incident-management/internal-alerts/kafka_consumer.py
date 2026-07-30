# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Kafka Alert Consumer
# Source: Production casino platform (sanitized)
# Chapter 35 - Incident Management
#
# Consumes JSON alert messages from the Kafka alerts topic and persists
# them to the database with status=Pending so the outbox service can
# dispatch them asynchronously.
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import os

from kafka import KafkaConsumer  # type: ignore
from kafka.errors import KafkaError  # type: ignore

from models import Alert, AlertMessage
from repository import AlertRepository

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_GROUP_ID          = os.getenv("KAFKA_GROUP_ID", "internal-alerts")
KAFKA_ALERT_TOPIC       = os.getenv("KAFKA_ALERT_TOPIC", "platform-alerts")
MAX_POLL_RECORDS        = int(os.getenv("KAFKA_MAX_POLL_RECORDS", "100"))


class KafkaAlertConsumer:
    """
    Polls the Kafka alerts topic and writes each message as a new
    Alert(status=Pending) row in the database.

    Message format:
    {
        "alertType": "AML_THRESHOLD_BREACHED",
        "userId": 12345,
        "brandId": 42,
        "params": { "amount": 15000, "currency": "GBP" }
    }
    """

    def __init__(self, alert_repository: AlertRepository) -> None:
        self._repo = alert_repository

    def _process_record(self, value: bytes) -> None:
        try:
            raw = json.loads(value.decode("utf-8"))
            msg = AlertMessage(
                alert_type=raw["alertType"],
                user_id=raw.get("userId"),
                brand_id=raw.get("brandId"),
                params=raw.get("params"),
            )
            alert = Alert.from_message(msg)
            self._repo.create_alert(alert)
        except Exception as exc:
            logger.error("could not process kafka message: %s", exc)

    def run_forever(self) -> None:
        """Blocking consumer loop. Run in a background thread."""
        logger.info(
            "KafkaAlertConsumer starting: topic=%s group=%s",
            KAFKA_ALERT_TOPIC, KAFKA_GROUP_ID,
        )
        consumer = KafkaConsumer(
            KAFKA_ALERT_TOPIC,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            group_id=KAFKA_GROUP_ID,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            max_poll_records=MAX_POLL_RECORDS,
            value_deserializer=None,  # raw bytes, we parse manually
        )
        try:
            for message in consumer:
                self._process_record(message.value)
                consumer.commit()
        except KafkaError as exc:
            logger.error("Kafka consumer error: %s", exc)
        finally:
            consumer.close()
            logger.info("KafkaAlertConsumer stopped")
