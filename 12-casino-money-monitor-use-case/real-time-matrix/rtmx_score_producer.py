# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# RTMX Score Message Producer
# Source: Production casino platform (sanitized)
# Chapter 12 - Casino Money Monitor
#
# Publishes RtmxScoreMessage records to the RTMX_SCORES Kafka topic.
# Consumed by the compliance dashboard and responsible gaming workflows.
# =============================================================================

from __future__ import annotations

import json
import logging
import os
from typing import Any

from models import RtmxScoreMessage

logger = logging.getLogger(__name__)

RTMX_SCORES_TOPIC = "RTMX_SCORES"
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


class RtmxScoreMessageProducer:
    """
    Thin wrapper around the Kafka producer for RTMX score messages.
    Each message is keyed by user_id for partition affinity so that
    all events for a given player land on the same partition.
    """

    def __init__(self, bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS) -> None:
        from kafka import KafkaProducer  # type: ignore
        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: str(k).encode("utf-8"),
            acks="all",
            retries=3,
        )

    def send_score_messages(self, messages: list[RtmxScoreMessage]) -> None:
        for msg in messages:
            payload: dict[str, Any] = {
                "userId": msg.user_id,
                "matrixEvent": msg.matrix_event,
                "params": msg.params,
            }
            self._producer.send(
                topic=RTMX_SCORES_TOPIC,
                key=msg.user_id,
                value=payload,
            )
            logger.debug("Sent RTMX score message: userId=%s event=%s", msg.user_id, msg.matrix_event)
        self._producer.flush()

    def close(self) -> None:
        self._producer.close()
