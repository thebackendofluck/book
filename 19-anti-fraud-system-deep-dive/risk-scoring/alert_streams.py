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
alert_streams.py — Real-time payment risk detection using Kafka.

Mirrors AlertStreams.scala and RiskAlertingApp.scala.

Each AlertStream class implements a specific detection pattern:
  - Windowed aggregation  : total deposits in 24 hours
  - Consecutive failures  : last 5 deposits declined
  - First-event detection : high first deposit >= $5,000

Because Python's kafka-python / confluent-kafka do not have a Streams DSL,
these are implemented as stateful consumer loops using a per-user in-memory
state store (backed by Redis in production). The interface mirrors the Scala
AlertStream trait so it is easy to swap in a Faust or Bytewax topology.
"""
from __future__ import annotations

import json
import logging
import os
import time as time_module
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from models import Alert, AlertDescription, OpsgenieAlert

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

@dataclass
class DepositMessage:
    user_id: int
    payment_id: int
    status: str         # SUCCEEDED | FAILED
    amount: int         # cents
    currency: str
    timestamp: datetime


@dataclass
class AlertsCache:
    """In-memory cache of AlertDescription records (mirrors AlertsDescriptionCache)."""
    _data: dict[str, AlertDescription] = field(default_factory=dict)

    def load(self, descriptions: list[AlertDescription]) -> None:
        self._data = {d.alert_name: d for d in descriptions}

    def get(self, alert_name: str) -> AlertDescription | None:
        return self._data.get(alert_name)

    def is_active(self, alert_name: str) -> bool:
        d = self._data.get(alert_name)
        return d.active if d else False


alerts_cache = AlertsCache()


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class AlertStream(ABC):
    """Base class for all alert detection streams."""

    @abstractmethod
    def process_message(self, msg: DepositMessage) -> OpsgenieAlert | None:
        ...

    @property
    @abstractmethod
    def alert_name(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Detector: Total deposits in 24 hours
# ---------------------------------------------------------------------------

class TotalAmountOfDepositsIn24Hours(AlertStream):
    """
    Detects when a user's total deposits in a rolling 24-hour window
    exceed the threshold (default 1,000 currency units = 100,000 cents).

    Mirrors TotalAmountOfDepositsIn24Hours.scala.
    """
    THRESHOLD_CENTS = 100_000
    WINDOW_SECONDS  = 24 * 3600
    alert_name = "TotalAmountOfDepositsIn24Hours"

    def __init__(self) -> None:
        # user_id -> deque of (timestamp, amount_cents, currency)
        self._windows: dict[int, deque] = defaultdict(deque)

    def process_message(self, msg: DepositMessage) -> OpsgenieAlert | None:
        if msg.status != "SUCCEEDED":
            return None

        window = self._windows[msg.user_id]
        now_ts = msg.timestamp.timestamp()
        window.append((now_ts, msg.amount, msg.currency))

        # Drop events older than 24 hours
        cutoff = now_ts - self.WINDOW_SECONDS
        while window and window[0][0] < cutoff:
            window.popleft()

        # Group by currency and check each
        by_currency: dict[str, int] = defaultdict(int)
        for _, amt, cur in window:
            by_currency[cur] += amt

        for currency, total in by_currency.items():
            if total > self.THRESHOLD_CENTS:
                return OpsgenieAlert(
                    message=(
                        f"User {msg.user_id} deposited more than "
                        f"{self.THRESHOLD_CENTS // 100} {currency} in 24h"
                    ),
                    alert_name=self.alert_name,
                    details={"userId": str(msg.user_id), "amount": str(total)},
                    priority=_get_priority(self.alert_name),
                    user_ids=[str(msg.user_id)],
                )
        return None


# ---------------------------------------------------------------------------
# Detector: Last 5 deposits declined
# ---------------------------------------------------------------------------

class Last5DepositsDeclined(AlertStream):
    """
    Detects when a user's last 5 consecutive deposit attempts all failed.
    Counter resets to 0 on any successful deposit.

    Mirrors Last5DepositsDeclined.scala.
    """
    THRESHOLD = 5
    alert_name = "Last5DepositsDeclined"

    def __init__(self) -> None:
        self._failure_counts: dict[int, int] = defaultdict(int)

    def process_message(self, msg: DepositMessage) -> OpsgenieAlert | None:
        if msg.status == "SUCCEEDED":
            self._failure_counts[msg.user_id] = 0
            return None
        if msg.status == "FAILED":
            self._failure_counts[msg.user_id] += 1
        count = self._failure_counts[msg.user_id]
        if count >= self.THRESHOLD:
            return OpsgenieAlert(
                message="Most recent 5 deposit attempts were unsuccessful",
                alert_name=self.alert_name,
                details={"userId": str(msg.user_id), "failureCount": str(count)},
                priority=_get_priority(self.alert_name),
                user_ids=[str(msg.user_id)],
            )
        return None


# ---------------------------------------------------------------------------
# Detector: High first deposit >= $5,000
# ---------------------------------------------------------------------------

class HighDepositor(AlertStream):
    """
    Detects when a user's very first successful deposit is >= $5,000 (500,000 cents).
    Mirrors HighDepositor.scala.
    """
    THRESHOLD_CENTS = 500_000
    alert_name = "HighDepositor"

    def __init__(self) -> None:
        self._deposit_counts: dict[int, int] = defaultdict(int)

    def process_message(self, msg: DepositMessage) -> OpsgenieAlert | None:
        if msg.status != "SUCCEEDED":
            return None
        self._deposit_counts[msg.user_id] += 1
        if self._deposit_counts[msg.user_id] == 1 and msg.amount >= self.THRESHOLD_CENTS:
            return OpsgenieAlert(
                message="First deposit equal to or greater than $5000.00",
                alert_name=self.alert_name,
                details={"userId": str(msg.user_id), "paymentId": str(msg.payment_id)},
                priority=_get_priority(self.alert_name),
                user_ids=[str(msg.user_id)],
            )
        return None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_priority(alert_name: str) -> str | None:
    d = alerts_cache.get(alert_name)
    return d.priority if d else None


# ---------------------------------------------------------------------------
# Risk alerting application (mirrors RiskAlertingApp.scala)
# ---------------------------------------------------------------------------

class RiskAlertingApp:
    """
    Kafka consumer loop that fans out to all registered alert detectors.
    Mirrors the topology wiring in RiskAlertingApp.createTopology().
    """

    def __init__(self, kafka_cfg: dict[str, str], opsgenie_enabled: bool = False) -> None:
        self._consumer = Consumer({
            "bootstrap.servers": kafka_cfg["bootstrap_servers"],
            "group.id": "risk-alerting",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": "false",
        })
        self._producer = Producer({"bootstrap.servers": kafka_cfg["bootstrap_servers"]})
        self._opsgenie_enabled = opsgenie_enabled

        self._streams: list[AlertStream] = [
            TotalAmountOfDepositsIn24Hours(),
            Last5DepositsDeclined(),
            HighDepositor(),
        ]

    def run(self) -> None:
        self._consumer.subscribe(["payment-status-changes"])
        log.info("risk alerting consumer started")

        try:
            while True:
                raw = self._consumer.poll(timeout=1.0)
                if raw is None:
                    continue
                if raw.error():
                    if raw.error().code() != KafkaError._PARTITION_EOF:
                        log.error("kafka error", error=str(raw.error()))
                    continue

                try:
                    payload = json.loads(raw.value())
                    msg = self._parse_deposit_message(payload)
                    self._process(msg)
                    self._consumer.commit(raw)
                except Exception as exc:
                    log.error("message processing error", error=str(exc))
        except KeyboardInterrupt:
            pass
        finally:
            self._consumer.close()

    def _process(self, msg: DepositMessage) -> None:
        for stream in self._streams:
            if not alerts_cache.is_active(stream.alert_name):
                continue
            alert = stream.process_message(msg)
            if alert:
                self._emit_alert(alert)

    def _emit_alert(self, alert: OpsgenieAlert) -> None:
        self._producer.produce(
            "opsgenie-alerts",
            key=alert.alert_name,
            value=json.dumps(alert.model_dump()),
        )
        self._producer.flush()
        log.info("alert emitted", alert_name=alert.alert_name)

    @staticmethod
    def _parse_deposit_message(payload: dict) -> DepositMessage:
        content = payload.get("content", payload)
        return DepositMessage(
            user_id=int(content["userId"]),
            payment_id=int(content.get("paymentId", 0)),
            status=content["status"],
            amount=int(content.get("amount", 0)),
            currency=content.get("currency", "GBP"),
            timestamp=datetime.fromisoformat(
                content.get("timestamp", datetime.now(timezone.utc).isoformat())
            ),
        )


if __name__ == "__main__":
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
    cfg = {
        "bootstrap_servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    }
    app = RiskAlertingApp(cfg, opsgenie_enabled=os.environ.get("OPSGENIE_ENABLED", "false") == "true")
    app.run()
