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
kafka_consumer.py – Kafka consumer for the risk-alerting service.

Consumes deposit and withdrawal payment events, maintains per-user event
histories, runs every alert rule via alert_engine, and forwards triggered
alerts to the notification layer.

Topic layout (mirrors original Scala configuration):
  - payment.status.change   – deposit events (PaymentStatusChangeEvent)
  - withdrawal.message      – withdrawal events (WithdrawalEvent)
  - opsgenie.alerts         – outbound alert topic (produced)
"""

from __future__ import annotations

import json
import os
import signal
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from alert_engine import evaluate_deposit_rules, evaluate_withdrawal_rules
from models import DepositEvent, PaymentStatusChangeEvent, RiskAlert, WithdrawalEvent
from notification import NotificationDispatcher

import structlog
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration (resolved from environment variables)
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "risk-alerting-consumer")
KAFKA_AUTO_OFFSET_RESET = os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest")

TOPIC_PAYMENT_STATUS = os.getenv("TOPIC_PAYMENT_STATUS_CHANGE", "payment.status.change")
TOPIC_WITHDRAWAL = os.getenv("TOPIC_WITHDRAWAL_MESSAGE", "withdrawal.message")
TOPIC_ALERTS_OUT = os.getenv("TOPIC_ALERTS_OUT", "opsgenie.alerts")

# Maximum events kept per-user for sliding-window calculations (configurable)
MAX_HISTORY_PER_USER = int(os.getenv("MAX_HISTORY_PER_USER", "500"))

# ---------------------------------------------------------------------------
# In-memory event store (replace with Redis in production)
# ---------------------------------------------------------------------------


class EventStore:
    """
    Lightweight in-memory per-user event store.

    Stores the most-recent MAX_HISTORY_PER_USER events for each user so that
    sliding-window checks in alert_engine don't need external state.
    """

    def __init__(self, max_per_user: int = MAX_HISTORY_PER_USER) -> None:
        self._deposits: Dict[int, List[DepositEvent]] = defaultdict(list)
        self._withdrawals: Dict[int, List[WithdrawalEvent]] = defaultdict(list)
        self._shared_methods: Dict[str, set] = defaultdict(set)  # recurring_ref -> {user_ids}
        self._max = max_per_user

    # -- deposits --

    def append_deposit(self, event: DepositEvent) -> None:
        uid = event.content.user_id
        self._deposits[uid].append(event)
        if len(self._deposits[uid]) > self._max:
            self._deposits[uid] = self._deposits[uid][-self._max:]
        # Track shared payment instruments
        ref = event.content.recurring_reference
        if ref:
            self._shared_methods[ref].add(uid)

    def get_deposits(self, user_id: int) -> List[DepositEvent]:
        return self._deposits.get(user_id, [])

    # -- withdrawals --

    def append_withdrawal(self, event: WithdrawalEvent) -> None:
        uid = event.user_id
        self._withdrawals[uid].append(event)
        if len(self._withdrawals[uid]) > self._max:
            self._withdrawals[uid] = self._withdrawals[uid][-self._max:]

    def get_withdrawals(self, user_id: int) -> List[WithdrawalEvent]:
        return self._withdrawals.get(user_id, [])

    # -- shared methods --

    def get_users_for_method(self, recurring_ref: str) -> set:
        return self._shared_methods.get(recurring_ref, set())


# ---------------------------------------------------------------------------
# Kafka message parsers
# ---------------------------------------------------------------------------


def _parse_deposit_event(raw: str) -> Optional[DepositEvent]:
    """Deserialize a JSON Kafka message into a DepositEvent."""
    try:
        payload = json.loads(raw)
        content = PaymentStatusChangeEvent(**payload.get("content", payload))
        return DepositEvent(
            event_id=payload.get("event_id", ""),
            content=content,
        )
    except Exception as exc:
        log.warning("Could not parse deposit event: %s — %s", exc, raw[:200])
        return None


def _parse_withdrawal_event(raw: str) -> Optional[WithdrawalEvent]:
    """Deserialize a JSON Kafka message into a WithdrawalEvent."""
    try:
        payload = json.loads(raw)
        return WithdrawalEvent(**payload)
    except Exception as exc:
        log.warning("Could not parse withdrawal event: %s — %s", exc, raw[:200])
        return None


# ---------------------------------------------------------------------------
# Event processors
# ---------------------------------------------------------------------------


def process_deposit_event(
    event: DepositEvent,
    store: EventStore,
    dispatcher: NotificationDispatcher,
    now: Optional[datetime] = None,
) -> List[RiskAlert]:
    """
    Persist the deposit event and evaluate all deposit alert rules.
    Returns the list of triggered alerts (may be empty).
    """
    store.append_deposit(event)
    user_id = event.content.user_id
    history = store.get_deposits(user_id)
    alerts = evaluate_deposit_rules(user_id, history, now=now)

    # Check shared payment methods cross-user
    ref = event.content.recurring_reference
    if ref:
        from .alert_engine import check_shared_payment_methods
        users = store.get_users_for_method(ref)
        shared_alert = check_shared_payment_methods(ref, users)
        if shared_alert:
            alerts.append(shared_alert)

    for alert in alerts:
        try:
            dispatcher.dispatch(alert)
            log.info("Alert dispatched: %s for user %s", alert.alert_name, user_id)
        except Exception as exc:
            log.exception("Failed to dispatch alert %s: %s", alert.alert_name, exc)

    return alerts


def process_withdrawal_event(
    event: WithdrawalEvent,
    store: EventStore,
    dispatcher: NotificationDispatcher,
    now: Optional[datetime] = None,
) -> List[RiskAlert]:
    """Persist the withdrawal and evaluate all withdrawal alert rules."""
    store.append_withdrawal(event)
    history = store.get_withdrawals(event.user_id)
    alerts = evaluate_withdrawal_rules(event.user_id, history, now=now)

    for alert in alerts:
        try:
            dispatcher.dispatch(alert)
            log.info("Withdrawal alert dispatched: %s for user %s", alert.alert_name, event.user_id)
        except Exception as exc:
            log.exception("Failed to dispatch withdrawal alert %s: %s", alert.alert_name, exc)

    return alerts


# ---------------------------------------------------------------------------
# Kafka consumer loop (requires confluent-kafka or kafka-python)
# ---------------------------------------------------------------------------


def _make_consumer():
    """
    Build a Kafka consumer.  Tries confluent-kafka first, falls back to
    kafka-python.  Returns a consumer instance and a consume function.
    """
    try:
        from confluent_kafka import Consumer as CConsumer, KafkaError

        conf = {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": KAFKA_GROUP_ID,
            "auto.offset.reset": KAFKA_AUTO_OFFSET_RESET,
            "enable.auto.commit": True,
        }
        consumer = CConsumer(conf)
        consumer.subscribe([TOPIC_PAYMENT_STATUS, TOPIC_WITHDRAWAL])

        def poll() -> Optional[tuple]:
            msg = consumer.poll(1.0)
            if msg is None:
                return None
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("Kafka error: %s", msg.error())
                return None
            return msg.topic(), msg.value().decode("utf-8")

        return consumer, poll

    except ImportError:
        from kafka import KafkaConsumer as KConsumer  # type: ignore

        consumer = KConsumer(
            TOPIC_PAYMENT_STATUS,
            TOPIC_WITHDRAWAL,
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS.split(","),
            group_id=KAFKA_GROUP_ID,
            auto_offset_reset=KAFKA_AUTO_OFFSET_RESET,
            value_deserializer=lambda v: v.decode("utf-8"),
        )

        _iter = iter(consumer)

        def poll() -> Optional[tuple]:
            try:
                msg = next(_iter)
                return msg.topic, msg.value
            except StopIteration:
                return None

        return consumer, poll


def run_consumer(
    store: Optional[EventStore] = None,
    dispatcher: Optional[NotificationDispatcher] = None,
) -> None:
    """
    Main consumer loop.  Blocks indefinitely, processing events as they arrive.

    Parameters
    ----------
    store:
        Event store instance (uses a fresh in-memory store if not provided).
    dispatcher:
        Notification dispatcher (uses the default dispatcher if not provided).
    """
    if store is None:
        store = EventStore()
    if dispatcher is None:
        dispatcher = NotificationDispatcher()

    consumer, poll = _make_consumer()

    # Graceful shutdown
    running = True

    def _shutdown(signum, frame):
        nonlocal running
        log.info("Shutting down Kafka consumer (signal %s)", signum)
        running = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info(
        "Risk-alerting consumer started. Topics: %s, %s",
        TOPIC_PAYMENT_STATUS,
        TOPIC_WITHDRAWAL,
    )

    while running:
        result = poll()
        if result is None:
            continue
        topic, raw_value = result

        if topic == TOPIC_PAYMENT_STATUS:
            event = _parse_deposit_event(raw_value)
            if event:
                process_deposit_event(event, store, dispatcher)
        elif topic == TOPIC_WITHDRAWAL:
            event = _parse_withdrawal_event(raw_value)
            if event:
                process_withdrawal_event(event, store, dispatcher)
        else:
            log.warning("Received message on unexpected topic: %s", topic)

    log.info("Consumer loop exited.")
