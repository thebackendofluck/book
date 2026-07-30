#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Kafka-Based Event Ingestion for Casino CDP
============================================
High-throughput event ingestion pipeline that captures all player
interactions (bets, deposits, logins, page views, bonus claims) and
feeds them into the Customer Data Platform.

Architecture:
  Player Actions -> Casino Platform -> Kafka Topics -> This Consumer
  -> Event Store (ClickHouse) + Identity Resolution + Real-Time Segments

Regulatory Notes:
- Events containing PII are encrypted at rest (AES-256)
- Retention policies per jurisdiction (UK: 3 years, Malta: 5 years)
- GDPR: event deletion cascade via unified_profile_id
- Responsible gambling events (deposit limits, self-exclusion) are prioritized
"""

import json
import time
import logging
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event Schema
# ---------------------------------------------------------------------------

class EventCategory(Enum):
    GAMING = "gaming"
    FINANCIAL = "financial"
    IDENTITY = "identity"
    MARKETING = "marketing"
    RESPONSIBLE_GAMBLING = "responsible_gambling"
    SYSTEM = "system"


class EventType(Enum):
    # Gaming events
    BET_PLACED = "bet_placed"
    BET_SETTLED = "bet_settled"
    GAME_ROUND_START = "game_round_start"
    GAME_ROUND_END = "game_round_end"
    JACKPOT_CONTRIBUTION = "jackpot_contribution"
    JACKPOT_WIN = "jackpot_win"

    # Financial events
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    BONUS_CLAIMED = "bonus_claimed"
    BONUS_WAGERED = "bonus_wagered"
    BONUS_COMPLETED = "bonus_completed"
    BONUS_FORFEITED = "bonus_forfeited"

    # Identity events
    REGISTRATION = "registration"
    LOGIN = "login"
    LOGOUT = "logout"
    KYC_SUBMITTED = "kyc_submitted"
    KYC_VERIFIED = "kyc_verified"

    # Marketing events
    PAGE_VIEW = "page_view"
    CAMPAIGN_CLICK = "campaign_click"
    PUSH_RECEIVED = "push_received"
    EMAIL_OPENED = "email_opened"
    SMS_CLICKED = "sms_clicked"

    # Responsible gambling
    DEPOSIT_LIMIT_SET = "deposit_limit_set"
    LOSS_LIMIT_SET = "loss_limit_set"
    SESSION_LIMIT_SET = "session_limit_set"
    REALITY_CHECK_SHOWN = "reality_check_shown"
    SELF_EXCLUSION_REQUESTED = "self_exclusion_requested"
    COOL_OFF_REQUESTED = "cool_off_requested"


@dataclass
class CasinoEvent:
    """Canonical event schema for the casino CDP."""
    event_id: str
    event_type: str
    event_category: str
    player_id: str                      # Platform player ID
    unified_profile_id: Optional[str]   # CDP unified profile ID
    timestamp: str                      # ISO 8601
    properties: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)  # device, ip, geo, session
    source: str = ""                    # "website", "mobile_app", "backoffice"
    schema_version: str = "1.0"

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str) -> "CasinoEvent":
        data = json.loads(raw)
        return cls(**data)

    def validate(self) -> list[str]:
        """Validate event against schema rules."""
        errors = []
        if not self.event_id:
            errors.append("event_id is required")
        if not self.event_type:
            errors.append("event_type is required")
        if not self.player_id:
            errors.append("player_id is required")
        if not self.timestamp:
            errors.append("timestamp is required")
        try:
            datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            errors.append(f"Invalid timestamp format: {self.timestamp}")
        return errors


# ---------------------------------------------------------------------------
# Kafka Consumer (abstracted for portability)
# ---------------------------------------------------------------------------

class EventConsumer(ABC):
    """Abstract base for event consumers."""

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass


class KafkaEventConsumer(EventConsumer):
    """
    Kafka consumer for casino event ingestion.

    Topics consumed:
    - casino.events.gaming      (partitioned by player_id)
    - casino.events.financial   (partitioned by player_id)
    - casino.events.identity    (partitioned by player_id)
    - casino.events.marketing   (partitioned by player_id)
    - casino.events.rg          (partitioned by player_id, HIGH PRIORITY)

    Consumer group: cdp-event-ingestion

    Production config:
    - auto.offset.reset: earliest (no event loss)
    - enable.auto.commit: false (manual commit after processing)
    - max.poll.records: 500
    - session.timeout.ms: 30000
    """

    TOPICS = [
        "casino.events.gaming",
        "casino.events.financial",
        "casino.events.identity",
        "casino.events.marketing",
        "casino.events.rg",
    ]

    def __init__(self, bootstrap_servers: str = "localhost:9092",
                 group_id: str = "cdp-event-ingestion",
                 event_store=None,
                 identity_resolver=None):
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.event_store = event_store or InMemoryEventStore()
        self.identity_resolver = identity_resolver
        self._running = False
        self._consumer = None

        # Metrics
        self.events_processed = 0
        self.events_failed = 0
        self.events_by_type: dict[str, int] = {}

    def _create_consumer(self):
        """
        Create Kafka consumer. In production, use confluent_kafka:

            from confluent_kafka import Consumer
            return Consumer({
                'bootstrap.servers': self.bootstrap_servers,
                'group.id': self.group_id,
                'auto.offset.reset': 'earliest',
                'enable.auto.commit': False,
                'max.poll.records': 500,
                'session.timeout.ms': 30000,
            })
        """
        logger.info(
            "Kafka consumer configured: servers=%s, group=%s",
            self.bootstrap_servers, self.group_id
        )
        return None  # Placeholder for demo

    def start(self):
        """Start consuming events."""
        self._consumer = self._create_consumer()
        self._running = True
        logger.info("Event ingestion consumer started on topics: %s", self.TOPICS)

    def stop(self):
        """Graceful shutdown."""
        self._running = False
        if self._consumer:
            # self._consumer.close()
            pass
        logger.info(
            "Consumer stopped. Processed: %d, Failed: %d",
            self.events_processed, self.events_failed,
        )

    def process_message(self, raw_message: str) -> bool:
        """
        Process a single Kafka message.
        Returns True if successfully processed.
        """
        try:
            event = CasinoEvent.from_json(raw_message)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error("Failed to parse event: %s", e)
            self._send_to_dlq(raw_message, str(e))
            self.events_failed += 1
            return False

        # Validate schema
        errors = event.validate()
        if errors:
            logger.warning("Event validation failed: %s", errors)
            self._send_to_dlq(raw_message, str(errors))
            self.events_failed += 1
            return False

        # Enrich with unified profile ID
        if not event.unified_profile_id and self.identity_resolver:
            event.unified_profile_id = self._resolve_identity(event)

        # Apply event-specific processing
        self._apply_event_rules(event)

        # Store event
        self.event_store.store(event)

        # Update metrics
        self.events_processed += 1
        event_type = event.event_type
        self.events_by_type[event_type] = self.events_by_type.get(event_type, 0) + 1

        return True

    def process_batch(self, messages: list[str]) -> dict:
        """Process a batch of messages. Returns summary stats."""
        results = {"success": 0, "failed": 0, "total": len(messages)}
        for msg in messages:
            if self.process_message(msg):
                results["success"] += 1
            else:
                results["failed"] += 1
        return results

    def _resolve_identity(self, event: CasinoEvent) -> Optional[str]:
        """Resolve player_id to unified_profile_id via identity resolution."""
        # In production, call IdentityResolutionEngine
        return f"unified_{hashlib.md5(event.player_id.encode()).hexdigest()[:12]}"

    def _apply_event_rules(self, event: CasinoEvent):
        """
        Apply real-time rules based on event type.
        Casino-specific triggers that fire during ingestion.
        """
        # Responsible gambling events get priority processing
        if event.event_category == EventCategory.RESPONSIBLE_GAMBLING.value:
            self._handle_rg_event(event)

        # Large bet alerts
        if event.event_type == EventType.BET_PLACED.value:
            amount = event.properties.get("amount", 0)
            if amount >= 10000:  # High-value bet threshold
                self._trigger_alert("high_value_bet", event)

        # Deposit velocity check
        if event.event_type == EventType.DEPOSIT.value:
            self._check_deposit_velocity(event)

    def _handle_rg_event(self, event: CasinoEvent):
        """
        Responsible gambling events require immediate action.
        UK Gambling Commission LCCP: must be processed within seconds.
        """
        logger.info(
            "RG event processed: player=%s, type=%s",
            event.player_id, event.event_type,
        )
        # In production: update player limits in real-time cache,
        # trigger self-exclusion across all channels

    def _check_deposit_velocity(self, event: CasinoEvent):
        """
        Check if player is depositing too frequently.
        AML red flag: >5 deposits in 1 hour, or >10 in 24 hours.
        """
        # In production: query event store for recent deposits
        pass

    def _trigger_alert(self, alert_type: str, event: CasinoEvent):
        """Send alert to risk/compliance team."""
        logger.warning(
            "Alert triggered: %s for player %s, amount=%s",
            alert_type, event.player_id,
            event.properties.get("amount"),
        )

    def _send_to_dlq(self, raw_message: str, error: str):
        """Send failed message to Dead Letter Queue for manual review."""
        logger.error("DLQ: %s | Error: %s", raw_message[:200], error)


# ---------------------------------------------------------------------------
# Event Store (ClickHouse abstraction)
# ---------------------------------------------------------------------------

class InMemoryEventStore:
    """
    In-memory event store for testing/demo.
    Production: ClickHouse with the following schema:

    CREATE TABLE casino_events (
        event_id        String,
        event_type      LowCardinality(String),
        event_category  LowCardinality(String),
        player_id       String,
        unified_profile_id Nullable(String),
        timestamp       DateTime64(3),
        properties      String,  -- JSON
        context         String,  -- JSON
        source          LowCardinality(String),
        _partition_date Date DEFAULT toDate(timestamp)
    )
    ENGINE = MergeTree()
    PARTITION BY _partition_date
    ORDER BY (player_id, timestamp, event_type)
    TTL _partition_date + INTERVAL 3 YEAR  -- UK LCCP retention
    """

    def __init__(self):
        self.events: list[CasinoEvent] = []

    def store(self, event: CasinoEvent):
        self.events.append(event)

    def query_by_player(self, player_id: str,
                        event_type: Optional[str] = None,
                        limit: int = 100) -> list[CasinoEvent]:
        results = [e for e in self.events if e.player_id == player_id]
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        return results[:limit]

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.event_type] = counts.get(e.event_type, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Event Builder Helpers
# ---------------------------------------------------------------------------

class CasinoEventBuilder:
    """Convenience builder for common casino events."""

    @staticmethod
    def bet_placed(player_id: str, game_id: str, amount: float,
                   currency: str = "GBP", game_type: str = "slots") -> CasinoEvent:
        return CasinoEvent(
            event_id=f"bet_{hashlib.md5(f'{player_id}{time.time()}'.encode()).hexdigest()[:16]}",
            event_type=EventType.BET_PLACED.value,
            event_category=EventCategory.GAMING.value,
            player_id=player_id,
            unified_profile_id=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            properties={
                "game_id": game_id,
                "amount": amount,
                "currency": currency,
                "game_type": game_type,
            },
            source="game_engine",
        )

    @staticmethod
    def deposit(player_id: str, amount: float, method: str,
                currency: str = "GBP") -> CasinoEvent:
        return CasinoEvent(
            event_id=f"dep_{hashlib.md5(f'{player_id}{time.time()}'.encode()).hexdigest()[:16]}",
            event_type=EventType.DEPOSIT.value,
            event_category=EventCategory.FINANCIAL.value,
            player_id=player_id,
            unified_profile_id=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            properties={
                "amount": amount,
                "currency": currency,
                "method": method,
            },
            source="cashier",
        )

    @staticmethod
    def bonus_claimed(player_id: str, bonus_id: str, bonus_type: str,
                      amount: float, wagering_requirement: float) -> CasinoEvent:
        return CasinoEvent(
            event_id=f"bon_{hashlib.md5(f'{player_id}{time.time()}'.encode()).hexdigest()[:16]}",
            event_type=EventType.BONUS_CLAIMED.value,
            event_category=EventCategory.FINANCIAL.value,
            player_id=player_id,
            unified_profile_id=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            properties={
                "bonus_id": bonus_id,
                "bonus_type": bonus_type,
                "amount": amount,
                "wagering_requirement": wagering_requirement,
                "wagering_multiplier": wagering_requirement / amount if amount > 0 else 0,
            },
            source="bonus_engine",
        )

    @staticmethod
    def self_exclusion(player_id: str, duration_days: int,
                       reason: str = "") -> CasinoEvent:
        return CasinoEvent(
            event_id=f"se_{hashlib.md5(f'{player_id}{time.time()}'.encode()).hexdigest()[:16]}",
            event_type=EventType.SELF_EXCLUSION_REQUESTED.value,
            event_category=EventCategory.RESPONSIBLE_GAMBLING.value,
            player_id=player_id,
            unified_profile_id=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            properties={
                "duration_days": duration_days,
                "reason": reason,
                "effective_immediately": True,
            },
            source="responsible_gambling",
        )


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    consumer = KafkaEventConsumer()
    consumer.start()

    # Simulate a player session
    builder = CasinoEventBuilder()

    events = [
        builder.deposit("player_42", 100.0, "visa"),
        builder.bonus_claimed("player_42", "welcome_100", "deposit_match", 100.0, 3500.0),
        builder.bet_placed("player_42", "starburst", 2.50, game_type="slots"),
        builder.bet_placed("player_42", "starburst", 2.50, game_type="slots"),
        builder.bet_placed("player_42", "starburst", 5.00, game_type="slots"),
        builder.bet_placed("player_42", "roulette_eu", 25.00, game_type="table"),
        builder.bet_placed("player_42", "blackjack_vip", 15000.00, game_type="table"),  # High value!
    ]

    messages = [e.to_json() for e in events]
    result = consumer.process_batch(messages)
    print(f"\nBatch result: {result}")
    print(f"Events by type: {consumer.events_by_type}")

    # Responsible gambling event (priority processing)
    rg_event = builder.self_exclusion("player_42", duration_days=180, reason="personal")
    consumer.process_message(rg_event.to_json())

    consumer.stop()
