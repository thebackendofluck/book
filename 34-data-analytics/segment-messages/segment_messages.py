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
segment_messages.py — Segment event message types and Kafka producer.

Mirrors Events.scala, MessageEnvelope.scala, KafkaOps.scala,
and Send2SegmentService.scala.

This module defines the domain events that are published to Segment via Kafka,
and provides the producer service to send them.

Topics:
  flag-events:  FlagApplied, FlagRemoved
  bonus-events: BonusAwarded, BonusConverted, BonusClawback, BonusRevoke

MessageEnvelope:
  - created:   when the envelope was created
  - uid:       UUID for deduplication
  - signature: MD5 hash of serialized payload (fast equality check)
  - data:      the SegmentMessage payload

Send2SegmentService:
  - sendFlagEvent  → flag-events topic
  - sendBonusEvent → bonus-events topic
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional

import structlog
from confluent_kafka import Producer
from fastapi import FastAPI

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain event types (mirrors Events.scala)
# ---------------------------------------------------------------------------

class BonusGroup(str, Enum):
    CASINO = "Casino"
    SPORTS = "Sports"


class BonusChannel(str, Enum):
    BACK_OFFICE = "BackOffice"
    EXTERNAL    = "External"
    SYSTEM      = "System"


@dataclass
class FlagCoreInfo:
    user_id:          int
    external_user_id: str


@dataclass
class BonusCoreInfo:
    user_id:          int
    external_user_id: str
    happen:           datetime


@dataclass
class BonusPromotionDetail:
    id:   int
    name: str


@dataclass
class BonusDetail:
    campaign_name:           str
    amount:                  int          # amount in cents/minor units
    bonus_group:             BonusGroup
    bonus_account_id:        int
    awarded_by:              Optional[BonusPromotionDetail] = None
    promotion_category:      Optional[BonusPromotionDetail] = None
    deductability_category:  Optional[BonusPromotionDetail] = None


# --- Flag events ---

@dataclass
class FlagApplied:
    """Emitted when a flag (exclusion, block, KYC state) is applied to a user."""
    core_info:               FlagCoreInfo
    flag_name:               str
    blocks_bonuses:          bool
    excludes_from_marketing: bool
    disallows_withdrawals:   bool
    flag_id:                 int
    date_time_added:         datetime
    agent_id:                Optional[int] = None
    event_type: str          = field(default="FlagApplied", init=False)


@dataclass
class FlagRemoved:
    """Emitted when a flag is lifted from a user."""
    core_info:               FlagCoreInfo
    flag_name:               str
    blocks_bonuses:          bool
    excludes_from_marketing: bool
    disallows_withdrawals:   bool
    flag_id:                 int
    date_time_removed:       datetime
    agent_id:                Optional[int] = None
    event_type: str          = field(default="FlagRemoved", init=False)


# --- Bonus events ---

@dataclass
class BonusAwarded:
    """Emitted when one or more bonuses are awarded to a user."""
    core_info:    BonusCoreInfo
    channel:      BonusChannel
    bonus_details: list[BonusDetail]
    event_type: str = field(default="BonusAwarded", init=False)


@dataclass
class BonusConverted:
    """Emitted when bonus balance is converted to cash balance (wagering complete)."""
    core_info:    BonusCoreInfo
    bonus_details: list[BonusDetail]
    event_type: str = field(default="BonusConverted", init=False)


@dataclass
class BonusClawback:
    """Emitted when a bonus is reclaimed (e.g., T&C violation detected)."""
    core_info:    BonusCoreInfo
    bonus_details: list[BonusDetail]
    event_type: str = field(default="BonusClawback", init=False)


@dataclass
class BonusRevoke:
    """Emitted when a bonus is revoked before conversion (e.g., account closure)."""
    core_info:         BonusCoreInfo
    countdown_balance: int           # remaining wagering requirement in minor units
    bonus_details:     list[BonusDetail]
    event_type: str    = field(default="BonusRevoke", init=False)


SegmentMessage = FlagApplied | FlagRemoved | BonusAwarded | BonusConverted | BonusClawback | BonusRevoke


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_message(msg) -> str:
    """Serialize any SegmentMessage dataclass to JSON, handling nested types."""
    def _convert(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _convert(v) for k, v in vars(obj).items()}
        if isinstance(obj, (list, tuple)):
            return [_convert(i) for i in obj]
        return obj

    return json.dumps(_convert(msg))


def _md5_signature(serialized: str) -> str:
    # Non-security content fingerprint for message dedup; not used for auth.
    return hashlib.md5(serialized.encode(), usedforsecurity=False).hexdigest()


# ---------------------------------------------------------------------------
# Message envelope (mirrors MessageEnvelope.scala)
# ---------------------------------------------------------------------------

@dataclass
class MessageEnvelope:
    """
    Wraps a SegmentMessage with metadata for idempotency and deduplication.

    uid:       UUID v4 — used by downstream consumers to deduplicate
    signature: MD5 of serialized data — fast equality/change detection
    created:   wall-clock time of envelope creation (not event time)
    """
    created:   datetime
    uid:       str
    signature: str
    data:      object  # SegmentMessage

    @classmethod
    def wrap(cls, message) -> "MessageEnvelope":
        serialized = _serialize_message(message)
        return cls(
            created=datetime.now(timezone.utc),
            uid=str(uuid.uuid4()),
            signature=_md5_signature(serialized),
            data=message,
        )

    def to_json(self) -> str:
        return json.dumps({
            "created":   self.created.isoformat(),
            "uid":       self.uid,
            "signature": self.signature,
            "data":      json.loads(_serialize_message(self.data)),
        })


# ---------------------------------------------------------------------------
# Kafka ops (mirrors KafkaOps.scala)
# ---------------------------------------------------------------------------

def _message_key(msg) -> str:
    """
    Derive the Kafka message key from a SegmentMessage.
    Flag events → externalUserId; bonus events → externalUserId.
    Using externalUserId as key ensures events for the same user
    land on the same partition (preserving order per user).
    """
    if hasattr(msg, "core_info"):
        core = msg.core_info
        if hasattr(core, "external_user_id"):
            return str(core.external_user_id)
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Kafka producer
# ---------------------------------------------------------------------------

class KafkaEventProducer:
    """Low-level Kafka producer wrapper."""

    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def send(self, topic: str, key: str, value: str) -> None:
        self._producer.produce(topic, key=key, value=value)
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0) -> None:
        self._producer.flush(timeout=timeout)


# ---------------------------------------------------------------------------
# Send2SegmentService (mirrors Send2SegmentService.scala)
# ---------------------------------------------------------------------------

FLAG_EVENTS_TOPIC  = "flag-events"
BONUS_EVENTS_TOPIC = "bonus-events"


class Send2SegmentService:
    """
    Publishes FlagEvent and BonusEvent messages to their respective Kafka topics.

    Each message is wrapped in a MessageEnvelope before publishing, which adds
    deduplication (uid), integrity (signature), and creation timestamp metadata.
    """

    def __init__(self, kafka: KafkaEventProducer) -> None:
        self._kafka = kafka

    def send_flag_event(self, event: FlagApplied | FlagRemoved) -> None:
        envelope = MessageEnvelope.wrap(event)
        key      = _message_key(event)
        self._kafka.send(FLAG_EVENTS_TOPIC, key, envelope.to_json())
        log.info(
            "flag event sent",
            event_type=event.event_type,
            user_id=event.core_info.user_id,
            flag=event.flag_name,
        )

    def send_bonus_event(self, event) -> None:
        envelope = MessageEnvelope.wrap(event)
        key      = _message_key(event)
        self._kafka.send(BONUS_EVENTS_TOPIC, key, envelope.to_json())
        log.info(
            "bonus event sent",
            event_type=event.event_type,
            user_id=event.core_info.user_id,
        )

    def flush(self) -> None:
        self._kafka.flush()


# ---------------------------------------------------------------------------
# FastAPI health endpoints
# ---------------------------------------------------------------------------

app = FastAPI(title="Segment Messages Service", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    return {"pong": True}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    kafka     = KafkaEventProducer(bootstrap)
    service   = Send2SegmentService(kafka)

    # In production, the service is called by other platform services.
    # Here we just run the health-check HTTP server.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
